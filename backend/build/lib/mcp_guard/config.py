"""Paths, settings and the shared API token.

Everything lives under ``~/.mcp-guard`` (override with ``MCP_GUARD_HOME``):

- ``config.json``  daemon settings (host, port, timeout, default action)
- ``token``        bearer token shared by the daemon, proxies and the desktop UI
- ``audit.db``     SQLite audit log and policy rules
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

PolicyAction = Literal["allow", "block", "ask"]


def guard_home() -> Path:
    home = Path(os.environ.get("MCP_GUARD_HOME", Path.home() / ".mcp-guard")).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    return home


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    # How long a request waits for a user decision before it is denied.
    approval_timeout_seconds: int = Field(default=60, ge=5, le=3600)
    # What happens when no policy rule matches a tool call.
    default_action: PolicyAction = "ask"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def config_path() -> Path:
    return guard_home() / "config.json"


def load_settings() -> Settings:
    path = config_path()
    if path.exists():
        return Settings.model_validate_json(path.read_text())
    return Settings()


def save_settings(settings: Settings) -> None:
    path = config_path()
    path.write_text(json.dumps(settings.model_dump(), indent=2))


def db_path() -> Path:
    return guard_home() / "audit.db"


def load_or_create_token() -> str:
    """Return the shared API token, creating it (mode 0600) on first use."""
    env = os.environ.get("MCP_GUARD_TOKEN")
    if env:
        return env
    path = guard_home() / "token"
    if path.exists():
        return path.read_text().strip()
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token
