"""HTTP + WebSocket API of the MCP Guard daemon.

Two kinds of callers use it, both authenticated with the shared bearer token:

- **Proxies** (one per protected MCP server) submit tool calls, long-poll for
  the decision, and report execution results.
- **The desktop UI** lists pending requests, sends Allow / Block decisions,
  browses history, edits rules and settings, and receives live events on ``/ws``.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import __version__
from .approvals import ApprovalManager, RequestNotFound
from .config import Settings, save_settings
from .db import Database
from .events import EventHub
from .models import (
    DecisionIn,
    PolicyRule,
    RequestStatus,
    ResultIn,
    RuleIn,
    SettingsPatch,
    SubmitRequest,
    ToolRequest,
)

log = logging.getLogger(__name__)

MAX_WAIT_SECONDS = 30.0


def create_app(
    settings: Settings, db: Database, token: str, *, persist_settings: bool = True
) -> FastAPI:
    events = EventHub()
    manager = ApprovalManager(db, settings, events)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        manager.recover()
        yield
        manager.shutdown()

    app = FastAPI(title="MCP Guard", version=__version__, lifespan=lifespan)
    app.state.manager = manager
    app.state.events = events

    bearer = HTTPBearer(auto_error=False)

    def require_token(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if creds is None or not secrets.compare_digest(creds.credentials, token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing token")

    def lookup(request_id: str) -> ToolRequest:
        try:
            return manager.get(request_id)
        except RequestNotFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "request not found") from None

    auth = [Depends(require_token)]

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    # ---- proxy-facing -------------------------------------------------------

    @app.post("/api/requests", dependencies=auth, status_code=201)
    async def submit(body: SubmitRequest) -> ToolRequest:
        return manager.submit(body.server, body.tool, body.arguments)

    @app.get("/api/requests/{request_id}/wait", dependencies=auth)
    async def wait(
        request_id: str, timeout: float = Query(25.0, gt=0, le=MAX_WAIT_SECONDS)
    ) -> ToolRequest:
        lookup(request_id)
        return await manager.wait(request_id, timeout)

    @app.post("/api/requests/{request_id}/cancel", dependencies=auth)
    async def cancel(request_id: str) -> ToolRequest:
        lookup(request_id)
        return manager.cancel(request_id)

    @app.post("/api/requests/{request_id}/result", dependencies=auth)
    async def result(request_id: str, body: ResultIn) -> ToolRequest:
        req = lookup(request_id)
        if req.status != RequestStatus.APPROVED:
            raise HTTPException(status.HTTP_409_CONFLICT, "request was not approved")
        return manager.record_result(
            request_id,
            success=body.success,
            result_preview=body.result_preview,
            error=body.error,
            duration_ms=body.duration_ms,
        )

    # ---- UI-facing ----------------------------------------------------------

    @app.get("/api/requests", dependencies=auth)
    async def list_requests(
        status_: RequestStatus | None = Query(None, alias="status"),
        server: str | None = None,
        tool: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> list[ToolRequest]:
        return db.list_requests(status_, server, tool, limit, offset)

    @app.get("/api/requests/{request_id}", dependencies=auth)
    async def get_request(request_id: str) -> ToolRequest:
        return lookup(request_id)

    @app.post("/api/requests/{request_id}/decision", dependencies=auth)
    async def decide(request_id: str, body: DecisionIn) -> ToolRequest:
        req = lookup(request_id)
        if req.status != RequestStatus.PENDING:
            raise HTTPException(status.HTTP_409_CONFLICT, f"request is already {req.status.value}")
        return manager.decide(
            request_id, body.decision == "allow", remember=body.remember, reason=body.reason
        )

    @app.get("/api/stats", dependencies=auth)
    async def stats() -> dict[str, int]:
        return db.stats()

    @app.get("/api/rules", dependencies=auth)
    async def list_rules() -> list[PolicyRule]:
        return db.list_rules()

    @app.post("/api/rules", dependencies=auth, status_code=201)
    async def add_rule(body: RuleIn) -> PolicyRule:
        rule = db.add_rule(PolicyRule(**body.model_dump()))
        events.publish("rule.created", rule.model_dump(mode="json"))
        return rule

    @app.delete("/api/rules/{rule_id}", dependencies=auth, status_code=204)
    async def delete_rule(rule_id: int) -> None:
        if not db.delete_rule(rule_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
        events.publish("rule.deleted", {"id": rule_id})

    @app.get("/api/settings", dependencies=auth)
    async def get_settings() -> Settings:
        return settings

    @app.patch("/api/settings", dependencies=auth)
    async def patch_settings(body: SettingsPatch) -> Settings:
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(settings, key, value)
        if persist_settings:
            save_settings(settings)
        events.publish("settings.updated", settings.model_dump())
        return settings

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, token_: str = Query("", alias="token")) -> None:
        # Browsers cannot set headers on WebSocket requests, so the token is a query param.
        if not secrets.compare_digest(token_, token):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        queue = events.subscribe()
        try:
            await websocket.send_json(
                {
                    "event": "snapshot",
                    "data": {
                        "pending": [r.model_dump(mode="json") for r in manager.pending()],
                        "stats": db.stats(),
                    },
                }
            )
            receiver = asyncio.create_task(_drain(websocket))
            while not receiver.done():
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    await websocket.send_json(getter.result())
                else:
                    getter.cancel()
        except WebSocketDisconnect:
            pass
        finally:
            events.unsubscribe(queue)

    return app


async def _drain(websocket: WebSocket) -> None:
    """Consume (and ignore) client messages; returns when the socket closes."""
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return
