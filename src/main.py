import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.account_manager import account_manager
from src.converter import SSE_DONE, chat_completion, error_body, format_openai_chunk, new_completion_id, sse, usage_block
from src.engine import Completion, UpstreamError, complete, invalid_request, open_completion
from src.model_registry import model_registry
from src.keys import key_store
from src.security import get_logger, verify_admin_key, verify_api_key

logger = get_logger("main")

HTTP_ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    405: "invalid_request_error",
    413: "invalid_request_error",
    429: "rate_limit_error",
}

MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", str(10 * 1024 * 1024)))


class RequestTooLarge(Exception):
    pass


class ConcurrencyLimiter:
    """
    Caps simultaneous completions, overall and per key. Each one runs an agy
    process, so without a cap a single key (or a leaked one) could exhaust the
    VPS's memory and take down everything else running on it.
    """

    def __init__(self, total: int, per_key: int):
        self.total = total
        self.per_key = per_key
        self._active: Dict[str, int] = {}

    def acquire(self, key_id: str) -> "Slot":
        if sum(self._active.values()) >= self.total:
            raise UpstreamError(429, f"Server is at its limit of {self.total} concurrent requests; retry shortly.",
                                "rate_limit_error", code="concurrency_limit")
        if self._active.get(key_id, 0) >= self.per_key:
            raise UpstreamError(429, f"This key already has {self.per_key} requests in progress; retry shortly.",
                                "rate_limit_error", code="concurrency_limit")
        self._active[key_id] = self._active.get(key_id, 0) + 1
        return Slot(self, key_id)

    def _release(self, key_id: str) -> None:
        self._active[key_id] -= 1
        if not self._active[key_id]:
            del self._active[key_id]


class Slot:
    def __init__(self, limiter: ConcurrencyLimiter, key_id: str):
        self._limiter, self._key_id, self._held = limiter, key_id, True

    def release(self) -> None:
        if self._held:
            self._held = False
            self._limiter._release(self._key_id)


limiter = ConcurrencyLimiter(
    total=int(os.environ.get("MAX_CONCURRENT_REQUESTS", "8")),
    per_key=int(os.environ.get("MAX_CONCURRENT_PER_KEY", "4")),
)


