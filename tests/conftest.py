import asyncio
import json
import os
import sys

import pytest

from src import cli
from src.account_manager import account_manager
from src.model_registry import model_registry

FAKE_AGY = os.path.join(os.path.dirname(__file__), "fake_agy.py")
API_KEY = "sk-test-key"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


class FakeAccounts:
    """Two fake accounts on disk, wired to tests/fake_agy.py."""

    def __init__(self, root):
        self.root = root
        self.work_root = os.path.join(root, "work")
        self.dir = os.path.join(root, "accounts")
        for i in (1, 2):
            home = os.path.join(self.dir, f"account_{i}")
            os.makedirs(home)
            with open(os.path.join(home, "metadata.json"), "w") as f:
                json.dump({"email": f"user{i}@example.com", "priority": i,
                           "role": "primary" if i == 1 else "secondary"}, f)

    def home(self, account_id):
        return os.path.join(self.dir, account_id)

    def set(self, account_id, **scenario):
        with open(os.path.join(self.home(account_id), "scenario.json"), "w") as f:
            json.dump(scenario, f)

    def calls(self, account_id, kind="chat"):
        path = os.path.join(self.home(account_id), "calls.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [c for c in map(json.loads, f) if c["kind"] == kind]

    def account(self, account_id):
        return next(a for a in account_manager.accounts if a.id == account_id)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "agy_command", lambda: [sys.executable, FAKE_AGY])
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.setenv("PROXY_API_KEY", API_KEY)
    accounts = FakeAccounts(str(tmp_path))
    monkeypatch.setenv("AGY_WORKDIR_ROOT", accounts.work_root)
    monkeypatch.setenv("API_KEYS_FILE", os.path.join(accounts.root, "keys.json"))
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)

    monkeypatch.setattr(account_manager, "accounts_dir", accounts.dir)
    account_manager.discover_accounts()
    monkeypatch.setattr(model_registry, "_models", [])
    # Each test runs on its own event loop; a lock used by an earlier test is bound to that loop.
    monkeypatch.setattr(model_registry, "_lock", asyncio.Lock())
    return accounts


@pytest.fixture
def client(fake):
    from fastapi.testclient import TestClient
    from src.main import app

    with TestClient(app) as c:
        yield c
