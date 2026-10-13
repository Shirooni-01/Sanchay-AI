"""HTTP Route Controllers for Notifications & Alerts (Phase 7).

Serves both Jinja2 HTML views (/notifications) and REST JSON APIs (/api/v1/notifications/*).
Enforces strict user ownership, multi-tenant isolation, and CSRF protection.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import (
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from app.blueprints.notifications import notifications_bp
from app.extensions import db
from app.models.organization import Shop
from app.models.user import User
from app.services.notification_service import NotificationService
from app.utils.decorators import verify_authenticated_user, verify_shop_access
from app.utils.errors import (
    AuthorizationError,
    DomainError,
    ResourceNotFoundError,
    ValidationError,
)
from app.utils.validators import (
    validate_notification_query_params,
    validate_pagination_params,
)

logger = logging.getLogger(__name__)


# ============================================================================
# HTML Views
# ============================================================================


@notifications_bp.route("/notifications", methods=["GET"])
def notification_center_page():
    """Render the user-scoped notification center page."""
    actor = verify_authenticated_user()

    raw_filter = request.args.get("status", "all").strip().lower()
    is_read: bool | None = None
    if raw_filter == "unread":
        is_read = False
    elif raw_filter == "read":
        is_read = True

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    notif_type = request.args.get("type", "").strip().upper() or None

    result = NotificationService.list_user_notifications(
        user=actor,
        is_read=is_read,
        notification_type=notif_type,
        page=page,
        per_page=per_page,
    )

    # Context shop for header/sidebar navigation if user is assigned to one
    active_shop = None
    shop_id_arg = request.args.get("shop_id", type=int)
    if shop_id_arg:
        try:
            active_shop, _ = verify_shop_access(
                user=actor,
                shop_id=shop_id_arg,
                allow_inactive=True,
                required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
            )
        except AuthorizationError:
            active_shop = None

    if active_shop is None and actor.shop_assignments:
        for a in actor.shop_assignments:
            if a.is_active:
                active_shop = a.shop
                break

    return render_template(
        "notifications/list.html",
        user=actor,
        active_shop=active_shop,
        notifications=result["items"],
        unread_count=result["unread_count"],
        meta=result["meta"],
        current_status=raw_filter,
        current_type=notif_type or "",
    )


@notifications_bp.route("/shops/<int:shop_id>/notifications", methods=["GET"])
def shop_notifications_redirect(shop_id: int):
    """Convenience redirect passing shop context into the notification center."""
    return redirect(url_for("notifications.notification_center_page", shop_id=shop_id))


# ============================================================================
# REST JSON APIs (/api/v1/notifications/*)
# ============================================================================


@notifications_bp.route("/api/v1/notifications", methods=["GET"])
def api_list_notifications():
    """API-036: Return paginated notifications strictly scoped to the authenticated user."""
    actor = verify_authenticated_user()

    is_read, notif_type, page, per_page = validate_notification_query_params(
        raw_is_read=request.args.get("is_read"),
        raw_type=request.args.get("notification_type") or request.args.get("type"),
        raw_page=request.args.get("page"),
        raw_per_page=request.args.get("per_page"),
    )

    result = NotificationService.list_user_notifications(
        user=actor,
        is_read=is_read,
        notification_type=notif_type,
        page=page,
        per_page=per_page,
    )

    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "notifications": result["items"],
                    "unread_count": result["unread_count"],
                },
                "meta": result["meta"],
            }
        ),
        200,
    )


@notifications_bp.route("/api/v1/notifications/unread-count", methods=["GET"])
def api_unread_count():
    """Return the unread notification count for the authenticated user."""
    actor = verify_authenticated_user()
    count = NotificationService.get_unread_count(user=actor)
    return (
        jsonify(
            {
                "success": True,
                "data": {"unread_count": count},
            }
        ),
        200,
    )


@notifications_bp.route("/api/v1/notifications/<int:notification_id>/read", methods=["POST", "PATCH"])
def api_mark_as_read(notification_id: int):
    """API-037: Mark a specific notification as read, enforcing recipient user ownership."""
    actor = verify_authenticated_user()
    notif = NotificationService.mark_as_read(
        user=actor,
        notification_id=notification_id,
    )

    return (
        jsonify(
            {
                "success": True,
                "data": {"notification": notif.to_dict()},
                "message": "Notification marked as read.",
            }
        ),
        200,
    )


@notifications_bp.route("/api/v1/notifications/read-all", methods=["POST"])
def api_mark_all_as_read():
    """Mark all unread notifications for the authenticated user as read."""
    actor = verify_authenticated_user()
    count = NotificationService.mark_all_as_read(user=actor)

    return (
        jsonify(
            {
                "success": True,
                "data": {"marked_count": count},
                "message": f"{count} notifications marked as read.",
            }
        ),
        200,
    )


@notifications_bp.route("/api/v1/shops/<int:shop_id>/inventory/check-alerts", methods=["POST"])
def api_check_shop_inventory_alerts(shop_id: int):
    """Trigger on-demand evaluation of low-stock and near-expiry alerts for a shop."""
    actor = verify_authenticated_user()
    verify_shop_access(
        user=actor,
        shop_id=shop_id,
        allow_inactive=False,
        required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
    )

    created_notifs = NotificationService.check_and_notify_inventory_alerts(
        shop_id=shop_id,
    )

    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "notifications_generated": len(created_notifs),
                    "notifications": [n.to_dict() for n in created_notifs],
                },
                "message": f"Evaluated alerts: {len(created_notifs)} notifications generated.",
            }
        ),
        200,
    )
