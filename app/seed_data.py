"""
app/seed_data.py

Populates the local SQLite DB with synthetic completed sessions for testing
analytics.py's aggregate queries -- including the demographic axes and the
suppression threshold (MIN_COHORT_SIZE). Every value here is synthetic;
nothing routes through Telegram, nothing is real student data.

DEV/TEST ONLY. Don't run this against a database that has real responses in
it -- it can't distinguish seeded rows from real ones, so it would silently
corrupt your actual aggregate stats. Start from a clean DB:
    rm data/wellness.db
    python -m app.db
    python -m app.seed_data

Deliberately uneven demographic weights, so faculty/hall breakdowns land on
BOTH sides of the suppression floor -- otherwise you'd never actually see
_suppress() in analytics.py do anything.
"""

import random

from app.db import init_db
from app import session_store as store
from app.questions import ALL_QUESTIONS, PHQ9_QUESTIONS, GAD7_QUESTIONS, SELF_HARM_OVERRIDE_QUESTION_ID
from app.scoring import score

random.seed(42)  # reproducible runs -- remove for fresh randomness each time

FACULTIES = ["Computing", "Science", "Engineering", "Arts", "Social Sciences",
             "Law", "Vet Medicine", "Clinical Sciences", "Agriculture", "Other"]
GENDERS = ["Male", "Female", "Prefer not to say"]
HALLS = ["Sultan Bello", "Tedder", "Queen Idia", "Queen Elizabeth II",
         "Independence", "Mellanby", "Kuti", "Nnamdi Azikwe", "Obafemi Awolowo",
         "Off-campus", "Other"]

# Skewed on purpose: a few faculties/halls stay comfortably above
# MIN_COHORT_SIZE=15, a few sit right around it, a few stay well below --
# gives you all three suppression outcomes to check against.
FACULTY_WEIGHTS = [30, 25, 20, 8, 8, 3, 2, 2, 1, 1]
HALL_WEIGHTS =    [15, 12, 10, 10, 8, 8, 6, 5, 4, 15, 7]
GENDER_WEIGHTS =  [45, 45, 10]


def _weighted_choice(options, weights, skip_rate: float):
    if random.random() < skip_rate:
        return None
    return random.choices(options, weights=weights, k=1)[0]


def _answers_for_band(question_ids: list[str], target_total: int) -> dict:
    """Spreads target_total across the given questions, each capped 0-3,
    with variance per question rather than a flat identical score --
    so get_item_level_breakdown shows realistic per-symptom differences."""
    n = len(question_ids)
    target_total = max(0, min(target_total, n * 3))

    values = [0] * n
    remaining = target_total
    indices = list(range(n))
    random.shuffle(indices)
    for i in indices:
        if remaining <= 0:
            break
        take = random.randint(0, min(3, remaining))
        values[i] = take
        remaining -= take
    for i in range(n):
        while remaining > 0 and values[i] < 3:
            values[i] += 1
            remaining -= 1

    return dict(zip(question_ids, values))


def _generate_session_answers(severity_target: str) -> dict:
    """
    severity_target:
      'minimal_mild' / 'moderate' / 'severe_score' -- hit severity via totals
      'severe_q9' -- low totals everywhere, severity forced purely via the
                     Q9 override, so the override path gets real coverage
                     independent of the scoring-band path
    """
    phq9_ids = [q["id"] for q in PHQ9_QUESTIONS]
    gad7_ids = [q["id"] for q in GAD7_QUESTIONS]

    bands = {
        "minimal_mild": (range(0, 10), range(0, 10)),
        "moderate":      (range(10, 20), range(5, 15)),
        "severe_score":  (range(20, 28), range(15, 22)),
        "severe_q9":     (range(0, 9), range(0, 9)),
    }
    if severity_target not in bands:
        raise ValueError(f"Unknown severity_target: {severity_target}")
    phq9_range, gad7_range = bands[severity_target]

    phq9_answers = _answers_for_band(phq9_ids, random.choice(phq9_range))
    gad7_answers = _answers_for_band(gad7_ids, random.choice(gad7_range))

    if severity_target == "severe_q9":
        phq9_answers[SELF_HARM_OVERRIDE_QUESTION_ID] = random.choice([1, 2, 3])
    else:
        phq9_answers[SELF_HARM_OVERRIDE_QUESTION_ID] = 0  # keep override off outside its own bucket

    answers = {**phq9_answers, **gad7_answers}
    assert set(answers.keys()) == {q["id"] for q in ALL_QUESTIONS}
    return answers


def _seed_one_session(chat_id: int, severity_target: str, skip_demo: bool) -> None:
    session_id = store.create_session(chat_id)

    faculty = _weighted_choice(FACULTIES, FACULTY_WEIGHTS, skip_rate=1.0 if skip_demo else 0.1)
    gender = _weighted_choice(GENDERS, GENDER_WEIGHTS, skip_rate=1.0 if skip_demo else 0.1)
    hall = _weighted_choice(HALLS, HALL_WEIGHTS, skip_rate=1.0 if skip_demo else 0.15)
    store.save_demographics(session_id, faculty, gender, hall)

    answers = _generate_session_answers(severity_target)
    for idx, q in enumerate(ALL_QUESTIONS):
        advanced = store.record_answer(session_id, q["id"], answers[q["id"]], idx)
        assert advanced, f"Seed script race-guard mismatch on {q['id']} -- shouldn't happen single-threaded"

    result = score(answers)
    store.complete_session(session_id, result)


def seed(n_sessions: int = 300) -> None:
    """Rough real-world-ish shape: most respondents minimal/mild, moderate
    the middle band, severe (both score-based and Q9-based) a smaller but
    non-trivial slice -- enough rows per bucket that severity_percent isn't
    noisy from n=2."""
    buckets = (
        ["minimal_mild"] * int(n_sessions * 0.55)
        + ["moderate"] * int(n_sessions * 0.25)
        + ["severe_score"] * int(n_sessions * 0.12)
        + ["severe_q9"] * int(n_sessions * 0.08)
    )
    random.shuffle(buckets)

    chat_id_start = 900_000_000  # well clear of any real chat_id you've used while testing

    for i, severity_target in enumerate(buckets):
        skip_demo = random.random() < 0.05  # a slice fully anonymous even on demographics
        _seed_one_session(chat_id_start + i, severity_target, skip_demo)

    print(f"Seeded {len(buckets)} completed sessions.")
    print("Bucket counts:", {b: buckets.count(b) for b in set(buckets)})


if __name__ == "__main__":
    init_db()
    seed()