# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately through GitHub:
**[Security → Report a vulnerability](https://github.com/AbdulWaheedorg/Zero-Cost-Gemini/security/advisories/new)**.

Include what an attacker can do, how to reproduce it, and the version or commit. You should get a first reply within 7 days. Fixes are released as a new version and credited in the advisory unless you prefer otherwise.

Never include real API keys, OAuth tokens or account data in a report.

## Supported versions

| Version | Supported |
| :--- | :--- |
| 1.x (latest) | ✅ |
| older | ❌ |

## Scope

In scope: the gateway code in this repository (authentication, key storage, request handling, the agy process wrapper, and the documented deployment templates).

Out of scope: Google's Antigravity CLI itself (report those to Google), the security of your own server, and findings that require an already valid admin key.

## How the gateway is protected

See [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md) for the full threat model. In short: hashed and revocable API keys, a separate admin key, the agent's tools denied by default, concurrency and body-size limits, HTTPS-only behind Cloudflare, no public API explorer, and tokens redacted from logs.
