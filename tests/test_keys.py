import json
import os

import pytest

from src.keys import KEY_PREFIX, KeyStore, main
from tests.conftest import AUTH

ADMIN = "admin-test-key"
ADMIN_AUTH = {"Authorization": f"Bearer {ADMIN}"}


def models(client, key):
    return client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})


# --- store ------------------------------------------------------------------

def test_create_verify_revoke(tmp_path):
    store = KeyStore(str(tmp_path / "keys.json"))
    record, token = store.create("laptop")
    assert token.startswith(KEY_PREFIX) and len(token) > 40
    assert record["active"] and record["name"] == "laptop" and token.startswith(record["prefix"])
    assert store.verify(token)["id"] == record["id"]
    assert store.verify(token + "x") is None

    assert store.revoke(record["id"])["active"] is False
    assert store.verify(token) is None
    assert store.revoke("key_nope") is None


def test_file_stores_hashes_only_with_private_permissions(tmp_path):
    path = tmp_path / "keys.json"
    store = KeyStore(str(path))
    _, token = store.create("phone")
    raw = path.read_text()
    assert token not in raw and token[len(KEY_PREFIX):] not in raw
    assert json.loads(raw)["keys"][0]["sha256"]
    if os.name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600


def test_changes_from_another_process_are_picked_up(tmp_path):
    """The CLI and the server are different processes sharing the file."""
    path = str(tmp_path / "keys.json")
    server, cli = KeyStore(path), KeyStore(path)
    _, token = cli.create("ci")
    assert server.verify(token)
    cli.revoke(cli.list()[0]["id"])
    assert server.verify(token) is None


def test_legacy_env_key_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXY_API_KEY", "sk-legacy")
    store = KeyStore(str(tmp_path / "keys.json"))
    assert store.verify("sk-legacy")["id"] == "legacy-env"
    monkeypatch.delenv("PROXY_API_KEY")
    assert store.verify("sk-legacy") is None
    assert not store.has_active_keys()


def test_empty_name_rejected(tmp_path):
    with pytest.raises(ValueError):
        KeyStore(str(tmp_path / "keys.json")).create("  ")


def test_cli(tmp_path, capsys):
    path = str(tmp_path / "keys.json")
    assert main(["--file", path, "create", "cursor"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert token.startswith(KEY_PREFIX)

    assert main(["--file", path, "list"]) == 0
    listing = capsys.readouterr().out
    key_id = listing.split()[0]
    assert "cursor" in listing and "active" in listing and token not in listing

    assert main(["--file", path, "revoke", key_id]) == 0
    assert main(["--file", path, "revoke", "key_missing"]) == 1
    capsys.readouterr()
    assert KeyStore(path).verify(token) is None


# --- HTTP -------------------------------------------------------------------

def test_store_keys_authenticate_and_revocation_is_immediate(client):
    from src.keys import key_store
    record, token = key_store.create("integration")
    assert models(client, token).status_code == 200
    key_store.revoke(record["id"])
    resp = models(client, token)
    assert resp.status_code == 401
    assert "revoked" in resp.json()["error"]["message"]


def test_works_without_legacy_key(client, monkeypatch):
    from src.keys import key_store
    monkeypatch.delenv("PROXY_API_KEY")
    _, token = key_store.create("only-key")
    assert models(client, token).status_code == 200
    assert models(client, "sk-test-key").status_code == 401


def test_fails_closed_when_every_key_is_revoked(client, monkeypatch, caplog):
    from src.keys import key_store
    monkeypatch.delenv("PROXY_API_KEY")
    record, token = key_store.create("last")
    key_store.revoke(record["id"])
    with caplog.at_level("ERROR", logger="security"):
        assert models(client, token).status_code == 401
        assert models(client, "").status_code == 401
    assert "No active API keys" in caplog.text


def test_admin_api_hidden_without_admin_key(client):
    assert client.get("/admin/keys", headers=AUTH).status_code == 404
    assert client.post("/admin/keys", headers=AUTH, json={"name": "x"}).status_code == 404


def test_admin_api_lifecycle(client, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN)
    assert client.get("/admin/keys").status_code == 401
    assert client.get("/admin/keys", headers=AUTH).status_code == 401  # client keys aren't admin keys

    created = client.post("/admin/keys", headers=ADMIN_AUTH, json={"name": "tablet"})
    assert created.status_code == 201
    body = created.json()
    token, key_id = body["key"], body["id"]
    assert body["active"] and body["name"] == "tablet"

    listing = client.get("/admin/keys", headers=ADMIN_AUTH).json()["data"]
    assert [k["id"] for k in listing] == [key_id]
    assert "key" not in listing[0] and "sha256" not in listing[0]

    assert models(client, token).status_code == 200
    assert models(client, ADMIN).status_code == 401  # admin key can't call the model API

    revoked = client.delete(f"/admin/keys/{key_id}", headers=ADMIN_AUTH)
    assert revoked.status_code == 200 and revoked.json()["active"] is False
    assert models(client, token).status_code == 401
    assert client.delete("/admin/keys/key_nope", headers=ADMIN_AUTH).status_code == 404
    assert client.post("/admin/keys", headers=ADMIN_AUTH, json={}).status_code == 400


def test_chat_logs_which_key_was_used(client, caplog):
    from src.keys import key_store
    record, token = key_store.create("logged")
    with caplog.at_level("INFO", logger="main"):
        resp = client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {token}"},
                           json={"model": "gemini-3.8-flash-low", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert f"key={record['id']}" in caplog.text
    assert token not in caplog.text
