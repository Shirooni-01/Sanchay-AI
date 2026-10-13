"""Point-of-Sale (POS) Billing, Sales History, Receipts, and Returns Blueprint (Phase 4)."""

from flask import Blueprint

sales_bp = Blueprint("sales", __name__)

from app.blueprints.sales import routes  # noqa: E402, F401
