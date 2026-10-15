"""HTTP and REST JSON API routes for Phase 2: Organization, Shops & Permissions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.extensions import db
from app.models.organization import Organization, Shop
from app.models.user import User
from app.services.org_service import (
    MembershipService,
    OrganizationService,
    ShopService,
)
from app.services.report_service import ReportService
from app.utils.decorators import (
    require_organization_access,
    require_role,
    require_shop_access,
    verify_shop_access,
)
from app.utils.errors import DomainError

admin_bp = Blueprint("admin", __name__)


def _client_ip() -> str | None:
    return request.remote_addr


def _client_ua() -> str | None:
    return request.headers.get("User-Agent")


def _meta_now() -> dict[str, str]:
    return {"timestamp": datetime.now(timezone.utc).isoformat()}


# ============================================================================
# Server-Rendered HTML Views (Phase 2: Dashboard, Organization, Shops, Users)
# ============================================================================


@admin_bp.route("/dashboard", methods=["GET"])
@login_required
def dashboard_page():
    """Render role-appropriate Phase 8 Operational Dashboard."""
    user: User = current_user._get_current_object()
    org: Organization | None = user.organization
    shops: list[Shop] = []
    if org is not None:
        shops = ShopService.list_shops(actor=user)

    org_metrics: dict[str, Any] | None = None
    shop_metrics: dict[str, Any] | None = None
    sales_trends: list[dict[str, Any]] = []
    selected_shop: Shop | None = None

    if user.role == "SUPER_ADMIN" and org is not None:
        try:
            org_metrics = ReportService.get_organization_dashboard_metrics(actor=user)
            sales_trends = ReportService.get_sales_trends(actor=user, shop_id=None, days=14)
        except DomainError:
            pass
    elif user.role == "SHOP_OWNER":
        raw_shop_id = request.args.get("shop_id")
        target_shop_id = None
        if raw_shop_id:
            try:
                target_shop_id = int(raw_shop_id)
            except ValueError:
                target_shop_id = None

        if target_shop_id:
            selected_shop = next((s for s in shops if s.id == target_shop_id), None)
        if not selected_shop and shops:
            selected_shop = shops[0]

        if selected_shop:
            try:
                shop_metrics = ReportService.get_shop_dashboard_metrics(actor=user, shop_id=selected_shop.id)
                sales_trends = ReportService.get_sales_trends(actor=user, shop_id=selected_shop.id, days=14)
            except DomainError:
                pass

    return render_template(
        "admin/dashboard.html",
        user=user,
        organization=org,
        shops=shops,
        selected_shop=selected_shop,
        org_metrics=org_metrics,
        shop_metrics=shop_metrics,
        sales_trends=sales_trends,
    )


@admin_bp.route("/organization", methods=["GET"])
@login_required
def organization_page():
    """Render Organization overview and settings page."""
    user: User = current_user._get_current_object()
    org: Organization | None = None
    if user.organization_id is not None:
        org = OrganizationService.get_organization_for_user(actor=user)

    return render_template(
        "admin/organization.html",
        user=user,
        organization=org,
    )


@admin_bp.route("/organization/create", methods=["POST"])
@require_role("SUPER_ADMIN")
def create_organization_action():
    """Handle form submission to create the Super Admin's organization."""
    user: User = current_user._get_current_object()
    payload = {
        "name": request.form.get("name"),
        "code": request.form.get("code"),
        "contact_email": request.form.get("contact_email"),
        "contact_phone": request.form.get("contact_phone"),
        "address": request.form.get("address"),
        "low_stock_default_threshold": request.form.get(
            "low_stock_default_threshold", 10
        ),
        "near_expiry_days_threshold": request.form.get(
            "near_expiry_days_threshold", 15
        ),
        "status": request.form.get("status", "ACTIVE"),
    }
    try:
        org = OrganizationService.create_organization(
            actor=user,
            payload=payload,
            attach_actor=True,
            ip_address=_client_ip(),
            user_agent=_client_ua(),
        )
        flash(f"Organization '{org.name}' ({org.code}) created successfully.", "success")
        return redirect(url_for("admin.organization_page"))
    except DomainError as exc:
        flash(exc.message, "error")
        return (
            render_template(
                "admin/organization.html",
                user=user,
                organization=user.organization,
            ),
            exc.http_status,
        )


