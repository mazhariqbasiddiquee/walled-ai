"""Approval Manager: owns the lifecycle of every intercepted tool call.

Each pending request gets its *own* future, so any number of concurrent tool
calls can wait for decisions independently. A request ends in exactly one
terminal state; the first transition wins and later ones are ignored.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Any

from . import policy
from .config import Settings
from .db import Database
from .events import EventHub
from .models import (
    TERMINAL_DECISIONS,
    DecidedBy,
    PolicyRule,
    RequestStatus,
    ToolRequest,
    utcnow,
)

log = logging.getLogger(__name__)

RESULT_PREVIEW_LIMIT = 4000


def _glob_literal(text: str) -> str:
    """Escape glob metacharacters so a remembered rule matches only this exact name."""
    return "".join(f"[{c}]" if c in "*?[" else c for c in text)


class RequestNotFound(KeyError):
    pass


class ApprovalManager:
    def __init__(self, db: Database, settings: Settings, events: EventHub):
        self.db = db
        self.settings = settings
        self.events = events
        self._waiters: dict[str, asyncio.Future[ToolRequest]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}

    # ---- startup / shutdown -------------------------------------------------

    def recover(self) -> None:
        n = self.db.expire_stale_pending(utcnow())
        if n:
            log.info("expired %d request(s) left pending by a previous run", n)

    def shutdown(self) -> None:
        for request_id in list(self._waiters):
            self._finish(request_id, RequestStatus.EXPIRED, "system", "gateway shutting down")

    # ---- lifecycle ----------------------------------------------------------

    def submit(self, server: str, tool: str, arguments: dict[str, Any]) -> ToolRequest:
        """Register a new tool call. Policy may decide it immediately."""
        now = utcnow()
        req = ToolRequest(
            request_id=f"req_{uuid.uuid4().hex[:16]}",
            server=server,
            tool=tool,
            arguments=arguments,
            created_at=now,
        )
        verdict = policy.evaluate(server, tool, self.db.list_rules(), self.settings.default_action)

        if verdict.action in ("allow", "block"):
            req.status = RequestStatus.APPROVED if verdict.action == "allow" else RequestStatus.REJECTED
            req.decided_by = "policy"
            req.decision_reason = verdict.reason
            req.decided_at = now
            self.db.save_request(req)
            self.events.publish("request.decided", req.model_dump(mode="json"))
            return req

        timeout = self.settings.approval_timeout_seconds
        req.expires_at = now + timedelta(seconds=timeout)
        self.db.save_request(req)

        loop = asyncio.get_running_loop()
        self._waiters[req.request_id] = loop.create_future()
        self._timers[req.request_id] = loop.call_later(
            timeout,
            self._finish,
            req.request_id,
            RequestStatus.EXPIRED,
            "timeout",
            f"no decision within {timeout}s",
        )
        self.events.publish("request.created", req.model_dump(mode="json"))
        return req

    async def wait(self, request_id: str, timeout: float) -> ToolRequest:
        """Wait up to ``timeout`` seconds for a decision; returns the current state."""
        future = self._waiters.get(request_id)
        if future is not None:
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout)
            except asyncio.TimeoutError:
                pass
        return self.get(request_id)

    def decide(
        self, request_id: str, approve: bool, *, remember: bool = False, reason: str | None = None
    ) -> ToolRequest:
        req = self.get(request_id)
        if req.status != RequestStatus.PENDING:
            return req  # already decided / expired; idempotent
        status = RequestStatus.APPROVED if approve else RequestStatus.REJECTED
        if remember:
            rule = self.db.add_rule(
                PolicyRule(
                    server=_glob_literal(req.server),
                    tool=_glob_literal(req.tool),
                    action="allow" if approve else "block",
                    note=f"remembered from {req.request_id}",
                )
            )
            self.events.publish("rule.created", rule.model_dump(mode="json"))
        return self._finish(request_id, status, "user", reason or ("approved" if approve else "blocked"))

    def cancel(self, request_id: str, reason: str = "cancelled by MCP client") -> ToolRequest:
        req = self.get(request_id)
        if req.status != RequestStatus.PENDING:
            return req
        return self._finish(request_id, RequestStatus.CANCELLED, "client", reason)

    def record_result(
        self,
        request_id: str,
        *,
        success: bool,
        result_preview: str | None,
        error: str | None,
        duration_ms: int | None,
    ) -> ToolRequest:
        req = self.get(request_id)
        req.execution_status = "success" if success else "error"
        req.result_preview = (result_preview or "")[:RESULT_PREVIEW_LIMIT] or None
        req.error = error
        req.duration_ms = duration_ms
        req.completed_at = utcnow()
        self.db.save_request(req)
        self.events.publish("request.completed", req.model_dump(mode="json"))
        return req

    # ---- queries ------------------------------------------------------------

    def get(self, request_id: str) -> ToolRequest:
        req = self.db.get_request(request_id)
        if req is None:
            raise RequestNotFound(request_id)
        return req

    def pending(self) -> list[ToolRequest]:
        return self.db.list_requests(status=RequestStatus.PENDING, limit=1000)

    # ---- internals ----------------------------------------------------------

    def _finish(
        self, request_id: str, status: RequestStatus, decided_by: DecidedBy, reason: str
    ) -> ToolRequest:
        assert status in TERMINAL_DECISIONS
        req = self.get(request_id)
        if req.status != RequestStatus.PENDING:
            return req
        req.status = status
        req.decided_by = decided_by
        req.decision_reason = reason
        req.decided_at = utcnow()
        self.db.save_request(req)

        timer = self._timers.pop(request_id, None)
        if timer:
            timer.cancel()
        future = self._waiters.pop(request_id, None)
        if future and not future.done():
            future.set_result(req)

        self.events.publish("request.decided", req.model_dump(mode="json"))
        return req
