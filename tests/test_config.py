"""Configuration tests for remote2mac."""

from __future__ import annotations

from pathlib import Path

import pytest

from remote2mac.config import load_settings


def test_load_settings_reads_tools(settings) -> None:
    """Settings should load the configured whitelist."""
    assert settings.remote.ssh_target == "allen@vps.example.com"
    assert "scriptcmd" in settings.tools
    assert settings.tools["scriptcmd"].timeout_sec == 1


def test_load_settings_rejects_relative_tool_paths(tmp_path: Path) -> None:
    """Tool paths must be absolute."""
    config = tmp_path / "config.toml"
    config.write_text(
        """
[remote]
ssh_host = "vps.example.com"
ssh_user = "allen"

[tools.bad]
path = "relative-tool"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="tool path must be absolute"):
        load_settings(config)

