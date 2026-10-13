"""Routes and REST API endpoints for POS Billing, Sales History, Receipts, and Returns (Phase 4).

HTML Views:
- GET  /pos                                         : Redirect to primary/accessible shop POS counter
- GET  /sales                                       : Redirect to primary/accessible shop sales history
- GET  /shops/<shop_id>/pos                         : Interactive POS Billing Counter
- POST /shops/<shop_id>/pos/checkout                : Form fallback for POS checkout
- GET  /shops/<shop_id>/sales                       : Shop sales history & financial summary
- GET  /shops/<shop_id>/sales/<sale_id>             : Sale invoice detail, returns & cancellation
- GET  /shops/<shop_id>/sales/<sale_id>/receipt     : Print-friendly customer receipt
- POST /shops/<shop_id>/sales/<sale_id>/cancel      : Form action to cancel same-day sale
- POST /shops/<shop_id>/sales/<sale_id>/returns     : Form action to process item return

REST APIs:
- POST   /api/v1/shops/<shop_id>/sales                      (API-020)
- GET    /api/v1/shops/<shop_id>/sales                      (API-021)
- GET    /api/v1/shops/<shop_id>/sales/summary              (API-021b)
- GET    /api/v1/shops/<shop_id>/sales/<sale_id>            (API-022)
- GET    /api/v1/shops/<shop_id>/sales/<sale_id>/receipt    (API-022b)
- POST   /api/v1/shops/<shop_id>/sales/<sale_id>/cancel     (API-023)
- POST   /api/v1/shops/<shop_id>/sales/<sale_id>/returns    (API-024)
- DELETE /api/v1/shops/<shop_id>/sales/<sale_id>            (409 DESTRUCTIVE_DELETE_PROHIBITED)
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
import uuid

from flask import flash, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.sales import sales_bp
from app.services.catalog_service import CatalogService
from app.services.inventory_service import get_business_today
from app.services.sales_service import SalesService
from app.utils.decorators import require_shop_access, verify_authenticated_user
from app.utils.errors import DomainError, ResourceConflictError
from app.utils.validators import (
    VALID_PAYMENT_MODES,
    VALID_RETURN_CONDITIONS,
    VALID_SALE_STATUSES,
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client_ip() -> str | None:
    if request.headers.get("X-Forwarded-For"):
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr


def _user_agent() -> str | None:
    return request.user_agent.string if request.user_agent else None


def _idempotency_header() -> str | None:
    return (
        request.headers.get("X-Idempotency-Key")
        or request.headers.get("Idempotency-Key")
        or None
    )


# ==============================================================================
# HTML VIEWS
# ==============================================================================


@sales_bp.route("/pos", methods=["GET"])
@login_required
def pos_entry_redirect():
    """Redirect user to their primary/first accessible shop's POS counter."""
    user = verify_authenticated_user()
    if user.organization_id is None:
        flash("Please configure or join an organization first.", "warning")
        return redirect(url_for("admin.organization_page"))

    shops = user.get_accessible_shops(include_inactive=True)
    if not shops:
        flash("Create or get assigned to a shop before opening POS billing.", "info")
        return redirect(url_for("admin.shops_page"))

    primary = user.get_primary_assignment()
    target_shop_id = primary.shop_id if primary else shops[0].id
    return redirect(url_for("sales.pos_counter_page", shop_id=target_shop_id))


@sales_bp.route("/sales", methods=["GET"])
@login_required
def sales_entry_redirect():
    """Redirect user to their primary/first accessible shop's sales history."""
    user = verify_authenticated_user()
    if user.organization_id is None:
        flash("Please configure or join an organization first.", "warning")
        return redirect(url_for("admin.organization_page"))

    shops = user.get_accessible_shops(include_inactive=True)
    if not shops:
        flash("Create or get assigned to a shop before viewing sales history.", "info")
        return redirect(url_for("admin.shops_page"))

    primary = user.get_primary_assignment()
    target_shop_id = primary.shop_id if primary else shops[0].id
    return redirect(url_for("sales.sales_history_page", shop_id=target_shop_id))


