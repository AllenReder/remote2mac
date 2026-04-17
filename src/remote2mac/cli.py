"""CLI entrypoints for remote2mac."""

from __future__ import annotations

import argparse
import platform
import shutil
from pathlib import Path

import uvicorn

from remote2mac.app import create_app
from remote2mac.config import APP_NAME, load_settings, render_config_template, resolve_config_path
from remote2mac.runtime import Runtime, generate_session_token
from remote2mac.services import RemoteBootstrapError, bootstrap_remote, preflight_remote


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--config", type=Path, default=None, help="Path to config.toml")

    subparsers = parser.add_subparsers(dest="command", required=True)

    agent = subparsers.add_parser("agent", help="Run the local HTTP agent and SSH supervisor")
    agent.add_argument("--log-level", default="info", help="uvicorn log level")

    init = subparsers.add_parser("init", help="Create a starter config.toml")
    init.add_argument("--force", action="store_true", help="Overwrite an existing config file")

    subparsers.add_parser("doctor", help="Check local tools, SSH, and remote path readiness")
    subparsers.add_parser("bootstrap", help="Install or refresh the remote wrappers only")
    return parser


def _load_settings_or_exit(config_path: Path | None):
    try:
        return load_settings(config_path)
    except Exception as exc:
        print(f"error: {exc}")
        raise SystemExit(1) from exc


def run_agent(args: argparse.Namespace) -> int:
    """Run the FastAPI server plus SSH supervision."""
    settings = _load_settings_or_exit(args.config)
    runtime = Runtime(settings)
    runtime.start()
    try:
        uvicorn.run(
            create_app(runtime),
            host=settings.local.listen_host,
            port=settings.local.listen_port,
            log_level=args.log_level.lower(),
            access_log=False,
        )
    finally:
        runtime.stop()
    return 0


def run_init(args: argparse.Namespace) -> int:
    """Create a starter config file."""
    config_path = resolve_config_path(args.config)
    if config_path.exists() and not args.force:
        print(f"error: config already exists: {config_path}")
        print("hint: use --force to overwrite it")
        return 1

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(render_config_template(), encoding="utf-8")
    print(f"Wrote starter config to {config_path}")
    return 0


def run_doctor(args: argparse.Namespace) -> int:
    """Check local and remote prerequisites."""
    settings = _load_settings_or_exit(args.config)
    errors = 0

    if platform.system() == "Darwin":
        print("OK local platform: Darwin")
    else:
        print(f"WARN local platform: {platform.system()} (TCC checks only make sense on macOS)")

    ssh_path = shutil.which("ssh")
    if ssh_path:
        print(f"OK ssh: {ssh_path}")
    else:
        print("ERROR ssh: not found")
        errors += 1

    for tool_name, tool in settings.tools.items():
        print(f"OK tool {tool_name}: {tool.path}")

    try:
        result = preflight_remote(settings)
    except RemoteBootstrapError as exc:
        print(f"ERROR remote preflight: {exc.stderr or exc}")
        errors += 1
    else:
        print(f"OK remote bin dir: {result.remote_bin_dir}")
        print(f"OK remote PATH includes bin dir: {result.path_contains_bin_dir}")
        print(f"OK remote shell: {result.shell_path}")
        print(f"OK remote python3: {result.python3_path}")

    return 1 if errors else 0


def run_bootstrap(args: argparse.Namespace) -> int:
    """Install or refresh remote wrappers."""
    settings = _load_settings_or_exit(args.config)
    try:
        result = bootstrap_remote(settings, generate_session_token())
    except RemoteBootstrapError as exc:
        print(f"error: {exc.stderr or exc}")
        return 1

    print(f"Installed {result.tool_count} wrapper(s) in {result.remote_bin_dir}")
    print(f"Dispatcher: {result.dispatcher_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return run_init(args)
    if args.command == "agent":
        return run_agent(args)
    if args.command == "doctor":
        return run_doctor(args)
    if args.command == "bootstrap":
        return run_bootstrap(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
