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
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models.user import User
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

    portal = (request.args.get("portal") or "").strip().lower()
    login_type = portal if portal in ("organization", "shop") else "organization"

    # Fixed demo credentials for instant, seamless login
    org_default_user = "admin"
    org_default_pass = "orga"
    shop_default_user = "shop"
    shop_default_pass = "12345"

    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()
        password = request.form.get("password") or ""
        submitted_portal = (request.form.get("login_type") or "").strip().lower()
        if submitted_portal in ("organization", "shop"):
            login_type = submitted_portal
        next_url = request.form.get("next") or request.args.get("next")

        try:
            user = AuthService.authenticate_user(
                identifier=identifier,
                password=password,
                ip_address=_client_ip(),
                user_agent=_client_ua(),
            )

            # Role boundary check when portal was explicitly selected
            if submitted_portal == "organization" and user.role != "SUPER_ADMIN":
                flash(
                    "Access denied: This is a Shop account. Please switch to the Shop Login tab to sign in.",
                    "error",
                )
                return (
                    render_template(
                        "auth/login.html",
                        identifier=identifier,
                        password=password,
                        login_type="shop",
                        org_default_user=org_default_user,
                        org_default_pass=org_default_pass,
                        shop_default_user=shop_default_user,
                        shop_default_pass=shop_default_pass,
                    ),
                    403,
                )

            if submitted_portal == "shop" and user.role == "SUPER_ADMIN":
                flash(
                    "Access denied: This is an Organization Admin account. Please switch to the Organization Login tab to sign in.",
                    "error",
                )
                return (
                    render_template(
                        "auth/login.html",
                        identifier=identifier,
                        password=password,
                        login_type="organization",
                        org_default_user=org_default_user,
                        org_default_pass=org_default_pass,
                        shop_default_user=shop_default_user,
                        shop_default_pass=shop_default_pass,
                    ),
                    403,
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
                    password=password,
                    login_type=login_type,
                    org_default_user=org_default_user,
                    org_default_pass=org_default_pass,
                    shop_default_user=shop_default_user,
                    shop_default_pass=shop_default_pass,
                ),
                exc.http_status,
            )

    # For GET request: pre-fill credentials based on portal
    default_identifier = shop_default_user if login_type == "shop" else org_default_user
    default_password = shop_default_pass if login_type == "shop" else org_default_pass

    return render_template(
        "auth/login.html",
        identifier=default_identifier,
        password=default_password,
        login_type=login_type,
        org_default_user=org_default_user,
        org_default_pass=org_default_pass,
        shop_default_user=shop_default_user,
        shop_default_pass=shop_default_pass,
    )


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


@auth_bp.route("/auth/update-profile", methods=["POST"])
@login_required
def update_profile_action():
    """Update profile information (full_name, email, phone) for current user."""
    user: User = current_user._get_current_object()
    full_name = (request.form.get("full_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    phone = (request.form.get("phone") or "").strip() or None

    errors: dict[str, str] = {}
    if not full_name:
        errors["full_name"] = "Full name is required."
    elif len(full_name) > 120:
        errors["full_name"] = "Full name must not exceed 120 characters."

    if not email:
        errors["email"] = "Email address is required."
    elif "@" not in email or "." not in email:
        errors["email"] = "Please enter a valid email address."
    else:
        existing = (
            db.session.query(User)
            .filter(func.lower(User.email) == email, User.id != user.id)
            .first()
        )
        if existing:
            errors["email"] = "That email address is already registered to another user."

    if errors:
        for msg in errors.values():
            flash(msg, "error")
        return redirect(url_for("auth.landing_page"))

    user.full_name = full_name
    user.email = email
    user.phone = phone

    try:
        db.session.commit()
        flash("Profile details updated successfully.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Failed to update profile due to a database error.", "error")

    return redirect(url_for("auth.landing_page"))


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
