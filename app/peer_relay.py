"""
In-memory (per-process) message routing table for the two-way peer
counselor relay demo (Build Plan Phase 3). Maps the Telegram message_id of
a notification sent to the counselor's chat -> which student chat_id /
session_id it belongs to, so a REPLY to that notification can be forwarded
back to the right student.

Deliberately in-memory, NOT persisted to SQLite:
  - This is demo-only "Wizard of Oz" plumbing (spec Section 8), not a
    permanent product feature -- resetting on restart is acceptable.
  - The DB schema is locked at exactly 3 tables (sessions, responses,
    results) as a non-negotiable constraint (anonymity guarantee). This
    routing data doesn't belong there.

NOT thread-safe beyond what Flask's default single-process dev server
already gives implicitly. Fine for a hackathon demo; would need a real
store (Redis, a dedicated table) for concurrent production use.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RelayLink:
    student_chat_id: int
    session_id: str


_relay_links: dict[int, RelayLink] = {}


def register(counselor_message_id: int, student_chat_id: int, session_id: str) -> None:
    """Called right after we notify the counselor, with the message_id Telegram gave that notification."""
    _relay_links[counselor_message_id] = RelayLink(student_chat_id=student_chat_id, session_id=session_id)


def resolve(counselor_message_id: int) -> RelayLink | None:
    """Called when the counselor replies to a message -- looks up which student that reply belongs to."""
    return _relay_links.get(counselor_message_id)


def clear() -> None:
    """Test/dev helper -- wipes all links. Not used by the app itself."""
    _relay_links.clear()