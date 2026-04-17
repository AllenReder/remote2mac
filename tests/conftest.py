"""Pytest fixtures for remote2mac."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from remote2mac.config import get_settings, load_settings


@pytest.fixture
def tool_script(tmp_path: Path) -> Path:
    """Create an executable test helper script."""
    script = tmp_path / "tool.py"
    script.write_text(
        f"""#!{sys.executable}
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
if mode == "ok":
    print(" ".join(sys.argv[2:]))
elif mode == "fail":
    print("boom", file=sys.stderr)
    raise SystemExit(3)
elif mode == "binary":
    sys.stdout.buffer.write(b"\\x00\\x01\\x02")
elif mode == "spam":
    sys.stdout.write("x" * 64)
    sys.stderr.write("y" * 64)
elif mode == "sleep":
    time.sleep(2)
else:
    print(mode)
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


@pytest.fixture
def config_file(tmp_path: Path, tool_script: Path) -> Path:
    """Write a remote2mac config file for tests."""
    config = tmp_path / "config.toml"
    config.write_text(
        f"""
[local]
listen_host = "127.0.0.1"
listen_port = 18123

[remote]
ssh_host = "vps.example.com"
ssh_user = "allen"
ssh_port = 22
remote_forward_port = 48123
remote_bin_dir = "~/.local/bin"

[tools.scriptcmd]
path = "{tool_script}"
timeout_sec = 1
max_output_bytes = 16
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config


@pytest.fixture
def settings(config_file: Path, monkeypatch: pytest.MonkeyPatch):
    """Load settings using the temporary config."""
    monkeypatch.setenv("REMOTE2MAC_CONFIG", str(config_file))
    get_settings.cache_clear()
    loaded = load_settings()
    yield loaded
    get_settings.cache_clear()
    monkeypatch.delenv("REMOTE2MAC_CONFIG", raising=False)


@pytest.fixture
def session_token() -> str:
    """Return a fixed test session token."""
    return "test-session-token"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unrelated settings from leaking into tests."""
    monkeypatch.setenv("PYTHONPATH", os.environ.get("PYTHONPATH", ""))
