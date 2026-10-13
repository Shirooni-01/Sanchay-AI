"""HTTP and REST JSON API routes for Phase 1 Authentication & Health Checks."""

from __future__ import annotations

from datetime import datetime, timezone

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.services.auth_service import AuthService
from app.utils.errors import DomainError
from app.utils.validators import is_safe_redirect_url

auth_bp = Blueprint("auth", __name__)


def _client_ip() -> str | None:
    return request.remote_addr


def _client_ua() -> str | None:
    return request.headers.get("User-Agent")


def _establish_authenticated_session(user) -> None:
    """Mitigate session fixation by clearing pre-login session keys before login_user."""
    csrf_token = session.get("csrf_token")
    session.clear()
    if csrf_token:
        session["csrf_token"] = csrf_token
    session.permanent = True
    login_user(user, remember=False, fresh=True)
    session.modified = True


# ============================================================================
# HTML Views (Server-Rendered Login & Authenticated Landing Page)
# ============================================================================


@auth_bp.route("/", methods=["GET"])
@login_required
def landing_page():
    """Authenticated landing page proving server-side sessions and RBAC identity."""
    return render_template("auth/landing.html", user=current_user)


@auth_bp.route("/auth/login", methods=["GET", "POST"])
def login_page():
    """Render login page (GET) or process form login with CSRF validation (POST)."""
    if current_user.is_authenticated:
        return redirect(url_for("auth.landing_page"))

    if request.method == "POST":
        identifier = request.form.get("identifier", "")
        password = request.form.get("password", "")
        next_url = request.form.get("next") or request.args.get("next")

        try:
            user = AuthService.authenticate_user(
                identifier=identifier,
                password=password,
                ip_address=_client_ip(),
                user_agent=_client_ua(),
            )
            _establish_authenticated_session(user)
            flash("Signed in successfully.", "success")
            if next_url and is_safe_redirect_url(next_url):
                return redirect(next_url)
            return redirect(url_for("auth.landing_page"))
        except DomainError as exc:
            flash(exc.message, "error")
            return (
                render_template(
                    "auth/login.html",
                    identifier=identifier,
                ),
                exc.http_status,
            )

    return render_template("auth/login.html", identifier="")


@auth_bp.route("/auth/logout", methods=["POST"])
@login_required
def logout_action():
    """Invalidate server-side session token, clear cookie session, and redirect to login."""
    user = current_user._get_current_object()
    AuthService.invalidate_user_session(
        user=user,
        ip_address=_client_ip(),
        user_agent=_client_ua(),
    )
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login_page"))


@auth_bp.route("/auth/change-password", methods=["POST"])
@login_required
def change_password_action():
    """Process form-based password change and refresh authenticated session token."""
    user = current_user._get_current_object()
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    try:
        updated_user = AuthService.change_password(
            user=user,
            current_password=current_password,
            new_password=new_password,
            confirm_password=confirm_password,
            ip_address=_client_ip(),
            user_agent=_client_ua(),
        )
        _establish_authenticated_session(updated_user)
        flash("Password updated successfully.", "success")
        return redirect(url_for("auth.landing_page"))
    except DomainError as exc:
        flash(exc.message, "error")
        return render_template("auth/landing.html", user=current_user), exc.http_status


# ============================================================================
# REST JSON API Endpoints (/api/v1/auth/* and /api/v1/health)
# ============================================================================


@auth_bp.route("/api/v1/health", methods=["GET"])
def api_health():
    """Verify application and PostgreSQL database connectivity."""
    try:
        db.session.execute(text("SELECT 1"))
        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "status": "healthy",
                        "database": "connected",
                        "phase": 1,
                        "ai_enabled": False,
                    },
                    "meta": {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                }
            ),
            200,
        )
    except SQLAlchemyError:
        db.session.rollback()
        return (
            jsonify(
                {
                    "success": False,
                    "error": {
                        "code": "DATABASE_UNAVAILABLE",
                        "message": "PostgreSQL database is unreachable.",
                        "details": {},
                    },
                }
            ),
            503,
        )


@auth_bp.route("/api/v1/auth/login", methods=["POST"])
def api_login():
    """API-001: Authenticate user with JSON payload and establish session."""
    payload = request.get_json(silent=True) or {}
    identifier = payload.get("identifier") or payload.get("username") or payload.get("email")
    password = payload.get("password")

    user = AuthService.authenticate_user(
        identifier=identifier,
        password=password,
        ip_address=_client_ip(),
        user_agent=_client_ua(),
    )
    _establish_authenticated_session(user)

    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "user": user.to_dict(),
                },
                "meta": {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }
        ),
        200,
    )


@auth_bp.route("/api/v1/auth/logout", methods=["POST"])
@login_required
def api_logout():
    """API-002: Invalidate current authenticated session."""
    user = current_user._get_current_object()
    AuthService.invalidate_user_session(
        user=user,
        ip_address=_client_ip(),
        user_agent=_client_ua(),
    )
    logout_user()
    session.clear()

    return (
        jsonify(
            {
                "success": True,
                "data": {"message": "Logged out successfully."},
                "meta": {"timestamp": datetime.now(timezone.utc).isoformat()},
            }
        ),
        200,
    )


@auth_bp.route("/api/v1/auth/me", methods=["GET"])
@login_required
def api_me():
    """API-003: Return currently authenticated user profile."""
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "user": current_user.to_dict(),
                },
                "meta": {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }
        ),
        200,
    )


@auth_bp.route("/api/v1/auth/change-password", methods=["POST"])
@login_required
def api_change_password():
    """API-004: Change authenticated user's password and rotate session token."""
    payload = request.get_json(silent=True) or {}
    current_password = payload.get("current_password")
    new_password = payload.get("new_password")
    confirm_password = payload.get("confirm_password")

    user = current_user._get_current_object()
    updated_user = AuthService.change_password(
        user=user,
        current_password=current_password,
        new_password=new_password,
        confirm_password=confirm_password,
        ip_address=_client_ip(),
        user_agent=_client_ua(),
    )
    _establish_authenticated_session(updated_user)

    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "message": "Password changed successfully.",
                    "user": updated_user.to_dict(),
                },
                "meta": {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }
        ),
        200,
    )
