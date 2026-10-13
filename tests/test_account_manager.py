import asyncio
import time

from src.account_manager import Account, AccountManager, parse_quota_data

SAMPLE_QUOTA_OUTPUT = {
    "status": "SUCCESS",
    "command": {
        "name": "usage",
        "data": {
            "groups": [
                {
                    "name": "Gemini Models",
                    "buckets": [
                        {"id": "gemini-weekly", "window": "weekly", "remaining_fraction": 0.854,
                         "reset_time": "2026-10-20T16:37:19Z", "description": "weekly desc"},
                        {"id": "gemini-5h", "window": "5h", "remaining_fraction": 0.725,
                         "reset_time": "2026-10-13T21:37:19Z", "description": "5h desc"},
                    ],
                },
                {
                    "name": "Claude and GPT models",
                    "buckets": [
                        {"id": "3p-weekly", "window": "weekly", "remaining_fraction": 0.5,
                         "reset_time": "2026-10-20T16:37:58Z"},
                        {"id": "3p-5h", "window": "5h", "remaining_fraction": 0.04,
                         "reset_time": "2026-10-13T21:37:58Z"},
                    ],
                },
            ]
        },
    },
}

def quota(g5h=1.0, gweek=1.0, t5h=1.0, tweek=1.0):
    return {
        "gemini_5h_fraction": g5h, "gemini_weekly_fraction": gweek,
        "third_party_5h_fraction": t5h, "third_party_weekly_fraction": tweek,
    }

def manager(*quotas):
    """An AccountManager whose accounts already hold fresh quota readings."""
    mgr = AccountManager(accounts_dir="/nonexistent")
    mgr.accounts = [
        Account(id=f"account_{i}", email=f"user{i}@example.com", priority=i,
                last_quota=q, last_quota_time=time.time())
        for i, q in enumerate(quotas, start=1)
    ]
    return mgr

def test_parse_quota_output():
    parsed = parse_quota_data(SAMPLE_QUOTA_OUTPUT)
    assert parsed["gemini_5h_fraction"] == 0.725
    assert parsed["gemini_5h_percent"] == 72.5
    assert parsed["gemini_5h_reset_time"] == "2026-10-13T21:37:19Z"
    assert parsed["gemini_5h_description"] == "5h desc"
    assert parsed["gemini_weekly_percent"] == 85.4
    assert parsed["third_party_5h_percent"] == 4.0
    assert parsed["third_party_weekly_fraction"] == 0.5
    assert parsed["status"] == "healthy"
    assert parsed["third_party_status"] == "low"

def test_parse_quota_status_thresholds():
    def with_5h(frac):
        data = {"command": {"data": {"groups": [{"name": "Gemini Models", "buckets": [
            {"window": "5h", "remaining_fraction": frac}]}]}}}
        return parse_quota_data(data)["status"]

    assert with_5h(0.5) == "healthy"
    assert with_5h(0.05) == "low"
    assert with_5h(0.01) == "exhausted"

async def test_rotates_when_primary_5h_low():
    mgr = manager(quota(g5h=0.03), quota())
    assert (await mgr.get_active_account("gemini-3.8-flash-low")).id == "account_2"

async def test_rotates_when_primary_weekly_low():
    mgr = manager(quota(g5h=0.9, gweek=0.02), quota())
    assert (await mgr.get_active_account("gemini-3.8-flash-low")).id == "account_2"

async def test_rotation_uses_quota_group_of_requested_model():
    mgr = manager(quota(g5h=0.01, t5h=0.9), quota())
    assert (await mgr.get_active_account("claude-sonnet-4-6")).id == "account_1"
    assert (await mgr.get_active_account("gemini-3.8-flash-low")).id == "account_2"

async def test_failback_to_primary_when_quota_restored():
    mgr = manager(quota(g5h=0.9), quota(g5h=0.99))
    assert (await mgr.get_active_account()).id == "account_1"

async def test_low_accounts_are_still_candidates_after_healthy_ones():
    mgr = manager(quota(g5h=0.01), quota(g5h=0.02))
    assert [a.id for a in await mgr.candidate_accounts("gemini-x")] == ["account_1", "account_2"]

