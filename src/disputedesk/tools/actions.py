"""Append-only log of actions the system took (refunds and escalations), in SQLite.

DuckDB allows one writer, and the tool service must keep the main database read-only, so writes go here.
A (case, kind) pair can be recorded once: repeating the call returns the first result, so a retried
request can never pay twice. Triggers block UPDATE and DELETE.
"""
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
  action_id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('refund', 'escalation')),
  amount_usd REAL,
  policy_ids TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (case_id, kind));
CREATE TRIGGER IF NOT EXISTS actions_no_update BEFORE UPDATE ON actions
  BEGIN SELECT RAISE(ABORT, 'actions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS actions_no_delete BEFORE DELETE ON actions
  BEGIN SELECT RAISE(ABORT, 'actions is append-only'); END;
"""


class Conflict(Exception):
    """The case already has this action, recorded with a different amount."""


def record(path: str, case_id: str, kind: str, amount_usd: float | None, policy_ids: list[str],
           reason: str) -> tuple[dict, bool]:
    """Record an action once. Returns (row, duplicate)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    try:
        con.executescript(SCHEMA)
        con.execute("PRAGMA journal_mode=WAL")
        with con:
            cur = con.execute(
                """INSERT INTO actions (case_id, kind, amount_usd, policy_ids, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (case_id, kind) DO NOTHING""",
                (case_id, kind, amount_usd, ",".join(policy_ids), reason, datetime.now(UTC).replace(tzinfo=None).isoformat()))
        duplicate = cur.rowcount == 0
        r = con.execute("SELECT action_id, case_id, kind, amount_usd, policy_ids, reason, created_at "
                        "FROM actions WHERE case_id = ? AND kind = ?", (case_id, kind)).fetchone()
    finally:
        con.close()
    if duplicate and r[3] != amount_usd:
        raise Conflict(f"{kind} for case {case_id} already recorded with amount {r[3]}")
    return dict(action_id=r[0], case_id=r[1], kind=r[2], amount_usd=r[3], policy_ids=[p for p in r[4].split(",") if p],
                reason=r[5], created_at=r[6]), duplicate
