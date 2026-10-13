# Deployment (Linux server)

This puts Zero-Cost Gemini on a Linux server (e.g. Ubuntu 24.04) behind HTTPS, so you can use it from anywhere. Expect about 15 minutes.

> [!WARNING]
> Read the [terms notice](../README.md#read-this-first) first. A public endpoint makes your subscription usable by anyone who has one of your keys, so treat keys like passwords.

## 1. Prerequisites

- A Linux server you control, with a non-root user (below: `YOUR_USER`).
- A domain or subdomain pointing at it (below: `api.example.com`).
- Python 3.11+ and git: `sudo apt install -y python3 python3-venv git`.
- A few hundred MB of free RAM per concurrent request you plan to allow (`MAX_CONCURRENT_REQUESTS`), and some free disk (`agy` keeps a history of every conversation under the account's home directory, so it grows).

## 2. Install and sign in to the Antigravity CLI

As `YOUR_USER`, using Google's official installer ([docs](https://antigravity.google/docs/cli/install)):
```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
```
Then run `agy` once and sign in. On a server without a browser, `agy` prints a sign-in URL: open it on your own computer, approve, and paste the code back into the terminal. Check that it works:
```bash
~/.local/bin/agy models
```

## 3. Install the gateway

```bash
cd ~
git clone https://github.com/AbdulWaheedorg/Zero-Cost-Gemini.git
cd Zero-Cost-Gemini
python3 -m venv venv
venv/bin/pip install -r requirements.txt

cp .env.example .env && chmod 600 .env
venv/bin/python -m src.keys create my-laptop      # save the printed key
```
Optionally set `ADMIN_API_KEY` in `.env` to manage keys over HTTP ([API Keys](API_KEYS.md)).

## 4. Run it as a service

Edit [`deploy/zero-cost-gemini.service`](../deploy/zero-cost-gemini.service) and replace `YOUR_USER`, then:
```bash
sudo cp deploy/zero-cost-gemini.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zero-cost-gemini
curl -s http://127.0.0.1:8200/healthz
```
The unit binds to `127.0.0.1:8200` only, so nothing is exposed until the reverse proxy is in place.

## 5. HTTPS with Caddy

Install [Caddy](https://caddyserver.com/docs/install), then use [`deploy/Caddyfile.example`](../deploy/Caddyfile.example) with your domain:
```bash
sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile   # edit api.example.com first
sudo systemctl reload caddy
curl -s https://api.example.com/healthz
```
Caddy gets a Let's Encrypt certificate automatically. `flush_interval -1` makes tokens stream to the client immediately.

**If Caddy runs in Docker:** it can't reach `127.0.0.1` on the host. Either run Caddy with host networking, or bind the service to the Docker bridge address (e.g. `--host 172.17.0.1`) and make sure your firewall blocks port 8200 from the internet.

## 6. Firewall and Cloudflare

- Allow only SSH, 80 and 443 inbound (`sudo ufw allow OpenSSH; sudo ufw allow 80,443/tcp; sudo ufw enable`). Never expose port 8200 directly.
- **Behind Cloudflare**, turn on **SSL/TLS → Edge Certificates → Always Use HTTPS** and set **Minimum TLS Version to 1.2**. The gateway also refuses requests that reached Cloudflare over plain `http://` (`403 https_required`), but by then the key has already crossed the internet unencrypted.

## 7. Verify

```bash
KEY="sk-agy-..."
curl -s https://api.example.com/v1/models -H "Authorization: Bearer $KEY"
curl -s https://api.example.com/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"gemini-3.8-flash-low","messages":[{"role":"user","content":"Say hi"}]}'
```

## 8. Operating it

```bash
sudo systemctl status zero-cost-gemini --no-pager
sudo journalctl -u zero-cost-gemini -f            # each chat logs the key id that made it
df -h /                                           # watch disk: agy's conversation history grows
du -sh ~/.gemini/antigravity-cli
```

### Updating, with a rollback point
```bash
cd ~/Zero-Cost-Gemini
mkdir -p backups && cp -a src "backups/src-$(date -u +%Y%m%dT%H%M%SZ)"
git pull
venv/bin/pip install -r requirements.txt
sudo systemctl restart zero-cost-gemini
curl -sf http://127.0.0.1:8200/healthz || echo "unhealthy: restore from backups/ and restart"
```

### Re-checking tool safety after an `agy` update
The gateway relies on `agy` denying tools in headless mode ([Security Model](SECURITY_MODEL.md)). After upgrading `agy`, run the live check from a development checkout:
```bash
AGY_LIVE=1 python -m pytest -m live -k tool_use_is_denied
```
