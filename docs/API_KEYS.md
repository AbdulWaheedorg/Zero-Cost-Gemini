# API Keys

Give every app, device or person **its own key**. If one leaks, or you stop using an app, revoke just that key; it stops working on the next request, with no restart and no effect on the others.

## How keys are stored

- Keys look like `sk-agy-` followed by 43 random characters (256 bits).
- Only the **SHA-256 hash** is stored, in `keys.json` (path set by `API_KEYS_FILE`, default `./keys.json`). The file is written atomically with mode `0600`.
- The plaintext key is shown **once**, when it's created. Lose it and you create a new one.
- The running server re-reads `keys.json` when it changes, so the CLI and the server can be used side by side.
- Each chat request is logged with the key's **id** (`chat key=key_… model=…`), never the key itself, so you can tell which key to revoke.

## From the command line (on the server)

Run these from the directory the service runs in:
```bash
python -m src.keys create laptop          # prints the new key once
python -m src.keys list                   # id, prefix, name, created, active/revoked
python -m src.keys revoke key_603534026f4c
```
Add `--file /path/to/keys.json` to manage a keys file elsewhere.

## Over HTTP (admin API)

Set `ADMIN_API_KEY` in `.env` to a long random value and restart:
```bash
python -c "import secrets; print('sk-admin-' + secrets.token_urlsafe(32))"
```
Then, from anywhere:
```bash
ADMIN="sk-admin-..."

# create (save the "key" from the response; it is never shown again)
curl -s -X POST https://api.example.com/admin/keys \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"name": "cursor-laptop"}'

# list
curl -s https://api.example.com/admin/keys -H "Authorization: Bearer $ADMIN"

# revoke
curl -s -X DELETE https://api.example.com/admin/keys/key_603534026f4c -H "Authorization: Bearer $ADMIN"
```

PowerShell:
```powershell
$admin = @{ Authorization = "Bearer sk-admin-..." }
$new = Invoke-RestMethod -Uri https://api.example.com/admin/keys -Method Post -Headers $admin -ContentType "application/json" -Body '{"name":"phone"}'
$new.key        # save this
(Invoke-RestMethod -Uri https://api.example.com/admin/keys -Headers $admin).data | Format-Table id, name, active
Invoke-RestMethod -Uri "https://api.example.com/admin/keys/$($new.id)" -Method Delete -Headers $admin
```

If you never set `ADMIN_API_KEY`, the `/admin/*` routes don't exist (`404`) and keys are managed only from the CLI. That's the smallest attack surface.

## The legacy single key

`PROXY_API_KEY` in the environment is still accepted, as key id `legacy-env`, for simple setups and older clients. Prefer named keys. To retire it: move every client to a named key, delete `PROXY_API_KEY` from `.env`, restart, and check the old key now gets `401`.

## No keys at all?

With no active key in `keys.json` and no `PROXY_API_KEY`, every request gets `401` and the log says `No active API keys`. The gateway never runs unauthenticated.
