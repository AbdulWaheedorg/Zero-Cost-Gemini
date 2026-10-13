"""
Named, revocable API keys.

Keys live in a JSON file (API_KEYS_FILE, default ./keys.json) that stores only
SHA-256 hashes, never the keys themselves. The file is re-read whenever it
changes, so keys created or revoked from the CLI take effect without a restart.

The legacy PROXY_API_KEY from .env is still accepted (as key id "legacy-env")
until it is removed from the environment.

CLI (run from the directory the service runs in):
    python -m src.keys create <name>     # prints the new key once
    python -m src.keys list
    python -m src.keys revoke <key_id>
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

KEY_PREFIX = "sk-agy-"
LEGACY_KEY_ID = "legacy-env"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def public_view(record: Dict[str, Any]) -> Dict[str, Any]:
    """A key record without its hash, safe to return from the API."""
    return {
        "id": record["id"],
        "name": record["name"],
        "prefix": record.get("prefix", ""),
        "created_at": record.get("created_at"),
        "revoked_at": record.get("revoked_at"),
        "active": not record.get("revoked_at"),
    }


class KeyStore:
    def __init__(self, path: Optional[str] = None):
        self._path = path
        self._records: List[Dict[str, Any]] = []
        self._signature: Optional[Tuple[int, int, int]] = None
        self._lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._path or os.environ.get("API_KEYS_FILE") or os.path.join(os.getcwd(), "keys.json")

    def _reload(self) -> None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            self._records, self._signature = [], None
            return
        signature = (st.st_mtime_ns, st.st_size, st.st_ino)
        if signature == self._signature:
            return
        with open(self.path, "r", encoding="utf-8") as f:
            self._records = json.load(f).get("keys", [])
        self._signature = signature

    def _write(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(prefix=".keys-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"keys": self._records}, f, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)  # atomic: readers never see a half-written file
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        self._signature = None

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._reload()
            return [public_view(r) for r in self._records]

    def create(self, name: str) -> Tuple[Dict[str, Any], str]:
        """Returns (public record, plaintext key). The plaintext is never stored."""
        name = name.strip()
        if not name:
            raise ValueError("Key name must not be empty.")
        token = KEY_PREFIX + secrets.token_urlsafe(32)
        record = {
            "id": "key_" + secrets.token_hex(6),
            "name": name[:100],
            "prefix": token[: len(KEY_PREFIX) + 6],
            "sha256": _hash(token),
            "created_at": _now(),
            "revoked_at": None,
        }
        with self._lock:
            self._signature = None
            self._reload()
            self._records.append(record)
            self._write()
        return public_view(record), token

    def revoke(self, key_id: str) -> Optional[Dict[str, Any]]:
        """Revokes a key. Returns its public record, or None if no such key."""
        with self._lock:
            self._signature = None
            self._reload()
            for record in self._records:
                if record["id"] == key_id:
                    if not record.get("revoked_at"):
                        record["revoked_at"] = _now()
                        self._write()
                    return public_view(record)
        return None

    def verify(self, token: str) -> Optional[Dict[str, Any]]:
        """Returns the matching active key record, or None."""
        token = token.strip()
        if not token:
            return None
        legacy = os.environ.get("PROXY_API_KEY", "").strip()
        if legacy and hmac.compare_digest(token, legacy):
            return {"id": LEGACY_KEY_ID, "name": "PROXY_API_KEY (.env)"}
        digest = _hash(token)
        with self._lock:
            self._reload()
            records = list(self._records)
        match = None
        for record in records:  # compare against every key so timing doesn't reveal which matched
            if hmac.compare_digest(digest, record.get("sha256", "")) and not record.get("revoked_at"):
                match = record
        return public_view(match) if match else None

    def has_active_keys(self) -> bool:
        if os.environ.get("PROXY_API_KEY", "").strip():
            return True
        with self._lock:
            self._reload()
            return any(not r.get("revoked_at") for r in self._records)


key_store = KeyStore()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.keys", description="Manage proxy API keys.")
    parser.add_argument("--file", help="keys file (default: $API_KEYS_FILE or ./keys.json)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list keys")
    create = sub.add_parser("create", help="create a key and print it once")
    create.add_argument("name", help="a label, e.g. laptop or cursor")
    revoke = sub.add_parser("revoke", help="revoke a key by id")
    revoke.add_argument("key_id")
    args = parser.parse_args(argv)

    store = KeyStore(args.file)
    if args.command == "create":
        record, token = store.create(args.name)
        print(f"Created {record['id']} ({record['name']}). Save this key now; it is not stored and cannot be shown again:")
        print(token)
    elif args.command == "revoke":
        record = store.revoke(args.key_id)
        if record is None:
            print(f"No key with id {args.key_id}", file=sys.stderr)
            return 1
        print(f"Revoked {record['id']} ({record['name']}) at {record['revoked_at']}")
    else:
        rows = store.list()
        if not rows:
            print(f"No keys in {store.path}")
        for r in rows:
            state = "active" if r["active"] else f"revoked {r['revoked_at']}"
            print(f"{r['id']}  {r['prefix']}…  {r['name']:<24} created {r['created_at']}  {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
