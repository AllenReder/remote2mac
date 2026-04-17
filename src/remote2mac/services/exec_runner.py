"""Tool execution service."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from remote2mac.config import Settings


class ToolNotAllowedError(Exception):
    """Raised when a tool is not present in the whitelist."""


class ExecutionError(Exception):
    """Raised when an execution cannot be started."""


@dataclass(slots=True)
class ExecutionResult:
    """Structured execution result."""

    exit_code: int
    stdout: bytes
    stderr: bytes
    duration_ms: int
    timed_out: bool = False
    truncated: bool = False


def _truncate(data: bytes | None, limit: int) -> tuple[bytes, bool]:
    """Truncate byte output at the configured limit."""
    payload = data or b""
    if len(payload) <= limit:
        return payload, False
    return payload[:limit], True


class ExecRunner:
    """Run whitelisted tools without shell interpolation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def run_tool(self, tool_name: str, argv: list[str]) -> ExecutionResult:
        """Execute a configured tool with argv passthrough."""
        tool = self._settings.tools.get(tool_name)
        if tool is None:
            raise ToolNotAllowedError(f"tool '{tool_name}' is not allowed")

        start = time.perf_counter()
        try:
            completed = subprocess.run(
                [str(tool.path), *argv],
                capture_output=True,
                check=False,
                timeout=tool.timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            stdout, stdout_truncated = _truncate(exc.stdout, tool.max_output_bytes)
            stderr, stderr_truncated = _truncate(exc.stderr, tool.max_output_bytes)
            duration_ms = int((time.perf_counter() - start) * 1000)
            return ExecutionResult(
                exit_code=124,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                timed_out=True,
                truncated=stdout_truncated or stderr_truncated,
            )
        except OSError as exc:
            raise ExecutionError(str(exc)) from exc

        stdout, stdout_truncated = _truncate(completed.stdout, tool.max_output_bytes)
        stderr, stderr_truncated = _truncate(completed.stderr, tool.max_output_bytes)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return ExecutionResult(
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=False,
            truncated=stdout_truncated or stderr_truncated,
        )
