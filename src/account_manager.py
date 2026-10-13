import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import orjson

from src.cli import run_agy
from src.security import get_logger

logger = get_logger("account_manager")

# An account whose 5h or weekly bucket is at or below this fraction is skipped
# by rotation (proactive failover before Google starts returning 429s).
LOW_QUOTA_FRACTION = 0.05
EXHAUSTED_QUOTA_FRACTION = 0.01

GEMINI = "gemini"
THIRD_PARTY = "third_party"

QUOTA_COMMAND = ["--dangerously-skip-permissions", "--output-format", "json", "-p", "/quota"]
# `/quota` is a slash command, so --disable-slash-commands must NOT be passed here.
# --dangerously-skip-permissions is safe for this fixed, server-chosen prompt
# (no user input reaches it) and prevents a headless permission prompt from
# stalling the quota call.


def model_quota_group(model: str) -> str:
    """Maps a model id to the agy quota group that bills it."""
    return GEMINI if "gemini" in model.lower() else THIRD_PARTY


@dataclass
class Account:
    id: str
    email: str
    priority: int = 1
    role: str = "primary"
    home_dir: str = ""
    exhausted_until: Optional[float] = None
    last_quota: Optional[Dict[str, Any]] = None
    last_quota_time: float = 0.0
    last_quota_error: Optional[str] = None
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    _refresh_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)

    def in_backoff(self, now: float) -> bool:
        return self.exhausted_until is not None and now < self.exhausted_until

    def has_quota_for(self, group: str) -> bool:
        """True unless a *known* quota reading says this group is low. Unknown counts as usable."""
        q = self.last_quota
        if not q:
            return True
        for window in ("5h", "weekly"):
            fraction = q.get(f"{group}_{window}_fraction")
            if fraction is not None and fraction <= LOW_QUOTA_FRACTION:
                return False
        return True


def _bucket_status(fractions: List[float]) -> str:
    if any(f <= EXHAUSTED_QUOTA_FRACTION for f in fractions):
        return "exhausted"
    if any(f <= LOW_QUOTA_FRACTION for f in fractions):
        return "low"
    return "healthy"


