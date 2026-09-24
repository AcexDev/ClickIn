# Ran Once
from app.db import get_connection

# with get_connection() as conn:
#     conn.execute("ALTER TABLE results ADD COLUMN gad7_band TEXT")

with get_connection() as conn:
    conn.execute(
        "ALTER TABLE sessions ADD COLUMN demo_question_index INTEGER NOT NULL DEFAULT 0"
    )