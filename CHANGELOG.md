# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-14

First public release.

### Added
- OpenAI-compatible `POST /v1/chat/completions` (streaming and non-streaming), `GET /v1/models`, `GET /v1/models/{id}`, `GET /v1/quota`, `GET /healthz`.
- Server-Sent Events streaming with `stream_options.include_usage`, and token usage mapping (including reasoning and cached tokens).
- Dynamic model discovery from `agy models`, refreshed every 30 minutes.
- Named, revocable API keys stored as SHA-256 hashes (`python -m src.keys`, `/admin/keys`) with a separate admin key; revocation applies on the next request.
- Quota tracking for the 5-hour and weekly windows, per quota pool, cached off the request path.
- Optional multi-account mode with failover (see the warnings in `docs/MULTI_ACCOUNT.md`).
- Hardening: agent tools denied by default, isolated scratch directory per request, prompts delivered over stdin, conversation history JSON-encoded against role forgery, concurrency and body-size limits, HTTPS enforcement behind Cloudflare, no public API explorer, OpenAI-shaped errors everywhere, token redaction in logs.
- Reliability: idle and total timeouts, process-tree cleanup on timeout/error/client disconnect, concurrent stderr draining, retry on transient overload.
- Hermetic test suite (~100 tests) with a fake Antigravity CLI, opt-in live tests, and CI on Python 3.11–3.13.
- Deployment templates for systemd and Caddy, and full documentation.

[1.0.0]: https://github.com/AbdulWaheedorg/Zero-Cost-Gemini/releases/tag/v1.0.0
