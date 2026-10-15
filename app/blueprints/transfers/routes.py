"""Routes and REST API endpoints for Inter-Store Stock Exchange & Transfers (Phase 6).

HTML Views:
- GET  /transfers                                         : Redirect to primary/accessible shop transfers
- GET  /shops/<shop_id>/transfers                         : Shop transfer management hub (incoming/outgoing)
- GET  /shops/<shop_id>/transfers/new                     : Propose new inter-store transfer form
- GET  /shops/<shop_id>/transfers/<transfer_id>           : Transfer detail, audit timeline, action controls
- POST /shops/<shop_id>/transfers                         : HTML form action to create proposal
- POST /shops/<shop_id>/transfers/<transfer_id>/approve   : HTML form action to approve & reserve stock
- POST /shops/<shop_id>/transfers/<transfer_id>/reject    : HTML form action to reject transfer
- POST /shops/<shop_id>/transfers/<transfer_id>/cancel    : HTML form action to cancel transfer
- POST /shops/<shop_id>/transfers/<transfer_id>/dispatch  : HTML form action to dispatch stock
- POST /shops/<shop_id>/transfers/<transfer_id>/receive   : HTML form action to confirm physical receipt

REST APIs:
- GET  /api/v1/shops/<shop_id>/transfer-recipients        : Eligible peer shops with stock & demand indicators
- GET  /api/v1/shops/<shop_id>/network-surplus-offers     (API-026) : Peer surplus available for transfer
- POST /api/v1/transfers                                  (API-027) : Create transfer proposal
- GET  /api/v1/transfers                                  (API-028) : List transfers with filters
- GET  /api/v1/transfers/<transfer_id>                    (API-029) : Get transfer details & status logs
- POST /api/v1/transfers/<transfer_id>/approve            (API-030) : Approve proposal & reserve stock
- POST /api/v1/transfers/<transfer_id>/reject             (API-031) : Reject proposal & release reservation
- POST /api/v1/transfers/<transfer_id>/cancel             (API-032) : Cancel proposal & release reservation
- POST /api/v1/transfers/<transfer_id>/dispatch           (API-033) : Confirm dispatch (in-transit)
- POST /api/v1/transfers/<transfer_id>/confirm-receipt    (API-034) : Confirm receipt & execute atomic transfer
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from flask import flash, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.transfers import transfers_bp
from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch
from app.models.organization import Shop
from app.models.transfer import TransferRequest
from app.services.inventory_service import get_business_today
from app.services.surplus_service import StockAnalysisService
from app.services.transfer_service import TransferService
from app.utils.decorators import require_shop_access, verify_authenticated_user, verify_shop_access
from app.utils.errors import DomainError, ResourceConflictError, ResourceNotFoundError
from app.utils.validators import (
    VALID_TRANSFER_DIRECTIONS,
    VALID_TRANSFER_STATUSES,
    validate_pagination_params,
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client_ip() -> str | None:
    if request.headers.get("X-Forwarded-For"):
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr


def _user_agent() -> str | None:
    return request.user_agent.string if request.user_agent else None


def _get_primary_shop_id() -> int | None:
    """Resolve active shop for current user."""
    if not current_user.is_authenticated:
        return None
    if current_user.role == "SUPER_ADMIN":
        first_shop = db.session.query(Shop).filter(Shop.status == "ACTIVE").first()
        return first_shop.id if first_shop else None
    primary_assignment = next(
        (a for a in current_user.shop_assignments if a.is_primary and a.is_active),
        None,
    )
    if primary_assignment:
        return primary_assignment.shop_id
    active_assignment = next(
        (a for a in current_user.shop_assignments if a.is_active),
        None,
    )
    return active_assignment.shop_id if active_assignment else None


# ============================================================================
# HTML Web Views
# ============================================================================


@transfers_bp.route("/transfers", methods=["GET"])
@login_required
def transfers_hub_redirect():
    """Redirect to user's primary/active shop transfer dashboard."""
    shop_id = _get_primary_shop_id()
    if not shop_id:
        flash("No active shop found for transfers.", "warning")
        return redirect(url_for("admin.dashboard"))
    return redirect(url_for("transfers.shop_transfers_list", shop_id=shop_id))


