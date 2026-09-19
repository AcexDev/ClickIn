"""
The 16-question battery: PHQ-9 (depression, questions 1-9) + GAD-7 (anxiety, questions 10-16).

Each question is answered on the same 0-3 scale:
  0 = Not at all
  1 = Several days
  2 = More than half the days
  3 = Nearly every day

IMPORTANT: PHQ-9 question 9 (index 8 in PHQ9_QUESTIONS, id "phq9_9") is the
self-harm screening question. Its answer is handled specially by the scoring
engine (see scoring.py) — any non-zero answer triggers an automatic severe
override regardless of total score. Do not reorder or renumber it without
updating scoring.py's Q9 lookup.
"""

ANSWER_SCALE = [
    {"value": 0, "label": "Not at all"},
    {"value": 1, "label": "Several days"},
    {"value": 2, "label": "More than half the days"},
    {"value": 3, "label": "Nearly every day"},
]

PHQ9_QUESTIONS = [
    {"id": "phq9_1", "instrument": "PHQ9", "index": 1,
     "text": "Little interest or pleasure in doing things"},
    {"id": "phq9_2", "instrument": "PHQ9", "index": 2,
     "text": "Feeling down, depressed, or hopeless"},
    {"id": "phq9_3", "instrument": "PHQ9", "index": 3,
     "text": "Trouble falling or staying asleep, or sleeping too much"},
    {"id": "phq9_4", "instrument": "PHQ9", "index": 4,
     "text": "Feeling tired or having little energy"},
    {"id": "phq9_5", "instrument": "PHQ9", "index": 5,
     "text": "Poor appetite or overeating"},
    {"id": "phq9_6", "instrument": "PHQ9", "index": 6,
     "text": "Feeling bad about yourself — or that you are a failure, or have let yourself or your family down"},
    {"id": "phq9_7", "instrument": "PHQ9", "index": 7,
     "text": "Trouble concentrating on things, such as schoolwork or reading"},
    {"id": "phq9_8", "instrument": "PHQ9", "index": 8,
     "text": "Moving or speaking so slowly that other people could have noticed, or the opposite — being so fidgety or restless that you have been moving around a lot more than usual"},
    {"id": "phq9_9", "instrument": "PHQ9", "index": 9,
     "text": "Thoughts that you would be better off dead, or of hurting yourself in some way",
     "is_self_harm_override_question": True},
]

GAD7_QUESTIONS = [
    {"id": "gad7_1", "instrument": "GAD7", "index": 1,
     "text": "Feeling nervous, anxious, or on edge"},
    {"id": "gad7_2", "instrument": "GAD7", "index": 2,
     "text": "Not being able to stop or control worrying"},
    {"id": "gad7_3", "instrument": "GAD7", "index": 3,
     "text": "Worrying too much about different things"},
    {"id": "gad7_4", "instrument": "GAD7", "index": 4,
     "text": "Trouble relaxing"},
    {"id": "gad7_5", "instrument": "GAD7", "index": 5,
     "text": "Being so restless that it is hard to sit still"},
    {"id": "gad7_6", "instrument": "GAD7", "index": 6,
     "text": "Becoming easily annoyed or irritable"},
    {"id": "gad7_7", "instrument": "GAD7", "index": 7,
     "text": "Feeling afraid, as if something awful might happen"},
]

ALL_QUESTIONS = PHQ9_QUESTIONS + GAD7_QUESTIONS  # 16 total, order = presentation order

SELF_HARM_OVERRIDE_QUESTION_ID = next(
    q["id"] for q in ALL_QUESTIONS if q.get("is_self_harm_override_question")
)

assert len(ALL_QUESTIONS) == 16, "Question battery must be exactly 16 questions"
assert len(PHQ9_QUESTIONS) == 9, "PHQ-9 must be exactly 9 questions"
assert len(GAD7_QUESTIONS) == 7, "GAD-7 must be exactly 7 questions"