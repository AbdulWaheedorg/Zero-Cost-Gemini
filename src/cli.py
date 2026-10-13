"""
Thin async wrapper around the Antigravity CLI (`agy`).

Every subprocess the proxy launches goes through here so that binary lookup,
per-account HOME isolation, timeouts, stderr draining and cleanup are handled
in exactly one place.
"""
import asyncio
import os
import shutil
import signal
import subprocess
import tempfile
from typing import Any, AsyncIterator, Dict, List, Optional

import orjson

from src.security import get_logger

logger = get_logger("cli")

# Keep only the tail of stderr; agy can be chatty and we only need it for errors.
STDERR_TAIL_BYTES = 8192


class CLITimeout(Exception):
    pass


def agy_command() -> List[str]:
    """
    Returns the argv prefix used to invoke agy. `AGY_BIN` overrides discovery.
    Returned as a list so tests can substitute `[python, fake_agy.py]`.
    """
    override = os.environ.get("AGY_BIN")
    if override:
        return [override]

    found = shutil.which("agy") or shutil.which("agy.exe")
    if found:
        return [found]

    candidates = [os.path.expanduser("~/.local/bin/agy")]
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        candidates.insert(0, os.path.join(os.environ["LOCALAPPDATA"], "agy", "bin", "agy.exe"))
    for path in candidates:
        if os.path.exists(path):
            return [path]

    raise FileNotFoundError("Antigravity binary 'agy' is not installed or not in PATH.")


def account_env(home_dir: str) -> Dict[str, str]:
    env = os.environ.copy()
    if home_dir:
        env["HOME"] = home_dir
        env["USERPROFILE"] = home_dir
    return env


def workdir_root() -> str:
    root = os.environ.get("AGY_WORKDIR_ROOT") or os.path.join(os.getcwd(), ".agy-work")
    os.makedirs(root, mode=0o700, exist_ok=True)
    return root


class AgyProcess:
    """
    One running agy invocation. Use as an async context manager; leaving the
    context always terminates the process and removes its scratch directory,
    including when the consuming request is cancelled (client disconnect).
    """

    def __init__(self, args: List[str], home_dir: str = "", stdin_data: Optional[bytes] = None):
        self.args = args
        self.home_dir = home_dir
        self.stdin_data = stdin_data
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._stderr = bytearray()
        self._stderr_task: Optional[asyncio.Task] = None
        self._workdir: Optional[str] = None

    async def __aenter__(self) -> "AgyProcess":
        # Each run gets an empty working directory so the agent never starts
        # inside the proxy's install dir (next to .env and account tokens).
        self._workdir = tempfile.mkdtemp(prefix="run-", dir=workdir_root())
        kwargs: Dict[str, Any] = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True  # lets us kill agy's whole process group
        self.proc = await asyncio.create_subprocess_exec(
            *agy_command(),
            *self.args,
            stdin=asyncio.subprocess.PIPE if self.stdin_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=account_env(self.home_dir),
            cwd=self._workdir,
            **kwargs,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        if self.stdin_data is not None:
            try:
                self.proc.stdin.write(self.stdin_data)
                await self.proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                self.proc.stdin.close()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.terminate()

    async def _drain_stderr(self) -> None:
        # Read stderr concurrently so a full stderr pipe can never block agy's stdout.
        while True:
            chunk = await self.proc.stderr.read(4096)
            if not chunk:
                return
            self._stderr += chunk
            if len(self._stderr) > STDERR_TAIL_BYTES:
                del self._stderr[: len(self._stderr) - STDERR_TAIL_BYTES]

    @property
    def stderr_tail(self) -> str:
        return self._stderr.decode("utf-8", errors="replace").strip()

    async def final_stderr(self, timeout: float = 2.0) -> str:
        """
        stderr is a separate pipe, so it can lag behind stdout. When agy has
        finished (or is finishing), wait briefly for it to exit and for stderr
        to drain before reading it for an error message.
        """
        try:
            await asyncio.wait_for(self.proc.wait(), timeout)
            if self._stderr_task:
                await asyncio.wait_for(asyncio.shield(self._stderr_task), timeout)
        except asyncio.TimeoutError:
            pass
        return self.stderr_tail

    async def readline(self, timeout: Optional[float]) -> bytes:
        try:
            return await asyncio.wait_for(self.proc.stdout.readline(), timeout)
        except asyncio.TimeoutError:
            raise CLITimeout(f"agy produced no output for {timeout}s")

    async def events(self, idle_timeout: Optional[float]) -> AsyncIterator[Dict[str, Any]]:
        """Yields each JSON line from stdout; non-JSON lines are skipped."""
        while True:
            line = await self.readline(idle_timeout)
            if not line:
                return
            line = line.strip()
            if not line:
                continue
            try:
                yield orjson.loads(line)
            except orjson.JSONDecodeError:
                continue

    async def communicate(self, timeout: float) -> bytes:
        try:
            stdout = await asyncio.wait_for(self.proc.stdout.read(), timeout)
            await asyncio.wait_for(self.proc.wait(), timeout)
        except asyncio.TimeoutError:
            raise CLITimeout(f"agy did not finish within {timeout}s")
        if self._stderr_task:
            await self._stderr_task
        return stdout

    def _kill_tree(self) -> None:
        # Synchronous on purpose: this often runs while the request task is being
        # cancelled (client disconnect), where any await would itself be cancelled
        # before the kill happened. agy spawns helper children, so kill the tree.
        pid = self.proc.pid
        try:
            if os.name != "nt":
                os.killpg(pid, signal.SIGKILL)
            else:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (ProcessLookupError, subprocess.SubprocessError, OSError):
            pass
        if self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass

    async def terminate(self) -> None:
        proc = self.proc
        try:
            if proc is not None and proc.returncode is None:
                self._kill_tree()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except asyncio.TimeoutError:
                    logger.error(f"agy pid {proc.pid} did not exit after kill")
            elif proc is not None and os.name != "nt":
                # agy exited by itself; reap any helpers left in its process group.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        finally:
            if self._stderr_task and not self._stderr_task.done():
                self._stderr_task.cancel()
            if self._workdir:
                try:
                    shutil.rmtree(self._workdir)
                except OSError:
                    # Windows releases the dead process's handle on its cwd a moment late.
                    asyncio.get_running_loop().call_later(2, shutil.rmtree, self._workdir, True)
                self._workdir = None

    @property
    def returncode(self) -> Optional[int]:
        return self.proc.returncode if self.proc else None


async def run_agy(args: List[str], home_dir: str = "", timeout: float = 30.0) -> tuple:
    """Runs agy to completion. Returns (returncode, stdout_text, stderr_tail)."""
    async with AgyProcess(args, home_dir) as p:
        stdout = await p.communicate(timeout)
        return p.returncode, stdout.decode("utf-8", errors="replace"), p.stderr_tail