@admin_bp.route("/organization/update", methods=["POST"])
@require_role("SUPER_ADMIN")
def update_organization_action():
    """Handle form submission to update organization settings or status."""
    user: User = current_user._get_current_object()
    payload: dict[str, Any] = {}
    for field in (
        "name",
        "code",
        "contact_email",
        "contact_phone",
        "address",
        "low_stock_default_threshold",
        "near_expiry_days_threshold",
        "status",
    ):
        if field in request.form:
            payload[field] = request.form.get(field)

    try:
        org = OrganizationService.update_organization(
            actor=user,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_client_ua(),
        )
        flash(f"Organization '{org.name}' updated (Status: {org.status}).", "success")
        return redirect(url_for("admin.organization_page"))
    except DomainError as exc:
        flash(exc.message, "error")
        return (
            render_template(
                "admin/organization.html",
                user=user,
                organization=user.organization,
            ),
            exc.http_status,
        )


@admin_bp.route("/shops", methods=["GET"])
@login_required
def shops_page():
    """Render Shop directory with search and status/city filtering."""
    user: User = current_user._get_current_object()
    if user.organization_id is None:
        flash("Please create or join an organization before managing shops.", "warning")
        return redirect(url_for("admin.organization_page"))

    status_filter = request.args.get("status", "").strip() or None
    city_filter = request.args.get("city", "").strip() or None
    search_query = request.args.get("search", "").strip() or None

    shops = ShopService.list_shops(
        actor=user,
        status=status_filter,
        city=city_filter,
        search=search_query,
    )
    return render_template(
        "admin/shops.html",
        user=user,
        organization=user.organization,
        shops=shops,
        status_filter=status_filter or "",
        city_filter=city_filter or "",
        search_query=search_query or "",
    )


@admin_bp.route("/shops/create", methods=["POST"])
@require_role("SUPER_ADMIN")
def create_shop_action():
    """Handle form submission to create a new shop within the organization."""
    user: User = current_user._get_current_object()
    payload = {
        "name": request.form.get("name"),
        "code": request.form.get("code"),
        "address": request.form.get("address"),
        "city": request.form.get("city"),
        "pincode": request.form.get("pincode"),
        "latitude": request.form.get("latitude"),
        "longitude": request.form.get("longitude"),
        "contact_phone": request.form.get("contact_phone"),
        "contact_email": request.form.get("contact_email"),
        "low_stock_default_threshold": request.form.get("low_stock_default_threshold"),
        "near_expiry_days_threshold": request.form.get("near_expiry_days_threshold"),
        "status": request.form.get("status", "ACTIVE"),
    }
    try:
        shop = ShopService.create_shop(
            actor=user,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_client_ua(),
        )
        flash(f"Shop '{shop.name}' ({shop.code}) created successfully.", "success")
        return redirect(url_for("admin.shop_detail_page", shop_id=shop.id))
    except DomainError as exc:
        flash(exc.message, "error")
        shops = (
            ShopService.list_shops(actor=user)
            if user.organization_id is not None
            else []
        )
        return (
            render_template(
                "admin/shops.html",
                user=user,
                organization=user.organization,
                shops=shops,
                status_filter="",
                city_filter="",
                search_query="",
            ),
            exc.http_status,
        )


@admin_bp.route("/shops/<int:shop_id>", methods=["GET"])
@login_required
def shop_detail_page(shop_id: int):
    """Render Shop detail, edit form, and shop membership assignments."""
    user: User = current_user._get_current_object()
    shop_data = ShopService.get_shop_details(
        actor=user,
        shop_id=shop_id,
        allow_inactive=True,
    )
    can_manage_shop = shop_data.get("my_assignment_role") in (
        "SUPER_ADMIN",
        "SHOP_OWNER",
    )
    assignable_users: list[User] = []
    if can_manage_shop:
        if user.role == "SUPER_ADMIN":
            assignable_users = [
                u
                for u in MembershipService.list_users(actor=user)
                if u.role != "SUPER_ADMIN" and u.is_active
            ]

    return render_template(
        "admin/shop_detail.html",
        user=user,
        organization=user.organization,
        shop=shop_data,
        can_manage_shop=can_manage_shop,
        assignable_users=assignable_users,
    )


