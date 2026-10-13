from src.model_registry import ModelRegistry, parse_models_output


def test_parse_tab_separated_output_skips_banner():
    out = "Fetching available models...\ngemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n\n"
    [m] = parse_models_output(out, 123)
    assert m == {"id": "gemini-3.8-flash-low", "object": "model", "created": 123,
                 "owned_by": "google", "display_name": "Gemini 3.8 Flash (Low)"}


def test_parse_space_separated_output():
    """Regression: lines without a tab used to become the model id verbatim."""
    [m] = parse_models_output("claude-sonnet-4-6   Claude Sonnet 4.6\n", 0)
    assert m["id"] == "claude-sonnet-4-6"
    assert m["display_name"] == "Claude Sonnet 4.6"


def test_parse_bare_id():
    [m] = parse_models_output("gemini-4.0-argon\n", 0)
    assert m["id"] == m["display_name"] == "gemini-4.0-argon"


async def test_registry_discovers_models_from_cli(fake):
    registry = ModelRegistry()
    models = await registry.get_models()
    assert [m["id"] for m in models][:2] == ["gemini-3.8-flash-low", "gemini-3.8-flash-high"]
    assert (await registry.get_model("claude-sonnet-4-6"))["display_name"].startswith("Claude")
    assert await registry.get_model("nope") is None


async def test_registry_keeps_last_list_when_cli_fails(fake, monkeypatch):
    from src import cli
    registry = ModelRegistry()
    await registry.refresh_models()
    before = registry.cached_models

    def missing():
        raise FileNotFoundError("no agy")

    monkeypatch.setattr(cli, "agy_command", missing)
    await registry.refresh_models()
    assert registry.cached_models == before


async def test_daemon_refreshes_periodically_and_stops_on_cancel(fake):
    import asyncio
    registry = ModelRegistry(refresh_interval_seconds=0.3)
    task = asyncio.create_task(registry.start_daemon())
    await asyncio.sleep(1.5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.done()
    assert len(fake.calls("account_1", "models")) >= 3