@transfers_bp.route("/shops/<int:shop_id>/transfers", methods=["GET"])
@login_required
@require_shop_access(required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"})
def shop_transfers_list(shop_id: int):
    """Transfer management hub showing incoming, outgoing, pending, and completed transfers."""
    shop = g.current_shop
    direction = request.args.get("direction", "all").strip().lower()
    if direction not in VALID_TRANSFER_DIRECTIONS:
        direction = "all"
    status_filter = request.args.get("status", "").strip().upper() or None
    page, per_page = validate_pagination_params(
        request.args.get("page"),
        request.args.get("per_page"),
        default_per_page=20,
    )

    transfers, total = TransferService.list_transfers(
        actor=current_user,
        shop_id=shop.id,
        direction=direction,
        status=status_filter,
        page=page,
        per_page=per_page,
    )

    # Calculate pending action count for current user
    pending_incoming = (
        db.session.query(TransferRequest)
        .filter(
            TransferRequest.recipient_shop_id == shop.id,
            TransferRequest.status == "PENDING_RECIPIENT_APPROVAL",
        )
        .count()
    )
    pending_outgoing = (
        db.session.query(TransferRequest)
        .filter(
            TransferRequest.source_shop_id == shop.id,
            TransferRequest.status == "PENDING_SOURCE_APPROVAL",
        )
        .count()
    )
    ready_to_dispatch = (
        db.session.query(TransferRequest)
        .filter(
            TransferRequest.source_shop_id == shop.id,
            TransferRequest.status == "APPROVED_RESERVED",
        )
        .count()
    )
    in_transit_incoming = (
        db.session.query(TransferRequest)
        .filter(
            TransferRequest.recipient_shop_id == shop.id,
            TransferRequest.status == "DISPATCHED_IN_TRANSIT",
        )
        .count()
    )

    return render_template(
        "transfers/list.html",
        shop=shop,
        transfers=transfers,
        total=total,
        page=page,
        per_page=per_page,
        direction=direction,
        status_filter=status_filter,
        pending_incoming=pending_incoming,
        pending_outgoing=pending_outgoing,
        ready_to_dispatch=ready_to_dispatch,
        in_transit_incoming=in_transit_incoming,
        today=get_business_today(),
    )


@transfers_bp.route("/shops/<int:shop_id>/transfers/recommendations", methods=["GET"])
@login_required
@require_shop_access(required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"})
def shop_redistribution_recommendations_page(shop_id: int):
    """Render the Automated Multi-Shop Stock Redistribution Dashboard (PRD Module F)."""
    shop = g.current_shop
    user = current_user
    accessible_shops = user.get_accessible_shops(include_inactive=False)

    max_dist = request.args.get("max_dist", default=100.0, type=float)
    recommendations = TransferService.get_redistribution_recommendations(
        actor=user,
        shop_id=shop.id,
        max_distance_km=max_dist,
    )

    return render_template(
        "transfers/redistribution_recommendations.html",
        shop=shop,
        accessible_shops=accessible_shops,
        recommendations=recommendations,
        max_dist=max_dist,
    )


@transfers_bp.route("/shops/<int:shop_id>/transfers/new", methods=["GET"])
@login_required
@require_shop_access(required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"})
def new_transfer_form(shop_id: int):
    """Propose new transfer form with recipient shop and active batch selection."""
    shop = g.current_shop
    today = get_business_today()

    # Recipient peer shops
    eligible_recipients = TransferService.get_eligible_recipients(
        actor=current_user,
        source_shop_id=shop.id,
    )

    # Active source batches that have sellable stock
    batches = (
        db.session.query(InventoryBatch)
        .join(Product, InventoryBatch.product_id == Product.id)
        .filter(
            InventoryBatch.shop_id == shop.id,
            InventoryBatch.status == "ACTIVE",
            InventoryBatch.available_qty > 0,
            Product.is_active.is_(True),
        )
        .order_by(Product.name.asc(), InventoryBatch.expiry_date.asc())
        .all()
    )

    # Filter out expired batches
    valid_batches = [b for b in batches if not b.is_expired_on(today)]

    # Pre-selection query parameters (if initiated from surplus/recommendations view)
    preselected_batch_id = request.args.get("batch_id", type=int)
    preselected_recipient_id = request.args.get("recipient_shop_id", type=int)
    preselected_qty = request.args.get("quantity", type=int)

    return render_template(
        "transfers/create.html",
        shop=shop,
        recipients=eligible_recipients,
        batches=valid_batches,
        preselected_batch_id=preselected_batch_id,
        preselected_recipient_id=preselected_recipient_id,
        preselected_qty=preselected_qty,
        today=today,
    )


@transfers_bp.route("/shops/<int:shop_id>/transfers", methods=["POST"])
@login_required
@require_shop_access(required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"})
def create_transfer_form_action(shop_id: int):
    """Handle HTML form submission to propose a transfer."""
    shop = g.current_shop
    payload = {
        "source_shop_id": shop.id,
        "recipient_shop_id": request.form.get("recipient_shop_id"),
        "product_id": request.form.get("product_id"),
        "source_batch_id": request.form.get("source_batch_id"),
        "quantity": request.form.get("quantity"),
        "unit_transfer_price": request.form.get("unit_transfer_price"),
        "offer_duration_days": request.form.get("offer_duration_days") or 7,
        "notes": request.form.get("notes"),
    }

    # If product_id not directly provided in form, resolve it from batch
    if not payload["product_id"] and payload["source_batch_id"]:
        try:
            b_id = int(payload["source_batch_id"])
            b = db.session.query(InventoryBatch).get(b_id)
            if b:
                payload["product_id"] = b.product_id
        except (ValueError, TypeError):
            pass

    try:
        transfer = TransferService.create_transfer_proposal(
            actor=current_user,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_user_agent(),
        )
        flash(
            f"Transfer proposal {transfer.transfer_code} successfully created! Awaiting counterparty approval.",
            "success",
        )
        return redirect(
            url_for("transfers.transfer_detail_page", shop_id=shop.id, transfer_id=transfer.id)
        )
    except DomainError as exc:
        flash(f"Failed to propose transfer: {exc.message}", "danger")
        return redirect(url_for("transfers.new_transfer_form", shop_id=shop.id))


@transfers_bp.route("/shops/<int:shop_id>/transfers/<int:transfer_id>", methods=["GET"])
@login_required
@require_shop_access(required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"})
def transfer_detail_page(shop_id: int, transfer_id: int):
    """View transfer details, batch info, status audit timeline, and action buttons."""
    shop = g.current_shop
    transfer = TransferService.get_transfer(actor=current_user, transfer_id=transfer_id)

    # Determine user permissions relative to this transfer
    is_admin = current_user.role == "SUPER_ADMIN"
    user_roles_by_shop = {
        a.shop_id: a.assignment_role
        for a in current_user.shop_assignments
        if a.is_active
    }
    is_source_owner = is_admin or user_roles_by_shop.get(transfer.source_shop_id) == "SHOP_OWNER"
    is_recipient_owner = is_admin or user_roles_by_shop.get(transfer.recipient_shop_id) == "SHOP_OWNER"

    can_approve = False
    if transfer.status == "PENDING_RECIPIENT_APPROVAL" and is_recipient_owner:
        can_approve = True
    elif transfer.status == "PENDING_SOURCE_APPROVAL" and is_source_owner:
        can_approve = True

    can_reject = False
    if transfer.status in ("PENDING_RECIPIENT_APPROVAL", "PENDING_SOURCE_APPROVAL"):
        if is_source_owner or is_recipient_owner:
            can_reject = True

    can_cancel = False
    if transfer.status in ("PENDING_RECIPIENT_APPROVAL", "PENDING_SOURCE_APPROVAL", "APPROVED_RESERVED"):
        if is_source_owner or is_recipient_owner or transfer.initiated_by_user_id == current_user.id:
            can_cancel = True

    can_dispatch = (transfer.status == "APPROVED_RESERVED") and is_source_owner
    can_receive = (transfer.status == "DISPATCHED_IN_TRANSIT") and is_recipient_owner

    return render_template(
        "transfers/detail.html",
        shop=shop,
        transfer=transfer,
        can_approve=can_approve,
        can_reject=can_reject,
        can_cancel=can_cancel,
        can_dispatch=can_dispatch,
        can_receive=can_receive,
        is_source_owner=is_source_owner,
        is_recipient_owner=is_recipient_owner,
        today=get_business_today(),
    )


@transfers_bp.route("/shops/<int:shop_id>/transfers/<int:transfer_id>/approve", methods=["POST"])
@login_required
def approve_transfer_form_action(shop_id: int, transfer_id: int):
    """Handle HTML form action to approve transfer and reserve stock."""
    payload = {"notes": request.form.get("notes")}
    try:
        transfer = TransferService.approve_transfer(
            actor=current_user,
            transfer_id=transfer_id,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_user_agent(),
        )
        flash(
            f"Transfer {transfer.transfer_code} approved! Stock reserved in source shop.",
            "success",
        )
    except DomainError as exc:
        flash(f"Approval failed: {exc.message}", "danger")
    return redirect(
        url_for("transfers.transfer_detail_page", shop_id=shop_id, transfer_id=transfer_id)
    )


@transfers_bp.route("/shops/<int:shop_id>/transfers/<int:transfer_id>/reject", methods=["POST"])
@login_required
def reject_transfer_form_action(shop_id: int, transfer_id: int):
    """Handle HTML form action to reject transfer."""
    payload = {"reason": request.form.get("reason")}
    try:
        transfer = TransferService.reject_transfer(
            actor=current_user,
            transfer_id=transfer_id,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_user_agent(),
        )
        flash(f"Transfer {transfer.transfer_code} rejected.", "info")
    except DomainError as exc:
        flash(f"Rejection failed: {exc.message}", "danger")
    return redirect(
        url_for("transfers.transfer_detail_page", shop_id=shop_id, transfer_id=transfer_id)
    )


@transfers_bp.route("/shops/<int:shop_id>/transfers/<int:transfer_id>/cancel", methods=["POST"])
@login_required
def cancel_transfer_form_action(shop_id: int, transfer_id: int):
    """Handle HTML form action to cancel transfer."""
    payload = {"reason": request.form.get("reason")}
    try:
        transfer = TransferService.cancel_transfer(
            actor=current_user,
            transfer_id=transfer_id,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_user_agent(),
        )
        flash(f"Transfer {transfer.transfer_code} cancelled and stock released.", "info")
    except DomainError as exc:
        flash(f"Cancellation failed: {exc.message}", "danger")
    return redirect(
        url_for("transfers.transfer_detail_page", shop_id=shop_id, transfer_id=transfer_id)
    )


@transfers_bp.route("/shops/<int:shop_id>/transfers/<int:transfer_id>/dispatch", methods=["POST"])
@login_required
def dispatch_transfer_form_action(shop_id: int, transfer_id: int):
    """Handle HTML form action to confirm physical handover/dispatch."""
    payload = {
        "dispatch_notes": request.form.get("dispatch_notes"),
        "carrier_reference": request.form.get("carrier_reference"),
    }
    try:
        transfer = TransferService.dispatch_transfer(
            actor=current_user,
            transfer_id=transfer_id,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_user_agent(),
        )
        flash(f"Transfer {transfer.transfer_code} dispatched in transit!", "success")
    except DomainError as exc:
        flash(f"Dispatch failed: {exc.message}", "danger")
    return redirect(
        url_for("transfers.transfer_detail_page", shop_id=shop_id, transfer_id=transfer_id)
    )


@transfers_bp.route("/shops/<int:shop_id>/transfers/<int:transfer_id>/receive", methods=["POST"])
@login_required
def receive_transfer_form_action(shop_id: int, transfer_id: int):
    """Handle HTML form action to confirm receipt and atomically move stock."""
    payload = {
        "receipt_notes": request.form.get("receipt_notes"),
        "received_good_qty": request.form.get("received_good_qty") or None,
        "transit_damaged_qty": request.form.get("transit_damaged_qty") or 0,
    }
    try:
        transfer = TransferService.confirm_receipt(
            actor=current_user,
            transfer_id=transfer_id,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_user_agent(),
        )
        flash(
            f"Transfer {transfer.transfer_code} completed! Stock added to recipient shop inventory.",
            "success",
        )
    except DomainError as exc:
        flash(f"Receipt confirmation failed: {exc.message}", "danger")
    return redirect(
        url_for("transfers.transfer_detail_page", shop_id=shop_id, transfer_id=transfer_id)
    )


# ============================================================================
# REST APIs (Module E2)
# ============================================================================


@transfers_bp.route("/api/v1/shops/<int:shop_id>/transfer-recipients", methods=["GET"])
def api_get_transfer_recipients(shop_id: int):
    """List eligible recipient sister shops with stock and replenishment indicators."""
    actor = verify_authenticated_user()
    product_id = request.args.get("product_id", type=int)

    recipients = TransferService.get_eligible_recipients(
        actor=actor,
        source_shop_id=shop_id,
        product_id=product_id,
    )
    return jsonify({"success": True, "data": {"recipients": recipients}}), 200


@transfers_bp.route("/api/v1/shops/<int:shop_id>/network-surplus-offers", methods=["GET"])
def api_get_network_surplus_offers(shop_id: int):
    """API-026: Scoped view (OD-003) of surplus stock in sister shops matching demand."""
    actor = verify_authenticated_user()
    source_shop, _ = verify_shop_access(
        user=actor,
        shop_id=shop_id,
        allow_inactive=False,
        required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
    )

    # Find sister shops
    sister_shops = (
        db.session.query(Shop)
        .filter(
            Shop.organization_id == source_shop.organization_id,
            Shop.id != source_shop.id,
            Shop.status == "ACTIVE",
        )
        .all()
    )

    today = get_business_today()
    surplus_offers: list[dict[str, Any]] = []

    for sister in sister_shops:
        # Get surplus batches in sister shop
        batches = (
            db.session.query(InventoryBatch)
            .join(Product, InventoryBatch.product_id == Product.id)
            .filter(
                InventoryBatch.shop_id == sister.id,
                InventoryBatch.status == "ACTIVE",
                InventoryBatch.available_qty > 0,
                Product.is_active.is_(True),
            )
            .order_by(InventoryBatch.expiry_date.asc())
            .all()
        )

        for b in batches:
            if b.is_expired_on(today):
                continue
            days_left = b.days_to_expiry_on(today)
            surplus_offers.append(
                {
                    "source_shop_id": sister.id,
                    "source_shop_name": sister.name,
                    "product_id": b.product_id,
                    "product_sku": b.product.sku,
                    "product_name": b.product.name,
                    "product_unit": b.product.unit,
                    "source_batch_id": b.id,
                    "batch_code": b.batch_code,
                    "available_qty": b.available_qty,
                    "unit_cost": float(b.unit_cost or 0),
                    "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
                    "days_to_expiry": days_left,
                    "is_near_expiry": b.is_near_expiry_on(today, 15),
                }
            )

    return jsonify({"success": True, "data": {"surplus_offers": surplus_offers}}), 200


@transfers_bp.route("/api/v1/shops/<int:shop_id>/transfers/redistribution-recommendations", methods=["GET"])
@login_required
@require_shop_access(required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"})
def api_redistribution_recommendations(shop_id: int):
    """API: Return multi-shop stock balancing recommendations (PRD Module F)."""
    max_dist = request.args.get("max_distance_km", default=100.0, type=float)
    recs = TransferService.get_redistribution_recommendations(
        actor=current_user,
        shop_id=shop_id,
        max_distance_km=max_dist,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "shop_id": shop_id,
                    "count": len(recs),
                    "recommendations": recs,
                },
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@transfers_bp.route("/api/v1/transfers", methods=["POST"])
def api_create_transfer():
    """API-027: Create an inter-store transfer proposal."""
    actor = verify_authenticated_user()
    payload = request.get_json(silent=True) or {}

    transfer = TransferService.create_transfer_proposal(
        actor=actor,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_user_agent(),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"transfer": transfer.to_dict()},
                "message": f"Transfer proposal {transfer.transfer_code} created successfully.",
            }
        ),
        201,
    )


@transfers_bp.route("/api/v1/transfers", methods=["GET"])
def api_list_transfers():
    """API-028: List transfers scoped to authorized shops with filters."""
    actor = verify_authenticated_user()
    shop_id = request.args.get("shop_id", type=int)
    direction = request.args.get("direction", "all").strip().lower()
    status = request.args.get("status", "").strip().upper() or None
    product_id = request.args.get("product_id", type=int)
    page, per_page = validate_pagination_params(
        request.args.get("page"),
        request.args.get("per_page"),
        default_per_page=20,
    )

    transfers, total = TransferService.list_transfers(
        actor=actor,
        shop_id=shop_id,
        direction=direction,
        status=status,
        product_id=product_id,
        page=page,
        per_page=per_page,
    )

    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "transfers": [t.to_dict() for t in transfers],
                    "total": total,
                    "page": page,
                    "per_page": per_page,
                },
            }
        ),
        200,
    )


