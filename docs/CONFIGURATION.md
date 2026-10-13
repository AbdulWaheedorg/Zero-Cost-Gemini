# Configuration

Everything is configured with environment variables. Put them in `.env` (start from [`.env.example`](../.env.example), `chmod 600 .env`) and load it with `uvicorn src.main:app --env-file .env` or `EnvironmentFile=` in systemd. Every variable is optional.

## Authentication

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `ADMIN_API_KEY` | *(unset: admin API off)* | Enables `/admin/keys` and `/admin/quota` |
| `PROXY_API_KEY` | *(unset)* | Legacy single client key, accepted alongside `keys.json` |
| `API_KEYS_FILE` | `./keys.json` | Hashed client keys (managed with `python -m src.keys`) |

## The Antigravity CLI

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `AGY_BIN` | auto-detect | Path to `agy`. Otherwise searched on `PATH`, then `~/.local/bin/agy` (Linux/macOS) or `%LOCALAPPDATA%\agy\bin\agy.exe` (Windows) |
| `AGY_WORKDIR_ROOT` | `./.agy-work` | Where per-request scratch directories are created (and deleted) |
| `AGY_IDLE_TIMEOUT` | `180` | Seconds `agy` may stay silent before the request fails with `504` |
| `AGY_REQUEST_TIMEOUT` | `600` | Hard limit per request in seconds (also passed to `agy` as `--print-timeout`) |

## Limits

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `MAX_CONCURRENT_REQUESTS` | `8` | Completions running at once, server-wide. Each runs an `agy` process of a few hundred MB, so size this to your RAM |
| `MAX_CONCURRENT_PER_KEY` | `4` | Completions running at once per key, so one key can't starve the others |
| `MAX_REQUEST_BYTES` | `10485760` | Largest accepted request body (10 MB) |

## Accounts (multi-account mode only)

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `ACCOUNTS_DIR` | `./accounts` | Account directories. If missing or empty, the gateway uses the sign-in of the user running it |
| `QUOTA_BACKOFF_SECONDS` | `300` | How long an account is skipped after a quota error |

See [Multi-Account Mode](MULTI_ACCOUNT.md) and its warnings before using these.

## Built-in behaviour (not configurable)

| Setting | Value |
| :--- | :--- |
| Quota cache | 60 s (failed readings: 30 s), `/quota` timeout 60 s |
| Model list refresh | every 30 minutes |
| Low-quota threshold for failover | 5% of the 5-hour or weekly bucket |

## Files the service creates

| Path | Mode | Contents |
| :--- | :--- | :--- |
| `keys.json` | `0600` | Client key hashes |
| `.agy-work/run-*` | `0700` | Per-request scratch dirs, removed after each request |

`.gitignore` excludes `.env`, `keys.json`, `accounts/` and `.agy-work/`, so they can't be committed by accident.
