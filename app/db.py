"""
SQLite schema. Exactly 3 tables: sessions, responses, results. No users table
— that omission is the anonymity guarantee.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "wellness.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    chat_id         INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'expired')),
    current_question_index INTEGER NOT NULL DEFAULT 0,
    demo_question_index INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS responses (
    response_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    question_id     TEXT NOT NULL,
    answer_value    INTEGER NOT NULL CHECK (answer_value BETWEEN 0 AND 3),
    answered_at     TEXT NOT NULL,
    UNIQUE (session_id, question_id)
);

CREATE TABLE IF NOT EXISTS results (
    session_id          TEXT PRIMARY KEY REFERENCES sessions(session_id),
    phq9_total           INTEGER NOT NULL,
    gad7_total            INTEGER NOT NULL,
    phq9_band             TEXT NOT NULL,
    gad7_band             TEXT NOT NULL,
    self_harm_override   INTEGER NOT NULL CHECK (self_harm_override IN (0, 1)),
    severity              TEXT NOT NULL CHECK (severity IN ('minimal_mild', 'moderate', 'severe')),
    scored_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS demographics (
    session_id      TEXT PRIMARY KEY REFERENCES sessions(session_id),
    faculty         TEXT,
    gender          TEXT,
    hall_of_residence TEXT
);


CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_results_severity ON results(severity);
CREATE INDEX IF NOT EXISTS idx_demographics_faculty ON demographics(faculty);
CREATE INDEX IF NOT EXISTS idx_demographics_gender ON demographics(gender);
CREATE INDEX IF NOT EXISTS idx_demographics_hall ON demographics(hall_of_residence);
"""


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_connection(db_path: Path = DEFAULT_DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DEFAULT_DB_PATH}")