async def test_reactive_backoff_and_expiry():
    mgr = manager(quota(), quota())
    mgr.mark_account_exhausted("account_1", duration_seconds=600)
    assert (await mgr.get_active_account()).id == "account_2"

    mgr.accounts[0].exhausted_until = time.time() - 1
    assert (await mgr.get_active_account()).id == "account_1"

async def test_all_in_backoff_returns_soonest_expiring_first():
    mgr = manager(quota(), quota())
    mgr.mark_account_exhausted("account_1", duration_seconds=600)
    mgr.mark_account_exhausted("account_2", duration_seconds=60)
    assert [a.id for a in await mgr.candidate_accounts()] == ["account_2", "account_1"]

async def test_quota_fetch_via_cli(fake):
    fake.set("account_1", quota={"gemini_5h": 0.42})
    from src.account_manager import account_manager
    acc = fake.account("account_1")
    q = await account_manager.fetch_account_quota(acc)
    assert q["gemini_5h_percent"] == 42.0
    assert acc.last_quota_error is None

async def test_quota_failure_is_unknown_and_cached(fake):
    """A failing quota call must not report 100% healthy, and must not re-run on every request."""
    from src.account_manager import account_manager
    fake.set("account_1", quota_fail=True)
    acc = fake.account("account_1")
    assert await account_manager.fetch_account_quota(acc) is None
    assert "exit code 1" in acc.last_quota_error
    for _ in range(5):
        await account_manager.get_active_account()
    assert len(fake.calls("account_1", "quota")) == 1  # failure is cached for failure_ttl

    status = await account_manager.get_all_quotas()
    acc_status = next(s for s in status["accounts"] if s["id"] == "account_1")
    assert acc_status["quota"] == {"status": "unknown"}
    assert acc_status["quota_error"]

async def test_stale_quota_is_served_while_refreshing_in_background(fake):
    from src.account_manager import account_manager
    fake.set("account_1", quota={"gemini_5h": 0.5})
    acc = fake.account("account_1")
    acc.last_quota = parse_quota_data({})
    acc.last_quota_time = time.time() - 3600  # stale

    q = await account_manager.fetch_account_quota(acc)
    assert q["gemini_5h_percent"] == 100.0  # stale value returned immediately
    await acc._refresh_task
    assert acc.last_quota["gemini_5h_percent"] == 50.0

async def test_concurrent_first_reads_run_one_quota_call_per_account(fake):
    from src.account_manager import account_manager
    await asyncio.gather(*(account_manager.get_active_account() for _ in range(10)))
    assert len(fake.calls("account_1", "quota")) == 1
    assert len(fake.calls("account_2", "quota")) == 1

async def test_discovery_reads_metadata_and_sorts(fake):
    from src.account_manager import account_manager
    assert [a.id for a in account_manager.accounts] == ["account_1", "account_2"]
    assert account_manager.accounts[0].email == "user1@example.com"

async def test_discovery_falls_back_to_default_account(tmp_path):
    mgr = AccountManager(accounts_dir=str(tmp_path / "missing"))
    mgr.discover_accounts()
    assert [a.id for a in mgr.accounts] == ["default"]
    assert mgr.accounts[0].home_dir == ""


async def test_email_read_from_token_when_metadata_has_none(fake):
    import base64
    import json
    import os
    from src.account_manager import account_manager

    home = os.path.join(fake.dir, "account_3")
    token_dir = os.path.join(home, ".gemini", "antigravity-cli")
    os.makedirs(token_dir)
    payload = base64.urlsafe_b64encode(json.dumps({"email": "tok@example.com"}).encode()).decode().rstrip("=")
    with open(os.path.join(token_dir, "antigravity-oauth-token"), "w") as f:
        json.dump({"id_token": f"h.{payload}.s"}, f)

    account_manager.discover_accounts()
    acc = next(a for a in account_manager.accounts if a.id == "account_3")
    assert acc.email == "tok@example.com" and acc.priority == 99


async def test_hung_quota_call_times_out(fake, monkeypatch):
    from src.account_manager import account_manager
    monkeypatch.setattr(account_manager, "quota_timeout", 1.0)
    fake.set("account_1", quota_hang=True)
    started = time.monotonic()
    assert await account_manager.fetch_account_quota(fake.account("account_1")) is None
    assert time.monotonic() - started < 5
    assert "did not finish" in fake.account("account_1").last_quota_error
