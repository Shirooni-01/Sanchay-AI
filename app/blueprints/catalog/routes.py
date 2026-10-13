"""HTML Views and REST JSON API Routes for Product Catalog (Phase 3)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app.blueprints.catalog import catalog_bp
from app.extensions import db
from app.models.catalog import Product
from app.services.catalog_service import CatalogService
from app.utils.decorators import (
    require_organization_access,
    require_shop_access,
    verify_authenticated_user,
    verify_organization_access,
)
from app.utils.errors import DomainError, ResourceNotFoundError
from app.utils.validators import VALID_PRODUCT_UNITS, _parse_bool


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_optional_active_filter(raw_val: str | None) -> bool | None:
    if raw_val is None or raw_val.strip() == "" or raw_val.strip().lower() == "all":
        return None
    lower = raw_val.strip().lower()
    if lower in ("true", "1", "active", "yes"):
        return True
    if lower in ("false", "0", "inactive", "no"):
        return False
    return _parse_bool(raw_val, "is_active")


# ============================================================================
# HTML UI Routes — Product Catalog
# ============================================================================


@catalog_bp.route("/catalog", methods=["GET"])
@catalog_bp.route("/products", methods=["GET"])
@login_required
def catalog_Entry_redirect():
    """Redirect user to their primary/first accessible shop's product catalog."""
    user = verify_authenticated_user()
    if user.organization_id is None:
        flash("Please configure or join an organization first.", "warning")
        return redirect(url_for("admin.organization_page"))

    shops = user.get_accessible_shops(include_inactive=True)
    if not shops:
        flash("Create or get assigned to a shop before managing products.", "info")
        return redirect(url_for("admin.shops_page"))

    primary = user.get_primary_assignment()
    target_shop_id = primary.shop_id if primary else shops[0].id
    return redirect(url_for("catalog.shop_products_page", shop_id=target_shop_id))


