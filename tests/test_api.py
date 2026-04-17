"""HTTP API tests for remote2mac."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from remote2mac.app import create_app
from remote2mac.runtime import Runtime


class AliveProcess:
    """Minimal process stub that looks alive to Runtime."""

    pid = 1234

    def poll(self):
        return None


def make_client(settings, session_token: str) -> TestClient:
    """Build a TestClient with a prepared runtime."""
    runtime = Runtime(settings)
    runtime.session_token = session_token
    runtime.tunnel_status.active = True
    runtime.tunnel_status.pid = 1234
    runtime.bootstrap_status.ready = True
    runtime._tunnel_process = AliveProcess()
    app = create_app(runtime)
    return TestClient(app)


def test_health_no_auth_required(settings) -> None:
    """Health should remain public."""
    with make_client(settings, "token") as client:
        response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["ready"] is True
    assert payload["version"] == "0.1.0"


def test_exec_requires_session_token(settings) -> None:
    """Exec should reject missing auth."""
    with make_client(settings, "token") as client:
        response = client.post("/v1/exec", json={"tool": "scriptcmd", "argv": ["ok", "hello"]})
    assert response.status_code == 401
    assert "Missing session token" in response.json()["detail"]


def test_exec_rejects_invalid_token(settings) -> None:
    """Exec should reject bad tokens."""
    with make_client(settings, "token") as client:
        response = client.post(
            "/v1/exec",
            headers={"X-Session-Token": "wrong"},
            json={"tool": "scriptcmd", "argv": ["ok", "hello"]},
        )
    assert response.status_code == 401
    assert "Invalid session token" in response.json()["detail"]


def test_exec_runs_tool_and_returns_base64(settings, session_token: str) -> None:
    """Exec should run the configured tool and return encoded output."""
    with make_client(settings, session_token) as client:
        response = client.post(
            "/v1/exec",
            headers={"X-Session-Token": session_token},
            json={"tool": "scriptcmd", "argv": ["ok", "hello", "world"]},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["exit_code"] == 0
    assert base64.b64decode(payload["stdout_b64"]) == b"hello world\n"
    assert base64.b64decode(payload["stderr_b64"]) == b""
