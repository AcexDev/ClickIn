"""
Conversation state machine. This is the orchestration layer: it knows how to
walk a session through the 16 questions and, on the last answer, hand off to
scoring.py + escalation.py. It does NOT talk to Telegram directly -- that's
telegram_webhook.py's job. Keeping this layer Telegram-agnostic means it can
be unit tested without any HTTP mocking, and would survive a channel switch
(e.g. WhatsApp later, per spec Section 4).
"""

from dataclasses import dataclass

from app import session_store as store
from app.escalation import EscalationOutcome, route
from app.questions import ALL_QUESTIONS
from app.scoring import score
from app.questions import ALL_QUESTIONS, DEMOGRAPHIC_QUESTIONS
from app import session_store as store


class SessionMismatchError(ValueError):
    """Raised when an incoming answer doesn't match the session's expected current question."""


@dataclass(frozen=True)
class NextQuestion:
    question: dict
    question_number: int   # 1-indexed, for "Question 3 of 16" style prompts
    total_questions: int


@dataclass(frozen=True)
class SessionComplete:
    outcome: EscalationOutcome
    session_id: str


def get_or_start_session(chat_id: int) -> dict:
    """
    Returns the chat's active session, creating one if none exists (or the
    old one expired). Always expires stale sessions first, so an idle
    12h+ session never gets silently reused.
    """
    store.expire_stale_sessions()
    session = store.get_active_session(chat_id)
    if session is None:
        store.create_session(chat_id)
        session = store.get_active_session(chat_id)
    return session


def current_question(session: dict) -> NextQuestion | None:
    """The question this session should be asked right now, or None if it already finished."""
    idx = session["current_question_index"]
    if idx >= len(ALL_QUESTIONS):
        return None
    return NextQuestion(
        question=ALL_QUESTIONS[idx],
        question_number=idx + 1,
        total_questions=len(ALL_QUESTIONS),
    )


def handle_answer(session: dict, question_id: str, value: int) -> NextQuestion | SessionComplete | None:
    """
    Records one answer against `session` and returns what should happen next:
      - NextQuestion: ask the next question
      - SessionComplete: all 16 answered; scoring + escalation already ran
        and the result is already persisted
      - None: this was a losing duplicate/racing tap for the CURRENT
        question -- another request already advanced the session past it.
        The caller should do nothing further (no resend, no re-score).

    Raises SessionMismatchError if question_id doesn't match what this
    session is currently expecting -- e.g. a stale button tap from a
    question the student already answered several steps ago, or an
    out-of-order client bug.

    IMPORTANT: `session` must be freshly fetched immediately before calling
    this -- see original note. The expected_index passed to the store is
    exactly this snapshot's current_question_index, which is what makes the
    race guard work.
    """
    expected = current_question(session)
    if expected is None or expected.question["id"] != question_id:
        raise SessionMismatchError(
            f"Session {session['session_id']} expected a different question than {question_id!r}"
        )

    advanced = store.record_answer(
        session["session_id"], question_id, value, session["current_question_index"]
    )
    if not advanced:
        return None

    next_index = session["current_question_index"] + 1
    if next_index >= len(ALL_QUESTIONS):
        answers = store.get_answers(session["session_id"])
        result = score(answers)
        outcome = route(result)
        store.complete_session(session["session_id"], result)
        return SessionComplete(outcome=outcome, session_id=session["session_id"])

    return NextQuestion(
        question=ALL_QUESTIONS[next_index],
        question_number=next_index + 1,
        total_questions=len(ALL_QUESTIONS),
    )

@dataclass(frozen=True)
class NextDemographicQuestion:
    question: dict
    question_number: int
    total_questions: int


def current_demographic_question(session: dict) -> NextDemographicQuestion | None:
    """
    Demographic questions run before the 16-item battery. Tracked via a
    separate `demo_question_index` on the session row so it doesn't collide
    with `current_question_index` (which stays reserved for the PHQ/GAD flow).
    """
    idx = session.get("demo_question_index", 0)
    if idx >= len(DEMOGRAPHIC_QUESTIONS):
        return None
    return NextDemographicQuestion(
        question=DEMOGRAPHIC_QUESTIONS[idx],
        question_number=idx + 1,
        total_questions=len(DEMOGRAPHIC_QUESTIONS),
    )


def handle_demographic_answer(session: dict, question_id: str, value: str | None) -> NextDemographicQuestion | None:
    """
    value=None means the student tapped "Skip". Records into `demographics`
    only if a value was actually given -- skipped fields just stay NULL.
    Returns the next demographic question, or None once all 3 are done
    (caller should then move on to current_question() for Q1).
    """
    expected = current_demographic_question(session)
    if expected is None or expected.question["id"] != question_id:
        raise SessionMismatchError(
            f"Session {session['session_id']} expected a different demographic question than {question_id!r}"
        )

    if value is not None:
        store.record_demographic_answer(session["session_id"], question_id, value)

    store.advance_demographic_index(session["session_id"])
    session["demo_question_index"] = session.get("demo_question_index", 0) + 1

    return current_demographic_question(session)