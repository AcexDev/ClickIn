"""
Escalation router — decides which of the 3 fixed outcome messages to send.

Per spec (Section 9): all three messages are pre-written constants. Nothing
here is AI-generated; this module picks from a small, fixed set of templates
and never generates new text.

The "no-filtering rule" (Section 9): every session that scores SEVERE gets
the real SURPIN message. No exceptions, no "are you sure" gate, no quiet
downgrade. This module has no branch that can suppress or alter the severe
message — that is intentional and should stay that way.
"""

from dataclasses import dataclass

from app.scoring import ScoringResult, Severity

SURPIN_URL = "https://www.surpinng.com/"
SURPIN_MTN_NUMBER = "+2349163114032"
SURPIN_9MOBILE_NUMBER = "+2349080217555"
SURPIN_HAUSA_NUMBER = "+2348142241007"

TIER_1_MINIMAL_MILD = (
    "Thanks for checking in. Based on your answers, you're not showing signs of "
    "serious distress right now — but everyone has rough patches. Here are a few "
    "things that can help: getting enough sleep, talking to a friend, or checking "
    "out campus resources. You can check in again anytime."
)

TIER_2_MODERATE = (
    "It sounds like you've been carrying a lot lately. We'd like to connect you "
    "with a peer counselor who can talk this through with you. Hang tight — "
    "someone will be with you shortly."
)

TIER_3_SEVERE = (
    "What you've shared matters, and you don't have to handle this alone. "
    "Please reach out now — this is a real team, available right now, and "
    "confidential:\n\n"
    "📞 SURPIN: {phone}\n\n"
    "Tap the number above to call."
).format(phone=SURPIN_MTN_NUMBER)


@dataclass(frozen=True)
class EscalationOutcome:
    tier: Severity
    message: str
    tel_link: str | None       # tel: URI, kept for reference/dashboard use -- NOT used as a Telegram button URL (see telegram_webhook.py's send_outcome)
    triggers_peer_relay: bool  # only true for moderate tier


def route(result: ScoringResult) -> EscalationOutcome:
    """
    Pure function: ScoringResult -> EscalationOutcome.

    No DB, no network, no Telegram calls happen here — this only decides
    *what* should be sent. The webhook handler / relay code is responsible
    for actually sending it.
    """
    if result.severity == Severity.SEVERE:
        return EscalationOutcome(
            tier=Severity.SEVERE,
            message=TIER_3_SEVERE,
            tel_link=f"tel:{SURPIN_MTN_NUMBER}",
            triggers_peer_relay=False,
        )
    if result.severity == Severity.MODERATE:
        return EscalationOutcome(
            tier=Severity.MODERATE,
            message=TIER_2_MODERATE,
            tel_link=None,
            triggers_peer_relay=True,
        )
    return EscalationOutcome(
        tier=Severity.MINIMAL_MILD,
        message=TIER_1_MINIMAL_MILD,
        tel_link=None,
        triggers_peer_relay=False,
    )