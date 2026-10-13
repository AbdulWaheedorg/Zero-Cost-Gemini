# Development

## Project layout

```
src/
├── main.py             HTTP API, guard middleware, limits, SSE, admin routes
├── engine.py           prompt building, agy events, error mapping, failover
├── cli.py              the agy subprocess wrapper (all process handling)
├── account_manager.py  accounts, quota parsing/caching, account selection
├── model_registry.py   model discovery from `agy models`
├── keys.py             hashed API keys + `python -m src.keys` CLI
├── security.py         key checks, log redaction
└── converter.py        OpenAI body / chunk / error builders
tests/
├── fake_agy.py         stand-in for the Antigravity CLI, scripted per test
├── conftest.py         fixtures: two fake accounts, temp keys file, test client
├── test_*.py           hermetic tests (~100)
└── test_live.py        opt-in tests against your real, signed-in agy
deploy/                 systemd unit and Caddyfile templates
docs/                   this documentation
```

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pyflakes src tests
python -m pytest
```

The suite is **hermetic**: it never contacts Google and needs no account. CI runs it on Python 3.11, 3.12 and 3.13 ([workflow](../.github/workflows/ci.yml)).

### The fake CLI

[`tests/fake_agy.py`](../tests/fake_agy.py) imitates `agy`: `models`, `/quota`, and chat over stdin with `stream-json` events. The `fake` fixture creates two account directories, and each test scripts behaviour by writing that account's `scenario.json`:

```python
async def test_quota_error_rotates_to_next_account(fake):
    fake.set("account_1", chat="quota_error")
    completion, text, _ = await complete(MODEL, USER)
    assert completion.account.id == "account_2"
```

Available `chat` scenarios: `ok`, `quota_error`, `overloaded`, `concurrency`, `model_error`, `hang`, `hang_after_first`, `hang_with_child`, `slow`, `stderr_flood`, `no_result`, `empty`, `error_after_first`. There are also `quota`, `quota_fail`, `quota_hang`, `reply` and `stderr` keys. Every invocation is logged to the account's `calls.jsonl`, so tests can assert on argv, working directory, PIDs and call counts (`fake.calls("account_1", "quota")`).

### Live tests

[`tests/test_live.py`](../tests/test_live.py) runs the real API against your signed-in `agy`. It spends real quota, so it only runs when asked:
```bash
AGY_LIVE=1 python -m pytest -m live
AGY_LIVE=1 ACCOUNTS_DIR=/path/to/accounts python -m pytest -m live   # multi-account setup
```
It covers models, plain and streamed chat, multi-turn, third-party models, ignored parameters, usage chunks, quota, unknown models, and a check that the model **cannot run shell commands**.

## Running locally

```bash
python -m src.keys create dev
uvicorn src.main:app --reload --port 8200
```

## Guidelines

- Every subprocess goes through `src/cli.py`; don't call `asyncio.create_subprocess_exec` elsewhere.
- Classify upstream failures from `agy`'s `result` event (see `error_from_message`), never by grepping stderr.
- New behaviour needs a hermetic test; add a fake scenario rather than relying on real credentials.
- Never weaken the agent-tool controls (see [Security Model](SECURITY_MODEL.md)).
