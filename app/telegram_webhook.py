"""
Telegram webhook handler.

Flow:
  /start (or any message) with no active session -> create session, ask Q1
  Button tap (callback_query) -> record answer, ask next question or,
    on the 16th answer, send the escalation outcome and (if moderate)
    notify the peer-counselor relay chat.

NO PII LOGGING (spec Section 4 & 8): the only Telegram field we ever read,
store, or log is chat_id (a number). We never touch update["message"]["from"]
(which contains username/first_name/last_name), and we never log message
text or answer content.
"""

import logging
import os

import requests
from flask import Flask, request, jsonify

from app import conversation as conv
from app import peer_relay
from app.dashboard_api import dashboard_bp
from app.db import init_db
from app.questions import ANSWER_SCALE
from dotenv import load_dotenv
from flask_cors import CORS

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telegram_webhook")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

print(TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else print("Token not found")
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

_counselor_chat_id_raw = os.getenv("COUNSELOR_RELAY_CHAT_ID", "")
COUNSELOR_RELAY_CHAT_ID = int(_counselor_chat_id_raw) if _counselor_chat_id_raw else None

app = Flask(__name__)
CORS(app)  # dev only -- allows all origins; lock this down before any public deploy
app.register_blueprint(dashboard_bp)
init_db()


WELCOME_MESSAGE = (
    "Hi, thanks for stopping by. 👋\n\n"
    "This is a quick, anonymous check-in — 16 short questions, more like a "
    "quiz than an interrogation. Nothing here is tied to your name, "
    "username, or student ID, and no one sees your answers but you.\n\n"
    "Just tap the option that fits best for each question. Let's get started:"
)


def _post_telegram(method: str, payload: dict) -> dict | None:
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set; skipping %s", method)
        return None
    resp = requests.post(f"{TELEGRAM_API_BASE}/{method}", json=payload, timeout=10)
    if not resp.ok:
        try:
            description = resp.json().get("description", "")
        except ValueError:
            description = ""
        logger.error("%s failed: status=%s description=%s", method, resp.status_code, description)
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def send_question(chat_id: int, next_question: conv.NextQuestion) -> None:
    q = next_question.question
    keyboard = {
        "inline_keyboard": [
            [{"text": opt["label"], "callback_data": f"ans:{q['id']}:{opt['value']}"}]
            for opt in ANSWER_SCALE
        ]
    }
    text = f"Question {next_question.question_number} of {next_question.total_questions}\n\n{q['text']}"
    _post_telegram("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": keyboard,
    })


def send_outcome(chat_id: int, outcome) -> None:
    _post_telegram("sendMessage", {"chat_id": chat_id, "text": outcome.message})


def notify_peer_relay(session_id: str, student_chat_id: int) -> None:
    """
    Sends the counselor a notification and remembers which student it's
    about, so a REPLY to this specific message can be routed back
    automatically (see _handle_counselor_reply). Two-way relay depends on
    capturing the message_id Telegram gives us for this send.
    """
    if COUNSELOR_RELAY_CHAT_ID is None:
        logger.warning("COUNSELOR_RELAY_CHAT_ID not set; skipping peer relay notification")
        return

    result = _post_telegram("sendMessage", {
        "chat_id": COUNSELOR_RELAY_CHAT_ID,
        "text": (
            f"New moderate-severity session needs a peer counselor.\n"
            f"Session: {session_id}\n\n"
            f"Reply directly to THIS message to relay your response to the student."
        ),
    })

    if not result or not result.get("ok"):
        logger.warning("Peer relay notification failed to send; reply routing won't work for this session")
        return

    message_id = result.get("result", {}).get("message_id")
    if message_id is None:
        logger.warning("Peer relay notification sent but no message_id returned; reply routing won't work")
        return

    peer_relay.register(message_id, student_chat_id, session_id)


def answer_callback_query(callback_query_id: str) -> None:
    _post_telegram("answerCallbackQuery", {"callback_query_id": callback_query_id})


