"""
Admin dashboard API. Exposes app/analytics.py's aggregate queries as JSON.

Registered as a Blueprint onto the same Flask app as the Telegram webhook
(see telegram_webhook.py), so one process serves both. Every response here
is aggregate-only -- see analytics.py's anonymity rule at the top of that
file, which this module inherits by construction (it never touches
sessions/responses/results directly, only calls into analytics.py).

OPTIONAL AUTH: set ADMIN_API_KEY in the environment and callers must send
it as the "X-Admin-Key" header. If ADMIN_API_KEY is unset, these endpoints
are open to anyone who can reach them -- fine for local dev behind ngrok
during the hackathon, NOT fine to leave unset if this ever sits on a public
URL past the demo. A startup warning is logged once if it's unset.
"""

import logging
import os
from functools import wraps

from flask import Blueprint, jsonify, request

from app.analytics import (get_daily_completed_counts, get_summary_stats,
                           get_item_level_breakdown, get_demographic_breakdown, get_comorbidity_matrix)
from app.analytics import (get_daily_completed_counts, get_summary_stats,
                           get_item_level_breakdown, get_demographic_breakdown, get_comorbidity_matrix,
                           get_correlation_stats, get_response_quality_stats)

logger = logging.getLogger("dashboard_api")

ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
if not ADMIN_API_KEY:
    logger.warning(
        "ADMIN_API_KEY not set -- /api/stats endpoints are UNAUTHENTICATED. "
        "Fine for local/demo use behind ngrok; set this before any public deploy."
    )

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api")


def require_admin_key(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not ADMIN_API_KEY:
            return view(*args, **kwargs)
        if request.headers.get("X-Admin-Key") != ADMIN_API_KEY:
            return jsonify({"error": "unauthorized"}), 401
        return view(*args, **kwargs)
    return wrapped


@dashboard_bp.route("/stats", methods=["GET"])
@require_admin_key
def stats():
    """
    ?days=7 for a rolling window, omit for all-time.
    Returns aggregate counts/percentages only.
    """
    days_param = request.args.get("days")
    days = int(days_param) if days_param else None
    summary = get_summary_stats(days=days)

    return jsonify({
        "window": summary.window_label,
        "total_sessions": summary.total_sessions,
        "completed_sessions": summary.completed_sessions,
        "in_progress_sessions": summary.in_progress_sessions,
        "expired_sessions": summary.expired_sessions,
        "completion_rate_percent": summary.completion_rate,
        "severity_breakdown": {
            "minimal_mild": summary.severity.minimal_mild,
            "moderate": summary.severity.moderate,
            "severe": summary.severity.severe,
        },
        "severity_percent": {
            "minimal_mild": summary.severity.percent("minimal_mild"),
            "moderate": summary.severity.percent("moderate"),
            "severe": summary.severity.percent("severe"),
        },
        "self_harm_override_count": summary.self_harm_override_count,
    })


@dashboard_bp.route("/stats/daily", methods=["GET"])
@require_admin_key
def daily_stats():
    """?days=30 (default) -- trend data for a chart, oldest day first."""
    days = int(request.args.get("days", 30))
    return jsonify(get_daily_completed_counts(days=days))

@dashboard_bp.route("/stats/symptoms", methods=["GET"])
@require_admin_key
def symptom_breakdown():
    return jsonify(get_item_level_breakdown())


@dashboard_bp.route("/stats/demographics/<axis>", methods=["GET"])
@require_admin_key
def demographic_breakdown(axis):
    try:
        return jsonify(get_demographic_breakdown(axis))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@dashboard_bp.route("/stats/comorbidity", methods=["GET"])
@require_admin_key
def comorbidity():
    return jsonify(get_comorbidity_matrix())


@dashboard_bp.route("/stats/correlation", methods=["GET"])
@require_admin_key
def correlation():
    result = get_correlation_stats()
    if result is None:
        return jsonify({"error": "insufficient data"}), 200
    return jsonify(result)


@dashboard_bp.route("/stats/quality", methods=["GET"])
@require_admin_key
def quality():
    return jsonify(get_response_quality_stats())

from app.analytics import get_symptom_breakdown_by_axis  # add to existing import line

@dashboard_bp.route("/stats/symptoms/<axis>", methods=["GET"])
@require_admin_key
def symptom_breakdown_by_axis(axis):
    try:
        return jsonify(get_symptom_breakdown_by_axis(axis))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400