def parse_quota_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parses `agy -p "/quota" --output-format json` into flat quota metrics,
    keyed `<group>_<window>_{fraction,percent,reset_time,description}`.
    """
    groups = data.get("command", {}).get("data", {}).get("groups", [])
    buckets: Dict[str, Dict[str, Any]] = {}

    for group in groups:
        gname = group.get("name", "")
        if "Gemini" in gname:
            prefix = GEMINI
        elif "Claude" in gname or "GPT" in gname or "3p" in gname:
            prefix = THIRD_PARTY
        else:
            continue
        for b in group.get("buckets", []):
            window = b.get("window") or ("5h" if b.get("id", "").endswith("5h") else "weekly")
            if window not in ("5h", "weekly"):
                continue
            buckets[f"{prefix}_{window}"] = b

    result: Dict[str, Any] = {}
    for prefix in (GEMINI, THIRD_PARTY):
        for window in ("5h", "weekly"):
            key = f"{prefix}_{window}"
            b = buckets.get(key, {})
            fraction = float(b.get("remaining_fraction", 1.0))
            result[f"{key}_fraction"] = round(fraction, 4)
            result[f"{key}_percent"] = round(fraction * 100.0, 1)
            result[f"{key}_reset_time"] = b.get("reset_time", "")
            result[f"{key}_description"] = b.get("description", "")

    result["status"] = _bucket_status([result["gemini_5h_fraction"], result["gemini_weekly_fraction"]])
    result["third_party_status"] = _bucket_status(
        [result["third_party_5h_fraction"], result["third_party_weekly_fraction"]]
    )
    return result


def _email_from_token(token_file: str) -> str:
    with open(token_file, "r", encoding="utf-8") as f:
        id_token = json.load(f).get("id_token", "")
    if not id_token or "." not in id_token:
        return ""
    payload = id_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")


class AccountManager:
    def __init__(
        self,
        accounts_dir: Optional[str] = None,
        cache_ttl: float = 60.0,
        failure_ttl: float = 30.0,
        quota_timeout: float = 60.0,  # /quota normally takes ~5s but spikes under load
    ):
        self.accounts_dir = accounts_dir or os.environ.get("ACCOUNTS_DIR") or os.path.join(os.getcwd(), "accounts")
        self.cache_ttl = cache_ttl
        self.failure_ttl = failure_ttl
        self.quota_timeout = quota_timeout
        self.accounts: List[Account] = []

    def discover_accounts(self) -> None:
        """
        Discovers accounts in self.accounts_dir:
            accounts_dir/<id>/metadata.json
            accounts_dir/<id>/.gemini/antigravity-cli/antigravity-oauth-token
        Falls back to a single account using the process's own HOME.
        """
        accounts = []
        if os.path.isdir(self.accounts_dir):
            for entry in sorted(os.listdir(self.accounts_dir)):
                acc_path = os.path.join(self.accounts_dir, entry)
                if not os.path.isdir(acc_path):
                    continue

                meta: Dict[str, Any] = {}
                meta_file = os.path.join(acc_path, "metadata.json")
                if os.path.exists(meta_file):
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                    except (OSError, ValueError) as e:
                        logger.warning(f"Failed to read metadata for {entry}: {e}")

                email = meta.get("email", "")
                token_file = os.path.join(acc_path, ".gemini", "antigravity-cli", "antigravity-oauth-token")
                if not email and os.path.exists(token_file):
                    try:
                        email = _email_from_token(token_file)
                    except (OSError, ValueError) as e:
                        logger.warning(f"Failed to extract email from token for {entry}: {e}")

                accounts.append(Account(
                    id=entry,
                    email=email or entry,
                    priority=int(meta.get("priority", 99)),
                    role=meta.get("role", "secondary"),
                    home_dir=acc_path,
                ))

        accounts.sort(key=lambda a: a.priority)
        if not accounts:
            accounts.append(Account(id="default", email="default", priority=1, role="primary"))

        self.accounts = accounts
        logger.info(f"Discovered {len(accounts)} accounts: {[f'{a.id} (p={a.priority})' for a in accounts]}")

    def ensure_discovered(self) -> None:
        if not self.accounts:
            self.discover_accounts()

    def _quota_is_fresh(self, account: Account, now: float) -> bool:
        if account.last_quota_error is not None:
            return now - account.last_quota_time < self.failure_ttl
        return account.last_quota is not None and now - account.last_quota_time < self.cache_ttl

    async def _refresh_quota(self, account: Account) -> None:
        async with account._refresh_lock:
            # Another caller may have refreshed while we waited for the lock.
            if self._quota_is_fresh(account, time.time()):
                return
            try:
                code, out, err = await run_agy(QUOTA_COMMAND, account.home_dir, timeout=self.quota_timeout)
                if code != 0 or not out.strip():
                    raise RuntimeError(f"exit code {code}: {err[-300:]}")
                account.last_quota = parse_quota_data(orjson.loads(out))
                account.last_quota_error = None
            except Exception as e:  # FileNotFoundError, CLITimeout, bad JSON, non-zero exit
                account.last_quota_error = str(e) or type(e).__name__
                logger.warning(f"Quota refresh failed for {account.id}: {account.last_quota_error}")
            account.last_quota_time = time.time()

    async def fetch_account_quota(self, account: Account, force: bool = False) -> Optional[Dict[str, Any]]:
        """
        Returns cached quota for an account. A stale cache is served immediately
        while a background refresh runs; only an account that has never been
        read blocks the caller.
        """
        now = time.time()
        if force or account.last_quota_time == 0.0:
            await self._refresh_quota(account)
        elif not self._quota_is_fresh(account, now):
            if account._refresh_task is None or account._refresh_task.done():
                account._refresh_task = asyncio.create_task(self._refresh_quota(account))
        return account.last_quota

    async def candidate_accounts(self, model: str = "") -> List[Account]:
        """
        Accounts in the order a request should try them:
        1. not in 429 backoff and with quota left for the model's group, by priority;
        2. then the remaining non-backoff accounts, by priority;
        3. if every account is in backoff, all of them, soonest-to-expire first.
        """
        self.ensure_discovered()
        group = model_quota_group(model) if model else GEMINI
        await asyncio.gather(*(self.fetch_account_quota(a) for a in self.accounts))

        now = time.time()
        available = [a for a in self.accounts if not a.in_backoff(now)]
        if not available:
            return sorted(self.accounts, key=lambda a: a.exhausted_until or 0)
        healthy = [a for a in available if a.has_quota_for(group)]
        return healthy + [a for a in available if a not in healthy]

    async def get_active_account(self, model: str = "") -> Account:
        return (await self.candidate_accounts(model))[0]

    def mark_account_exhausted(self, account_id: str, duration_seconds: float = 300.0) -> None:
        """Puts an account in reactive 429 backoff so traffic shifts to the next account."""
        for acc in self.accounts:
            if acc.id == account_id:
                acc.exhausted_until = time.time() + duration_seconds
                logger.warning(f"Account {acc.id} in quota backoff for {duration_seconds:.0f}s")
                return

    async def get_all_quotas(self, include_emails: bool = True) -> Dict[str, Any]:
        """Full status of all accounts, the active account and reset countdowns."""
        candidates = await self.candidate_accounts()
        active = candidates[0]
        now = time.time()

        statuses = []
        for acc in self.accounts:
            backoff = acc.in_backoff(now)
            statuses.append({
                "id": acc.id,
                "email": acc.email,
                "role": acc.role,
                "priority": acc.priority,
                "is_active": acc.id == active.id,
                "is_rate_limited": backoff,
                "rate_limit_expires_in_seconds": round(acc.exhausted_until - now, 1) if backoff else 0,
                "quota": acc.last_quota or {"status": "unknown"},
                "quota_age_seconds": round(now - acc.last_quota_time, 1) if acc.last_quota_time else None,
                "quota_error": acc.last_quota_error,
            })

        if not include_emails:
            for status in statuses:
                del status["email"]
        result = {
            "active_account_id": active.id,
            "total_accounts": len(self.accounts),
            "timestamp": int(now),
            "accounts": statuses,
        }
        if include_emails:
            result["active_email"] = active.email
        return result


account_manager = AccountManager()
