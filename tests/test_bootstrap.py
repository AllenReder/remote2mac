"""Bootstrap and dispatcher tests."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from remote2mac.services.bootstrap import (
    RemoteBootstrapResult,
    bootstrap_remote,
    build_dispatcher_script,
)


def test_dispatcher_forwards_tool_name_and_exit_code(tmp_path: Path) -> None:
    """The generated dispatcher should preserve argv and exit code."""
    captured: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            captured["path"] = self.path
            captured["token"] = self.headers["X-Session-Token"]
            captured["body"] = json.loads(self.rfile.read(length))
            payload = {
                "exit_code": 7,
                "stdout_b64": base64.b64encode(b"stdout-data").decode("ascii"),
                "stderr_b64": base64.b64encode(b"stderr-data").decode("ascii"),
                "duration_ms": 5,
                "timed_out": False,
                "truncated": False,
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    remote_state_dir = tmp_path / ".remote2mac"
    remote_state_dir.mkdir()
    (remote_state_dir / "bridge_state.json").write_text(
        json.dumps(
            {
                "session_token": "dispatch-token",
                "remote_forward_port": server.server_port,
            }
        ),
        encoding="utf-8",
    )

    home = tmp_path / "home"
    home.mkdir()
    (home / ".remote2mac").symlink_to(remote_state_dir)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    dispatcher = bin_dir / ".remote2mac-dispatch"
    dispatcher.write_text(build_dispatcher_script(), encoding="utf-8")
    dispatcher.chmod(0o755)
    tool_link = bin_dir / "remindctl"
    tool_link.symlink_to(dispatcher.name)

    result = subprocess.run(
        [str(tool_link), "show", "today"],
        capture_output=True,
        text=False,
        env={"HOME": str(home), "PATH": os.environ.get("PATH", "")},
        check=False,
    )
    server.shutdown()
    thread.join(timeout=2)

    assert result.returncode == 7
    assert result.stdout == b"stdout-data"
    assert result.stderr == b"stderr-data"
    assert captured["path"] == "/v1/exec"
    assert captured["token"] == "dispatch-token"
    assert captured["body"] == {"tool": "remindctl", "argv": ["show", "today"]}


def test_dispatcher_returns_offline_when_server_unreachable(tmp_path: Path) -> None:
    """The generated dispatcher should return the offline contract."""
    home = tmp_path / "home"
    home.mkdir()
    state_dir = home / ".remote2mac"
    state_dir.mkdir()
    (state_dir / "bridge_state.json").write_text(
        json.dumps({"session_token": "token", "remote_forward_port": 65530}),
        encoding="utf-8",
    )

    dispatcher = tmp_path / "dispatch"
    dispatcher.write_text(build_dispatcher_script(), encoding="utf-8")
    dispatcher.chmod(0o755)

    result = subprocess.run(
        [str(dispatcher)],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": os.environ.get("PATH", "")},
        check=False,
    )
    assert result.returncode == 111
    assert "remote2mac: remote Mac node is offline; cannot execute dispatch" in result.stderr


def test_bootstrap_remote_parses_remote_json(settings) -> None:
    """bootstrap_remote should parse the remote JSON payload."""

    def fake_runner(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(
                {
                    "remote_bin_dir": "/home/allen/.local/bin",
                    "dispatcher_path": "/home/allen/.local/bin/.remote2mac-dispatch",
                    "tool_count": 1,
                }
            ),
            stderr="",
        )

    result = bootstrap_remote(settings, "session-token", runner=fake_runner)
    assert result == RemoteBootstrapResult(
        remote_bin_dir="/home/allen/.local/bin",
        dispatcher_path="/home/allen/.local/bin/.remote2mac-dispatch",
        tool_count=1,
    )