@sales_bp.route("/shops/<int:shop_id>/pos", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def pos_counter_page(shop_id: int):
    """Render the Point-of-Sale (POS) product selector and cart checkout screen."""
    shop = g.current_shop
    search_q = request.args.get("q", "").strip()
    category_filter = request.args.get("category", "").strip()

    catalog_data = CatalogService.list_shop_products(
        actor=current_user,
        shop_id=shop.id,
        search_query=search_q or None,
        category=category_filter or None,
        is_active=True,
        page=1,
        per_page=100,
    )
    today = get_business_today()
    recent_sales = SalesService.list_sales(
        actor=current_user,
        shop_id=shop.id,
        date_from=today,
        date_to=today,
        page=1,
        per_page=8,
    )

    return render_template(
        "sales/pos.html",
        shop=shop,
        products=catalog_data["items"],
        categories=catalog_data.get("categories", []),
        search_q=search_q,
        category_filter=category_filter,
        recent_sales=recent_sales["items"],
        today_summary=recent_sales["summary"],
        payment_modes=sorted(VALID_PAYMENT_MODES),
        form_idempotency_key=f"pos_{uuid.uuid4().hex}",
    )


@sales_bp.route("/shops/<int:shop_id>/pos/checkout", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def pos_checkout_action(shop_id: int):
    """Process server-side HTML form submission for POS checkout."""
    raw_cart_json = request.form.get("cart_json", "").strip()
    items: list[dict[str, Any]] = []

    if raw_cart_json:
        try:
            parsed = json.loads(raw_cart_json)
            if isinstance(parsed, list):
                items = parsed
        except ValueError:
            items = []

    if not items:
        product_ids = request.form.getlist("product_id")
        quantities = request.form.getlist("quantity")
        line_discounts = request.form.getlist("line_discount")
        for idx, pid in enumerate(product_ids):
            if not str(pid).strip():
                continue
            qty_val = quantities[idx] if idx < len(quantities) else "1"
            disc_val = line_discounts[idx] if idx < len(line_discounts) else "0.00"
            items.append(
                {
                    "product_id": pid,
                    "quantity": qty_val,
                    "line_discount": disc_val,
                }
            )

    payload = {
        "items": items,
        "payment_mode": request.form.get("payment_mode", "CASH"),
        "discount_amount": request.form.get("discount_amount", "0.00"),
        "customer_name": request.form.get("customer_name"),
        "customer_phone": request.form.get("customer_phone"),
        "notes": request.form.get("notes"),
        "idempotency_key": request.form.get("idempotency_key"),
    }

    try:
        sale = SalesService.create_sale(
            actor=current_user,
            shop_id=shop_id,
            payload=payload,
            header_idempotency_key=_idempotency_header(),
            ip_address=_client_ip(),
            user_agent=_user_agent(),
        )
        flash(
            f"Sale completed! Invoice {sale.invoice_number} generated for ₹{sale.total_amount:.2f}.",
            "success",
        )
        return redirect(
            url_for("sales.sale_detail_page", shop_id=shop_id, sale_id=sale.id)
        )
    except DomainError as exc:
        flash(exc.message, "error")
        return redirect(url_for("sales.pos_counter_page", shop_id=shop_id))


@sales_bp.route("/shops/<int:shop_id>/sales", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def sales_history_page(shop_id: int):
    """Render the shop sales history and financial summary screen."""
    shop = g.current_shop
    status_filter = request.args.get("status", "").strip()
    payment_filter = request.args.get("payment_mode", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    search_q = request.args.get("q", "").strip()
    page = request.args.get("page", 1)
    per_page = request.args.get("per_page", 20)

    result = SalesService.list_sales(
        actor=current_user,
        shop_id=shop.id,
        status=status_filter or None,
        payment_mode=payment_filter or None,
        date_from=date_from or None,
        date_to=date_to or None,
        search_query=search_q or None,
        page=page,
        per_page=per_page,
    )

    return render_template(
        "sales/history.html",
        shop=shop,
        sales=result["items"],
        summary=result["summary"],
        meta=result["meta"],
        status_filter=status_filter,
        payment_filter=payment_filter,
        date_from=date_from,
        date_to=date_to,
        search_q=search_q,
        statuses=sorted(VALID_SALE_STATUSES),
        payment_modes=sorted(VALID_PAYMENT_MODES),
    )


@sales_bp.route("/shops/<int:shop_id>/sales/<int:sale_id>", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def sale_detail_page(shop_id: int, sale_id: int):
    """Render the sale detail, FEFO batch allocations, and return/cancellation view."""
    shop = g.current_shop
    sale_data = SalesService.get_sale_detail(
        actor=current_user,
        shop_id=shop.id,
        sale_id=sale_id,
    )
    return render_template(
        "sales/invoice_detail.html",
        shop=shop,
        sale=sale_data,
        return_conditions=sorted(VALID_RETURN_CONDITIONS),
        return_idempotency_key=f"ret_{uuid.uuid4().hex}",
    )


@sales_bp.route("/shops/<int:shop_id>/sales/<int:sale_id>/receipt", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def sale_receipt_page(shop_id: int, sale_id: int):
    """Render the print-friendly customer receipt page."""
    shop = g.current_shop
    sale_data = SalesService.get_sale_detail(
        actor=current_user,
        shop_id=shop.id,
        sale_id=sale_id,
    )
    return render_template(
        "sales/receipt.html",
        shop=shop,
        sale=sale_data,
    )


@sales_bp.route("/shops/<int:shop_id>/sales/<int:sale_id>/cancel", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def cancel_sale_action(shop_id: int, sale_id: int):
    """Handle HTML form submission to cancel a same-day completed sale."""
    payload = {"reason": request.form.get("reason", "")}
    try:
        sale = SalesService.cancel_sale(
            actor=current_user,
            shop_id=shop_id,
            sale_id=sale_id,
            payload=payload,
            ip_address=_client_ip(),
            user_agent=_user_agent(),
        )
        flash(
            f"Invoice {sale.invoice_number} cancelled and stock restored to batches.",
            "success",
        )
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("sales.sale_detail_page", shop_id=shop_id, sale_id=sale_id))


@sales_bp.route("/shops/<int:shop_id>/sales/<int:sale_id>/returns", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def process_return_action(shop_id: int, sale_id: int):
    """Handle HTML form submission to return sold items."""
    payload = {
        "sale_item_id": request.form.get("sale_item_id"),
        "quantity_returned": request.form.get("quantity_returned"),
        "return_condition": request.form.get("return_condition", "RESELLABLE"),
        "reason": request.form.get("reason", ""),
        "idempotency_key": request.form.get("idempotency_key"),
    }
    try:
        sale, created_returns = SalesService.process_return(
            actor=current_user,
            shop_id=shop_id,
            sale_id=sale_id,
            payload=payload,
            header_idempotency_key=_idempotency_header(),
            ip_address=_client_ip(),
            user_agent=_user_agent(),
        )
        total_refund = sum(float(r.refund_amount) for r in created_returns)
        flash(
            f"Return processed on {sale.invoice_number} (Refund: ₹{total_refund:.2f}).",
            "success",
        )
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("sales.sale_detail_page", shop_id=shop_id, sale_id=sale_id))


# ==============================================================================
# REST JSON API ENDPOINTS (/api/v1/shops/<shop_id>/sales*)
# ==============================================================================


@sales_bp.route("/api/v1/shops/<int:shop_id>/sales", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def api_create_sale(shop_id: int):
    """API-020: Create a POS sale with automatic FEFO batch deduction."""
    payload = request.get_json(silent=True) or {}
    sale = SalesService.create_sale(
        actor=current_user,
        shop_id=shop_id,
        payload=payload,
        header_idempotency_key=_idempotency_header(),
        ip_address=_client_ip(),
        user_agent=_user_agent(),
    )
    sale_dict = sale.to_dict(include_details=True)
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "sale": sale_dict,
                    "invoice_number": sale.invoice_number,
                    "receipt_number": sale.invoice_number,
                    "allocations": sale_dict["allocations"],
                },
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        201,
    )


@sales_bp.route("/api/v1/shops/<int:shop_id>/sales", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def api_list_sales(shop_id: int):
    """API-021: List shop sales invoices with date range, status, search filters, and summary."""
    result = SalesService.list_sales(
        actor=current_user,
        shop_id=shop_id,
        status=request.args.get("status"),
        payment_mode=request.args.get("payment_mode"),
        date_from=request.args.get("date_from") or request.args.get("start_date"),
        date_to=request.args.get("date_to") or request.args.get("end_date"),
        search_query=request.args.get("q") or request.args.get("search"),
        page=request.args.get("page", 1),
        per_page=request.args.get("per_page", 20),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "items": result["items"],
                    "sales": result["items"],
                    "summary": result["summary"],
                },
                "meta": {
                    **result["meta"],
                    "timestamp": _utc_iso(),
                },
            }
        ),
        200,
    )


@sales_bp.route("/api/v1/shops/<int:shop_id>/sales/summary", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def api_sales_summary(shop_id: int):
    """API-021b: Return shop sales summary metrics for a date range."""
    result = SalesService.list_sales(
        actor=current_user,
        shop_id=shop_id,
        status=request.args.get("status"),
        payment_mode=request.args.get("payment_mode"),
        date_from=request.args.get("date_from") or request.args.get("start_date"),
        date_to=request.args.get("date_to") or request.args.get("end_date"),
        search_query=request.args.get("q") or request.args.get("search"),
        page=1,
        per_page=20,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "summary": result["summary"],
                },
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@sales_bp.route("/api/v1/shops/<int:shop_id>/sales/<int:sale_id>", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def api_get_sale_detail(shop_id: int, sale_id: int):
    """API-022: Retrieve full invoice details including FEFO batch breakdown and return history."""
    sale_dict = SalesService.get_sale_detail(
        actor=current_user,
        shop_id=shop_id,
        sale_id=sale_id,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "sale": sale_dict,
                },
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@sales_bp.route("/api/v1/shops/<int:shop_id>/sales/<int:sale_id>/receipt", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def api_get_sale_receipt(shop_id: int, sale_id: int):
    """API-022b: Retrieve structured printable receipt data."""
    sale_dict = SalesService.get_sale_detail(
        actor=current_user,
        shop_id=shop_id,
        sale_id=sale_id,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "receipt": {
                        "receipt_number": sale_dict["invoice_number"],
                        "invoice_number": sale_dict["invoice_number"],
                        "shop": sale_dict["shop"],
                        "status": sale_dict["status"],
                        "created_at": sale_dict["created_at"],
                        "customer_name": sale_dict["customer_name"] or "Walk-in Customer",
                        "customer_phone": sale_dict["customer_phone"],
                        "payment_mode": sale_dict["payment_mode"],
                        "cashier_name": sale_dict["sold_by_name"],
                        "subtotal_amount": sale_dict["subtotal_amount"],
                        "discount_amount": sale_dict["discount_amount"],
                        "total_amount": sale_dict["total_amount"],
                        "returned_amount": sale_dict["returned_amount"],
                        "net_revenue_amount": sale_dict["net_revenue_amount"],
                        "product_lines": sale_dict["product_lines"],
                        "allocations": sale_dict["allocations"],
                        "returns": sale_dict["returns"],
                    }
                },
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@sales_bp.route("/api/v1/shops/<int:shop_id>/sales/<int:sale_id>/cancel", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def api_cancel_sale(shop_id: int, sale_id: int):
    """API-023: Cancel a same-day completed sale and restore stock to original batches."""
    payload = request.get_json(silent=True) or {}
    sale = SalesService.cancel_sale(
        actor=current_user,
        shop_id=shop_id,
        sale_id=sale_id,
        payload=payload,
        ip_address=_client_ip(),
        user_agent=_user_agent(),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "sale": sale.to_dict(include_details=True),
                },
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@sales_bp.route("/api/v1/shops/<int:shop_id>/sales/<int:sale_id>/returns", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def api_process_return(shop_id: int, sale_id: int):
    """API-024: Process partial or full item return (RESELLABLE, DAMAGED, or EXPIRED)."""
    payload = request.get_json(silent=True) or {}
    sale, created_returns = SalesService.process_return(
        actor=current_user,
        shop_id=shop_id,
        sale_id=sale_id,
        payload=payload,
        header_idempotency_key=_idempotency_header(),
        ip_address=_client_ip(),
        user_agent=_user_agent(),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "sale": sale.to_dict(include_details=True),
                    "returns": [r.to_dict() for r in created_returns],
                },
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        201,
    )


@sales_bp.route("/api/v1/shops/<int:shop_id>/sales/<int:sale_id>", methods=["DELETE"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def api_delete_sale_prohibited(shop_id: int, sale_id: int):
    """Block destructive deletion of historical sales records (Requirement 3)."""
    raise ResourceConflictError(
        message="Historical sales transactions cannot be deleted. Use same-day cancellation or item returns.",
        code="DESTRUCTIVE_DELETE_PROHIBITED",
        details={"sale_id": sale_id, "shop_id": shop_id},
    )