@admin_bp.route("/shops/<int:shop_id>/update", methods=["POST"])
@login_required
def update_shop_action(shop_id: int):
    """Handle form submission to update shop details or status."""
    user: User = current_user._get_current_object()
    payload: dict[str, Any] = {}
    for field in (
        "name",
        "code",
        "address",
        "city",
        "pincode",
        "latitude",
        "longitude",
        "contact_phone",
        "contact_email",
        "low_stock_default_threshold",
        "near_expiry_days_threshold",
        "status",
    ):
        if field in request.form:
            payload[field] = request.form.get(field)

    try:
        updated = ShopService.update_shop(
            actor=user,
            shop_id=shop_id,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_client_ua(),
        )
        flash(f"Shop '{updated.name}' updated successfully.", "success")
        return redirect(url_for("admin.shop_detail_page", shop_id=shop_id))
    except DomainError as exc:
        flash(exc.message, "error")
        return redirect(url_for("admin.shop_detail_page", shop_id=shop_id))


@admin_bp.route("/shops/<int:shop_id>/assignments", methods=["POST"])
@require_role("SUPER_ADMIN")
def assign_shop_member_action(shop_id: int):
    """Handle form submission to assign a user to a shop."""
    user: User = current_user._get_current_object()
    payload = {
        "user_id": request.form.get("user_id"),
        "assignment_role": "SHOP_OWNER",
        "is_primary": request.form.get("is_primary", "true"),
        "is_active": "true",
    }
    try:
        assignment = MembershipService.assign_user_to_shop(
            actor=user,
            shop_id=shop_id,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_client_ua(),
        )
        flash(
            f"Assigned user #{assignment.user_id} as {assignment.assignment_role}.",
            "success",
        )
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("admin.shop_detail_page", shop_id=shop_id))


@admin_bp.route(
    "/shops/<int:shop_id>/assignments/<int:assignment_id>/update",
    methods=["POST"],
)
@login_required
def update_shop_assignment_action(shop_id: int, assignment_id: int):
    """Handle form submission to update or revoke a shop assignment."""
    user: User = current_user._get_current_object()
    payload: dict[str, Any] = {}
    if "assignment_role" in request.form:
        payload["assignment_role"] = request.form.get("assignment_role")
    if "is_primary" in request.form:
        payload["is_primary"] = request.form.get("is_primary")
    if "is_active" in request.form:
        payload["is_active"] = request.form.get("is_active")

    try:
        MembershipService.update_shop_assignment(
            actor=user,
            shop_id=shop_id,
            assignment_id=assignment_id,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_client_ua(),
        )
        flash("Shop membership assignment updated.", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("admin.shop_detail_page", shop_id=shop_id))


@admin_bp.route("/users", methods=["GET"])
@require_role("SUPER_ADMIN", "SHOP_OWNER")
def users_page():
    """Render Organization/Shop User & Role Management directory."""
    user: User = current_user._get_current_object()
    if user.organization_id is None:
        flash("Please create an organization before managing users.", "warning")
        return redirect(url_for("admin.organization_page"))

    role_filter = request.args.get("role", "").strip() or None
    raw_shop_id = request.args.get("shop_id", "").strip()
    shop_id_filter = int(raw_shop_id) if raw_shop_id.isdigit() else None

    users = MembershipService.list_users(
        actor=user,
        role_filter=role_filter,
        shop_id_filter=shop_id_filter,
    )
    accessible_shops = ShopService.list_shops(actor=user, status="ACTIVE")

    return render_template(
        "admin/users.html",
        user=user,
        organization=user.organization,
        users=users,
        accessible_shops=accessible_shops,
        role_filter=role_filter or "",
        shop_id_filter=shop_id_filter,
    )


