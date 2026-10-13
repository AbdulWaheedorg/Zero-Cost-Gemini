# Security Model

What the gateway protects, how, and what it leaves to you. To report a vulnerability, see [SECURITY.md](../SECURITY.md).

## Assets

1. Your **Google account and its subscription quota** (the reason anyone would attack this).
2. The **server** the gateway runs on, and everything else on it.
3. **API keys**, the admin key, and the `agy` sign-in.

## Attackers considered

| Attacker | Can they… | Protection |
| :--- | :--- | :--- |
| **Anyone on the internet, no key** | use the API? | Every endpoint except `/healthz` needs a key. Keys are 256-bit random values (not guessable); comparisons are constant-time |
| | map the API? | `/docs`, `/redoc`, `/openapi.json` are disabled; unknown paths return a generic OpenAI-style 404 |
| | sniff a key? | Plain `http://` is refused (`403`) behind Cloudflare; Caddy serves HTTPS with HSTS. Also enable Cloudflare *Always Use HTTPS* |
| **Someone holding a client key** (yours, or a leaked one) | run commands or read files on the server through the agent? | No: see *Agent tools* below |
| | exhaust the server's memory? | At most `MAX_CONCURRENT_REQUESTS` (8) completions at once and `MAX_CONCURRENT_PER_KEY` (4) per key; extra requests get `429`. Bodies over 10 MB get `413`; absurdly nested JSON gets a clean `400` |
| | forge chat turns ("System: …") in the prompt? | Multi-turn history is JSON-encoded inside a wrapper, so message text can't impersonate another role or close the wrapper |
| | forge log lines? | Model names are logged with `repr()`; prompts are never logged |
| | see your account emails? | Hidden from client keys; only `/admin/quota` shows them |
| | manage keys? | No: `/admin/*` needs the separate admin key |
| | keep access after you notice? | Revoke the key; it fails on the next request, no restart needed |
| **Someone with the admin key** | create/revoke keys | That's its job. Keep it off client machines, or leave `ADMIN_API_KEY` unset and use the CLI only |

## Agent tools: the most important control

`agy` is an *agent*: it has tools for shell commands, file read/write, a browser, web fetching and more. Exposed carelessly, a chat API on top of it would be remote code execution as a service. The gateway:

- **Never passes `--dangerously-skip-permissions`** for chat. In headless mode, `agy` then auto-denies tools that need approval.
- **Starts every request in an empty scratch directory**, so nothing sensitive is "inside the workspace".
- **Tells the model up front** that it has no tools and must answer in text.

Verified on a Linux server (account home with no `settings.json`):

| The model tried | Result |
| :--- | :--- |
| `run_command` (shell) | denied (`command` permission) |
| writing a file | denied |
| `view_file` on a file outside the scratch dir (absolute path, and `../`) | denied (`read_file` permission) |
| `read_url_content` to a service on `127.0.0.1` | denied (`read_url` permission); the test server received no request |

The [live test suite](DEVELOPMENT.md#live-tests) includes a shell-command check you can rerun after upgrading `agy`.

**Keep it that way:**
- Don't add `--dangerously-skip-permissions`.
- Don't create a `settings.json` with `permissions.allow` rules or `trustedWorkspaces` in the account's `~/.gemini/antigravity-cli/`. On a developer machine whose settings *trust* the home directory, `view_file` did read files there without asking.
- Run the gateway as a dedicated, unprivileged user if you can: anything that user can read is what's at stake if `agy`'s policy ever changes.

## Secrets at rest

| Secret | Where | Protection |
| :--- | :--- | :--- |
| Client keys | `keys.json` | SHA-256 hashes only, file mode `0600`, atomic writes |
| Admin / legacy key | `.env` | `chmod 600`, git-ignored |
| `agy` sign-in | `~/.gemini/antigravity-cli/` or the OS keychain | Managed by `agy`; git-ignored patterns cover token files |
| Logs | journald | OAuth-token patterns (`ya29.…`, `1//…`) are redacted; keys and prompts are never logged |

## Not covered: your responsibility

- **Google's terms.** Technical controls don't change what the terms allow. See the [notice](../README.md#read-this-first).
- **The rest of the server:** SSH hardening, OS updates, other services, backups.
- **Origin exposure:** if your origin also answers HTTPS directly (bypassing Cloudflare), consider allowing port 443 only from [Cloudflare's IP ranges](https://www.cloudflare.com/ips/).
- **TLS settings at the edge:** set Cloudflare's minimum TLS version to 1.2.
- **Brute-force noise:** failed authentications aren't rate-limited (guessing a 256-bit key is infeasible). Add Cloudflare rate limiting if you want quieter logs.
