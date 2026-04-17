"""CLI tests for remote2mac."""

from __future__ import annotations

from pathlib import Path

from remote2mac.cli import build_parser, main
from remote2mac.services.bootstrap import RemoteBootstrapResult, RemotePreflightResult


def test_build_parser_exposes_subcommands() -> None:
    """The CLI parser should expose the documented commands."""
    parser = build_parser()
    args = parser.parse_args(["init"])
    assert args.command == "init"


def test_main_init_writes_config(tmp_path: Path) -> None:
    """init should create a starter config file."""
    config_path = tmp_path / "config.toml"
    assert main(["--config", str(config_path), "init"]) == 0
    contents = config_path.read_text(encoding="utf-8")
    assert '[remote]' in contents
    assert '[tools.remindctl]' in contents
    assert '[tools.osascript]' not in contents


def test_main_init_refuses_existing_file(tmp_path: Path) -> None:
    """init should not overwrite an existing config by default."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("existing = true\n", encoding="utf-8")
    assert main(["--config", str(config_path), "init"]) == 1
    assert config_path.read_text(encoding="utf-8") == "existing = true\n"


def test_main_doctor_success(monkeypatch, settings) -> None:
    """doctor should return 0 on successful checks."""
    monkeypatch.setattr("remote2mac.cli.load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "remote2mac.cli.preflight_remote",
        lambda _settings: RemotePreflightResult(
            remote_bin_dir="/home/allen/.local/bin",
            path_contains_bin_dir=True,
            shell_path="/bin/zsh",
            python3_path="/usr/bin/python3",
        ),
    )
    monkeypatch.setattr("remote2mac.cli.shutil.which", lambda name: f"/usr/bin/{name}")
    assert main(["doctor"]) == 0


def test_main_bootstrap_success(monkeypatch, settings) -> None:
    """bootstrap should return 0 when remote installation succeeds."""
    monkeypatch.setattr("remote2mac.cli.load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "remote2mac.cli.bootstrap_remote",
        lambda _settings, _token: RemoteBootstrapResult(
            remote_bin_dir="/home/allen/.local/bin",
            dispatcher_path="/home/allen/.local/bin/.remote2mac-dispatch",
            tool_count=1,
        ),
    )
    monkeypatch.setattr("remote2mac.cli.generate_session_token", lambda: "token")
    assert main(["bootstrap"]) == 0