@admin_bp.route("/users/create", methods=["POST"])
@require_role("SUPER_ADMIN")
def create_user_action():
    """Handle form submission to create a new organization user / shop member."""
    user: User = current_user._get_current_object()
    payload = {
        "username": request.form.get("username"),
        "email": request.form.get("email"),
        "full_name": request.form.get("full_name"),
        "phone": request.form.get("phone"),
        "role": request.form.get("role", "SHOP_OWNER"),
        "password": request.form.get("password"),
        "shop_id": request.form.get("shop_id"),
    }
    try:
        created = MembershipService.create_member_user(
            actor=user,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_client_ua(),
        )
        flash(
            f"User '{created.username}' ({created.role}) created successfully.",
            "success",
        )
        return redirect(url_for("admin.users_page"))
    except DomainError as exc:
        flash(exc.message, "error")
        users = (
            MembershipService.list_users(actor=user)
            if user.organization_id is not None
            else []
        )
        accessible_shops = (
            ShopService.list_shops(actor=user, status="ACTIVE")
            if user.organization_id is not None
            else []
        )
        return (
            render_template(
                "admin/users.html",
                user=user,
                organization=user.organization,
                users=users,
                accessible_shops=accessible_shops,
                role_filter="",
                shop_id_filter=None,
            ),
            exc.http_status,
        )


@admin_bp.route("/users/<int:user_id>/update", methods=["POST"])
@require_role("SUPER_ADMIN", "SHOP_OWNER")
def update_user_action(user_id: int):
    """Handle form submission to update a user's active status or role."""
    user: User = current_user._get_current_object()
    payload: dict[str, Any] = {}
    for field in ("full_name", "email", "phone", "role", "is_active"):
        if field in request.form:
            payload[field] = request.form.get(field)

    try:
        updated = MembershipService.update_member_user(
            actor=user,
            target_user_id=user_id,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_client_ua(),
        )
        flash(f"User '{updated.username}' updated successfully.", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("admin.users_page"))


@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@require_role("SUPER_ADMIN")
def reset_user_password_action(user_id: int):
    """Allow Super Admin to reset a user's password directly from the Web UI."""
    actor: User = current_user._get_current_object()
    new_password = (request.form.get("new_password") or "").strip()

    target_user = db.session.get(User, user_id)
    if not target_user:
        flash(f"User #{user_id} was not found.", "error")
        return redirect(url_for("admin.users_page"))

    if actor.organization_id is not None and target_user.organization_id != actor.organization_id:
        flash("Cannot modify user belonging to another organization.", "error")
        return redirect(url_for("admin.users_page"))

    try:
        from app.services.audit_service import AuditService
        from app.utils.validators import validate_password_strength

        validated_pwd = validate_password_strength(new_password, field_name="new_password")
        target_user.set_password(validated_pwd)
        target_user.rotate_session_token()
        AuditService.record(
            action="USER_PASSWORD_RESET_ADMIN",
            entity_type="USER",
            entity_id=target_user.id,
            actor=actor,
            organization_id=actor.organization_id,
            ip_address=_client_ip(),
            user_agent=_client_ua(),
        )
        db.session.commit()
        flash(f"Password for user '{target_user.username}' successfully reset.", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    except Exception:
        db.session.rollback()
        flash("Failed to reset password due to a server error.", "error")

    return redirect(url_for("admin.users_page"))


# ============================================================================
# REST JSON API Endpoints (/api/v1/organization*, /api/v1/shops*, /api/v1/users*)
# ============================================================================


@admin_bp.route("/api/v1/organizations", methods=["POST"])
@require_role("SUPER_ADMIN")
def api_create_organization():
    """Create a new organization (SUPER_ADMIN only)."""
    user: User = current_user._get_current_object()
    payload = request.get_json(silent=True) or {}
    org = OrganizationService.create_organization(
        actor=user,
        payload=payload,
        attach_actor=True,
        ip_address=_client_ip(),
        user_agent=_client_ua(),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"organization": org.to_dict(include_counts=True)},
                "meta": _meta_now(),
            }
        ),
        201,
    )


