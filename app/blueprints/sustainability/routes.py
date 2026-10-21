"""HTTP and REST JSON API routes for Carbon Credits & Sustainability Leaderboard."""

from __future__ import annotations

import logging
from typing import Any

from flask import (
    jsonify,
    render_template,
    request,
)
from flask_login import current_user, login_required

from app.blueprints.sustainability import sustainability_bp
from app.models.catalog import Product
from app.models.organization import Shop
from app.models.sustainability import EmissionFactor
from app.models.user import User
from app.services.org_service import ShopService
from app.services.sustainability_service import (
    ALL_FOOD_CATEGORIES,
    SustainabilityService,
)
from app.utils.decorators import (
    verify_authenticated_user,
    verify_shop_access,
)
from app.utils.errors import (
    AuthorizationError,
    DomainError,
    ResourceNotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# HTML VIEWS: Sustainability & Carbon Credits Dashboard
# ==============================================================================


@sustainability_bp.route("/sustainability", methods=["GET"])
@sustainability_bp.route("/carbon-credits", methods=["GET"])
@login_required
def dashboard_page():
    """Render the responsive Carbon Credits & Sustainability Leaderboard dashboard."""
    user: User = current_user._get_current_object()

    # Determine accessible shops
    if user.role == "SUPER_ADMIN":
        accessible_shops = (
            Shop.query.filter_by(organization_id=user.organization_id, status="ACTIVE")
            .order_by(Shop.name.asc())
            .all()
        )
    else:
        assigned_shop_ids = [a.shop_id for a in user.shop_assignments if a.is_active]
        accessible_shops = (
            Shop.query.filter(Shop.id.in_(assigned_shop_ids), Shop.status == "ACTIVE")
            .order_by(Shop.name.asc())
            .all()
        )

    # Selected shop filter from query param
    raw_shop_id = request.args.get("shop_id")
    selected_shop_id = int(raw_shop_id) if raw_shop_id and raw_shop_id.strip() else None

    # Validate shop access if specific shop selected
    if selected_shop_id:
        try:
            verify_shop_access(user=user, shop_id=selected_shop_id)
        except DomainError:
            selected_shop_id = None

    # Default to user's primary shop if not SUPER_ADMIN and no shop param
    if not selected_shop_id and user.role != "SUPER_ADMIN" and accessible_shops:
        selected_shop_id = accessible_shops[0].id

    # Gather data from service
    summary = SustainabilityService.get_dashboard_summary(
        actor=user,
        shop_id=selected_shop_id,
    )

    leaderboard_data = SustainabilityService.get_organization_leaderboard(
        actor=user,
        current_shop_id=selected_shop_id,
        page=1,
        per_page=20,
    )

    history_data = SustainabilityService.get_sustainability_history(
        actor=user,
        shop_id=selected_shop_id,
        page=1,
        per_page=15,
    )

    category_breakdown = SustainabilityService.get_category_breakdown(
        actor=user,
        shop_id=selected_shop_id,
    )

    return render_template(
        "sustainability/dashboard.html",
        user=user,
        shops=accessible_shops,
        selected_shop_id=selected_shop_id,
        summary=summary,
        leaderboard=leaderboard_data["leaderboard"],
        events=history_data["events"],
        history_total=history_data["total_items"],
        category_breakdown=category_breakdown,
        all_categories=ALL_FOOD_CATEGORIES,
    )


# ==============================================================================
# REST API ENDPOINTS (/api/v1/sustainability/*)
# ==============================================================================


@sustainability_bp.route("/api/v1/sustainability/dashboard", methods=["GET"])
@login_required
def api_dashboard_summary():
    """REST endpoint for carbon credits summary cards."""
    user: User = current_user._get_current_object()
    raw_shop_id = request.args.get("shop_id")
    shop_id = int(raw_shop_id) if raw_shop_id and raw_shop_id.strip() else None

    try:
        data = SustainabilityService.get_dashboard_summary(
            actor=user,
            shop_id=shop_id,
        )
        return jsonify({"success": True, "data": data}), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@sustainability_bp.route("/api/v1/sustainability/leaderboard", methods=["GET"])
@login_required
def api_leaderboard():
    """REST endpoint for the organization sustainability leaderboard."""
    user: User = current_user._get_current_object()
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    raw_shop_id = request.args.get("current_shop_id")
    current_shop_id = int(raw_shop_id) if raw_shop_id and raw_shop_id.strip() else None

    try:
        data = SustainabilityService.get_organization_leaderboard(
            actor=user,
            current_shop_id=current_shop_id,
            page=page,
            per_page=per_page,
        )
        return jsonify({"success": True, "data": data}), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@sustainability_bp.route("/api/v1/sustainability/history", methods=["GET"])
@login_required
def api_sustainability_history():
    """REST endpoint for paginated sustainability events history."""
    user: User = current_user._get_current_object()
    raw_shop_id = request.args.get("shop_id")
    shop_id = int(raw_shop_id) if raw_shop_id and raw_shop_id.strip() else None
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    status_filter = request.args.get("status")
    category_filter = request.args.get("category")

    try:
        data = SustainabilityService.get_sustainability_history(
            actor=user,
            shop_id=shop_id,
            page=page,
            per_page=per_page,
            status_filter=status_filter,
            category_filter=category_filter,
        )
        return jsonify({"success": True, "data": data}), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@sustainability_bp.route("/api/v1/sustainability/breakdown", methods=["GET"])
@login_required
def api_category_breakdown():
    """REST endpoint for environmental impact broken down by food category."""
    user: User = current_user._get_current_object()
    raw_shop_id = request.args.get("shop_id")
    shop_id = int(raw_shop_id) if raw_shop_id and raw_shop_id.strip() else None

    try:
        breakdown = SustainabilityService.get_category_breakdown(
            actor=user,
            shop_id=shop_id,
        )
        return jsonify({"success": True, "data": {"categories": breakdown}}), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@sustainability_bp.route("/api/v1/sustainability/events/<int:event_id>", methods=["GET"])
@login_required
def api_event_details(event_id: int):
    """REST endpoint for full event calculation details and audit trail."""
    user: User = current_user._get_current_object()

    try:
        details = SustainabilityService.get_event_details(actor=user, event_id=event_id)
        return jsonify({"success": True, "data": details}), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@sustainability_bp.route("/api/v1/sustainability/factors", methods=["GET"])
@login_required
def api_emission_factors():
    """REST endpoint listing active documented reference emission factors."""
    category = request.args.get("category")
    scope = request.args.get("scope", "WASTE_PREVENTION")

    query = EmissionFactor.query.filter_by(is_active=True, factor_scope=scope)
    if category:
        query = query.filter(EmissionFactor.category.ilike(f"%{category}%"))

    factors = query.order_by(EmissionFactor.category.asc(), EmissionFactor.subcategory.asc()).all()
    return jsonify({
        "success": True,
        "data": {
            "factors": [f.to_dict() for f in factors],
            "supported_categories": ALL_FOOD_CATEGORIES,
            "methodology_version": "v1.0.0-fao-warm",
        },
    }), 200


@sustainability_bp.route("/api/v1/sustainability/process-transfer/<int:transfer_id>", methods=["POST"])
@login_required
def api_process_transfer(transfer_id: int):
    """Idempotently evaluate and award sustainability credits for a completed stock transfer."""
    user: User = current_user._get_current_object()

    try:
        event = SustainabilityService.process_transfer_sustainability_event(
            actor=user,
            transfer_id=transfer_id,
        )
        return jsonify({
            "success": True,
            "data": event.to_dict(),
            "message": f"Sustainability event recorded: {event.net_estimated_impact} kg CO2e, {event.reward_points_awarded} points.",
        }), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@sustainability_bp.route("/api/v1/sustainability/reversals/<int:event_id>", methods=["POST"])
@login_required
def api_reverse_event(event_id: int):
    """Super Admin endpoint to record auditable reversal of a sustainability event."""
    user: User = current_user._get_current_object()
    body = request.get_json(silent=True) or {}
    reason = body.get("reason", "").strip() or "Administrative reversal"

    try:
        event = SustainabilityService.reverse_sustainability_event(
            actor=user,
            event_id=event_id,
            reason=reason,
        )
        return jsonify({
            "success": True,
            "data": event.to_dict(),
            "message": f"Sustainability event #{event_id} successfully reversed.",
        }), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )
