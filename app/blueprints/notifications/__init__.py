"""Notifications blueprint package (Phase 7)."""

from flask import Blueprint

notifications_bp = Blueprint("notifications", __name__)

from app.blueprints.notifications import routes  # noqa: E402, F401
