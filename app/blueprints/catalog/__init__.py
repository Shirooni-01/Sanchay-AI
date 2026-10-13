"""Product Catalog Blueprint package (Phase 3)."""

from flask import Blueprint

catalog_bp = Blueprint("catalog", __name__)

from app.blueprints.catalog import routes as _routes  # noqa: E402, F401
