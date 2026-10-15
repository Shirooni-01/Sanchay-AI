"""HTML Views and REST JSON API Routes for Batch Inventory & Stock Movements (Phase 3)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import flash, g, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app.blueprints.inventory import inventory_bp
from app.services.catalog_service import CatalogService
from app.services.inventory_service import InventoryService, get_business_today
from app.utils.decorators import require_shop_access, verify_authenticated_user
from app.utils.errors import DomainError, ValidationError


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_optional_int(raw: str | None, field_name: str = "id") -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        val = int(str(raw).strip())
        if val <= 0:
            raise ValueError
        return val
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            message=f"Invalid {field_name} parameter.",
            details={field_name: "Must be a positive integer."},
        ) from exc


# ============================================================================
# HTML UI Routes — Inventory Overview, Receive, Adjust, Batches, Alerts & Ledger
# ============================================================================


@inventory_bp.route("/inventory", methods=["GET"])
@login_required
def inventory_entry_redirect():
    """Redirect user to their primary/first accessible shop's inventory overview."""
    user = verify_authenticated_user()
    if user.organization_id is None:
        flash("Please configure or join an organization first.", "warning")
        return redirect(url_for("admin.organization_page"))

    shops = user.get_accessible_shops(include_inactive=True)
    if not shops:
        flash("Create or get assigned to a shop before viewing inventory.", "info")
        return redirect(url_for("admin.shops_page"))

    primary = user.get_primary_assignment()
    target_shop_id = primary.shop_id if primary else shops[0].id
    return redirect(url_for("inventory.shop_inventory_overview_page", shop_id=target_shop_id))