@catalog_bp.route("/shops/<int:shop_id>/products", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def shop_products_page(shop_id: int):
    """Render the shop-scoped Product Catalog listing and manual creation page."""
    user = verify_authenticated_user()
    search_query = request.args.get("q", request.args.get("search", "")).strip()
    category_filter = request.args.get("category", "").strip()
    status_filter = request.args.get("status", "").strip()
    low_stock_only = request.args.get("low_stock", "").strip().lower() in ("true", "1", "yes")
    near_expiry_only = request.args.get("near_expiry", "").strip().lower() in ("true", "1", "yes")
    page = request.args.get("page", 1)
    per_page = request.args.get("per_page", 20)

    is_active = _parse_optional_active_filter(
        request.args.get("is_active") if "is_active" in request.args else status_filter
    )

    result = CatalogService.list_shop_products(
        actor=user,
        shop_id=shop_id,
        search_query=search_query or None,
        category=category_filter or None,
        is_active=is_active,
        low_stock_only=low_stock_only,
        near_expiry_only=near_expiry_only,
        page=page,
        per_page=per_page,
    )

    from flask import g

    shop = g.current_shop
    assignment = g.current_shop_assignment
    can_manage_catalog = (
        shop.is_operational
        and (
            user.role == "SUPER_ADMIN"
            or (assignment is not None and assignment.assignment_role == "SHOP_OWNER")
        )
    )
    accessible_shops = user.get_accessible_shops(include_inactive=True)

    return render_template(
        "catalog/products.html",
        user=user,
        shop=shop,
        accessible_shops=accessible_shops,
        products=result["items"],
        categories=result["categories"],
        unenrolled_org_products=result["unenrolled_org_products"],
        meta=result["meta"],
        units=sorted(VALID_PRODUCT_UNITS),
        can_manage_catalog=can_manage_catalog,
        search_query=search_query,
        category_filter=category_filter,
        status_filter=status_filter,
        low_stock_only=low_stock_only,
        near_expiry_only=near_expiry_only,
    )


@catalog_bp.route("/shops/<int:shop_id>/products/create", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def create_shop_product_action(shop_id: int):
    """Handle HTML form submission to manually create or enroll a shop product."""
    user = verify_authenticated_user()
    payload: dict[str, Any] = {
        k: v for k, v in request.form.items() if k != "csrf_token"
    }
    if "product_id" not in payload or not str(payload.get("product_id", "")).strip():
        payload.pop("product_id", None)
        payload["is_perishable"] = request.form.get("is_perishable") in ("true", "on", "1", "yes")

    try:
        sp = CatalogService.create_or_enroll_shop_product(
            actor=user,
            shop_id=shop_id,
            payload=payload,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
        flash(
            f"Product '{sp.product.name}' ({sp.product.sku}) saved in shop catalog.",
            "success",
        )
    except DomainError as exc:
        flash(exc.message, "error")

    return redirect(url_for("catalog.shop_products_page", shop_id=shop_id))


@catalog_bp.route("/shops/<int:shop_id>/products/<int:shop_product_id>", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def shop_product_detail_page(shop_id: int, shop_product_id: int):
    """Render the detailed view and edit form for a specific shop product."""
    user = verify_authenticated_user()
    product_detail = CatalogService.get_shop_product_detail(
        actor=user,
        shop_id=shop_id,
        identifier=shop_product_id,
    )

    from flask import g

    shop = g.current_shop
    assignment = g.current_shop_assignment
    can_manage_catalog = (
        shop.is_operational
        and (
            user.role == "SUPER_ADMIN"
            or (assignment is not None and assignment.assignment_role == "SHOP_OWNER")
        )
    )

    return render_template(
        "catalog/product_detail.html",
        user=user,
        shop=shop,
        product=product_detail,
        units=sorted(VALID_PRODUCT_UNITS),
        can_manage_catalog=can_manage_catalog,
    )


@catalog_bp.route("/shops/<int:shop_id>/products/<int:shop_product_id>/update", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def update_shop_product_action(shop_id: int, shop_product_id: int):
    """Handle HTML form submission to update or deactivate a shop product."""
    user = verify_authenticated_user()
    payload: dict[str, Any] = {
        k: v for k, v in request.form.items() if k != "csrf_token"
    }

    try:
        sp = CatalogService.update_shop_product(
            actor=user,
            shop_id=shop_id,
            identifier=shop_product_id,
            payload=payload,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
        flash(f"Product '{sp.product.name}' updated successfully.", "success")
    except DomainError as exc:
        flash(exc.message, "error")

    return redirect(
        url_for(
            "catalog.shop_product_detail_page",
            shop_id=shop_id,
            shop_product_id=shop_product_id,
        )
    )


# ============================================================================
# REST JSON API Routes — Organization & Shop Product Catalog (/api/v1/*)
# ============================================================================


@catalog_bp.route("/api/v1/products", methods=["GET"])
@require_organization_access(require_active=False)
def api_list_org_products():
    """List paginated canonical products for the authenticated user's organization."""
    user = verify_authenticated_user()
    search_query = request.args.get("q", request.args.get("search"))
    category = request.args.get("category")
    is_active = _parse_optional_active_filter(request.args.get("is_active"))
    page = request.args.get("page", 1)
    per_page = request.args.get("per_page", 20)

    result = CatalogService.list_organization_products(
        actor=user,
        search_query=search_query,
        category=category,
        is_active=is_active,
        page=page,
        per_page=per_page,
    )
    meta = {**result["meta"], "timestamp": _utc_iso()}
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "products": result["items"],
                    "categories": result["categories"],
                },
                "meta": meta,
            }
        ),
        200,
    )


@catalog_bp.route("/api/v1/products", methods=["POST"])
@require_organization_access(require_active=True)
def api_create_org_product():
    """Create a canonical organization product (and optionally enroll in shop_id)."""
    user = verify_authenticated_user()
    payload = request.get_json(silent=True) or {}
    shop_id = payload.get("shop_id")
    if shop_id is not None and str(shop_id).strip() != "":
        shop_id = int(shop_id)
    else:
        shop_id = None

    product = CatalogService.create_organization_product(
        actor=user,
        payload=payload,
        shop_id=shop_id,
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"product": product.to_dict()},
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        201,
    )


@catalog_bp.route("/api/v1/products/<int:product_id>", methods=["GET"])
@require_organization_access(require_active=False)
def api_get_org_product(product_id: int):
    """Retrieve a single canonical organization product."""
    user = verify_authenticated_user()
    org = verify_organization_access(user=user, require_active=False)
    product = db.session.get(Product, int(product_id))
    if product is None:
        raise ResourceNotFoundError(message=f"Product #{product_id} was not found.")
    if product.organization_id != org.id:
        from app.utils.errors import AuthorizationError

        raise AuthorizationError(
            message="Cross-organization product access is forbidden.",
            code="UNAUTHORIZED_ORG_ACCESS",
        )
    return (
        jsonify(
            {
                "success": True,
                "data": {"product": product.to_dict()},
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@catalog_bp.route("/api/v1/products/<int:product_id>", methods=["PATCH", "PUT"])
@require_organization_access(require_active=True)
def api_update_org_product(product_id: int):
    """Update or deactivate an organization product."""
    user = verify_authenticated_user()
    payload = request.get_json(silent=True) or {}
    product = CatalogService.update_organization_product(
        actor=user,
        product_id=product_id,
        payload=payload,
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"product": product.to_dict()},
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@catalog_bp.route("/api/v1/products/<int:product_id>", methods=["DELETE"])
@login_required
def api_delete_org_product(product_id: int):
    """Block destructive deletion of products (soft-deactivation required)."""
    user = verify_authenticated_user()
    CatalogService.prevent_destructive_product_delete(actor=user, product_id=product_id)


@catalog_bp.route("/api/v1/shops/<int:shop_id>/products", methods=["GET"])
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def api_list_shop_products(shop_id: int):
    """API-013: Paginated list of shop products with reconciled stock totals."""
    user = verify_authenticated_user()
    search_query = request.args.get("q", request.args.get("search"))
    category = request.args.get("category")
    is_active = _parse_optional_active_filter(
        request.args.get("is_active", request.args.get("status"))
    )
    low_stock_only = str(request.args.get("low_stock", "")).strip().lower() in (
        "true",
        "1",
        "yes",
    )
    near_expiry_only = str(request.args.get("near_expiry", "")).strip().lower() in (
        "true",
        "1",
        "yes",
    )
    page = request.args.get("page", 1)
    per_page = request.args.get("per_page", 20)

    result = CatalogService.list_shop_products(
        actor=user,
        shop_id=shop_id,
        search_query=search_query,
        category=category,
        is_active=is_active,
        low_stock_only=low_stock_only,
        near_expiry_only=near_expiry_only,
        page=page,
        per_page=per_page,
    )
    meta = {**result["meta"], "timestamp": _utc_iso()}
    return (
        jsonify(
            {
                "success": True,
                "data": {
                    "products": result["items"],
                    "categories": result["categories"],
                },
                "meta": meta,
            }
        ),
        200,
    )


@catalog_bp.route("/api/v1/shops/<int:shop_id>/products", methods=["POST"])
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def api_create_shop_product(shop_id: int):
    """API-014: Manually create or link a product SKU in the shop catalog."""
    user = verify_authenticated_user()
    payload = request.get_json(silent=True) or {}
    shop_product = CatalogService.create_or_enroll_shop_product(
        actor=user,
        shop_id=shop_id,
        payload=payload,
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    detail = CatalogService.get_shop_product_detail(
        actor=user,
        shop_id=shop_id,
        identifier=shop_product.id,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"product": detail},
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        201,
    )


@catalog_bp.route(
    "/api/v1/shops/<int:shop_id>/products/<int:shop_product_id>",
    methods=["GET"],
)
@require_shop_access(
    allow_inactive=True,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
)
def api_get_shop_product(shop_id: int, shop_product_id: int):
    """Retrieve a single shop product with reconciled stock summary, batches, and movements."""
    user = verify_authenticated_user()
    detail = CatalogService.get_shop_product_detail(
        actor=user,
        shop_id=shop_id,
        identifier=shop_product_id,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"product": detail},
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@catalog_bp.route(
    "/api/v1/shops/<int:shop_id>/products/<int:shop_product_id>",
    methods=["PATCH", "PUT"],
)
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def api_update_shop_product(shop_id: int, shop_product_id: int):
    """API-015: Update shop product selling price, purchase price, thresholds, or active status."""
    user = verify_authenticated_user()
    payload = request.get_json(silent=True) or {}
    sp = CatalogService.update_shop_product(
        actor=user,
        shop_id=shop_id,
        identifier=shop_product_id,
        payload=payload,
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    detail = CatalogService.get_shop_product_detail(
        actor=user,
        shop_id=shop_id,
        identifier=sp.id,
    )
    return (
        jsonify(
            {
                "success": True,
                "data": {"product": detail},
                "meta": {"timestamp": _utc_iso()},
            }
        ),
        200,
    )


@catalog_bp.route(
    "/api/v1/shops/<int:shop_id>/products/<int:shop_product_id>",
    methods=["DELETE"],
)
@require_shop_access(
    allow_inactive=False,
    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
)
def api_delete_shop_product(shop_id: int, shop_product_id: int):
    """Block destructive deletion of shop products (soft-deactivation required)."""
    user = verify_authenticated_user()
    CatalogService.prevent_destructive_product_delete(
        actor=user,
        product_id=shop_product_id,
        shop_id=shop_id,
    )
