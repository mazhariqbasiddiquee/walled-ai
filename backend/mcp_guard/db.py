"""SQLite storage for the audit log and policy rules.

Writes are tiny and local, so a single connection guarded by a lock is plenty
for the MVP. Every lifecycle change of a request updates its row, so the
``requests`` table *is* the audit log.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import PolicyRule, RequestStatus, ToolRequest

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    request_id       TEXT PRIMARY KEY,
    server           TEXT NOT NULL,
    tool             TEXT NOT NULL,
    arguments        TEXT NOT NULL,
    status           TEXT NOT NULL,
    decided_by       TEXT,
    decision_reason  TEXT,
    created_at       TEXT NOT NULL,
    decided_at       TEXT,
    expires_at       TEXT,
    execution_status TEXT,
    result_preview   TEXT,
    error            TEXT,
    duration_ms      INTEGER,
    completed_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_created ON requests(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);

CREATE TABLE IF NOT EXISTS rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    server     TEXT NOT NULL,
    tool       TEXT NOT NULL,
    action     TEXT NOT NULL,
    note       TEXT,
    created_at TEXT NOT NULL
);
"""

_REQUEST_COLUMNS = list(ToolRequest.model_fields)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class Database:
    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- requests -----------------------------------------------------------

    def save_request(self, req: ToolRequest) -> None:
        row = req.model_dump()
        row["arguments"] = json.dumps(req.arguments, default=str)
        row["status"] = req.status.value
        for key in ("created_at", "decided_at", "expires_at", "completed_at"):
            row[key] = _iso(row[key])
        cols = ", ".join(_REQUEST_COLUMNS)
        marks = ", ".join(f":{c}" for c in _REQUEST_COLUMNS)
        with self._lock, self._conn:
            self._conn.execute(f"INSERT OR REPLACE INTO requests ({cols}) VALUES ({marks})", row)

    def get_request(self, request_id: str) -> ToolRequest | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        return self._to_request(row) if row else None

    def list_requests(
        self,
        status: RequestStatus | None = None,
        server: str | None = None,
        tool: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ToolRequest]:
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status.value)
        if server:
            where.append("server = ?")
            params.append(server)
        if tool:
            where.append("tool = ?")
            params.append(tool)
        sql = "SELECT * FROM requests"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._to_request(r) for r in rows]

    def stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM requests GROUP BY status"
            ).fetchall()
        counts = {s.value: 0 for s in RequestStatus}
        counts.update({r["status"]: r["n"] for r in rows})
        counts["TOTAL"] = sum(counts.values())
        return counts

    def expire_stale_pending(self, now: datetime) -> int:
        """Mark PENDING rows left over from a previous run as EXPIRED."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE requests SET status = ?, decided_by = 'system', "
                "decision_reason = 'gateway restarted', decided_at = ? WHERE status = ?",
                (RequestStatus.EXPIRED.value, now.isoformat(), RequestStatus.PENDING.value),
            )
            return cur.rowcount

    @staticmethod
    def _to_request(row: sqlite3.Row) -> ToolRequest:
        data: dict[str, Any] = dict(row)
        data["arguments"] = json.loads(data["arguments"])
        return ToolRequest.model_validate(data)

    # ---- rules --------------------------------------------------------------

    def add_rule(self, rule: PolicyRule) -> PolicyRule:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO rules (server, tool, action, note, created_at) VALUES (?, ?, ?, ?, ?)",
                (rule.server, rule.tool, rule.action, rule.note, rule.created_at.isoformat()),
            )
        return rule.model_copy(update={"id": cur.lastrowid})

    def list_rules(self) -> list[PolicyRule]:
        # Newest first: a fresh "remember" decision overrides older broad rules.
        with self._lock:
            rows = self._conn.execute("SELECT * FROM rules ORDER BY id DESC").fetchall()
        return [PolicyRule.model_validate(dict(r)) for r in rows]

    def delete_rule(self, rule_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        return cur.rowcount > 0