@inventory_bp.route("/shops/<int:shop_id>/inventory", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def shop_inventory_overview_page(shop_id: int):
    """Render the comprehensive Shop Inventory Overview page."""
    user = verify_authenticated_user()
    shop = g.current_shop
    assignment = g.current_shop_assignment

    alerts_and_kpis = InventoryService.evaluate_shop_inventory_alerts(
        actor=user,
        shop_id=shop_id,
    )
    catalog_result = CatalogService.list_shop_products(
        actor=user,
        shop_id=shop_id,
        page=request.args.get("page", 1),
        per_page=request.args.get("per_page", 25),
    )

    can_adjust_stock = (
        shop.is_operational
        and (
            user.role == "SUPER_ADMIN"
            or (assignment is not None and assignment.assignment_role == "SHOP_OWNER")
        )
    )
    can_receive_stock = shop.is_operational
    accessible_shops = user.get_accessible_shops(include_inactive=True)

    return render_template(
        "inventory/overview.html",
        user=user,
        shop=shop,
        accessible_shops=accessible_shops,
        overview=alerts_and_kpis,
        products=catalog_result["items"],
        meta=catalog_result["meta"],
        can_adjust_stock=can_adjust_stock,
        can_receive_stock=can_receive_stock,
    )


@inventory_bp.route("/shops/<int:shop_id>/inventory/receive", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def receive_stock_page(shop_id: int):
    """Render the manual batch stock receipt page."""
    user = verify_authenticated_user()
    shop = g.current_shop
    selected_product_id = _parse_optional_int(request.args.get("product_id"), "product_id")

    catalog_result = CatalogService.list_shop_products(
        actor=user,
        shop_id=shop_id,
        is_active=True,
        page=1,
        per_page=100,
    )
    batches_result = InventoryService.list_batches(
        actor=user,
        shop_id=shop_id,
        page=1,
        per_page=15,
    )
    accessible_shops = user.get_accessible_shops(include_inactive=True)

    return render_template(
        "inventory/receive.html",
        user=user,
        shop=shop,
        accessible_shops=accessible_shops,
        products=catalog_result["items"],
        recent_batches=batches_result["items"],
        selected_product_id=selected_product_id,
        today_iso=get_business_today().isoformat(),
    )


@inventory_bp.route("/shops/<int:shop_id>/inventory/receive", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def receive_stock_action(shop_id: int):
    """Handle HTML form submission for receiving a new batch."""
    user = verify_authenticated_user()
    payload: dict[str, Any] = {
        k: v for k, v in request.form.items() if k != "csrf_token"
    }

    try:
        batch, _ = InventoryService.receive_batch(
            actor=user,
            shop_id=shop_id,
            payload=payload,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
        flash(
            f"Received {batch.initial_qty} units into batch '{batch.batch_code}'.",
            "success",
        )
        return redirect(
            url_for("inventory.batch_detail_page", shop_id=shop_id, batch_id=batch.id)
        )
    except DomainError as exc:
        flash(exc.message, "error")
        return redirect(url_for("inventory.receive_stock_page", shop_id=shop_id))


@inventory_bp.route("/shops/<int:shop_id>/inventory/adjust", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def adjust_stock_page(shop_id: int):
    """Render the authorized stock adjustment page (Super Admin & Shop Owner only)."""
    user = verify_authenticated_user()
    shop = g.current_shop
    selected_batch_id = _parse_optional_int(request.args.get("batch_id"), "batch_id")

    batches_result = InventoryService.list_batches(
        actor=user,
        shop_id=shop_id,
        page=1,
        per_page=100,
    )
    accessible_shops = user.get_accessible_shops(include_inactive=True)

    return render_template(
        "inventory/adjust.html",
        user=user,
        shop=shop,
        accessible_shops=accessible_shops,
        batches=batches_result["items"],
        selected_batch_id=selected_batch_id,
    )


@inventory_bp.route("/shops/<int:shop_id>/inventory/adjust", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def adjust_stock_action(shop_id: int):
    """Handle HTML form submission for adjusting stock on a selected batch."""
    user = verify_authenticated_user()
    batch_id = _parse_optional_int(request.form.get("batch_id"), "batch_id")
    if batch_id is None:
        flash("Please select a valid batch to adjust.", "error")
        return redirect(url_for("inventory.adjust_stock_page", shop_id=shop_id))

    payload: dict[str, Any] = {
        "movement_type": request.form.get("movement_type"),
        "quantity": request.form.get("quantity"),
        "reason": request.form.get("reason"),
    }

    try:
        batch, _ = InventoryService.adjust_batch(
            actor=user,
            shop_id=shop_id,
            batch_id=batch_id,
            payload=payload,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
        flash(
            f"Stock adjustment recorded for batch '{batch.batch_code}' (Available: {batch.available_qty}).",
            "success",
        )
        return redirect(
            url_for("inventory.batch_detail_page", shop_id=shop_id, batch_id=batch.id)
        )
    except DomainError as exc:
        flash(exc.message, "error")
        return redirect(
            url_for("inventory.adjust_stock_page", shop_id=shop_id, batch_id=batch_id)
        )


@inventory_bp.route("/shops/<int:shop_id>/batches", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def shop_batches_page(shop_id: int):
    """Render the FEFO-ordered Batch Inventory listing page."""
    user = verify_authenticated_user()
    shop = g.current_shop
    assignment = g.current_shop_assignment

    product_id = _parse_optional_int(request.args.get("product_id"), "product_id")
    status_filter = request.args.get("status", "").strip().upper()
    search_query = request.args.get("q", request.args.get("search", "")).strip()
    near_expiry = request.args.get("near_expiry", "").strip().lower() in ("true", "1", "yes")
    expired_only = request.args.get("expired", "").strip().lower() in ("true", "1", "yes")
    page = request.args.get("page", 1)
    per_page = request.args.get("per_page", 20)

    batches_result = InventoryService.list_batches(
        actor=user,
        shop_id=shop_id,
        product_id=product_id,
        status=status_filter or None,
        near_expiry=near_expiry,
        expired_only=expired_only,
        search_query=search_query or None,
        page=page,
        per_page=per_page,
    )
    catalog_result = CatalogService.list_shop_products(
        actor=user,
        shop_id=shop_id,
        page=1,
        per_page=100,
    )

    can_adjust_stock = (
        shop.is_operational
        and (
            user.role == "SUPER_ADMIN"
            or (assignment is not None and assignment.assignment_role == "SHOP_OWNER")
        )
    )
    accessible_shops = user.get_accessible_shops(include_inactive=True)

    return render_template(
        "inventory/batches.html",
        user=user,
        shop=shop,
        accessible_shops=accessible_shops,
        batches=batches_result["items"],
        meta=batches_result["meta"],
        products=catalog_result["items"],
        product_filter=product_id,
        status_filter=status_filter,
        search_query=search_query,
        near_expiry=near_expiry,
        expired_only=expired_only,
        can_adjust_stock=can_adjust_stock,
    )


@inventory_bp.route("/shops/<int:shop_id>/batches/<int:batch_id>", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def batch_detail_page(shop_id: int, batch_id: int):
    """Render the Batch Detail & Ledger History view."""
    user = verify_authenticated_user()
    shop = g.current_shop
    assignment = g.current_shop_assignment

    batch = InventoryService.get_batch_detail(
        actor=user,
        shop_id=shop_id,
        batch_id=batch_id,
    )
    can_adjust_stock = (
        shop.is_operational
        and (
            user.role == "SUPER_ADMIN"
            or (assignment is not None and assignment.assignment_role == "SHOP_OWNER")
        )
    )

    return render_template(
        "inventory/batch_detail.html",
        user=user,
        shop=shop,
        batch=batch,
        can_adjust_stock=can_adjust_stock,
    )


@inventory_bp.route("/shops/<int:shop_id>/batches/<int:batch_id>/adjust-form", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def batch_detail_adjust_action(shop_id: int, batch_id: int):
    """Handle inline stock adjustment submission on the batch detail page."""
    user = verify_authenticated_user()
    payload: dict[str, Any] = {
        "movement_type": request.form.get("movement_type"),
        "quantity": request.form.get("quantity"),
        "reason": request.form.get("reason"),
    }

    try:
        batch, _ = InventoryService.adjust_batch(
            actor=user,
            shop_id=shop_id,
            batch_id=batch_id,
            payload=payload,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
        flash(
            f"Recorded {payload['movement_type']} on batch '{batch.batch_code}'.",
            "success",
        )
    except DomainError as exc:
        flash(exc.message, "error")

    return redirect(
        url_for("inventory.batch_detail_page", shop_id=shop_id, batch_id=batch_id)
    )


@inventory_bp.route("/shops/<int:shop_id>/inventory/expiry", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def expiry_alerts_page(shop_id: int):
    """Render the deterministic Near-Expiry and Expired-Stock view."""
    user = verify_authenticated_user()
    shop = g.current_shop
    assignment = g.current_shop_assignment

    alerts = InventoryService.evaluate_shop_inventory_alerts(
        actor=user,
        shop_id=shop_id,
    )
    can_adjust_stock = (
        shop.is_operational
        and (
            user.role == "SUPER_ADMIN"
            or (assignment is not None and assignment.assignment_role == "SHOP_OWNER")
        )
    )
    accessible_shops = user.get_accessible_shops(include_inactive=True)

    return render_template(
        "inventory/expiry.html",
        user=user,
        shop=shop,
        accessible_shops=accessible_shops,
        alerts=alerts,
        can_adjust_stock=can_adjust_stock,
    )


@inventory_bp.route("/shops/<int:shop_id>/inventory/low-stock", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def low_stock_alerts_page(shop_id: int):
    """Render the deterministic Low-Stock and Out-of-Stock view."""
    user = verify_authenticated_user()
    shop = g.current_shop

    alerts = InventoryService.evaluate_shop_inventory_alerts(
        actor=user,
        shop_id=shop_id,
    )
    accessible_shops = user.get_accessible_shops(include_inactive=True)

    return render_template(
        "inventory/low_stock.html",
        user=user,
        shop=shop,
        accessible_shops=accessible_shops,
        alerts=alerts,
        can_receive_stock=shop.is_operational,
    )


@inventory_bp.route("/shops/<int:shop_id>/stock-movements", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def stock_movements_page(shop_id: int):
    """Render the immutable Stock Movement Ledger history view."""
    user = verify_authenticated_user()
    shop = g.current_shop

    product_id = _parse_optional_int(request.args.get("product_id"), "product_id")
    batch_id = _parse_optional_int(request.args.get("batch_id"), "batch_id")
    movement_type = request.args.get("movement_type", "").strip().upper()
    bucket_affected = request.args.get("bucket_affected", "").strip().upper()
    page = request.args.get("page", 1)
    per_page = request.args.get("per_page", 25)

    result = InventoryService.list_stock_movements(
        actor=user,
        shop_id=shop_id,
        product_id=product_id,
        batch_id=batch_id,
        movement_type=movement_type or None,
        bucket_affected=bucket_affected or None,
        page=page,
        per_page=per_page,
    )
    catalog_result = CatalogService.list_shop_products(
        actor=user,
        shop_id=shop_id,
        page=1,
        per_page=100,
    )
    accessible_shops = user.get_accessible_shops(include_inactive=True)

    return render_template(
        "inventory/movements.html",
        user=user,
        shop=shop,
        accessible_shops=accessible_shops,
        movements=result["items"],
        meta=result["meta"],
        products=catalog_result["items"],
        product_filter=product_id,
        batch_filter=batch_id,
        movement_type_filter=movement_type,
        bucket_filter=bucket_affected,
    )


# ============================================================================
# REST JSON API Routes — Inventory, Batches, Alerts & Ledger (/api/v1/*)
# ============================================================================


@inventory_bp.route("/api/v1/shops/<int:shop_id>/inventory", methods=["GET"])
@inventory_bp.route("/api/v1/shops/<int:shop_id>/inventory/overview", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def api_shop_inventory_overview(shop_id: int):
    """Return reconciled shop inventory overview, KPIs, and deterministic alerts."""
    user = verify_authenticated_user()
    overview = InventoryService.evaluate_shop_inventory_alerts(
        actor=user,
        shop_id=shop_id,
    )
    reconciliation = InventoryService.verify_shop_ledger_reconciliation(shop_id=shop_id)
    overview["reconciliation"] = reconciliation
    return (
        jsonify(
            {
                "success": True,
                "data": overview,
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@inventory_bp.route("/api/v1/shops/<int:shop_id>/batches", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def api_list_shop_batches(shop_id: int):
    """API-016: List batches in FEFO order with product, status, and expiry filters."""
    user = verify_authenticated_user()
    product_id = _parse_optional_int(request.args.get("product_id"), "product_id")
    status = request.args.get("status")
    near_expiry = str(request.args.get("near_expiry", "")).strip().lower() in (
        "true",
        "1",
        "yes",
    )
    expired_only = str(request.args.get("expired", "")).strip().lower() in (
        "true",
        "1",
        "yes",
    )
    search_query = request.args.get("q", request.args.get("search"))
    page = request.args.get("page", 1)
    per_page = request.args.get("per_page", 20)

    result = InventoryService.list_batches(
        actor=user,
        shop_id=shop_id,
        product_id=product_id,
        status=status,
        near_expiry=near_expiry,
        expired_only=expired_only,
        search_query=search_query,
        page=page,
        per_page=per_page,
    )
    meta = {**result["meta"], "timestamp": _utc_iso()}
    return (
        jsonify(
            {
                "success": True,
                "data": {"batches": result["items"]},
                "meta": meta,
            }
        ),
        200,
    )


@inventory_bp.route("/api/v1/shops/<int:shop_id>/batches", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def api_receive_shop_batch(shop_id: int):
    """API-017: Receive a new inventory batch (RECEIPT stock movement)."""
    user = verify_authenticated_user()
    payload = request.get_json(silent=True) or {}
    batch, movement = InventoryService.receive_batch(
        actor=user,
        shop_id=shop_id,
        payload=payload,
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    today = get_business_today()
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "batch": batch.to_dict(ref_date=today),
                    "movement_id": movement.id,
                    "movement": movement.to_dict(),
                },
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        201,
    )


@inventory_bp.route("/api/v1/shops/<int:shop_id>/batches/<int:batch_id>", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def api_get_shop_batch(shop_id: int, batch_id: int):
    """Retrieve a single batch and its stock movement ledger."""
    user = verify_authenticated_user()
    detail = InventoryService.get_batch_detail(
        actor=user,
        shop_id=shop_id,
        batch_id=batch_id,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"batch": detail},
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@inventory_bp.route(
    "/api/v1/shops/<int:shop_id>/batches/<int:batch_id>/adjust",
    methods=["POST"],
)
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def api_adjust_shop_batch(shop_id: int, batch_id: int):
    """API-018: Authorized manual stock adjustment with mandatory reason."""
    user = verify_authenticated_user()
    payload = request.get_json(silent=True) or {}
    batch, movements = InventoryService.adjust_batch(
        actor=user,
        shop_id=shop_id,
        batch_id=batch_id,
        payload=payload,
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    today = get_business_today()
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "batch": batch.to_dict(ref_date=today),
                    "movements": [m.to_dict() for m in movements],
                },
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@inventory_bp.route("/api/v1/shops/<int:shop_id>/alerts/low-stock", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def api_low_stock_alerts(shop_id: int):
    """Return deterministic, deduplicated low-stock and out-of-stock alerts for a shop."""
    user = verify_authenticated_user()
    overview = InventoryService.evaluate_shop_inventory_alerts(
        actor=user,
        shop_id=shop_id,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "shop_id": shop_id,
                    "business_date": overview["business_date"],
                    "low_stock_count": overview["kpis"]["low_stock_count"],
                    "out_of_stock_count": overview["kpis"]["out_of_stock_count"],
                    "items": overview["low_stock_items"],
                },
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@inventory_bp.route("/api/v1/shops/<int:shop_id>/alerts/expiry", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def api_expiry_alerts(shop_id: int):
    """Return deterministic, deduplicated near-expiry and expired batch alerts for a shop."""
    user = verify_authenticated_user()
    overview = InventoryService.evaluate_shop_inventory_alerts(
        actor=user,
        shop_id=shop_id,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "shop_id": shop_id,
                    "business_date": overview["business_date"],
                    "timezone": overview["timezone"],
                    "near_expiry_batch_count": overview["kpis"]["near_expiry_batch_count"],
                    "expired_batch_count": overview["kpis"]["expired_batch_count"],
                    "near_expiry_batches": overview["near_expiry_batches"],
                    "expired_batches": overview["expired_batches"],
                },
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@inventory_bp.route("/api/v1/shops/<int:shop_id>/stock-movements", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def api_list_stock_movements(shop_id: int):
    """API-019: Paginated immutable ledger of stock movements."""
    user = verify_authenticated_user()
    product_id = _parse_optional_int(request.args.get("product_id"), "product_id")
    batch_id = _parse_optional_int(request.args.get("batch_id"), "batch_id")
    movement_type = request.args.get("movement_type")
    bucket_affected = request.args.get("bucket_affected")
    page = request.args.get("page", 1)
    per_page = request.args.get("per_page", 25)

    result = InventoryService.list_stock_movements(
        actor=user,
        shop_id=shop_id,
        product_id=product_id,
        batch_id=batch_id,
        movement_type=movement_type,
        bucket_affected=bucket_affected,
        page=page,
        per_page=per_page,
    )
    meta = {**result["meta"], "timestamp": _utc_iso()}
    return (
        jsonify(
            {
                "success": True,
                "data": {"movements": result["items"]},
                "meta": meta,
            }
        ),
        200,
    )
