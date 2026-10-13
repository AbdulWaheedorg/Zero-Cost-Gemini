# Troubleshooting

Start with the logs: `sudo journalctl -u zero-cost-gemini -n 100 --no-pager` (or the terminal running `uvicorn`).

| Symptom | Likely cause | Fix |
| :--- | :--- | :--- |
| `401 authentication_error` | Missing, mistyped or revoked key; or no keys exist at all (the log says `No active API keys`) | Check the `Authorization: Bearer …` header; `python -m src.keys list`; create a key |
| `403 https_required` | Client used `http://` through Cloudflare | Use `https://`. Consider replacing the key, since it was sent unencrypted |
| `404 model_not_found` | Model not in your plan or no longer offered | `GET /v1/models` for the current list ([Google's model list](https://antigravity.google/docs/models)) |
| `404` on `/admin/...` | `ADMIN_API_KEY` isn't set | Set it in `.env` and restart, or use the CLI |
| `429 insufficient_quota` | Out of quota for that model's pool | Use a model from the other pool, or wait for the reset time shown in `/v1/quota` |
| `429 concurrency_limit` | Too many requests at once | Retry (OpenAI SDKs do this automatically) or raise `MAX_CONCURRENT_*` if your RAM allows |
| `502 empty_response` | The model tried to use a tool, which is denied | Rephrase the request to ask for a direct text answer |
| `502 upstream_error` | `agy` reported an error | The message has `agy`'s reason; check the log |
| `503 cli_unavailable` | `agy` not found | Install it, or set `AGY_BIN` |
| `503 overloaded` | Google is busy, or the account's concurrent-request cap was hit | Retry shortly |
| `504 timeout` | No output for 3 minutes, or over 10 minutes total | Use a faster model or a shorter request; tune `AGY_*_TIMEOUT` |
| `quota_error` in `/v1/quota` | `agy /quota` failed (often: signed out) | Run `agy` as the service user and sign in again |
| `active_models_count: 0` | Discovery still running, or `agy models` fails | Wait a few seconds; run `~/.local/bin/agy models` as the service user |
| Streaming arrives in one big chunk | A proxy is buffering | Caddy: `flush_interval -1`; Nginx: `proxy_buffering off` |
| Slow first token (3–20 s) | Normal: `agy` is an agent with startup overhead | Use `-low` / `flash` models for latency-sensitive work |

## Checking `agy` directly

Run as the same user as the service (from an empty directory):
```bash
cd "$(mktemp -d)"
echo '{"event":"user","message":{"role":"user","content":"Say hello"}}' | \
  ~/.local/bin/agy --disable-slash-commands --input-format stream-json --output-format stream-json --model=gemini-3.8-flash-low
```
The last line is a `{"event":"result",…}` with `"status":"SUCCESS"` or an `error` explaining what's wrong.

## PowerShell tips

Use `Invoke-RestMethod` rather than `curl.exe` with inline JSON, which PowerShell's quoting tends to break:
```powershell
$h = @{ Authorization = "Bearer sk-agy-..." }
$b = @{ model = "gemini-3.8-flash-low"; messages = @(@{ role = "user"; content = "Hi" }) } | ConvertTo-Json -Depth 5
(Invoke-RestMethod -Uri https://api.example.com/v1/chat/completions -Method Post -Headers $h -ContentType "application/json" -Body $b).choices[0].message.content
```