@admin_bp.route("/api/v1/organization", methods=["GET"])
@require_organization_access(require_active=False)
def api_get_organization():
    """API-005: Retrieve the authenticated user's organization details."""
    user: User = current_user._get_current_object()
    target_org_id = request.args.get("organization_id", type=int)
    org = OrganizationService.get_organization_for_user(
        actor=user,
        target_organization_id=target_org_id,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"organization": org.to_dict(include_counts=True)},
                "meta": _meta_now(),
            }
        ),
        200,
    )


@admin_bp.route("/api/v1/organization", methods=["PATCH"])
@require_role("SUPER_ADMIN")
def api_update_organization():
    """API-006: Update organization details, thresholds, or status (SUPER_ADMIN only)."""
    user: User = current_user._get_current_object()
    payload = request.get_json(silent=True) or {}
    target_org_id = request.args.get("organization_id", type=int)
    org = OrganizationService.update_organization(
        actor=user,
        payload=payload,
        target_organization_id=target_org_id,
        ip_address=_client_ip(),
        user_agent=_client_ua(),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"organization": org.to_dict(include_counts=True)},
                "meta": _meta_now(),
            }
        ),
        200,
    )


@admin_bp.route("/api/v1/shops", methods=["GET"])
@login_required
def api_list_shops():
    """API-007: List shops accessible to the authenticated user within their organization."""
    user: User = current_user._get_current_object()
    status_filter = request.args.get("status")
    city_filter = request.args.get("city")
    search_query = request.args.get("search")
    target_org_id = request.args.get("organization_id", type=int)

    shops = ShopService.list_shops(
        actor=user,
        status=status_filter,
        city=city_filter,
        search=search_query,
        target_organization_id=target_org_id,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "shops": [s.to_dict(include_assignments=False) for s in shops],
                },
                "meta": {
                    "count": len(shops),
                    **_meta_now(),
                },
            }
        ),
        200,
    )


@admin_bp.route("/api/v1/shops", methods=["POST"])
@require_role("SUPER_ADMIN")
def api_create_shop():
    """API-008: Create a new shop within the authorized organization (SUPER_ADMIN only)."""
    user: User = current_user._get_current_object()
    payload = request.get_json(silent=True) or {}
    shop = ShopService.create_shop(
        actor=user,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_client_ua(),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"shop": shop.to_dict(include_assignments=True)},
                "meta": _meta_now(),
            }
        ),
        201,
    )


@admin_bp.route("/api/v1/shops/<int:shop_id>", methods=["GET"])
@login_required
def api_get_shop(shop_id: int):
    """API-009 (GET): Retrieve a single shop's details if authorized."""
    user: User = current_user._get_current_object()
    shop_data = ShopService.get_shop_details(
        actor=user,
        shop_id=shop_id,
        allow_inactive=True,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"shop": shop_data},
                "meta": _meta_now(),
            }
        ),
        200,
    )


@admin_bp.route("/api/v1/shops/<int:shop_id>", methods=["PATCH"])
@login_required
def api_update_shop(shop_id: int):
    """API-009 (PATCH): Update shop details or status."""
    user: User = current_user._get_current_object()
    payload = request.get_json(silent=True) or {}
    shop = ShopService.update_shop(
        actor=user,
        shop_id=shop_id,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_client_ua(),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"shop": shop.to_dict(include_assignments=True)},
                "meta": _meta_now(),
            }
        ),
        200,
    )


@admin_bp.route("/api/v1/shops/<int:shop_id>/operational-check", methods=["POST"])
@require_shop_access(allow_inactive=False)
def api_shop_operational_check(shop_id: int):
    """Verify that the shop and its parent organization are ACTIVE for operational actions."""
    user: User = current_user._get_current_object()
    shop, assignment = verify_shop_access(
        user=user,
        shop_id=shop_id,
        allow_inactive=False,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "shop_id": shop.id,
                    "organization_id": shop.organization_id,
                    "status": shop.status,
                    "is_operational": shop.is_operational,
                    "effective_role": (
                        "SUPER_ADMIN"
                        if user.role == "SUPER_ADMIN"
                        else (assignment.assignment_role if assignment else None)
                    ),
                },
                "meta": _meta_now(),
            }
        ),
        200,
    )


