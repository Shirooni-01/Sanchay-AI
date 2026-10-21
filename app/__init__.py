"""Flask Application Factory for Sanchay AI."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any

from flask import Flask, g, jsonify, redirect, request, url_for

from app.blueprints.admin import admin_bp
from app.blueprints.analysis import analysis_bp
from app.blueprints.auth import auth_bp
from app.blueprints.catalog import catalog_bp
from app.blueprints.inventory import inventory_bp
from app.blueprints.notifications import notifications_bp
from app.blueprints.reports import reports_bp
from app.blueprints.sales import sales_bp
from app.blueprints.sustainability import sustainability_bp
from app.blueprints.transfers import transfers_bp
from app.cli import register_cli_commands
from app.config import CONFIG_BY_NAME, validate_app_config
from app.extensions import csrf, db, login_manager, migrate
from app.utils.errors import (
    build_error_payload,
    register_error_handlers,
    wants_json_response,
)


def _configure_logging(app: Flask) -> None:
    """Configure rotating file and stream logging without exposing secrets."""
    log_level_name = str(app.config.get("LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    app.logger.setLevel(log_level)

    if app.config.get("TESTING"):
        return

    log_file_path = app.config.get("LOG_FILE_PATH", "logs/sanchay_ai.log")
    if log_file_path:
        log_dir = os.path.dirname(log_file_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        if not any(isinstance(h, RotatingFileHandler) for h in app.logger.handlers):
            file_handler = RotatingFileHandler(
                log_file_path,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            formatter = logging.Formatter(
                "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(log_level)
            app.logger.addHandler(file_handler)


def _register_security_hooks(app: Flask) -> None:
    """Attach HTTP security headers and unauthorized access handlers."""

    @app.before_request
    def clear_request_login_user():
        if "_login_user" in g:
            del g._login_user

    @login_manager.unauthorized_handler
    def handle_unauthorized():
        if wants_json_response():
            return (
                jsonify(
                    build_error_payload(
                        code="AUTHENTICATION_REQUIRED",
                        message="Authentication is required to access this resource.",
                    )
                ),
                401,
            )
        return redirect(url_for("auth.login_page", next=request.full_path.rstrip("?")))

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:;",
        )
        # Prevent browser caching of authenticated pages or auth responses
        if (
            request.path.startswith("/auth")
            or request.path.startswith("/api/")
            or request.path.startswith("/dashboard")
            or request.path.startswith("/organization")
            or request.path.startswith("/shops")
            or request.path.startswith("/users")
            or request.path.startswith("/catalog")
            or request.path.startswith("/products")
            or request.path.startswith("/inventory")
            or request.path.startswith("/pos")
            or request.path.startswith("/sales")
            or request.path.startswith("/analysis")
            or request.path.startswith("/transfers")
            or request.path.startswith("/notifications")
            or request.path.startswith("/reports")
            or request.path.startswith("/analytics")
            or request.path.startswith("/sustainability")
            or request.path.startswith("/carbon-credits")
            or request.path == "/"
        ):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response


def create_app(
    config_name: str | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> Flask:
    """Create and configure the Flask application instance."""
    app = Flask(__name__)

    env_name = (config_name or os.getenv("FLASK_ENV", "development")).strip().lower()
    if env_name not in CONFIG_BY_NAME:
        raise RuntimeError(
            f"Unknown configuration environment '{env_name}'. "
            f"Valid options are: {', '.join(CONFIG_BY_NAME.keys())}."
        )

    app.config.from_object(CONFIG_BY_NAME[env_name])
    if config_overrides:
        app.config.update(config_overrides)

    # Validate PostgreSQL URI and secret requirements (no silent SQLite fallback)
    validate_app_config(app.config)

    _configure_logging(app)

    # Initialize Flask extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Import models so SQLAlchemy / Alembic metadata registers them
    from app import models as _models  # noqa: F401

    # Register blueprints, error handlers, security hooks, and CLI commands
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(analysis_bp)
    app.register_blueprint(transfers_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(sustainability_bp)
    register_error_handlers(app)
    _register_security_hooks(app)
    register_cli_commands(app)

    _auto_bootstrap_database(app)

    return app


def _auto_bootstrap_database(app: Flask) -> None:
    """Auto-migrate and seed demo data on platforms like Render where shell access is unavailable."""
    if app.config.get("TESTING"):
        return

    if os.getenv("RENDER") or os.getenv("AUTO_SEED_DEMO", "true").lower() in ("1", "true", "yes"):
        try:
            with app.app_context():
                # Ensure all tables exist in PostgreSQL
                db.create_all()

                # Attempt Alembic migrations if configured
                try:
                    from flask_migrate import upgrade
                    upgrade()
                except Exception as alembic_err:
                    app.logger.info(f"Alembic auto-upgrade notice: {alembic_err}")

                # If no admin exists or admin/shop needs seeding, seed demo dataset
                from app.models.user import User
                from scripts.seed_demo_data import seed_all
                from scripts.seed_emission_factors import seed_emission_factors

                # Ensure emission factors are populated
                seed_emission_factors()

                admin = User.query.filter_by(username="admin").first()
                if not admin:
                    counts = seed_all()
                    app.logger.info(f"Auto-bootstrap: Demo data seeded successfully: {counts}")
                else:
                    app.logger.info("Auto-bootstrap: Database tables and admin account already exist.")
        except Exception as exc:
            app.logger.warning(f"Auto-bootstrap encountered an error: {exc}")


def __getattr__(name: str) -> Any:
    """Lazy module-level attribute resolution.

    Enables deployment servers (like Render's default `gunicorn app:app`)
    to acquire the application instance seamlessly without executing it
    during test collection or plain imports.
    """
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
