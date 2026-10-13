"""Inventory, Batch & Stock Movement Blueprint package (Phase 3)."""

from flask import Blueprint

inventory_bp = Blueprint("inventory", __name__)

from app.blueprints.inventory import routes as _routes  # noqa: E402, F401
