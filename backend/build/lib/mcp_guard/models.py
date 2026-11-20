"""Data models shared by the daemon, the proxy and the API."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config import PolicyAction


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"  # the MCP client gave up before a decision


TERMINAL_DECISIONS = {
    RequestStatus.APPROVED,
    RequestStatus.REJECTED,
    RequestStatus.EXPIRED,
    RequestStatus.CANCELLED,
}

DecidedBy = Literal["user", "policy", "timeout", "client", "system"]
ExecutionStatus = Literal["success", "error"]


class ToolRequest(BaseModel):
    """One intercepted tool call and everything that happened to it."""

    request_id: str
    server: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: RequestStatus = RequestStatus.PENDING
    decided_by: DecidedBy | None = None
    decision_reason: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    # Filled in after an approved call has run on the upstream server.
    execution_status: ExecutionStatus | None = None
    result_preview: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    completed_at: datetime | None = None


class PolicyRule(BaseModel):
    id: int | None = None
    server: str = "*"  # glob, e.g. "filesystem"
    tool: str = "*"  # glob, e.g. "read_*"
    action: PolicyAction
    note: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


# ---- API payloads -----------------------------------------------------------


class SubmitRequest(BaseModel):
    server: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class DecisionIn(BaseModel):
    decision: Literal["allow", "block"]
    # Store a policy rule so the same server+tool is decided automatically next time.
    remember: bool = False
    reason: str | None = None


class ResultIn(BaseModel):
    success: bool
    result_preview: str | None = None
    error: str | None = None
    duration_ms: int | None = None


class RuleIn(BaseModel):
    server: str = "*"
    tool: str = "*"
    action: PolicyAction
    note: str | None = None


class SettingsPatch(BaseModel):
    approval_timeout_seconds: int | None = Field(default=None, ge=5, le=3600)
    default_action: PolicyAction | None = None
