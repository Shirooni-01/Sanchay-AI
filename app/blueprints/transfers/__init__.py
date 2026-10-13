"""Inter-Store Stock Exchange / Transfer Blueprint (Phase 6)."""

from flask import Blueprint

transfers_bp = Blueprint("transfers", __name__)

from app.blueprints.transfers import routes  # noqa: E402, F401
