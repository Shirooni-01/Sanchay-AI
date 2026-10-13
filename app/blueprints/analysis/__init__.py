"""Blueprint for Rule-Based Stock Analysis & Risk Insights (Phase 5)."""

from __future__ import annotations

from flask import Blueprint

analysis_bp = Blueprint("analysis", __name__)

from app.blueprints.analysis import routes  # noqa: E402, F401
