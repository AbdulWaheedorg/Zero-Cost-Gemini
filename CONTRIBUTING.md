# Contributing

Thanks for helping! Bug fixes, docs improvements, tests and well-scoped features are all welcome.

## Development setup

```bash
git clone https://github.com/AbdulWaheedorg/Zero-Cost-Gemini.git
cd Zero-Cost-Gemini
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest                    # ~100 tests, a few seconds, no Google account needed
```

The test suite is **hermetic**: [`tests/fake_agy.py`](tests/fake_agy.py) stands in for the Antigravity CLI and is scripted per test through a `scenario.json` (quota errors, hangs, crashes, slow streams, …). You never need real credentials to work on the code.

To also run the opt-in live tests against your own signed-in `agy` (these use your real quota):
```bash
AGY_LIVE=1 python -m pytest -m live
```

More detail: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Pull requests

1. Open an issue first for anything larger than a small fix, so we can agree on the approach.
2. Keep changes focused; match the style of the surrounding code.
3. Add or update tests. A bug fix should come with a test that fails without it.
4. Run `python -m pyflakes src tests` and `python -m pytest` before pushing; CI runs both on Python 3.11–3.13.
5. Update the docs when behaviour, configuration or endpoints change.

## Never commit secrets

No API keys, admin keys, `.env`, `keys.json`, OAuth tokens, account emails, server IPs or personal domains. `.gitignore` covers the usual files; double-check your diff anyway.

## Code of conduct

Participation is governed by our [Code of Conduct](CODE_OF_CONDUCT.md).
