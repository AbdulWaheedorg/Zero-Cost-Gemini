import asyncio
import time
from typing import Any, Dict, List, Optional

from src.account_manager import account_manager
from src.cli import run_agy
from src.security import get_logger

logger = get_logger("model_registry")


def parse_models_output(output: str, created_ts: int) -> List[Dict[str, Any]]:
    """
    Parses `agy models`, whose lines look like `<model_id>\\t<Display Name>`
    after a "Fetching available models..." banner. Lines without a tab are
    split on the first run of whitespace.
    """
    models = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("fetching") or line.startswith("="):
            continue
        if "\t" in line:
            model_id, _, display_name = line.partition("\t")
        else:
            model_id, _, display_name = line.partition(" ")
        model_id, display_name = model_id.strip(), display_name.strip()
        if not model_id:
            continue
        models.append({
            "id": model_id,
            "object": "model",
            "created": created_ts,
            "owned_by": "google",
            "display_name": display_name or model_id,
        })
    return models


class ModelRegistry:
    """
    Discovers available models from `agy models` and refreshes them periodically,
    so newly released models appear without code changes.
    """

    def __init__(self, refresh_interval_seconds: int = 1800, timeout: float = 60.0):
        self.refresh_interval = refresh_interval_seconds
        self.timeout = timeout
        self._models: List[Dict[str, Any]] = []
        self._last_refresh: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def cached_models(self) -> List[Dict[str, Any]]:
        return self._models

    async def get_models(self) -> List[Dict[str, Any]]:
        """Returns the model list, fetching it first if it has never loaded."""
        if not self._models:
            await self.refresh_models()
        return self._models

    async def get_model(self, model_id: str) -> Optional[Dict[str, Any]]:
        return next((m for m in await self.get_models() if m["id"] == model_id), None)

    async def refresh_models(self) -> None:
        async with self._lock:
            account_manager.ensure_discovered()
            home = account_manager.accounts[0].home_dir
            try:
                code, output, err = await run_agy(["models"], home, timeout=self.timeout)
            except Exception as e:  # FileNotFoundError, CLITimeout
                logger.warning(f"Could not run 'agy models': {e}")
                return
            if code != 0:
                logger.warning(f"'agy models' exited {code}: {err[-300:]}")
                return

            models = parse_models_output(output, int(time.time()))
            if not models:
                logger.warning(f"Could not parse models from 'agy models' output: {output[:200]!r}")
                return
            self._models = models
            self._last_refresh = time.time()
            logger.info(f"Discovered {len(models)} models: {[m['id'] for m in models]}")

    async def start_daemon(self) -> None:
        """Refreshes the model list now and then every `refresh_interval` seconds."""
        while True:
            try:
                await self.refresh_models()
                await asyncio.sleep(self.refresh_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Model registry daemon error: {e}")
                await asyncio.sleep(60)


model_registry = ModelRegistry()
