"""Flask Blueprint for Carbon Credits & Sustainability Hub."""

from flask import Blueprint

sustainability_bp = Blueprint("sustainability", __name__)

from app.blueprints.sustainability import routes  # noqa: E402, F401