def _handle_counselor_reply(message: dict) -> None:
    """
    Handles messages sent in the counselor-relay chat. Only messages that
    are Telegram replies (reply_to_message) to one of our own relay
    notifications get forwarded -- anything else the counselor types in
    that chat is ignored, so they can chat freely without every message
    being relayed to a student.
    """
    reply_to = message.get("reply_to_message")
    if not reply_to:
        return  # not a reply to anything -- nothing to relay

    reply_to_id = reply_to.get("message_id")
    text = message.get("text")
    if reply_to_id is None or not text:
        return

    link = peer_relay.resolve(reply_to_id)
    if link is None:
        _post_telegram("sendMessage", {
            "chat_id": COUNSELOR_RELAY_CHAT_ID,
            "text": "Couldn't match this reply to a student session -- make sure "
                    "you're replying directly to the original session notification.",
        })
        return

    _post_telegram("sendMessage", {"chat_id": link.student_chat_id, "text": text})
    _post_telegram("sendMessage", {"chat_id": COUNSELOR_RELAY_CHAT_ID, "text": "✅ Delivered to student."})


def _handle_message(message: dict) -> None:
    chat_id = message.get("chat", {}).get("id")
    if chat_id is None:
        return

    if COUNSELOR_RELAY_CHAT_ID is not None and chat_id == COUNSELOR_RELAY_CHAT_ID:
        _handle_counselor_reply(message)
        return

    # Checking for the literal "/start" command text is not PII -- it's an
    # instruction, not personal data -- so this doesn't violate the
    # no-PII-logging rule above. We don't log or store the text, just
    # compare it.
    text = (message.get("text") or "").strip().lower()
    if text == "/start":
        _post_telegram("sendMessage", {"chat_id": chat_id, "text": WELCOME_MESSAGE})

    session = conv.get_or_start_session(chat_id)
    if session["status"] != "in_progress":
        return

    next_q = conv.current_question(session)
    if next_q is not None:
        send_question(chat_id, next_q)


def clear_keyboard(chat_id: int, message_id: int) -> None:
    """Strips the inline keyboard off an already-answered question message,
    so a physical double-tap on the old buttons has nothing left to hit."""
    if message_id is None:
        return
    _post_telegram("editMessageReplyMarkup", {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": []},
    })


def _handle_callback_query(callback_query: dict) -> None:
    callback_query_id = callback_query.get("id")
    message = callback_query.get("message", {}) or {}
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    data = callback_query.get("data", "")

    if callback_query_id:
        answer_callback_query(callback_query_id)

    if chat_id is None or not data.startswith("ans:"):
        return

    try:
        _, question_id, value_str = data.split(":", 2)
        value = int(value_str)
    except ValueError:
        logger.warning("Malformed callback_data received; dropping")
        return

    session = conv.get_or_start_session(chat_id)

    try:
        result = conv.handle_answer(session, question_id, value)
    except conv.SessionMismatchError:
        current = conv.current_question(session)
        if current is not None:
            send_question(chat_id, current)
        return

    # Always clear the tapped message's keyboard, whether this tap won the
    # race or was a duplicate -- either way that question is settled now.
    clear_keyboard(chat_id, message_id)

    if result is None:
        # Losing duplicate/racing tap -- the winning tap already advanced
        # the session and sent whatever comes next. Nothing more to do.
        return

    if isinstance(result, conv.NextQuestion):
        send_question(chat_id, result)
    else:
        send_outcome(chat_id, result.outcome)
        if result.outcome.triggers_peer_relay:
            notify_peer_relay(result.session_id, chat_id)

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True) or {}

    if "callback_query" in update:
        _handle_callback_query(update["callback_query"])
    elif "message" in update or "edited_message" in update:
        _handle_message(update.get("message") or update.get("edited_message"))
    else:
        logger.info("Received unhandled update type")

    return jsonify({"ok": True})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        logger.warning(
            "TELEGRAM_BOT_TOKEN is not set. Set it before registering a real "
            "webhook: export TELEGRAM_BOT_TOKEN=your_token_here"
        )
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))