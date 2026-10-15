"""Reports and analytics blueprint for Sanchay AI (Phase 8)."""

from __future__ import annotations

from flask import Blueprint

reports_bp = Blueprint("reports", __name__)

from app.blueprints.reports import routes  # noqa: E402, F401
