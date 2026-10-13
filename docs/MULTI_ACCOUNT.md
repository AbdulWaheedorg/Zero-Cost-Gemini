# Multi-Account Mode (optional)

> [!CAUTION]
> **Read this before enabling it.** Google's [Terms of Service](https://policies.google.com/terms) prohibit "bypassing our systems or protective measures", and the [Antigravity terms](https://antigravity.google/terms) (clause 6) say breaches can lead to suspension of your Antigravity and Gemini CLI accounts. **Rotating several accounts to get around usage limits can be treated as exactly that, and all of the accounts involved can be suspended.** This mode is off by default and you use it entirely at your own risk. Only ever use accounts that are **yours**, never other people's, and never resell or share access.

By default the gateway serves everything from **one** account: the `agy` sign-in of the user running the service. Multi-account mode adds an ordered list of signed-in accounts, with automatic failover between them.

## How it behaves

- Accounts are tried in **priority order** (1 first).
- An account is skipped while either its 5-hour **or** weekly bucket is at or below 5% **for the quota pool of the requested model** (Gemini models and Claude/GPT models are separate pools).
- If Google reports a quota error, that account is benched for `QUOTA_BACKOFF_SECONDS` (default 300 s) and the request is retried on the next account immediately, for streaming requests too, because the stream only starts after the first token.
- Transient "busy" errors also move to the next account, without benching.
- When the preferred account recovers, traffic returns to it within about a minute (the quota cache) or when its backoff ends.
- `GET /v1/quota` shows every account; the `X-Active-Account` header shows which one served each request.

## Platform support

Each account is isolated by giving its `agy` process its own `HOME` directory. This is **verified on headless Linux servers**, where there is no desktop keyring and `agy` keeps the sign-in in files under `$HOME/.gemini/antigravity-cli/`. Where `agy` can use an OS keyring (Windows Credential Manager, macOS Keychain, a desktop Linux session with Secret Service), sign-ins are stored there instead and per-directory isolation may not work. Use a headless Linux server for this mode.

## Setup

### 1. Create a directory per account
```bash
cd ~/Zero-Cost-Gemini
mkdir -p accounts/account_1 accounts/account_2
chmod 700 accounts accounts/*
```
Directory names become account ids (shown in `/v1/quota` and `X-Active-Account`).

### 2. Describe each account
`accounts/account_1/metadata.json`:
```json
{"email": "first-account@example.com", "priority": 1, "role": "primary"}
```
`accounts/account_2/metadata.json`:
```json
{"email": "second-account@example.com", "priority": 2, "role": "secondary"}
```
`priority`: lower is tried first. `email` is only for display (hidden from client keys); if it's omitted, the gateway reads it from the account's token, or falls back to the directory name.

### 3. Sign each account in, inside its own directory
Run `agy` with `HOME` pointed at the account directory and complete the sign-in (on a headless server: open the printed URL on your computer, sign in **with that account**, paste the code back):
```bash
AGY="$HOME/.local/bin/agy"
HOME="$PWD/accounts/account_1" "$AGY"
HOME="$PWD/accounts/account_2" "$AGY"
```
Check each one, and make sure their quotas differ (proof they're different sign-ins):
```bash
# the same command the gateway runs; the prompt is the fixed /quota command, no user input
HOME="$PWD/accounts/account_1" "$AGY" --dangerously-skip-permissions --output-format json -p /quota
HOME="$PWD/accounts/account_2" "$AGY" --dangerously-skip-permissions --output-format json -p /quota
```
Afterwards each account directory contains `.gemini/antigravity-cli/` with that account's sign-in. Keep it `chmod 700`; `accounts/` is git-ignored.

Don't add a `settings.json` with allow rules or trusted workspaces to these directories (see the [Security Model](SECURITY_MODEL.md#agent-tools-the-most-important-control)).

### 4. Point the gateway at them and restart
`ACCOUNTS_DIR` defaults to `./accounts` relative to the service's working directory, so with the layout above nothing needs configuring. Otherwise set it in `.env`. Then:
```bash
sudo systemctl restart zero-cost-gemini
journalctl -u zero-cost-gemini -n 20 --no-pager | grep "Discovered"   # e.g. "Discovered 2 accounts"
```

### 5. Verify failover
```bash
curl -s https://api.example.com/v1/quota -H "Authorization: Bearer $KEY"
```
Both accounts should show readings with `quota_error: null`. Each chat response's `X-Active-Account` header says which account served it.

## Turning it off

Stop the service, move or delete the `accounts/` directory (or point `ACCOUNTS_DIR` at a path that doesn't exist), and start it again. The gateway falls back to the service user's own sign-in.
