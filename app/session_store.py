"""
Persistence layer for sessions/responses/results. db.py owns the schema and
connection; this module owns the actual read/write operations conversation.py
needs. Kept separate so db.py stays a pure "what does the schema look like"
file.

TTL rule (spec Section 8): 12-hour idle expiry. expires_at is recomputed on
every write that touches a session (creation, each answer), so the clock
resets on activity rather than counting from session start.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.db import get_connection

SESSION_TTL_HOURS = 12


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expiry_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)).isoformat()


def create_session(chat_id: int) -> str:
    """Creates a new in_progress session for this chat and returns its session_id."""
    session_id = str(uuid.uuid4())
    now = _now_iso()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO sessions
               (session_id, chat_id, status, current_question_index, created_at, updated_at, expires_at)
               VALUES (?, ?, 'in_progress', 0, ?, ?, ?)""",
            (session_id, chat_id, now, now, _expiry_iso()),
        )
    return session_id


def get_active_session(chat_id: int) -> dict | None:
    """
    Returns the row (as a dict) of the active, non-expired, in_progress
    session for this chat, or None if there isn't one. Does NOT expire
    stale sessions itself -- call expire_stale_sessions() separately
    (conversation.py does this on every incoming message).
    """
    now = _now_iso()
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM sessions
               WHERE chat_id = ? AND status = 'in_progress' AND expires_at > ?
               ORDER BY created_at DESC LIMIT 1""",
            (chat_id, now),
        ).fetchone()
        return dict(row) if row else None


def expire_stale_sessions() -> int:
    """
    Marks any in_progress session whose expires_at has passed as 'expired'.
    Returns the number of sessions expired. Cheap enough to call on every
    webhook hit rather than running as a separate cron job for the demo.
    """
    now = _now_iso()
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE sessions SET status = 'expired' WHERE status = 'in_progress' AND expires_at <= ?",
            (now,),
        )
        return cur.rowcount


def record_answer(session_id: str, question_id: str, value: int, expected_index: int) -> bool:
    """
    Records one answer and advances current_question_index from
    expected_index -> expected_index + 1, but ONLY if the session is still
    at expected_index at the moment of the write.

    Returns True if this call performed the advance (it "won").
    Returns False if the session had already moved past expected_index --
    meaning a concurrent or duplicate tap for the same question got there
    first. Callers must treat False as a no-op: don't re-score, don't
    re-send the outcome, don't double-fire the peer relay.

    The INSERT is still an upsert (idempotent on answer_value itself), but
    the index advance is guarded by a WHERE clause on the old value, which
    is what actually prevents double-counting.
    """
    now = _now_iso()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO responses (session_id, question_id, answer_value, answered_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id, question_id)
               DO UPDATE SET answer_value = excluded.answer_value, answered_at = excluded.answered_at""",
            (session_id, question_id, value, now),
        )
        cur = conn.execute(
            """UPDATE sessions
               SET current_question_index = ?, updated_at = ?, expires_at = ?
               WHERE session_id = ? AND current_question_index = ?""",
            (expected_index + 1, now, _expiry_iso(), session_id, expected_index),
        )
        return cur.rowcount == 1


def get_answers(session_id: str) -> dict:
    """Returns {question_id: answer_value} for every answer recorded so far."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT question_id, answer_value FROM responses WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return {row["question_id"]: row["answer_value"] for row in rows}


def complete_session(session_id: str, scoring_result) -> None:
    """Marks a session completed and writes its scoring result. Called once, at the end."""
    now = _now_iso()
    with get_connection() as conn:
        conn.execute(
            "UPDATE sessions SET status = 'completed', updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        conn.execute(
            """INSERT INTO results
               (session_id, phq9_total, gad7_total, phq9_band, self_harm_override, severity, scored_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                scoring_result.phq9_total,
                scoring_result.gad7_total,
                scoring_result.phq9_band,
                int(scoring_result.self_harm_override),
                scoring_result.severity.value,
                now,
            ),
        )