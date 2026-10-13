# API Reference

Base URL: `https://api.example.com` (your own domain). OpenAI SDKs take `https://api.example.com/v1`.

---

## 1. Authentication

All endpoints except `/healthz` need a bearer key:
```http
Authorization: Bearer sk-agy-...
```
- **Client keys** (`sk-agy-…`) are named and individually revocable. See [API Keys](API_KEYS.md).
- The optional legacy `PROXY_API_KEY` from the environment is accepted too.
- The **admin key** (`ADMIN_API_KEY`) works only on `/admin/*` and cannot call the model API; client keys cannot call `/admin/*`.

---

## 2. Errors

Every error, including unknown paths, uses OpenAI's envelope, so OpenAI SDKs raise their usual exception classes:
```json
{"error": {"message": "The model 'gpt-9000' does not exist. See GET /v1/models.", "type": "invalid_request_error", "param": "model", "code": "model_not_found"}}
```

| Status | `type` / `code` | When |
| :--- | :--- | :--- |
| `400` | `invalid_request_error` | Bad JSON, missing `model`/`messages`, unknown role, non-text content part, `n` ≠ 1 |
| `401` | `authentication_error` | Missing, wrong or revoked key |
| `403` | `https_required` | The request reached Cloudflare over plain `http://` |
| `404` | `model_not_found` / `not_found_error` | Unknown model, or unknown path |
| `413` | `request_too_large` | Body over `MAX_REQUEST_BYTES` (default 10 MB) |
| `429` | `insufficient_quota` | Every account is out of quota for this model's pool |
| `429` | `concurrency_limit` | Too many requests in progress (default 8 total, 4 per key); SDKs retry automatically |
| `502` | `upstream_error` / `empty_response` | `agy` failed, crashed, or produced no text |
| `503` | `overloaded` | Every account reported a transient capacity error; retry shortly |
| `503` | `cli_unavailable` | `agy` is not installed on the server |
| `504` | `timeout` | No output for `AGY_IDLE_TIMEOUT` s, or longer than `AGY_REQUEST_TIMEOUT` s |

There is no public API explorer: `/docs`, `/redoc` and `/openapi.json` are disabled on purpose.

---

## 3. Endpoints

### `GET /healthz`
Liveness check. No key needed, and it never starts a subprocess.
```json
{"status": "healthy", "active_models_count": 6, "total_accounts": 1, "timestamp": 1791910084}
```
`active_models_count` is `0` for a few seconds after startup, until model discovery finishes.

### `GET /v1/models`
Models discovered from `agy models`, in OpenAI format:
```json
{"object": "list", "data": [
  {"id": "gemini-3.8-flash-high", "object": "model", "created": 1791910000, "owned_by": "google", "display_name": "Gemini 3.8 Flash (High)"}
]}
```
Which models you see depends on your plan and on Google's current lineup ([model list](https://antigravity.google/docs/models)).

### `GET /v1/models/{model_id}`
One model object, or `404 model_not_found`.

### `GET /v1/quota` (alias `GET /v1/usage`)
Remaining quota for every account. Client keys don't see account emails; `GET /admin/quota` adds them.
```json
{
  "active_account_id": "default",
  "total_accounts": 1,
  "timestamp": 1791911590,
  "accounts": [{
    "id": "default", "role": "primary", "priority": 1,
    "is_active": true, "is_rate_limited": false, "rate_limit_expires_in_seconds": 0,
    "quota": {
      "gemini_5h_fraction": 0.967, "gemini_5h_percent": 96.7, "gemini_5h_reset_time": "2026-10-13T21:37:19Z",
      "gemini_weekly_fraction": 0.974, "gemini_weekly_percent": 97.4, "gemini_weekly_reset_time": "2026-10-20T16:37:19Z",
      "third_party_5h_percent": 100.0, "third_party_weekly_percent": 100.0,
      "status": "healthy", "third_party_status": "healthy"
    },
    "quota_age_seconds": 12.4,
    "quota_error": null
  }]
}
```
(Trimmed. Each bucket also has `_fraction`, `_reset_time` and `_description` fields.)
- `gemini_*` is the pool for Gemini models; `third_party_*` is the pool for Claude/GPT models.
- `status` is `healthy`, `low` (a bucket ≤ 5%) or `exhausted` (a bucket ≤ 1%).
- If quota can't be read, `quota` is `{"status": "unknown"}` and `quota_error` says why.

### `POST /v1/chat/completions`

| Field | Required | Notes |
| :--- | :--- | :--- |
| `model` | yes | An id from `GET /v1/models`, passed to `agy` unchanged |
| `messages` | yes | Roles `system`, `developer`, `user`, `assistant`, `tool`. Content is a string or an array of `text` parts |
| `stream` | no | `true` for Server-Sent Events |
| `stream_options.include_usage` | no | Adds a final usage chunk to the stream |
| `n` | no | Only `1` |
| `temperature`, `top_p`, `max_tokens`, `stop`, `tools`, `reasoning_effort`, … | no | Accepted for compatibility but **ignored**: `agy` has no usable equivalent. For more or less reasoning, pick the model variant (e.g. `-low` / `-high`) |

**Response headers** (both modes):
```http
X-Active-Account: default
X-Quota-5h-Remaining: 96.7%
X-Quota-Weekly-Remaining: 97.4%
```
The quota values are for the pool that bills the requested model, or `unknown`.

**Non-streaming response**
```json
{
  "id": "chatcmpl-67d2c06b880f4c2b9a1e3d77",
  "object": "chat.completion",
  "created": 1791912031,
  "model": "gemini-3.8-flash-low",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello! How can I help?"}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 11866, "completion_tokens": 9, "total_tokens": 11875}
}
```
`prompt_tokens` is high even for short prompts because `agy` adds its own instructions; it counts toward your quota.

**Streaming response.** The stream starts once the model produces its first token, so early failures arrive as normal HTTP errors.
```http
data: {"id":"chatcmpl-…","object":"chat.completion.chunk","created":1791911656,"model":"gemini-3.8-flash-low","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk","created":1791911656,"model":"gemini-3.8-flash-low","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```
With `include_usage`, a chunk with `"choices": []` and `usage` comes just before `[DONE]`. A failure mid-stream sends one `data: {"error": …}` event and ends without `[DONE]`. If the client disconnects, the gateway kills `agy` immediately.

---

## 4. Admin endpoints

Only available when `ADMIN_API_KEY` is set (otherwise `404`). Use `Authorization: Bearer <ADMIN_API_KEY>`.

| Endpoint | Purpose |
| :--- | :--- |
| `POST /admin/keys` `{"name": "laptop"}` | Create a key. The plaintext `key` is returned **only in this response** (`201`) |
| `GET /admin/keys` | List keys: id, name, prefix, created/revoked time, `active`. Never the key or its hash |
| `DELETE /admin/keys/{key_id}` | Revoke immediately (`404` if unknown; revoking twice is harmless) |
| `GET /admin/quota` | `/v1/quota` including account emails |

See [API Keys](API_KEYS.md) for examples.