class GuardMiddleware:
    """
    - Rejects requests that reached Cloudflare over plain HTTP (CF-Visitor header),
      so a client misconfigured with http:// fails loudly instead of leaking its key.
    - Enforces MAX_REQUEST_BYTES on request bodies.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])

        if scope["path"] != "/healthz":
            try:
                scheme = json.loads(headers.get(b"cf-visitor", b"{}")).get("scheme")
            except (ValueError, AttributeError):
                scheme = None
            if scheme == "http":
                return await _send_error(send, 403, "HTTPS is required: call this API with https:// instead of http://.",
                                         "permission_error", "https_required")

        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > MAX_REQUEST_BYTES:
            return await _send_error(send, 413, f"Request body exceeds {MAX_REQUEST_BYTES} bytes.",
                                     "invalid_request_error", "request_too_large")

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_REQUEST_BYTES:  # chunked bodies have no Content-Length
                    raise RequestTooLarge()
            return message

        await self.app(scope, limited_receive, send)


async def _send_error(send, status: int, message: str, err_type: str, code: str) -> None:
    body = json.dumps(error_body(message, err_type, code)).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Antigravity OpenAI-compatible proxy")
    account_manager.discover_accounts()
    registry_task = asyncio.create_task(model_registry.start_daemon())
    yield
    registry_task.cancel()
    await asyncio.gather(registry_task, return_exceptions=True)


app = FastAPI(
    title="Zero-Cost Gemini",
    description="Serves OpenAI chat completions through the Antigravity CLI with multi-account quota rotation.",
    version="1.0.0",
    lifespan=lifespan,
    # No public API explorer or schema: they would map every route (incl. /admin) for attackers.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(GuardMiddleware)

# Clients authenticate with a bearer token, not cookies, so credentials are not needed.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Active-Account", "X-Quota-5h-Remaining", "X-Quota-Weekly-Remaining"])


@app.exception_handler(UpstreamError)
async def upstream_error_handler(request: Request, exc: UpstreamError):
    return JSONResponse(status_code=exc.status, content=error_body(exc.message, exc.err_type, exc.code, exc.param))


@app.exception_handler(RequestTooLarge)
async def too_large_handler(request: Request, exc: RequestTooLarge):
    return JSONResponse(status_code=413, content=error_body(
        f"Request body exceeds {MAX_REQUEST_BYTES} bytes.", "invalid_request_error", "request_too_large"))


# Starlette's class also covers FastAPI's HTTPException and the router's own 404/405,
# so every error, even for unknown paths, has the OpenAI shape.
@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    err_type = HTTP_ERROR_TYPES.get(exc.status_code, "server_error")
    return JSONResponse(status_code=exc.status_code, content=error_body(str(exc.detail), err_type),
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content=error_body(str(exc.errors()), "invalid_request_error"))


@app.get("/healthz")
async def health_check():
    """Liveness check. Uses cached state only, so it never spawns a subprocess."""
    return {
        "status": "healthy",
        "active_models_count": len(model_registry.cached_models),
        "total_accounts": len(account_manager.accounts),
        "timestamp": int(time.time()),
    }


@app.get("/v1/models", dependencies=[Depends(verify_api_key)])
async def list_models():
    return {"object": "list", "data": await model_registry.get_models()}


@app.get("/v1/models/{model_id}", dependencies=[Depends(verify_api_key)])
async def retrieve_model(model_id: str):
    model = await model_registry.get_model(model_id)
    if model is None:
        raise UpstreamError(404, f"The model '{model_id}' does not exist.", "invalid_request_error",
                            code="model_not_found", param="model")
    return model


@app.get("/v1/quota", dependencies=[Depends(verify_api_key)])
async def get_quota():
    """5-hour and weekly quota for every account, plus the active account (no emails)."""
    return await account_manager.get_all_quotas(include_emails=False)


@app.get("/v1/usage", dependencies=[Depends(verify_api_key)])
async def get_usage():
    """Alias of /v1/quota."""
    return await account_manager.get_all_quotas(include_emails=False)


@app.get("/admin/quota", dependencies=[Depends(verify_admin_key)])
async def admin_quota():
    """/v1/quota including the Google account emails."""
    return await account_manager.get_all_quotas(include_emails=True)


async def _json_body(request: Request) -> Any:
    try:
        return await request.json()
    except (ValueError, RecursionError):  # RecursionError: absurdly deep nesting
        raise invalid_request("Request body is not valid JSON.")


@app.get("/admin/keys", dependencies=[Depends(verify_admin_key)])
async def admin_list_keys():
    return {"object": "list", "data": key_store.list()}


@app.post("/admin/keys", dependencies=[Depends(verify_admin_key)], status_code=201)
async def admin_create_key(request: Request):
    try:
        body = await _json_body(request)
    except UpstreamError:
        body = None
    name = body.get("name") if isinstance(body, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise invalid_request("'name' is required, e.g. {\"name\": \"laptop\"}.", param="name")
    record, token = key_store.create(name)
    logger.info(f"Created API key {record['id']} ({record['name']})")
    # The only time the plaintext key is ever returned.
    return {**record, "key": token}


@app.delete("/admin/keys/{key_id}", dependencies=[Depends(verify_admin_key)])
async def admin_revoke_key(key_id: str):
    record = key_store.revoke(key_id)
    if record is None:
        raise UpstreamError(404, f"No API key with id '{key_id}'.", "not_found_error", param="key_id")
    logger.info(f"Revoked API key {record['id']} ({record['name']})")
    return record


def _parse_request(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise invalid_request("Request body must be a JSON object.")

    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise invalid_request("'model' is required.", param="model")
    known = model_registry.cached_models
    if known and model not in {m["id"] for m in known}:
        raise UpstreamError(404, f"The model '{model}' does not exist. See GET /v1/models.",
                            "invalid_request_error", code="model_not_found", param="model")

    if body.get("n", 1) != 1:
        raise invalid_request("Only n=1 is supported.", param="n")

    stream_options = body.get("stream_options") or {}
    return {
        "model": model,
        "messages": body.get("messages"),
        "stream": bool(body.get("stream", False)),
        "include_usage": bool(isinstance(stream_options, dict) and stream_options.get("include_usage")),
    }


async def _sse_stream(completion: Completion, model: str, include_usage: bool) -> AsyncIterator[bytes]:
    cid = new_completion_id()
    created = int(time.time())
    first = True
    try:
        async for kind, value in completion.outputs():
            if kind == "text":
                delta = {"role": "assistant", "content": value} if first else {"content": value}
                first = False
                yield format_openai_chunk(delta, None, model, cid, created)
            else:
                yield format_openai_chunk({}, "stop", model, cid, created)
                if include_usage:
                    yield format_openai_chunk(None, None, model, cid, created, usage_block(value.get("usage")))
                yield SSE_DONE
    except UpstreamError as e:
        # Headers are already sent, so the error can only be reported in-band.
        yield sse(error_body(e.message, e.err_type, e.code))


class CompletionStreamingResponse(StreamingResponse):
    """
    Owns the agy process behind a stream. Starlette does not close the body
    iterator when the client disconnects, so cleanup is tied to the response
    itself: agy is killed however the stream ends.
    """

    def __init__(self, completion: Completion, content: AsyncIterator[bytes], slot: Slot, **kwargs):
        super().__init__(content, **kwargs)
        self.completion = completion
        self.slot = slot

    async def __call__(self, scope, receive, send) -> None:
        # Starlette only notices a disconnect when a write fails, and agy can go
        # quiet for many seconds while thinking, so watch for it directly.
        async def kill_on_disconnect():
            while (await receive())["type"] != "http.disconnect":
                pass
            await self.completion.aclose()

        watcher = asyncio.create_task(kill_on_disconnect())
        try:
            await super().__call__(scope, receive, send)
        finally:
            watcher.cancel()
            self.slot.release()
            await self.completion.aclose()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, api_key: Dict[str, Any] = Depends(verify_api_key)):
    """
    OpenAI chat completions, streaming and non-streaming. Model names are passed
    to agy unchanged. Accepted but ignored: temperature, top_p, max_tokens, stop,
    tools, reasoning_effort (agy exposes no usable equivalent).
    """
    req = _parse_request(await _json_body(request))
    # !r so a crafted model name cannot forge extra log lines.
    logger.info(f"chat key={api_key['id']} model={req['model']!r} stream={req['stream']}")

    slot = limiter.acquire(api_key["id"])
    if not req["stream"]:
        try:
            completion, text, usage = await complete(req["model"], req["messages"])
        finally:
            slot.release()
        return JSONResponse(
            content=chat_completion(new_completion_id(), req["model"], text, usage),
            headers=completion.quota_headers(req["model"]),
        )

    # Wait for the first token before responding, so that failover has picked the
    # serving account (for the headers) and early errors get a real HTTP status.
    try:
        completion = await open_completion(req["model"], req["messages"])
    except BaseException:
        slot.release()
        raise
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        **completion.quota_headers(req["model"]),
    }
    return CompletionStreamingResponse(
        completion,
        _sse_stream(completion, req["model"], req["include_usage"]),
        slot,
        media_type="text/event-stream",
        headers=headers,
    )
