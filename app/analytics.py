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
from app.questions import QUESTION_ID_TO_SHORT_LABEL  


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

MIN_COHORT_SIZE = 15  


def _suppress(n: int, value):
    return None if n < MIN_COHORT_SIZE else value


def get_item_level_breakdown() -> list[dict]:
    """
    Per-question aggregate: what % of respondents scored >=2 ("more than half
    the days" or worse) on each PHQ-9/GAD-7 item. No demographic axis here --
    this is the whole-population symptom heatmap.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT question_id,
                      COUNT(*) as n,
                      SUM(CASE WHEN answer_value >= 2 THEN 1 ELSE 0 END) as elevated
               FROM responses
               GROUP BY question_id"""
        ).fetchall()
    return [
            {
                "symptom": QUESTION_ID_TO_SHORT_LABEL[r["question_id"]],
                "respondents": r["n"],
                "elevated_percent": round(r["elevated"] / r["n"] * 100, 1) if r["n"] else 0.0,
            }
            for r in rows
        ]


def get_demographic_breakdown(axis: str) -> list[dict]:
    """
    axis: 'faculty', 'gender', or 'hall_of_residence'. Single-axis only --
    do not extend this to accept a second grouping column (see working notes:
    no cross-matching demographic variables).
    """
    if axis not in {"faculty", "gender", "hall_of_residence"}:
        raise ValueError(f"Unsupported axis: {axis}")

    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT d.{axis} as group_value, r.severity, COUNT(*) as n
                FROM results r
                JOIN demographics d ON d.session_id = r.session_id
                WHERE d.{axis} IS NOT NULL
                GROUP BY d.{axis}, r.severity"""
        ).fetchall()

    grouped: dict[str, dict] = {}
    for row in rows:
        g = grouped.setdefault(row["group_value"], {"minimal_mild": 0, "moderate": 0, "severe": 0})
        g[row["severity"]] = row["n"]

    out = []
    for group_value, counts in grouped.items():
        total = sum(counts.values())
        out.append({
            "group": group_value,
            "respondents": total,
            "severity_percent": _suppress(total, {
                k: round(v / total * 100, 1) for k, v in counts.items()
            }),
        })
    return out


def get_comorbidity_matrix() -> dict:
    """Depression band x anxiety band, whole population (not a demographic axis)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT phq9_band, gad7_band, COUNT(*) as n FROM results GROUP BY phq9_band, gad7_band"
        ).fetchall()
    return {f"{r['phq9_band']}__{r['gad7_band']}": r["n"] for r in rows}

import math

SPEED_RUN_THRESHOLD_SECONDS = 60  # 19 questions (3 demo + 16 battery) answered faster than this is flagged


def get_correlation_stats() -> dict | None:
    """
    Pearson correlation between phq9_total and gad7_total across all
    completed sessions. Whole-population, not a demographic axis -- but
    still suppressed below MIN_COHORT_SIZE, since a correlation computed
    from a handful of points is statistically meaningless, not just an
    anonymity risk.
    """
    with get_connection() as conn:
        rows = conn.execute("SELECT phq9_total, gad7_total FROM results").fetchall()

    n = len(rows)
    if n < MIN_COHORT_SIZE:
        return None

    xs = [r["phq9_total"] for r in rows]
    ys = [r["gad7_total"] for r in rows]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)

    denom = math.sqrt(var_x * var_y)
    r = round(cov / denom, 3) if denom else 0.0

    return {"pearson_r": r, "n": n}


def get_response_quality_stats() -> dict:
    """
    Aggregate-only signal on response quality:
      - speed_run: completed sessions answered suspiciously fast
      - incomplete: in_progress/expired as a share of all sessions started
      - repeat_chat_ids: how many DISTINCT chat_ids started more than one
        session -- the count itself is exposed, never which chat_id.
    """
    with get_connection() as conn:
        status_rows = conn.execute("SELECT status, COUNT(*) as n FROM sessions GROUP BY status").fetchall()
        status_counts = {row["status"]: row["n"] for row in status_rows}
        total_sessions = sum(status_counts.values())

        duration_rows = conn.execute(
            """SELECT s.session_id,
                      (julianday(s.updated_at) - julianday(s.created_at)) * 86400 as duration_seconds
               FROM sessions s
               JOIN results r ON r.session_id = s.session_id"""
        ).fetchall()

        repeat_row = conn.execute(
            """SELECT COUNT(*) as n FROM (
                 SELECT chat_id FROM sessions GROUP BY chat_id HAVING COUNT(*) > 1
               )"""
        ).fetchone()
        distinct_chat_ids_row = conn.execute("SELECT COUNT(DISTINCT chat_id) as n FROM sessions").fetchone()

    completed_n = len(duration_rows)
    speed_run_n = sum(1 for r in duration_rows if r["duration_seconds"] < SPEED_RUN_THRESHOLD_SECONDS)

    incomplete_n = status_counts.get("in_progress", 0) + status_counts.get("expired", 0)

    return {
        "total_sessions": total_sessions,
        "speed_run_count": speed_run_n,
        "speed_run_percent_of_completed": round(speed_run_n / completed_n * 100, 1) if completed_n else 0.0,
        "incomplete_count": incomplete_n,
        "incomplete_percent": round(incomplete_n / total_sessions * 100, 1) if total_sessions else 0.0,
        "distinct_chat_ids": distinct_chat_ids_row["n"],
        "chat_ids_with_multiple_sessions": repeat_row["n"],
    }

def get_symptom_breakdown_by_axis(axis: str) -> list[dict]:
    """
    Per-question elevated-answer percentage, split by one demographic axis
    at a time. e.g. axis='hall_of_residence' -> for each hall, what % of
    respondents scored >=2 on each of the 16 PHQ-9/GAD-7 items.

    Still single-axis (see working notes: no cross-matching demographic
    variables) -- this crosses ONE demographic dimension with the symptom
    items, never two demographic dimensions with each other.

    Suppressed per-group, same as get_demographic_breakdown: if a group's
    respondent count is below MIN_COHORT_SIZE, its whole item breakdown
    comes back None rather than 16 individually-small numbers.
    """
    if axis not in {"faculty", "gender", "hall_of_residence"}:
        raise ValueError(f"Unsupported axis: {axis}")

    with get_connection() as conn:
        totals_rows = conn.execute(
            f"""SELECT d.{axis} as group_value, COUNT(*) as n
                FROM results r
                JOIN demographics d ON d.session_id = r.session_id
                WHERE d.{axis} IS NOT NULL
                GROUP BY d.{axis}"""
        ).fetchall()
        totals = {row["group_value"]: row["n"] for row in totals_rows}

        rows = conn.execute(
            f"""SELECT d.{axis} as group_value, resp.question_id,
                       COUNT(*) as n,
                       SUM(CASE WHEN resp.answer_value >= 2 THEN 1 ELSE 0 END) as elevated
                FROM responses resp
                JOIN demographics d ON d.session_id = resp.session_id
                JOIN results r ON r.session_id = resp.session_id
                WHERE d.{axis} IS NOT NULL
                GROUP BY d.{axis}, resp.question_id"""
        ).fetchall()

    by_group: dict[str, dict] = {}
    for row in rows:
        by_group.setdefault(row["group_value"], {})[QUESTION_ID_TO_SHORT_LABEL[row["question_id"]]] = round(
            row["elevated"] / row["n"] * 100, 1
        ) if row["n"] else 0.0

    out = []
    for group_value, items in by_group.items():
        n = totals.get(group_value, 0)
        out.append({
            "group": group_value,
            "respondents": n,
            "item_elevated_percent": _suppress(n, items),
        })
    return out