"""API models for remote2mac."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from remote2mac.config import TOOL_NAME_PATTERN


class ExecRequest(BaseModel):
    """A tool execution request."""

    tool: str
    argv: list[str] = Field(default_factory=list)

    @field_validator("tool")
    @classmethod
    def validate_tool(cls, value: str) -> str:
        """Reject tool names that do not match whitelist key rules."""
        if not TOOL_NAME_PATTERN.fullmatch(value):
            raise ValueError("tool contains invalid characters")
        return value


class ExecResponse(BaseModel):
    """A tool execution response."""

    exit_code: int
    stdout_b64: str
    stderr_b64: str
    duration_ms: int
    timed_out: bool = False
    truncated: bool = False


class HealthResponse(BaseModel):
    """Health response for local status inspection."""

    status: str = "ok"
    version: str
    ready: bool
    session_token_configured: bool
    local: dict[str, Any]
    ssh_tunnel: dict[str, Any]
    bootstrap: dict[str, Any]