@transfers_bp.route("/api/v1/transfers/<int:transfer_id>", methods=["GET"])
def api_get_transfer(transfer_id: int):
    """API-029: Get transfer details and status timeline logs."""
    actor = verify_authenticated_user()
    transfer = TransferService.get_transfer(actor=actor, transfer_id=transfer_id)

    status_logs = [log.to_dict() for log in transfer.status_logs]
    data = transfer.to_dict()
    data["status_logs"] = status_logs

    return jsonify({"success": True, "data": {"transfer": data}}), 200


@transfers_bp.route("/api/v1/transfers/<int:transfer_id>/approve", methods=["POST"])
def api_approve_transfer(transfer_id: int):
    """API-030: Record counterparty approval, transition to APPROVED_RESERVED, and reserve stock."""
    actor = verify_authenticated_user()
    payload = request.get_json(silent=True) or {}

    transfer = TransferService.approve_transfer(
        actor=actor,
        transfer_id=transfer_id,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_user_agent(),
    )

    src_batch = transfer.source_batch
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "transfer": transfer.to_dict(),
                    "source_batch_balance": {
                        "batch_id": src_batch.id,
                        "available_qty": src_batch.available_qty,
                        "reserved_qty": src_batch.reserved_qty,
                    },
                },
                "message": f"Transfer {transfer.transfer_code} approved and stock reserved.",
            }
        ),
        200,
    )


