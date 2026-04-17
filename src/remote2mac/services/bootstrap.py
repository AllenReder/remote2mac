"""Remote bootstrap helpers for wrapper installation."""

from __future__ import annotations

import base64
import json
import subprocess
import textwrap
from dataclasses import dataclass

from remote2mac import __version__
from remote2mac.config import APP_NAME, Settings


class RemoteBootstrapError(Exception):
    """Raised when remote setup or validation fails."""

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


@dataclass(slots=True)
class RemoteBootstrapResult:
    """Result of remote wrapper installation."""

    remote_bin_dir: str
    dispatcher_path: str
    tool_count: int


@dataclass(slots=True)
class RemotePreflightResult:
    """Result of remote non-mutating checks."""

    remote_bin_dir: str
    path_contains_bin_dir: bool
    shell_path: str
    python3_path: str


def build_dispatcher_script() -> str:
    """Build the single dispatcher script installed on the VPS."""
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import base64
        import json
        import sys
        import urllib.error
        import urllib.request
        from pathlib import Path

        APP_NAME = "{APP_NAME}"
        STATE_PATH = Path.home() / ".remote2mac" / "bridge_state.json"

        def main() -> int:
            tool_name = Path(sys.argv[0]).name
            try:
                state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                url = f"http://127.0.0.1:{{state['remote_forward_port']}}/v1/exec"
                request = urllib.request.Request(
                    url,
                    data=json.dumps({{"tool": tool_name, "argv": sys.argv[1:]}}).encode("utf-8"),
                    headers={{
                        "Content-Type": "application/json",
                        "X-Session-Token": state["session_token"],
                    }},
                    method="POST",
                )
                with urllib.request.urlopen(
                    request,
                    timeout=state.get("http_timeout_sec", 60),
                ) as response:
                    payload = json.load(response)
            except Exception:
                print(
                    f"remote2mac: remote Mac node is offline; cannot execute {{tool_name}}",
                    file=sys.stderr,
                )
                return 111

            sys.stdout.buffer.write(base64.b64decode(payload.get("stdout_b64", "")))
            sys.stderr.buffer.write(base64.b64decode(payload.get("stderr_b64", "")))
            return int(payload.get("exit_code", 1))

        if __name__ == "__main__":
            raise SystemExit(main())
        """
    )


def _build_remote_management_script(payload_b64: str) -> str:
    """Build the remote Python script used for preflight and bootstrap."""
    return textwrap.dedent(
        f"""\
        import base64
        import json
        import os
        import pwd
        import subprocess
        import tempfile
        from pathlib import Path

        PAYLOAD = json.loads(base64.b64decode("{payload_b64}"))
        MODE = PAYLOAD["mode"]

        def expand_remote(path: str) -> Path:
            return Path(os.path.expanduser(path))

        def login_shell_path() -> tuple[str, list[str]]:
            shell = os.environ.get("SHELL") or pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"
            result = subprocess.run(
                [shell, "-lc", 'printf %s "$PATH"'],
                capture_output=True,
                text=True,
                check=False,
            )
            path_entries = [
                os.path.realpath(os.path.expanduser(entry))
                for entry in result.stdout.split(os.pathsep)
                if entry
            ]
            return shell, path_entries

        def write_text_atomic(path: Path, content: str, mode: int) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                dir=path.parent,
            ) as handle:
                handle.write(content)
                tmp_path = Path(handle.name)
            tmp_path.chmod(mode)
            tmp_path.replace(path)

        remote_bin_dir = expand_remote(PAYLOAD["remote_bin_dir"])
        remote_state_dir = expand_remote(PAYLOAD["remote_state_dir"])
        if MODE == "bootstrap":
            remote_bin_dir.mkdir(parents=True, exist_ok=True)
            remote_state_dir.mkdir(parents=True, exist_ok=True)

        if not remote_bin_dir.exists():
            raise SystemExit(f"remote_bin_dir does not exist: {{remote_bin_dir}}")
        if not remote_bin_dir.is_dir():
            raise SystemExit(f"remote_bin_dir is not a directory: {{remote_bin_dir}}")

        shell_path, path_entries = login_shell_path()
        normalized_bin_dir = os.path.realpath(remote_bin_dir)
        if normalized_bin_dir not in path_entries:
            raise SystemExit(
                f"remote_bin_dir is not present in login shell PATH: {{remote_bin_dir}}"
            )

        if MODE == "preflight":
            python3_path = subprocess.run(
                ["python3", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip() or "python3"
            print(
                json.dumps(
                    {{
                        "remote_bin_dir": str(remote_bin_dir),
                        "path_contains_bin_dir": True,
                        "shell_path": shell_path,
                        "python3_path": python3_path,
                    }}
                )
            )
            raise SystemExit(0)

        probe = remote_bin_dir / ".remote2mac-write-probe"
        try:
            probe.write_text("ok", encoding="utf-8")
        finally:
            if probe.exists():
                probe.unlink()

        dispatcher = base64.b64decode(PAYLOAD["dispatcher_b64"]).decode("utf-8")
        dispatcher_path = remote_bin_dir / ".remote2mac-dispatch"
        write_text_atomic(dispatcher_path, dispatcher, 0o755)

        for tool_name in PAYLOAD["tools"]:
            link_path = remote_bin_dir / tool_name
            if link_path.exists() or link_path.is_symlink():
                if link_path.is_dir() and not link_path.is_symlink():
                    raise SystemExit(f"cannot replace directory at {{link_path}}")
                link_path.unlink()
            link_path.symlink_to(dispatcher_path.name)

        state = {{
            "session_token": PAYLOAD["session_token"],
            "remote_forward_port": PAYLOAD["remote_forward_port"],
            "http_timeout_sec": 60,
            "version": PAYLOAD["version"],
            "tools": PAYLOAD["tools"],
        }}
        write_text_atomic(
            remote_state_dir / "bridge_state.json",
            json.dumps(state, indent=2, sort_keys=True) + "\\n",
            0o600,
        )
        print(
            json.dumps(
                {{
                    "remote_bin_dir": str(remote_bin_dir),
                    "dispatcher_path": str(dispatcher_path),
                    "tool_count": len(PAYLOAD["tools"]),
                }}
            )
        )
        """
    )


def _ssh_base_command(settings: Settings) -> list[str]:
    """Build the shared SSH base command."""
    return [
        "ssh",
        "-T",
        "-p",
        str(settings.remote.ssh_port),
        settings.remote.ssh_target,
    ]


def _run_remote_script(
    settings: Settings,
    script: str,
    *,
    runner=subprocess.run,
) -> subprocess.CompletedProcess[str]:
    """Run a Python script remotely over SSH."""
    return runner(
        [*_ssh_base_command(settings), "python3", "-"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )


def _render_payload(settings: Settings, session_token: str, mode: str) -> str:
    """Render the JSON payload used by remote helpers."""
    payload = {
        "app_name": APP_NAME,
        "dispatcher_b64": base64.b64encode(
            build_dispatcher_script().encode("utf-8")
        ).decode("ascii"),
        "mode": mode,
        "remote_bin_dir": settings.remote.remote_bin_dir,
        "remote_forward_port": settings.remote.remote_forward_port,
        "remote_state_dir": settings.remote_state_dir,
        "session_token": session_token,
        "tools": sorted(settings.tools.keys()),
        "version": __version__,
    }
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def preflight_remote(
    settings: Settings,
    *,
    runner=subprocess.run,
) -> RemotePreflightResult:
    """Perform non-mutating remote validation over SSH."""
    script = _build_remote_management_script(_render_payload(settings, "doctor-token", "preflight"))
    result = _run_remote_script(settings, script, runner=runner)
    if result.returncode != 0:
        raise RemoteBootstrapError("remote preflight failed", result.stderr.strip())
    payload = json.loads(result.stdout)
    return RemotePreflightResult(**payload)


def bootstrap_remote(
    settings: Settings,
    session_token: str,
    *,
    runner=subprocess.run,
) -> RemoteBootstrapResult:
    """Install or refresh the remote dispatcher and wrapper links."""
    script = _build_remote_management_script(_render_payload(settings, session_token, "bootstrap"))
    result = _run_remote_script(settings, script, runner=runner)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RemoteBootstrapError("remote bootstrap failed", stderr)
    payload = json.loads(result.stdout)
    return RemoteBootstrapResult(**payload)
