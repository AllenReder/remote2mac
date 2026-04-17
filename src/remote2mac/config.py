"""Configuration loading for remote2mac."""

from __future__ import annotations

import os
import re
import shutil
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

APP_NAME = "remote2mac"
DEFAULT_CONFIG_PATH = Path("~/.config/remote2mac/config.toml").expanduser()
DEFAULT_STATE_DIR = Path("~/.local/state/remote2mac").expanduser()
DEFAULT_REMOTE_STATE_DIR = "~/.remote2mac"
DEFAULT_REMOTE_BIN_DIR = "~/.local/bin"
DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 18123
DEFAULT_REMOTE_FORWARD_PORT = 48123
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._+-]+$")


class LocalConfig(BaseModel):
    """Local HTTP listener settings."""

    listen_host: str = DEFAULT_LISTEN_HOST
    listen_port: int = DEFAULT_LISTEN_PORT

    @field_validator("listen_host")
    @classmethod
    def validate_listen_host(cls, value: str) -> str:
        """Only allow localhost binding for the local HTTP API."""
        if value != DEFAULT_LISTEN_HOST:
            raise ValueError(f"listen_host must be {DEFAULT_LISTEN_HOST}")
        return value


class RemoteConfig(BaseModel):
    """SSH and remote wrapper settings."""

    ssh_host: str
    ssh_user: str
    ssh_port: int = 22
    remote_forward_port: int = DEFAULT_REMOTE_FORWARD_PORT
    remote_bin_dir: str = DEFAULT_REMOTE_BIN_DIR

    @field_validator("ssh_host", "ssh_user", "remote_bin_dir")
    @classmethod
    def non_empty(cls, value: str) -> str:
        """Reject empty remote connection settings."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("value cannot be empty")
        return stripped

    @property
    def ssh_target(self) -> str:
        """Return the user@host SSH target."""
        return f"{self.ssh_user}@{self.ssh_host}"


class ToolConfig(BaseModel):
    """A single whitelisted executable."""

    path: Path
    timeout_sec: int = 30
    max_output_bytes: int = 1024 * 1024

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, value: str | Path) -> Path:
        """Expand user paths during parsing."""
        return Path(value).expanduser()

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: Path) -> Path:
        """Require absolute executable paths."""
        if not value.is_absolute():
            raise ValueError("tool path must be absolute")
        return value

    @field_validator("timeout_sec")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        """Require sane timeouts."""
        if value <= 0:
            raise ValueError("timeout_sec must be > 0")
        return value

    @field_validator("max_output_bytes")
    @classmethod
    def validate_output_limit(cls, value: int) -> int:
        """Require positive output size limits."""
        if value <= 0:
            raise ValueError("max_output_bytes must be > 0")
        return value


class Settings(BaseModel):
    """Top-level application settings."""

    local: LocalConfig = Field(default_factory=LocalConfig)
    remote: RemoteConfig
    tools: dict[str, ToolConfig]
    config_path: Path | None = None

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, value: dict[str, ToolConfig]) -> dict[str, ToolConfig]:
        """Require at least one valid tool name."""
        if not value:
            raise ValueError("at least one tool must be configured")
        invalid = [name for name in value if not TOOL_NAME_PATTERN.fullmatch(name)]
        if invalid:
            raise ValueError(f"invalid tool names: {', '.join(sorted(invalid))}")
        return value

    @property
    def state_dir(self) -> Path:
        """Return the local state directory."""
        return DEFAULT_STATE_DIR

    @property
    def remote_state_dir(self) -> str:
        """Return the remote state directory."""
        return DEFAULT_REMOTE_STATE_DIR

    def validate_local_environment(self) -> None:
        """Validate local prerequisites and configured tools."""
        errors: list[str] = []
        if shutil.which("ssh") is None:
            errors.append("ssh executable not found in PATH")
        for name, tool in self.tools.items():
            if not tool.path.exists():
                errors.append(f"tool '{name}' path does not exist: {tool.path}")
                continue
            if tool.path.is_dir():
                errors.append(f"tool '{name}' path is a directory: {tool.path}")
                continue
            if not os.access(tool.path, os.X_OK):
                errors.append(f"tool '{name}' path is not executable: {tool.path}")
        if errors:
            raise ValueError("\n".join(errors))


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    """Resolve the active config path."""
    candidate = config_path or os.environ.get("REMOTE2MAC_CONFIG") or DEFAULT_CONFIG_PATH
    return Path(candidate).expanduser().resolve()


def render_config_template() -> str:
    """Render the default config template for first-time setup."""
    return (
        f'[local]\n'
        f'listen_host = "{DEFAULT_LISTEN_HOST}"\n'
        f"listen_port = {DEFAULT_LISTEN_PORT}\n"
        "\n"
        "[remote]\n"
        'ssh_host = "your-remote-server"\n'
        'ssh_user = "your-remote-user"\n'
        "ssh_port = 22\n"
        f"remote_forward_port = {DEFAULT_REMOTE_FORWARD_PORT}\n"
        f'remote_bin_dir = "{DEFAULT_REMOTE_BIN_DIR}"\n'
        "\n"
        "[tools.remindctl]\n"
        'path = "/opt/homebrew/bin/remindctl"\n'
        "timeout_sec = 30\n"
        "max_output_bytes = 1048576\n"
    )


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load settings from TOML and validate the local environment."""
    resolved = resolve_config_path(config_path)
    if not resolved.exists():
        raise FileNotFoundError(f"remote2mac config not found: {resolved}")
    with resolved.open("rb") as handle:
        payload = tomllib.load(handle)
    payload["config_path"] = resolved
    settings = Settings.model_validate(payload)
    settings.validate_local_environment()
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return settings


@lru_cache
def get_settings() -> Settings:
    """Return cached settings using the default config path resolution."""
    return load_settings()