@transfers_bp.route("/api/v1/transfers/<int:transfer_id>/reject", methods=["POST"])
def api_reject_transfer(transfer_id: int):
    """API-031: Reject transfer proposal and release any reserved stock."""
    actor = verify_authenticated_user()
    payload = request.get_json(silent=True) or {}

    transfer = TransferService.reject_transfer(
        actor=actor,
        transfer_id=transfer_id,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_user_agent(),
    )

    return (
        jsonify(
            {
                "success": True,
                "data": {"transfer": transfer.to_dict()},
                "message": f"Transfer {transfer.transfer_code} rejected.",
            }
        ),
        200,
    )


@transfers_bp.route("/api/v1/transfers/<int:transfer_id>/cancel", methods=["POST"])
def api_cancel_transfer(transfer_id: int):
    """API-032: Cancel transfer prior to dispatch and release reserved stock."""
    actor = verify_authenticated_user()
    payload = request.get_json(silent=True) or {}

    transfer = TransferService.cancel_transfer(
        actor=actor,
        transfer_id=transfer_id,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_user_agent(),
    )

    return (
        jsonify(
            {
                "success": True,
                "data": {"transfer": transfer.to_dict()},
                "message": f"Transfer {transfer.transfer_code} cancelled.",
            }
        ),
        200,
    )


