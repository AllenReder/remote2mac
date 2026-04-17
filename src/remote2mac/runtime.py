"""Runtime orchestration for the local agent process."""

from __future__ import annotations

import json
import secrets
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

from remote2mac.config import Settings
from remote2mac.services import ExecRunner, RemoteBootstrapError, bootstrap_remote


def generate_session_token() -> str:
    """Generate a fresh session token."""
    return secrets.token_urlsafe(32)


def build_tunnel_command(settings: Settings) -> list[str]:
    """Build the reverse SSH tunnel command."""
    return [
        "ssh",
        "-N",
        "-T",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-p",
        str(settings.remote.ssh_port),
        "-R",
        (
            f"127.0.0.1:{settings.remote.remote_forward_port}:"
            f"{settings.local.listen_host}:{settings.local.listen_port}"
        ),
        settings.remote.ssh_target,
    ]


@dataclass(slots=True)
class TunnelStatus:
    """Current SSH tunnel state."""

    active: bool = False
    pid: int | None = None
    restart_count: int = 0
    last_error: str | None = None


@dataclass(slots=True)
class BootstrapStatus:
    """Current remote wrapper state."""

    ready: bool = False
    tool_count: int = 0
    remote_bin_dir: str | None = None
    dispatcher_path: str | None = None
    last_error: str | None = None


class Runtime:
    """Own process state for HTTP, tunnel, and bootstrap supervision."""

    def __init__(
        self,
        settings: Settings,
        *,
        bootstrapper: Callable[[Settings, str], object] = bootstrap_remote,
        popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        supervisor_interval_sec: float = 5.0,
    ) -> None:
        self.settings = settings
        self.session_token = generate_session_token()
        self.exec_runner = ExecRunner(settings)
        self.bootstrapper = bootstrapper
        self.popen_factory = popen_factory
        self.supervisor_interval_sec = supervisor_interval_sec
        self.tunnel_status = TunnelStatus()
        self.bootstrap_status = BootstrapStatus()
        self.started_at = int(time.time())
        self._tunnel_process: subprocess.Popen[str] | None = None
        self._bootstrapped_pid: int | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._supervisor_thread: threading.Thread | None = None
        self._status_file = self.settings.state_dir / "status.json"

    @property
    def ready(self) -> bool:
        """Return whether the process is ready for remote traffic."""
        return self.tunnel_status.active and self.bootstrap_status.ready

    def start(self) -> None:
        """Start tunnel supervision and perform the initial bootstrap."""
        self.settings.state_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_ready()
        self._supervisor_thread = threading.Thread(target=self._supervisor_loop, daemon=True)
        self._supervisor_thread.start()

    def stop(self) -> None:
        """Stop background work and terminate the SSH process."""
        self._stop_event.set()
        if self._supervisor_thread is not None:
            self._supervisor_thread.join(timeout=self.supervisor_interval_sec + 1)
        self._terminate_tunnel()
        self._write_status_file()

    def health_payload(self) -> dict:
        """Return the health structure used by the HTTP handler."""
        self._sync_tunnel_status()
        return {
            "status": "ok",
            "ready": self.ready,
            "session_token_configured": bool(self.session_token),
            "local": {
                "listen_host": self.settings.local.listen_host,
                "listen_port": self.settings.local.listen_port,
                "config_path": (
                    str(self.settings.config_path) if self.settings.config_path else None
                ),
            },
            "ssh_tunnel": asdict(self.tunnel_status),
            "bootstrap": asdict(self.bootstrap_status),
        }

    def _supervisor_loop(self) -> None:
        """Periodically re-establish the tunnel and bootstrap if needed."""
        while not self._stop_event.wait(self.supervisor_interval_sec):
            self._ensure_ready()

    def _ensure_ready(self) -> None:
        """Ensure the tunnel is alive and wrappers are installed."""
        with self._lock:
            tunnel_changed = self._ensure_tunnel_running()
            if not self.tunnel_status.active:
                self._write_status_file()
                return
            if (
                tunnel_changed
                or not self.bootstrap_status.ready
                or self._bootstrapped_pid != self.tunnel_status.pid
            ):
                self._run_bootstrap()
            self._write_status_file()

    def _ensure_tunnel_running(self) -> bool:
        """Ensure the reverse tunnel process is active."""
        self._sync_tunnel_status()
        if self.tunnel_status.active:
            return False

        command = build_tunnel_command(self.settings)
        process = self.popen_factory(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(1)
        if process.poll() is not None:
            _, stderr = process.communicate(timeout=1)
            self.tunnel_status.active = False
            self.tunnel_status.pid = None
            self.tunnel_status.last_error = (stderr or "ssh exited unexpectedly").strip()
            self.bootstrap_status.ready = False
            self.bootstrap_status.last_error = "bootstrap skipped because SSH tunnel is unavailable"
            return False

        self._tunnel_process = process
        self.tunnel_status.active = True
        self.tunnel_status.pid = process.pid
        self.tunnel_status.restart_count += 1
        self.tunnel_status.last_error = None
        self.bootstrap_status.ready = False
        self.bootstrap_status.last_error = None
        return True

    def _sync_tunnel_status(self) -> None:
        """Refresh active tunnel bookkeeping based on process state."""
        if self._tunnel_process is None:
            self.tunnel_status.active = False
            self.tunnel_status.pid = None
            return

        if self._tunnel_process.poll() is None:
            self.tunnel_status.active = True
            self.tunnel_status.pid = self._tunnel_process.pid
            return

        stderr = ""
        if self._tunnel_process.stderr is not None:
            try:
                stderr = self._tunnel_process.stderr.read().strip()
            except OSError:
                stderr = ""
        self.tunnel_status.active = False
        self.tunnel_status.pid = None
        self.tunnel_status.last_error = stderr or "ssh tunnel exited"
        self.bootstrap_status.ready = False
        self._tunnel_process = None

    def _run_bootstrap(self) -> None:
        """Install or refresh the remote wrapper files."""
        try:
            result = self.bootstrapper(self.settings, self.session_token)
        except RemoteBootstrapError as exc:
            self.bootstrap_status.ready = False
            self.bootstrap_status.last_error = exc.stderr or str(exc)
            return

        self.bootstrap_status.ready = True
        self.bootstrap_status.tool_count = result.tool_count
        self.bootstrap_status.remote_bin_dir = result.remote_bin_dir
        self.bootstrap_status.dispatcher_path = result.dispatcher_path
        self.bootstrap_status.last_error = None
        self._bootstrapped_pid = self.tunnel_status.pid

    def _terminate_tunnel(self) -> None:
        """Terminate the SSH process if it exists."""
        if self._tunnel_process is None:
            return
        self._tunnel_process.terminate()
        try:
            self._tunnel_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._tunnel_process.kill()
            self._tunnel_process.wait(timeout=5)
        finally:
            self._tunnel_process = None
            self.tunnel_status.active = False
            self.tunnel_status.pid = None

    def _write_status_file(self) -> None:
        """Persist a small local status file for inspection."""
        payload = {
            "started_at": self.started_at,
            "ready": self.ready,
            "tunnel": asdict(self.tunnel_status),
            "bootstrap": asdict(self.bootstrap_status),
            "tools": sorted(self.settings.tools.keys()),
        }
        self._status_file.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
