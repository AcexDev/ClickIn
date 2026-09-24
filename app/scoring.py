"""
Scoring engine — pure functions only. No database, no network, no I/O.
"""

from dataclasses import dataclass
from enum import Enum

from app.questions import ALL_QUESTIONS, PHQ9_QUESTIONS, GAD7_QUESTIONS, SELF_HARM_OVERRIDE_QUESTION_ID

MIN_ANSWER = 0
MAX_ANSWER = 3
EXPECTED_QUESTION_COUNT = 16


class Severity(str, Enum):
    MINIMAL_MILD = "minimal_mild"
    MODERATE = "moderate"
    SEVERE = "severe"


@dataclass(frozen=True)
class ScoringResult:
    phq9_total: int
    gad7_total: int
    phq9_band: str
    gad7_band: str          # <-- new field
    self_harm_override: bool
    severity: Severity


class InvalidAnswersError(ValueError):
    pass


def _validate_answers(answers: dict) -> None:
    if set(answers.keys()) != {q["id"] for q in ALL_QUESTIONS}:
        missing = {q["id"] for q in ALL_QUESTIONS} - set(answers.keys())
        extra = set(answers.keys()) - {q["id"] for q in ALL_QUESTIONS}
        raise InvalidAnswersError(
            f"Answers must cover exactly the 16 question ids. Missing={missing} Extra={extra}"
        )
    for qid, value in answers.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidAnswersError(f"Answer for {qid} must be an int, got {type(value)}")
        if value < MIN_ANSWER or value > MAX_ANSWER:
            raise InvalidAnswersError(
                f"Answer for {qid} must be between {MIN_ANSWER} and {MAX_ANSWER}, got {value}"
            )


def check_self_harm_override(answers: dict) -> bool:
    """
    THE ONE PIECE OF LOGIC THAT MUST NEVER BREAK.
    Returns True if the student answered anything other than 0 on Q9.
    """
    return answers[SELF_HARM_OVERRIDE_QUESTION_ID] > 0


def _phq9_band(total: int) -> str:
    if total <= 4:
        return "Minimal"
    if total <= 9:
        return "Mild"
    if total <= 14:
        return "Moderate"
    if total <= 19:
        return "Moderately Severe"
    return "Severe"


def _gad7_band(total: int) -> str:
    if total <= 4:
        return "Minimal"
    if total <= 9:
        return "Mild"
    if total <= 14:
        return "Moderate"
    return "Severe"


_MODERATE_BANDS = {"Moderate", "Moderately Severe"}
_SEVERE_BANDS = {"Severe"}


def score(answers: dict) -> ScoringResult:
    _validate_answers(answers)

    phq9_total = sum(answers[q["id"]] for q in PHQ9_QUESTIONS)
    gad7_total = sum(answers[q["id"]] for q in GAD7_QUESTIONS)
    phq9_band = _phq9_band(phq9_total)
    gad7_band = _gad7_band(gad7_total)

    self_harm_override = check_self_harm_override(answers)

    if self_harm_override:
        severity = Severity.SEVERE
    elif phq9_band in _SEVERE_BANDS or gad7_band in _SEVERE_BANDS:
        severity = Severity.SEVERE
    elif phq9_band in _MODERATE_BANDS or gad7_band in _MODERATE_BANDS:
        severity = Severity.MODERATE
    else:
        severity = Severity.MINIMAL_MILD

    return ScoringResult(
        phq9_total=phq9_total,
        gad7_total=gad7_total,
        phq9_band=phq9_band,
        gad7_band=gad7_band,    # <-- new line
        self_harm_override=self_harm_override,
        severity=severity,
    )