@transfers_bp.route("/api/v1/transfers/<int:transfer_id>/dispatch", methods=["POST"])
def api_dispatch_transfer(transfer_id: int):
    """API-033: Confirm physical dispatch of reserved stock (DISPATCHED_IN_TRANSIT)."""
    actor = verify_authenticated_user()
    payload = request.get_json(silent=True) or {}

    transfer = TransferService.dispatch_transfer(
        actor=actor,
        transfer_id=transfer_id,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_user_agent(),
    )

    return (
        jsonify(
            {
                "success": True,
                "data": {"transfer": transfer.to_dict()},
                "message": f"Transfer {transfer.transfer_code} confirmed in transit.",
            }
        ),
        200,
    )


@transfers_bp.route("/api/v1/transfers/<int:transfer_id>/confirm-receipt", methods=["POST"])
def api_confirm_receipt(transfer_id: int):
    """API-034: Confirm physical receipt and atomically complete transfer & stock movements."""
    actor = verify_authenticated_user()
    payload = request.get_json(silent=True) or {}

    transfer = TransferService.confirm_receipt(
        actor=actor,
        transfer_id=transfer_id,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_user_agent(),
    )

    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "transfer": transfer.to_dict(),
                    "recipient_batch_id": transfer.recipient_batch_id,
                },
                "message": f"Transfer {transfer.transfer_code} confirmed received and completed.",
            }
        ),
        200,
    )
