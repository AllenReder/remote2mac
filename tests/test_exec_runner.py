"""Execution service tests."""

from __future__ import annotations

from remote2mac.services.exec_runner import ExecRunner


def test_runner_handles_success(settings) -> None:
    """The runner should capture stdout on success."""
    result = ExecRunner(settings).run_tool("scriptcmd", ["ok", "hello", "world"])
    assert result.exit_code == 0
    assert result.stdout == b"hello world\n"
    assert result.stderr == b""
    assert result.timed_out is False


def test_runner_handles_nonzero_exit(settings) -> None:
    """The runner should preserve stderr and exit code."""
    result = ExecRunner(settings).run_tool("scriptcmd", ["fail"])
    assert result.exit_code == 3
    assert result.stderr == b"boom\n"


def test_runner_truncates_large_output(settings) -> None:
    """The runner should truncate stdout/stderr independently."""
    result = ExecRunner(settings).run_tool("scriptcmd", ["spam"])
    assert result.truncated is True
    assert len(result.stdout) == 16
    assert len(result.stderr) == 16


def test_runner_times_out(settings) -> None:
    """The runner should return the timeout contract."""
    result = ExecRunner(settings).run_tool("scriptcmd", ["sleep"])
    assert result.exit_code == 124
    assert result.timed_out is True

