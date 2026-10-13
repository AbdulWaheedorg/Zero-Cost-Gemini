import json

from tests.conftest import AUTH

MODEL = "gemini-3.8-flash-low"


def chat(client, **body):
    payload = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], **body}
    return client.post("/v1/chat/completions", headers=AUTH, json=payload)


def sse_events(text):
    return [line[len("data: "):] for line in text.splitlines() if line.startswith("data: ")]


def streamed_text(text):
    chunks = [json.loads(e) for e in sse_events(text) if e != "[DONE]"]
    return "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])


def test_healthz_is_public_and_cheap(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
    assert resp.json()["total_accounts"] == 2


def test_auth_errors_use_openai_error_shape(client):
    resp = client.get("/v1/models")
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"
    assert resp.headers["www-authenticate"] == "Bearer"

    resp = client.get("/v1/models", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_list_and_retrieve_models(client):
    resp = client.get("/v1/models", headers=AUTH)
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["data"]]
    assert ids == ["gemini-3.8-flash-low", "gemini-3.8-flash-high", "claude-sonnet-4-6"]

    assert client.get(f"/v1/models/{MODEL}", headers=AUTH).json()["id"] == MODEL
    missing = client.get("/v1/models/nope", headers=AUTH)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "model_not_found"


def test_chat_non_streaming(client):
    resp = chat(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == MODEL
    assert data["choices"][0]["message"] == {"role": "assistant", "content": "Hello from account_1"}
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"] == {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
    assert resp.headers["x-active-account"] == "account_1"  # an id, not an email
    assert resp.headers["x-quota-5h-remaining"] == "100.0%"


def test_chat_streaming(client):
    resp = chat(client, stream=True, stream_options={"include_usage": True})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-active-account"] == "account_1"

    events = sse_events(resp.text)
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": "Hello "}
    assert streamed_text(resp.text) == "Hello from account_1"
    assert chunks[-2]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["choices"] == [] and chunks[-1]["usage"]["total_tokens"] == 13
    assert len({c["id"] for c in chunks}) == 1


def test_streaming_fails_over_and_reports_serving_account(client, fake):
    fake.set("account_1", chat="quota_error")
    resp = chat(client, stream=True)
    assert resp.status_code == 200
    assert resp.headers["x-active-account"] == "account_2"
    assert streamed_text(resp.text) == "Hello from account_2"


def test_streaming_errors_before_first_token_get_real_status(client, fake):
    fake.set("account_1", chat="quota_error")
    fake.set("account_2", chat="quota_error")
    resp = chat(client, stream=True)
    assert resp.status_code == 429
    assert resp.json()["error"]["type"] == "insufficient_quota"


def test_unknown_model_rejected_using_registry(client):
    client.get("/v1/models", headers=AUTH)  # make sure the registry is loaded
    resp = chat(client, model="gpt-9000")
    assert resp.status_code == 404
    assert resp.json()["error"]["param"] == "model"


def test_request_validation(client):
    def post(body):
        return client.post("/v1/chat/completions", headers=AUTH, json=body)

    assert post({"messages": [{"role": "user", "content": "hi"}]}).json()["error"]["param"] == "model"
    assert post({"model": MODEL}).json()["error"]["param"] == "messages"
    assert post({"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "n": 2}).status_code == 400
    assert post([1, 2]).status_code == 400
    bad_json = client.post("/v1/chat/completions", headers={**AUTH, "Content-Type": "application/json"},
                           content=b"{not json")
    assert bad_json.status_code == 400
    assert bad_json.json()["error"]["type"] == "invalid_request_error"
    assert chat(client, reasoning_effort="high", temperature=0.2, max_tokens=5).status_code == 200  # ignored


def test_quota_and_usage(client, fake):
    fake.set("account_1", quota={"gemini_5h": 0.25})
    for path in ("/v1/quota", "/v1/usage"):
        data = client.get(path, headers=AUTH).json()
        assert data["active_account_id"] == "account_1"
        acc = data["accounts"][0]
        assert acc["quota"]["gemini_5h_percent"] == 25.0
        assert acc["quota_error"] is None


async def test_client_disconnect_kills_cli_even_while_it_is_silent(fake):
    """agy can think silently for a long time; a disconnect must still kill it promptly."""
    import asyncio
    import time

    from src.engine import open_completion
    from src.main import CompletionStreamingResponse, _sse_stream, limiter
    from tests.test_engine import _pid_alive

    fake.set("account_1", chat="hang_after_first")
    completion = await open_completion(MODEL, [{"role": "user", "content": "hi"}])
    slot = limiter.acquire("disconnect-test")
    response = CompletionStreamingResponse(completion, _sse_stream(completion, MODEL, False), slot,
                                           media_type="text/event-stream")
    pid = fake.calls("account_1")[0]["pid"]

    async def receive():
        await asyncio.sleep(0.3)
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    started = time.monotonic()
    await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send), 10)
    assert time.monotonic() - started < 5
    assert not _pid_alive(pid)
    assert "disconnect-test" not in limiter._active  # slot released, so the key isn't locked out


def test_mid_stream_error_is_reported_in_band(client, fake):
    fake.set("account_1", chat="error_after_first", reply="one two")
    resp = chat(client, stream=True)
    assert resp.status_code == 200
    events = sse_events(resp.text)
    assert json.loads(events[0])["choices"][0]["delta"]["content"] == "one "
    assert json.loads(events[-1])["error"]["type"] == "upstream_error"
    assert "[DONE]" not in events


def test_claude_model_reports_third_party_quota(client, fake):
    fake.set("account_1", quota={"gemini_5h": 0.9, "tp_5h": 0.3, "tp_weekly": 0.6})
    resp = chat(client, model="claude-sonnet-4-6")
    assert resp.status_code == 200
    assert resp.headers["x-quota-5h-remaining"] == "30.0%"
    assert resp.headers["x-quota-weekly-remaining"] == "60.0%"


def test_quota_headers_say_unknown_when_quota_unreadable(client, fake):
    fake.set("account_1", quota_fail=True)
    resp = chat(client)
    assert resp.status_code == 200
    assert resp.headers["x-quota-5h-remaining"] == "unknown"


def test_missing_server_key_fails_closed(client, monkeypatch):
    monkeypatch.delenv("PROXY_API_KEY")
    resp = client.get("/v1/models", headers=AUTH)
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"


def test_cors(client):
    pre = client.options("/v1/chat/completions", headers={
        "Origin": "https://example.com", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in pre.headers

    resp = client.post(
        "/v1/chat/completions", headers={**AUTH, "Origin": "https://example.com"},
        json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}]})
    assert "X-Active-Account" in resp.headers["access-control-expose-headers"]
