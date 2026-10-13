import pytest

from src import main
from src.engine import UpstreamError
from tests.conftest import AUTH

MODEL = "gemini-3.8-flash-low"
CHAT = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]}


def post_chat(client, **kwargs):
    return client.post("/v1/chat/completions", headers={**AUTH, **kwargs.pop("headers", {})}, **kwargs)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_explorer_and_schema_are_not_public(client, path):
    resp = client.get(path)
    assert resp.status_code == 404
    assert resp.json()["error"]["type"] == "not_found_error"


def test_unknown_routes_and_methods_use_openai_errors(client):
    resp = client.get("/v1/nope", headers=AUTH)
    assert resp.status_code == 404 and "error" in resp.json() and "detail" not in resp.json()
    resp = client.put("/v1/chat/completions", headers=AUTH)
    assert resp.status_code == 405 and "error" in resp.json()


def test_plain_http_via_cloudflare_is_rejected(client):
    resp = post_chat(client, json=CHAT, headers={"CF-Visitor": '{"scheme":"http"}'})
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "https_required"
    assert client.get("/v1/models", headers={**AUTH, "CF-Visitor": '{"scheme":"http"}'}).status_code == 403
    assert post_chat(client, json=CHAT, headers={"CF-Visitor": '{"scheme":"https"}'}).status_code == 200
    assert client.get("/healthz", headers={"CF-Visitor": '{"scheme":"http"}'}).status_code == 200
    assert post_chat(client, json=CHAT, headers={"CF-Visitor": "garbage"}).status_code == 200


def test_oversized_body_is_rejected(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_REQUEST_BYTES", 1000)
    big = {"model": MODEL, "messages": [{"role": "user", "content": "x" * 5000}]}
    resp = post_chat(client, json=big)
    assert resp.status_code == 413 and resp.json()["error"]["code"] == "request_too_large"

    def chunks():  # no Content-Length: enforced while reading
        yield b'{"model": "' + MODEL.encode() + b'", "messages": [{"role": "user", "content": "'
        for _ in range(10):
            yield b"x" * 500
        yield b'"}]}'

    resp = post_chat(client, content=chunks(), headers={"Content-Type": "application/json"})
    assert resp.status_code == 413
    assert post_chat(client, json=CHAT).status_code == 200


def test_deeply_nested_json_is_a_clean_400(client):
    body = b"[" * 200_000 + b"]" * 200_000
    resp = post_chat(client, content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 400 and resp.json()["error"]["type"] == "invalid_request_error"


def test_concurrency_limiter_unit():
    lim = main.ConcurrencyLimiter(total=3, per_key=2)
    a1, a2 = lim.acquire("a"), lim.acquire("a")
    with pytest.raises(UpstreamError) as e:
        lim.acquire("a")
    assert e.value.status == 429 and e.value.code == "concurrency_limit"
    b1 = lim.acquire("b")
    with pytest.raises(UpstreamError):
        lim.acquire("c")  # total reached
    a1.release()
    a1.release()  # idempotent
    lim.acquire("c")
    for s in (a2, b1):
        s.release()


def test_concurrency_limit_over_http(client, monkeypatch, fake):
    lim = main.ConcurrencyLimiter(total=10, per_key=1)
    monkeypatch.setattr(main, "limiter", lim)
    held = lim.acquire("legacy-env")  # the test key's id
    resp = post_chat(client, json=CHAT)
    assert resp.status_code == 429 and resp.json()["error"]["code"] == "concurrency_limit"
    held.release()
    assert post_chat(client, json=CHAT).status_code == 200
    assert post_chat(client, json={**CHAT, "stream": True}).status_code == 200

    fake.set("account_1", chat="model_error")
    assert post_chat(client, json=CHAT).status_code == 502
    assert post_chat(client, json={**CHAT, "stream": True}).status_code == 502
    assert lim._active == {}  # slots released on success, streaming and errors alike


def test_quota_hides_emails_from_client_keys(client, monkeypatch):
    data = client.get("/v1/quota", headers=AUTH).json()
    assert "active_email" not in data and all("email" not in a for a in data["accounts"])
    assert "example.com" not in str(data)

    monkeypatch.setenv("ADMIN_API_KEY", "adm")
    full = client.get("/admin/quota", headers={"Authorization": "Bearer adm"}).json()
    assert full["active_email"] == "user1@example.com"
    assert client.get("/admin/quota", headers=AUTH).status_code == 401


def test_model_name_cannot_forge_log_lines(client, caplog, monkeypatch):
    from src.model_registry import model_registry
    monkeypatch.setattr(model_registry, "_models", [])  # registry not loaded: name passes through
    with caplog.at_level("INFO", logger="main"):
        post_chat(client, json={**CHAT, "model": "x\nINFO fake line"})
    assert "\nINFO fake line" not in caplog.text