@admin_bp.route("/api/v1/users", methods=["GET"])
@require_role("SUPER_ADMIN", "SHOP_OWNER")
def api_list_users():
    """API-010: List users within the authorized organization or shop scope."""
    user: User = current_user._get_current_object()
    role_filter = request.args.get("role")
    shop_id_filter = request.args.get("shop_id", type=int)
    target_org_id = request.args.get("organization_id", type=int)

    users = MembershipService.list_users(
        actor=user,
        role_filter=role_filter,
        shop_id_filter=shop_id_filter,
        target_organization_id=target_org_id,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "users": [u.to_dict(include_assignments=True) for u in users],
                },
                "meta": {
                    "count": len(users),
                    **_meta_now(),
                },
            }
        ),
        200,
    )


@admin_bp.route("/api/v1/users", methods=["POST"])
@require_role("SUPER_ADMIN", "SHOP_OWNER")
def api_create_user():
    """API-011: Create a new user in the organization and optionally assign to a shop."""
    user: User = current_user._get_current_object()
    payload = request.get_json(silent=True) or {}
    created = MembershipService.create_member_user(
        actor=user,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_client_ua(),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"user": created.to_dict(include_assignments=True)},
                "meta": _meta_now(),
            }
        ),
        201,
    )


@admin_bp.route("/api/v1/users/<int:user_id>", methods=["GET"])
@login_required
def api_get_user(user_id: int):
    """API-012 (GET): Retrieve a user profile within authorized scope."""
    user: User = current_user._get_current_object()
    target = MembershipService.get_user(actor=user, target_user_id=user_id)
    return (
        jsonify(
            {
                "success": True,
                "data": {"user": target.to_dict(include_assignments=True)},
                "meta": _meta_now(),
            }
        ),
        200,
    )


@admin_bp.route("/api/v1/users/<int:user_id>", methods=["PATCH"])
@login_required
def api_update_user(user_id: int):
    """API-012 (PATCH): Update user profile, status, or role within authorized scope."""
    user: User = current_user._get_current_object()
    payload = request.get_json(silent=True) or {}
    updated = MembershipService.update_member_user(
        actor=user,
        target_user_id=user_id,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_client_ua(),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"user": updated.to_dict(include_assignments=True)},
                "meta": _meta_now(),
            }
        ),
        200,
    )


@admin_bp.route("/api/v1/shops/<int:shop_id>/assignments", methods=["POST"])
@login_required
def api_assign_user_to_shop(shop_id: int):
    """Assign a user (SHOP_OWNER) to a shop within the same organization."""
    user: User = current_user._get_current_object()
    payload = request.get_json(silent=True) or {}
    assignment = MembershipService.assign_user_to_shop(
        actor=user,
        shop_id=shop_id,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_client_ua(),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "assignment": assignment.to_dict(
                        include_shop=True,
                        include_user=True,
                    )
                },
                "meta": _meta_now(),
            }
        ),
        201,
    )


@admin_bp.route(
    "/api/v1/shops/<int:shop_id>/assignments/<int:assignment_id>",
    methods=["PATCH"],
)
@login_required
def api_update_shop_assignment(shop_id: int, assignment_id: int):
    """Update or revoke an existing UserShopAssignment."""
    user: User = current_user._get_current_object()
    payload = request.get_json(silent=True) or {}
    assignment = MembershipService.update_shop_assignment(
        actor=user,
        shop_id=shop_id,
        assignment_id=assignment_id,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_client_ua(),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "assignment": assignment.to_dict(
                        include_shop=True,
                        include_user=True,
                    )
                },
                "meta": _meta_now(),
            }
        ),
        200,
    )
