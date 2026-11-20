"""The desktop UI's live channel: snapshot on connect, then events."""

from fastapi.testclient import TestClient

from mcp_guard.api import create_app
from mcp_guard.config import Settings
from mcp_guard.db import Database

TOKEN = "ws-token"


def test_websocket_snapshot_and_events(tmp_path):
    app = create_app(Settings(), Database(tmp_path / "t.db"), TOKEN, persist_settings=False)
    with TestClient(app) as client:
        h = {"Authorization": f"Bearer {TOKEN}"}
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            snap = ws.receive_json()
            assert snap["event"] == "snapshot" and snap["data"]["pending"] == []

            req = client.post("/api/requests", headers=h, json={"server": "fs", "tool": "write_file"}).json()
            created = ws.receive_json()
            assert created["event"] == "request.created"
            assert created["data"]["request_id"] == req["request_id"]

            client.post(f"/api/requests/{req['request_id']}/decision", headers=h, json={"decision": "allow"})
            decided = ws.receive_json()
            assert decided["event"] == "request.decided" and decided["data"]["status"] == "APPROVED"


def test_websocket_rejects_bad_token(tmp_path):
    app = create_app(Settings(), Database(tmp_path / "t.db"), TOKEN, persist_settings=False)
    with TestClient(app) as client:
        try:
            with client.websocket_connect("/ws?token=nope") as ws:
                ws.receive_json()
            raise AssertionError("expected close")
        except Exception as exc:
            assert "4401" in repr(exc) or "WebSocketDisconnect" in type(exc).__name__
