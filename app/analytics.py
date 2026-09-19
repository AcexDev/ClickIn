"""
Aggregate-only analytics queries for the admin dashboard.

CRITICAL ANONYMITY RULE (spec Section 5 & 6): every function in this module
returns AGGREGATE counts/percentages only. None of these functions may ever
return chat_id, session_id, or any row-level/individual data -- that would
break the "no individual student data ever appears" guarantee the admin
dashboard is built on. If you're adding a new query here, ask "could this
identify one student's answers?" before writing it. If the answer is
anything other than a clear no, don't add it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.db import get_connection


@dataclass(frozen=True)
class SeverityBreakdown:
    minimal_mild: int
    moderate: int
    severe: int

    @property
    def total(self) -> int:
        return self.minimal_mild + self.moderate + self.severe

    def percent(self, tier: str) -> float:
        if self.total == 0:
            return 0.0
        return round(getattr(self, tier) / self.total * 100, 1)


@dataclass(frozen=True)
class SummaryStats:
    total_sessions: int
    completed_sessions: int
    in_progress_sessions: int
    expired_sessions: int
    completion_rate: float          # completed / total_sessions, as a percent
    severity: SeverityBreakdown     # counts among COMPLETED sessions only
    self_harm_override_count: int   # sessions where Q9 forced severe -- useful institutional metric (spec Section 6)
    window_label: str               # e.g. "all time", "last 7 days"


def _since_iso(days: int | None) -> str | None:
    if days is None:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def get_summary_stats(days: int | None = None) -> SummaryStats:
    """
    days=None -> all time. days=7 -> sessions created in the last 7 days, etc.
    Every number returned here is a COUNT or PERCENTAGE -- never a list of
    individual sessions, chat_ids, or answers.
    """
    since = _since_iso(days)

    with get_connection() as conn:
        if since:
            status_rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM sessions WHERE created_at >= ? GROUP BY status",
                (since,),
            ).fetchall()
        else:
            status_rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM sessions GROUP BY status"
            ).fetchall()
        status_counts = {row["status"]: row["n"] for row in status_rows}

        total = sum(status_counts.values())
        completed = status_counts.get("completed", 0)
        in_progress = status_counts.get("in_progress", 0)
        expired = status_counts.get("expired", 0)

        severity_query = """
            SELECT r.severity, COUNT(*) as n
            FROM results r
            JOIN sessions s ON s.session_id = r.session_id
            {where}
            GROUP BY r.severity
        """
        override_query = """
            SELECT COUNT(*) as n
            FROM results r
            JOIN sessions s ON s.session_id = r.session_id
            WHERE r.self_harm_override = 1 {and_since}
        """
        if since:
            severity_rows = conn.execute(
                severity_query.format(where="WHERE s.created_at >= ?"), (since,)
            ).fetchall()
            override_row = conn.execute(
                override_query.format(and_since="AND s.created_at >= ?"), (since,)
            ).fetchone()
        else:
            severity_rows = conn.execute(severity_query.format(where="")).fetchall()
            override_row = conn.execute(override_query.format(and_since="")).fetchone()

        severity_counts = {row["severity"]: row["n"] for row in severity_rows}

    severity = SeverityBreakdown(
        minimal_mild=severity_counts.get("minimal_mild", 0),
        moderate=severity_counts.get("moderate", 0),
        severe=severity_counts.get("severe", 0),
    )

    completion_rate = round(completed / total * 100, 1) if total else 0.0

    return SummaryStats(
        total_sessions=total,
        completed_sessions=completed,
        in_progress_sessions=in_progress,
        expired_sessions=expired,
        completion_rate=completion_rate,
        severity=severity,
        self_harm_override_count=override_row["n"],
        window_label="all time" if days is None else f"last {days} days",
    )


def get_daily_completed_counts(days: int = 30) -> list[dict]:
    """
    Returns one dict per day for the last `days` days (oldest first):
      {"date": "2026-09-01", "total": 5, "severe": 1, "moderate": 2, "minimal_mild": 2}
    Days with zero completions are included with all-zero counts (no gaps),
    so a charting library on the frontend doesn't need to backfill missing
    dates itself. This is for the dashboard's trend line (spec Section 6:
    "real-time view of student wellbeing trends").
    """
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).date()

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT date(scored_at) as day, severity, COUNT(*) as n
               FROM results
               WHERE date(scored_at) >= ?
               GROUP BY day, severity
               ORDER BY day""",
            (cutoff_date.isoformat(),),
        ).fetchall()

    by_day: dict[str, dict] = {}
    for i in range(days + 1):
        day = (cutoff_date + timedelta(days=i)).isoformat()
        by_day[day] = {"date": day, "total": 0, "severe": 0, "moderate": 0, "minimal_mild": 0}

    for row in rows:
        day = row["day"]
        if day not in by_day:
            continue  # defensive: a row outside the expected window
        by_day[day][row["severity"]] = row["n"]
        by_day[day]["total"] += row["n"]

    return list(by_day.values())