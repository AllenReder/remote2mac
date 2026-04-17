"""FastAPI application factory for remote2mac."""

from __future__ import annotations

import base64

from fastapi import Depends, FastAPI, HTTPException

from remote2mac import __version__
from remote2mac.auth import verify_session_token
from remote2mac.models import ExecRequest, ExecResponse, HealthResponse
from remote2mac.runtime import Runtime
from remote2mac.services import ExecutionError, ToolNotAllowedError


def create_app(runtime: Runtime) -> FastAPI:
    """Build the FastAPI app for a running Runtime instance."""
    app = FastAPI(
        title="remote2mac",
        description="Whitelist-based bridge from remote processes to local macOS binaries.",
        version=__version__,
    )
    app.state.runtime = runtime

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> dict:
        payload = runtime.health_payload()
        payload["version"] = __version__
        return payload

    @app.post(
        "/v1/exec",
        response_model=ExecResponse,
        tags=["exec"],
        dependencies=[Depends(verify_session_token)],
    )
    async def exec_command(data: ExecRequest) -> ExecResponse:
        try:
            result = runtime.exec_runner.run_tool(data.tool, data.argv)
        except ToolNotAllowedError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ExecutionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return ExecResponse(
            exit_code=result.exit_code,
            stdout_b64=base64.b64encode(result.stdout).decode("ascii"),
            stderr_b64=base64.b64encode(result.stderr).decode("ascii"),
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
            truncated=result.truncated,
        )

    return app
