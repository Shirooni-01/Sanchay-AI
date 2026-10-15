"""Service for Phase 8: Dashboards, Analytics & Tabular Reports.

Provides:
- Organization-wide executive dashboard metrics (Super Admin).
- Shop-specific operational dashboard metrics (Shop Owner / Staff).
- Daily and monthly sales trends (with continuous date filling).
- Top-selling products by quantity and revenue.
- Stock distribution across product categories, stock states, and expiry horizons.
- Inter-store transfer activity and throughput metrics.
- Tabular report generation (SALES, INVENTORY, NEAR_EXPIRY, TRANSFERS) with date and store filtering.
- RFC 4180 compliant streaming CSV export.
- Strict tenant and shop-level RBAC isolation.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import io
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, has_app_context
from sqlalchemy import case, distinct, func

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch, StockMovement
from app.models.organization import Organization, Shop
from app.models.sales import Sale, SaleItem, SaleReturn
from app.models.transfer import TransferRequest
from app.models.user import User
from app.services.inventory_service import get_business_today
from app.utils.decorators import verify_shop_access
from app.utils.errors import (
    AuthorizationError,
    ResourceNotFoundError,
    ValidationError,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _get_business_tz() -> timezone | ZoneInfo:
    tz_name = "Asia/Kolkata"
    if has_app_context():
        tz_name = str(current_app.config.get("APP_TIMEZONE", "Asia/Kolkata"))
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=5, minutes=30))


def _to_float(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return round(float(val), 2)
    if isinstance(val, Decimal):
        return round(float(val), 2)
    try:
        return round(float(str(val)), 2)
    except (ValueError, TypeError):
        return 0.0


def _parse_date(val: Any) -> date | None:
    if not val:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    val_str = str(val).strip()
    if not val_str:
        return None
    try:
        return datetime.strptime(val_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(f"Invalid date format '{val_str}'. Expected YYYY-MM-DD.")


def validate_report_date_range(
    start_date_raw: Any,
    end_date_raw: Any,
    default_days: int = 30,
) -> tuple[date, date]:
    """Validate and normalize report start and end dates."""
    today = get_business_today()
    start_date = _parse_date(start_date_raw)
    end_date = _parse_date(end_date_raw)

    if end_date is None:
        end_date = today
    if start_date is None:
        start_date = end_date - timedelta(days=default_days)

    if start_date > end_date:
        raise ValidationError(
            f"Start date ({start_date.isoformat()}) cannot be after end date ({end_date.isoformat()})."
        )

    return start_date, end_date


class ReportService:
    """Core analytics and reporting business logic for Sanchay AI (Phase 8)."""

    # -------------------------------------------------------------------------
    # 1. Organization Executive Dashboard Metrics (Super Admin Only)
    # -------------------------------------------------------------------------
    @staticmethod
    def get_organization_dashboard_metrics(actor: User) -> dict[str, Any]:
        """Aggregate organization-wide KPIs across all shops.

        Requires SUPER_ADMIN role.
        """
        if actor.role != "SUPER_ADMIN":
            raise AuthorizationError("Only Super Admins can access organization-wide dashboard metrics.")

        if actor.organization_id is None:
            raise ResourceNotFoundError("Authenticated Super Admin is not associated with an organization.")

        org = Organization.query.get(actor.organization_id)
        if not org:
            raise ResourceNotFoundError(f"Organization ID {actor.organization_id} not found.")

        today = get_business_today()
        near_expiry_threshold = org.near_expiry_days_threshold or 30

        # Query shops
        shops = Shop.query.filter_by(organization_id=org.id).order_by(Shop.name.asc()).all()
        shop_ids = [s.id for s in shops]

        if not shop_ids:
            return {
                "organization": {
                    "id": org.id,
                    "name": org.name,
                    "code": org.code,
                    "status": org.status,
                    "currency": "INR",
                },
                "kpis": {
                    "total_shops": 0,
                    "active_shops": 0,
                    "total_products": 0,
                    "total_stock_qty": 0,
                    "total_inventory_valuation": 0.0,
                    "total_sales_count": 0,
                    "gross_revenue": 0.0,
                    "cogs": 0.0,
                    "gross_profit": 0.0,
                    "gross_margin_pct": 0.0,
                    "completed_transfers_count": 0,
                    "pending_transfers_count": 0,
                    "near_expiry_batch_count": 0,
                    "near_expiry_valuation_at_risk": 0.0,
                    "low_stock_products_count": 0,
                    "surplus_products_count": 0,
                },
                "shops_breakdown": [],
            }

        # 1. Total unique products in catalog for this org
        total_products = Product.query.filter_by(organization_id=org.id, is_active=True).count()

        # 2. Inventory batches aggregation across org shops
        batch_stats = (
            db.session.query(
                func.coalesce(func.sum(InventoryBatch.available_qty + InventoryBatch.reserved_qty), 0).label("total_stock_qty"),
                func.coalesce(
                    func.sum((InventoryBatch.available_qty + InventoryBatch.reserved_qty) * InventoryBatch.unit_cost), 0
                ).label("inventory_valuation"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (InventoryBatch.expiry_date <= today + timedelta(days=near_expiry_threshold))
                                & (InventoryBatch.available_qty > 0)
                                & (InventoryBatch.status == "ACTIVE"),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("near_expiry_batches"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (InventoryBatch.expiry_date <= today + timedelta(days=near_expiry_threshold))
                                & (InventoryBatch.available_qty > 0)
                                & (InventoryBatch.status == "ACTIVE"),
                                InventoryBatch.available_qty * InventoryBatch.unit_cost,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("near_expiry_valuation"),
            )
            .join(ShopProduct, InventoryBatch.shop_product_id == ShopProduct.id)
            .filter(ShopProduct.shop_id.in_(shop_ids))
            .first()
        )

        total_stock_qty = int(batch_stats.total_stock_qty or 0)
        inventory_val = _to_float(batch_stats.inventory_valuation)
        near_expiry_count = int(batch_stats.near_expiry_batches or 0)
        near_expiry_val = _to_float(batch_stats.near_expiry_valuation)

        # 3. Low stock products count across org
        # Sum of available quantity for each shop_product <= low_stock_threshold
        avail_subq = (
            db.session.query(
                InventoryBatch.shop_product_id.label("sp_id"),
                func.coalesce(func.sum(InventoryBatch.available_qty), 0).label("avail_qty"),
            )
            .filter(InventoryBatch.status == "ACTIVE", InventoryBatch.expiry_date > today)
            .group_by(InventoryBatch.shop_product_id)
            .subquery()
        )

        low_stock_count = (
            db.session.query(func.count(ShopProduct.id))
            .outerjoin(avail_subq, ShopProduct.id == avail_subq.c.sp_id)
            .filter(
                ShopProduct.shop_id.in_(shop_ids),
                ShopProduct.is_active == True,
                func.coalesce(avail_subq.c.avail_qty, 0) <= ShopProduct.low_stock_threshold,
            )
            .scalar()
            or 0
        )

        # 4. Sales aggregation across org shops
        sales_stats = (
            db.session.query(
                func.count(Sale.id).label("sales_count"),
                func.coalesce(func.sum(Sale.total_amount), 0).label("gross_revenue"),
                func.coalesce(func.sum(Sale.total_cost_amount), 0).label("cogs"),
                func.coalesce(func.sum(Sale.total_amount - Sale.total_cost_amount), 0).label("gross_profit"),
            )
            .filter(
                Sale.organization_id == org.id,
                Sale.status == "COMPLETED",
            )
            .first()
        )

        sales_count = int(sales_stats.sales_count or 0)
        gross_revenue = _to_float(sales_stats.gross_revenue)
        cogs = _to_float(sales_stats.cogs)
        gross_profit = _to_float(sales_stats.gross_profit)
        gross_margin = (
            round((gross_profit / gross_revenue) * 100, 2)
            if gross_revenue > 0
            else 0.0
        )

        # 5. Transfers aggregation
        completed_transfers = TransferRequest.query.filter(
            TransferRequest.organization_id == org.id,
            TransferRequest.status == "COMPLETED",
        ).count()

        pending_transfers = TransferRequest.query.filter(
            TransferRequest.organization_id == org.id,
            TransferRequest.status.in_(
                [
                    "PENDING_RECIPIENT_APPROVAL",
                    "PENDING_SOURCE_APPROVAL",
                    "APPROVED_RESERVED",
                    "DISPATCHED_IN_TRANSIT",
                ]
            ),
        ).count()

        # 6. Surplus products count (ShopProducts with stock exceeding 3x low_stock_threshold)
        surplus_count = (
            db.session.query(func.count(ShopProduct.id))
            .join(avail_subq, ShopProduct.id == avail_subq.c.sp_id)
            .filter(
                ShopProduct.shop_id.in_(shop_ids),
                ShopProduct.is_active == True,
                avail_subq.c.avail_qty > (ShopProduct.low_stock_threshold * 3),
            )
            .scalar()
            or 0
        )

        # 7. Shop-by-Shop Breakdown
        shops_breakdown = []
        for s in shops:
            # Per shop sales
            s_sales = (
                db.session.query(
                    func.count(Sale.id).label("cnt"),
                    func.coalesce(func.sum(Sale.total_amount), 0).label("rev"),
                    func.coalesce(func.sum(Sale.total_amount - Sale.total_cost_amount), 0).label("gp"),
                )
                .filter(Sale.shop_id == s.id, Sale.status == "COMPLETED")
                .first()
            )

            # Per shop inventory
            s_inv = (
                db.session.query(
                    func.coalesce(func.sum(InventoryBatch.available_qty + InventoryBatch.reserved_qty), 0).label("qty"),
                    func.coalesce(
                        func.sum((InventoryBatch.available_qty + InventoryBatch.reserved_qty) * InventoryBatch.unit_cost), 0
                    ).label("val"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    (InventoryBatch.expiry_date <= today + timedelta(days=near_expiry_threshold))
                                    & (InventoryBatch.available_qty > 0)
                                    & (InventoryBatch.status == "ACTIVE"),
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("near_exp"),
                )
                .join(ShopProduct, InventoryBatch.shop_product_id == ShopProduct.id)
                .filter(ShopProduct.shop_id == s.id)
                .first()
            )

            # Per shop low stock
            s_low = (
                db.session.query(func.count(ShopProduct.id))
                .outerjoin(avail_subq, ShopProduct.id == avail_subq.c.sp_id)
                .filter(
                    ShopProduct.shop_id == s.id,
                    ShopProduct.is_active == True,
                    func.coalesce(avail_subq.c.avail_qty, 0) <= ShopProduct.low_stock_threshold,
                )
                .scalar()
                or 0
            )

            # Per shop completed transfers
            s_trf = TransferRequest.query.filter(
                (TransferRequest.source_shop_id == s.id) | (TransferRequest.recipient_shop_id == s.id),
                TransferRequest.status == "COMPLETED",
            ).count()

            shops_breakdown.append(
                {
                    "shop_id": s.id,
                    "code": s.code,
                    "name": s.name,
                    "city": s.city,
                    "status": s.status,
                    "stock_qty": int(s_inv.qty or 0),
                    "inventory_valuation": _to_float(s_inv.val),
                    "sales_count": int(s_sales.cnt or 0),
                    "gross_revenue": _to_float(s_sales.rev),
                    "gross_profit": _to_float(s_sales.gp),
                    "low_stock_count": s_low,
                    "near_expiry_count": int(s_inv.near_exp or 0),
                    "completed_transfers": s_trf,
                }
            )

        return {
            "organization": {
                "id": org.id,
                "name": org.name,
                "code": org.code,
                "status": org.status,
                "currency": "INR",
            },
            "kpis": {
                "total_shops": len(shops),
                "active_shops": len([s for s in shops if s.status == "ACTIVE"]),
                "total_products": total_products,
                "total_stock_qty": total_stock_qty,
                "total_inventory_valuation": inventory_val,
                "total_sales_count": sales_count,
                "gross_revenue": gross_revenue,
                "cogs": cogs,
                "gross_profit": gross_profit,
                "gross_margin_pct": gross_margin,
                "completed_transfers_count": completed_transfers,
                "pending_transfers_count": pending_transfers,
                "near_expiry_batch_count": near_expiry_count,
                "near_expiry_valuation_at_risk": near_expiry_val,
                "low_stock_products_count": low_stock_count,
                "surplus_products_count": surplus_count,
            },
            "shops_breakdown": shops_breakdown,
        }

    # -------------------------------------------------------------------------
    # 2. Shop Operational Dashboard Metrics (Shop Owner / Staff / Super Admin)
    # -------------------------------------------------------------------------
    @staticmethod
    def get_shop_dashboard_metrics(actor: User, shop_id: int) -> dict[str, Any]:
        """Aggregate operational KPIs for a single shop.

        Enforces shop-level isolation via verify_shop_access.
        """
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=True,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
        )

        today = get_business_today()
        near_expiry_days = 30
        if shop.organization and shop.organization.near_expiry_days_threshold:
            near_expiry_days = shop.organization.near_expiry_days_threshold

        # 1. Product count
        total_products = ShopProduct.query.filter_by(shop_id=shop.id, is_active=True).count()

        # 2. Inventory Batches breakdown
        batch_query = (
            db.session.query(
                func.coalesce(func.sum(InventoryBatch.available_qty + InventoryBatch.reserved_qty), 0).label("total_qty"),
                func.coalesce(func.sum(InventoryBatch.reserved_qty), 0).label("reserved_qty"),
                func.coalesce(func.sum(InventoryBatch.damaged_qty), 0).label("damaged_qty"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (InventoryBatch.status == "ACTIVE") & (InventoryBatch.expiry_date > today),
                                InventoryBatch.available_qty,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("sellable_qty"),
                func.coalesce(func.sum(InventoryBatch.expired_qty), 0).label("expired_qty"),
                func.coalesce(
                    func.sum(InventoryBatch.available_qty * InventoryBatch.unit_cost), 0
                ).label("cost_valuation"),
                func.coalesce(
                    func.sum(InventoryBatch.available_qty * ShopProduct.selling_price), 0
                ).label("retail_valuation"),
            )
            .join(ShopProduct, InventoryBatch.shop_product_id == ShopProduct.id)
            .filter(ShopProduct.shop_id == shop.id)
            .first()
        )

        total_qty = int(batch_query.total_qty or 0)
        reserved_qty = int(batch_query.reserved_qty or 0)
        damaged_qty = int(batch_query.damaged_qty or 0)
        sellable_qty = int(batch_query.sellable_qty or 0)
        expired_qty = int(batch_query.expired_qty or 0)
        cost_valuation = _to_float(batch_query.cost_valuation)
        retail_valuation = _to_float(batch_query.retail_valuation)

        # 3. Sales statistics (All-time completed sales for shop)
        sales_query = (
            db.session.query(
                func.count(Sale.id).label("sales_count"),
                func.coalesce(func.sum(Sale.total_amount), 0).label("gross_revenue"),
                func.coalesce(func.sum(Sale.total_amount - Sale.returned_amount), 0).label("net_revenue"),
                func.coalesce(func.sum(Sale.total_cost_amount), 0).label("cogs"),
                func.coalesce(func.sum(Sale.total_amount - Sale.total_cost_amount), 0).label("gross_profit"),
            )
            .filter(Sale.shop_id == shop.id, Sale.status == "COMPLETED")
            .first()
        )

        sales_count = int(sales_query.sales_count or 0)
        gross_rev = _to_float(sales_query.gross_revenue)
        net_rev = _to_float(sales_query.net_revenue)
        cogs = _to_float(sales_query.cogs)
        gross_profit = _to_float(sales_query.gross_profit)
        profit_margin = round((gross_profit / gross_rev) * 100, 2) if gross_rev > 0 else 0.0
        aov = round(gross_rev / sales_count, 2) if sales_count > 0 else 0.0

        # 4. Transfers statistics
        incoming_pending = TransferRequest.query.filter(
            TransferRequest.recipient_shop_id == shop.id,
            TransferRequest.status.in_(
                [
                    "PENDING_RECIPIENT_APPROVAL",
                    "PENDING_SOURCE_APPROVAL",
                    "APPROVED_RESERVED",
                    "DISPATCHED_IN_TRANSIT",
                ]
            ),
        ).count()

        outgoing_pending = TransferRequest.query.filter(
            TransferRequest.source_shop_id == shop.id,
            TransferRequest.status.in_(
                [
                    "PENDING_RECIPIENT_APPROVAL",
                    "PENDING_SOURCE_APPROVAL",
                    "APPROVED_RESERVED",
                    "DISPATCHED_IN_TRANSIT",
                ]
            ),
        ).count()

        completed_transfers = TransferRequest.query.filter(
            (TransferRequest.source_shop_id == shop.id) | (TransferRequest.recipient_shop_id == shop.id),
            TransferRequest.status == "COMPLETED",
        ).count()

        # 5. Low stock alerts (top 10)
        avail_subq = (
            db.session.query(
                InventoryBatch.shop_product_id.label("sp_id"),
                func.coalesce(func.sum(InventoryBatch.available_qty), 0).label("avail_qty"),
            )
            .filter(InventoryBatch.status == "ACTIVE", InventoryBatch.expiry_date > today)
            .group_by(InventoryBatch.shop_product_id)
            .subquery()
        )

        low_stock_rows = (
            db.session.query(
                ShopProduct.id,
                Product.name,
                Product.sku,
                Product.category,
                ShopProduct.low_stock_threshold,
                func.coalesce(avail_subq.c.avail_qty, 0).label("current_stock"),
            )
            .join(Product, ShopProduct.product_id == Product.id)
            .outerjoin(avail_subq, ShopProduct.id == avail_subq.c.sp_id)
            .filter(
                ShopProduct.shop_id == shop.id,
                ShopProduct.is_active == True,
                func.coalesce(avail_subq.c.avail_qty, 0) <= ShopProduct.low_stock_threshold,
            )
            .order_by(avail_subq.c.avail_qty.asc())
            .limit(10)
            .all()
        )

        low_stock_items = [
            {
                "shop_product_id": r[0],
                "product_name": r[1],
                "sku": r[2],
                "category": r[3],
                "reorder_level": r[4],
                "current_stock": int(r[5]),
                "deficit": max(0, r[4] - int(r[5])),
            }
            for r in low_stock_rows
        ]

        # 6. Near-expiry batches (top 10)
        near_expiry_rows = (
            db.session.query(
                InventoryBatch.id,
                Product.name,
                Product.sku,
                InventoryBatch.batch_code,
                InventoryBatch.expiry_date,
                InventoryBatch.available_qty,
                InventoryBatch.unit_cost,
                (InventoryBatch.available_qty * InventoryBatch.unit_cost).label("risk_val"),
            )
            .join(ShopProduct, InventoryBatch.shop_product_id == ShopProduct.id)
            .join(Product, ShopProduct.product_id == Product.id)
            .filter(
                ShopProduct.shop_id == shop.id,
                InventoryBatch.status == "ACTIVE",
                InventoryBatch.available_qty > 0,
                InventoryBatch.expiry_date <= today + timedelta(days=near_expiry_days),
            )
            .order_by(InventoryBatch.expiry_date.asc())
            .limit(10)
            .all()
        )

        near_expiry_items = [
            {
                "batch_id": r[0],
                "product_name": r[1],
                "sku": r[2],
                "batch_code": r[3],
                "expiry_date": r[4].isoformat() if r[4] else None,
                "days_left": (r[4] - today).days if r[4] else 0,
                "quantity": int(r[5]),
                "purchase_cost": _to_float(r[6]),
                "valuation_at_risk": _to_float(r[7]),
            }
            for r in near_expiry_rows
        ]

        # 7. Recent sales (latest 5)
        recent_sales_rows = (
            Sale.query.filter_by(shop_id=shop.id, status="COMPLETED")
            .order_by(Sale.created_at.desc())
            .limit(5)
            .all()
        )

        recent_sales = [
            {
                "id": s.id,
                "invoice_number": s.invoice_number,
                "receipt_number": s.invoice_number,
                "customer_name": s.customer_name or "Walk-in Customer",
                "payment_mode": s.payment_mode,
                "total_amount": _to_float(s.total_amount),
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in recent_sales_rows
        ]

        # 8. Recent stock movements (latest 5)
        recent_mov_rows = (
            db.session.query(
                StockMovement.id,
                StockMovement.movement_type,
                StockMovement.quantity_change,
                StockMovement.balance_after,
                StockMovement.created_at,
                Product.name,
                InventoryBatch.batch_code,
            )
            .join(Product, StockMovement.product_id == Product.id)
            .outerjoin(InventoryBatch, StockMovement.batch_id == InventoryBatch.id)
            .filter(StockMovement.shop_id == shop.id)
            .order_by(StockMovement.created_at.desc())
            .limit(5)
            .all()
        )

        recent_movements = [
            {
                "id": m[0],
                "movement_type": m[1],
                "quantity": int(m[2]),
                "balance_after": int(m[3]),
                "created_at": m[4].isoformat() if m[4] else None,
                "product_name": m[5],
                "batch_code": m[6] or "—",
            }
            for m in recent_mov_rows
        ]

        return {
            "shop": {
                "id": shop.id,
                "name": shop.name,
                "code": shop.code,
                "status": shop.status,
                "city": shop.city,
                "currency": "INR",
            },
            "inventory": {
                "total_products": total_products,
                "total_stock_qty": total_qty,
                "sellable_qty": sellable_qty,
                "reserved_qty": reserved_qty,
                "damaged_qty": damaged_qty,
                "expired_qty": expired_qty,
                "cost_valuation": cost_valuation,
                "retail_valuation": retail_valuation,
            },
            "sales": {
                "sales_count": sales_count,
                "gross_revenue": gross_rev,
                "net_revenue": net_rev,
                "cogs": cogs,
                "gross_profit": gross_profit,
                "gross_margin_pct": profit_margin,
                "average_order_value": aov,
            },
            "transfers": {
                "incoming_pending_count": incoming_pending,
                "outgoing_pending_count": outgoing_pending,
                "completed_count": completed_transfers,
            },
            "alerts": {
                "low_stock_count": len(low_stock_items),
                "low_stock_items": low_stock_items,
                "near_expiry_count": len(near_expiry_items),
                "near_expiry_items": near_expiry_items,
            },
            "activity": {
                "recent_sales": recent_sales,
                "recent_stock_movements": recent_movements,
            },
        }

    # -------------------------------------------------------------------------
    # 3. Analytics: Daily & Monthly Sales Trends
    # -------------------------------------------------------------------------
    @staticmethod
    def get_sales_trends(
        actor: User,
        shop_id: int | None = None,
        days: int = 30,
        group_by: str = "daily",
    ) -> list[dict[str, Any]]:
        """Compute continuous sales trend series with zero-fill for missing intervals.

        Enforces tenant/shop RBAC.
        """
        if shop_id is not None:
            shop, _ = verify_shop_access(
                user=actor,
                shop_id=shop_id,
                allow_inactive=True,
                required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
            )
            filter_shop_ids = [shop.id]
        else:
            if actor.role != "SUPER_ADMIN":
                raise AuthorizationError("Only Super Admins can query organization-wide trends without a shop_id.")
            shops = Shop.query.filter_by(organization_id=actor.organization_id).all()
            filter_shop_ids = [s.id for s in shops]

        if not filter_shop_ids:
            return []

        today = get_business_today()

        if group_by == "monthly":
            # Past 6 or 12 months
            months_back = max(2, min(24, days // 30 if days > 60 else 6))
            start_date = (today.replace(day=1) - timedelta(days=(months_back - 1) * 31)).replace(day=1)

            records = (
                db.session.query(
                    func.to_char(func.timezone("Asia/Kolkata", Sale.created_at), "YYYY-MM").label("month_str"),
                    func.count(Sale.id).label("cnt"),
                    func.coalesce(func.sum(Sale.total_amount), 0).label("rev"),
                    func.coalesce(func.sum(Sale.total_amount - Sale.total_cost_amount), 0).label("gp"),
                )
                .filter(
                    Sale.shop_id.in_(filter_shop_ids),
                    Sale.status == "COMPLETED",
                    Sale.created_at >= datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc),
                )
                .group_by("month_str")
                .order_by("month_str")
                .all()
            )

            rec_map = {r[0]: (int(r[1]), _to_float(r[2]), _to_float(r[3])) for r in records}

            # Continuous month fill
            result = []
            curr_month = start_date
            while curr_month <= today:
                m_str = curr_month.strftime("%Y-%m")
                cnt, rev, gp = rec_map.get(m_str, (0, 0.0, 0.0))
                result.append(
                    {
                        "period": m_str,
                        "label": curr_month.strftime("%b %Y"),
                        "sales_count": cnt,
                        "revenue": rev,
                        "gross_profit": gp,
                    }
                )
                if curr_month.month == 12:
                    curr_month = curr_month.replace(year=curr_month.year + 1, month=1)
                else:
                    curr_month = curr_month.replace(month=curr_month.month + 1)

            return result

        else:
            # Daily trends (default)
            num_days = max(1, min(365, int(days)))
            start_date = today - timedelta(days=num_days - 1)

            records = (
                db.session.query(
                    func.to_char(func.timezone("Asia/Kolkata", Sale.created_at), "YYYY-MM-DD").label("day_str"),
                    func.count(Sale.id).label("cnt"),
                    func.coalesce(func.sum(Sale.total_amount), 0).label("rev"),
                    func.coalesce(func.sum(Sale.total_amount - Sale.total_cost_amount), 0).label("gp"),
                )
                .filter(
                    Sale.shop_id.in_(filter_shop_ids),
                    Sale.status == "COMPLETED",
                    Sale.created_at >= datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc),
                )
                .group_by("day_str")
                .order_by("day_str")
                .all()
            )

            rec_map = {r[0]: (int(r[1]), _to_float(r[2]), _to_float(r[3])) for r in records}

            result = []
            for d in range(num_days):
                curr_d = start_date + timedelta(days=d)
                d_str = curr_d.strftime("%Y-%m-%d")
                cnt, rev, gp = rec_map.get(d_str, (0, 0.0, 0.0))
                result.append(
                    {
                        "period": d_str,
                        "label": curr_d.strftime("%d %b"),
                        "sales_count": cnt,
                        "revenue": rev,
                        "gross_profit": gp,
                    }
                )

            return result

    # -------------------------------------------------------------------------
    # 4. Analytics: Top-Selling Products
    # -------------------------------------------------------------------------
    @staticmethod
    def get_top_selling_products(
        actor: User,
        shop_id: int | None = None,
        limit: int = 5,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Retrieve top products by units sold and by total revenue.

        Enforces tenant and shop isolation.
        """
        if shop_id is not None:
            shop, _ = verify_shop_access(
                user=actor,
                shop_id=shop_id,
                allow_inactive=True,
                required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
            )
            filter_shop_ids = [shop.id]
        else:
            if actor.role != "SUPER_ADMIN":
                raise AuthorizationError("Only Super Admins can query organization-wide top products.")
            shops = Shop.query.filter_by(organization_id=actor.organization_id).all()
            filter_shop_ids = [s.id for s in shops]

        if not filter_shop_ids:
            return {"by_quantity": [], "by_revenue": []}

        q = (
            db.session.query(
                Product.id,
                Product.name,
                Product.sku,
                Product.category,
                func.coalesce(func.sum(SaleItem.quantity), 0).label("units_sold"),
                func.coalesce(func.sum(SaleItem.line_total), 0).label("total_revenue"),
            )
            .join(SaleItem, Product.id == SaleItem.product_id)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .filter(
                Sale.shop_id.in_(filter_shop_ids),
                Sale.status == "COMPLETED",
            )
        )

        if start_date:
            q = q.filter(Sale.created_at >= datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc))
        if end_date:
            q = q.filter(Sale.created_at <= datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc))

        q = q.group_by(Product.id, Product.name, Product.sku, Product.category)

        # By quantity
        by_qty_rows = q.order_by(func.sum(SaleItem.quantity).desc()).limit(limit).all()
        by_qty = [
            {
                "product_id": r[0],
                "product_name": r[1],
                "sku": r[2],
                "category": r[3],
                "units_sold": int(r[4]),
                "revenue": _to_float(r[5]),
            }
            for r in by_qty_rows
        ]

        # By revenue
        by_rev_rows = q.order_by(func.sum(SaleItem.line_total).desc()).limit(limit).all()
        by_rev = [
            {
                "product_id": r[0],
                "product_name": r[1],
                "sku": r[2],
                "category": r[3],
                "units_sold": int(r[4]),
                "revenue": _to_float(r[5]),
            }
            for r in by_rev_rows
        ]

        return {"by_quantity": by_qty, "by_revenue": by_rev}

    # -------------------------------------------------------------------------
    # 5. Analytics: Stock Distribution
    # -------------------------------------------------------------------------
    @staticmethod
    def get_stock_distribution(
        actor: User,
        shop_id: int | None = None,
    ) -> dict[str, Any]:
        """Break down inventory by category, status, and expiry risk horizons."""
        if shop_id is not None:
            shop, _ = verify_shop_access(
                user=actor,
                shop_id=shop_id,
                allow_inactive=True,
                required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
            )
            filter_shop_ids = [shop.id]
        else:
            if actor.role != "SUPER_ADMIN":
                raise AuthorizationError("Only Super Admins can query organization-wide stock distribution.")
            shops = Shop.query.filter_by(organization_id=actor.organization_id).all()
            filter_shop_ids = [s.id for s in shops]

        if not filter_shop_ids:
            return {"by_category": [], "by_status": [], "by_expiry_horizon": []}

        today = get_business_today()

        # 1. By Category
        cat_rows = (
            db.session.query(
                Product.category,
                func.coalesce(func.sum(InventoryBatch.available_qty), 0).label("qty"),
                func.coalesce(
                    func.sum(InventoryBatch.available_qty * InventoryBatch.unit_cost), 0
                ).label("valuation"),
                func.count(distinct(Product.id)).label("prod_count"),
            )
            .join(ShopProduct, Product.id == ShopProduct.product_id)
            .join(InventoryBatch, ShopProduct.id == InventoryBatch.shop_product_id)
            .filter(
                ShopProduct.shop_id.in_(filter_shop_ids),
                InventoryBatch.available_qty > 0,
            )
            .group_by(Product.category)
            .order_by(func.sum(InventoryBatch.available_qty).desc())
            .all()
        )

        by_cat = [
            {
                "category": r[0] or "Uncategorized",
                "quantity": int(r[1]),
                "valuation": _to_float(r[2]),
                "product_count": int(r[3]),
            }
            for r in cat_rows
        ]

        # 2. By Batch Status
        status_rows = (
            db.session.query(
                InventoryBatch.status,
                func.coalesce(func.sum(InventoryBatch.available_qty + InventoryBatch.reserved_qty), 0).label("qty"),
                func.count(InventoryBatch.id).label("batch_count"),
            )
            .join(ShopProduct, InventoryBatch.shop_product_id == ShopProduct.id)
            .filter(ShopProduct.shop_id.in_(filter_shop_ids))
            .group_by(InventoryBatch.status)
            .order_by(func.sum(InventoryBatch.available_qty + InventoryBatch.reserved_qty).desc())
            .all()
        )

        by_status = [
            {
                "status": r[0],
                "quantity": int(r[1]),
                "batch_count": int(r[2]),
            }
            for r in status_rows
        ]

        # 3. By Expiry Horizon (for active batches)
        horizon_rows = (
            db.session.query(
                case(
                    (InventoryBatch.expiry_date <= today, "EXPIRED"),
                    (InventoryBatch.expiry_date <= today + timedelta(days=7), "CRITICAL_7_DAYS"),
                    (InventoryBatch.expiry_date <= today + timedelta(days=30), "NEAR_EXPIRY_30_DAYS"),
                    (InventoryBatch.expiry_date <= today + timedelta(days=90), "MEDIUM_90_DAYS"),
                    else_="HEALTHY_90_PLUS_DAYS",
                ).label("horizon"),
                func.coalesce(func.sum(InventoryBatch.available_qty), 0).label("qty"),
                func.coalesce(
                    func.sum(InventoryBatch.available_qty * InventoryBatch.unit_cost), 0
                ).label("val"),
            )
            .join(ShopProduct, InventoryBatch.shop_product_id == ShopProduct.id)
            .filter(
                ShopProduct.shop_id.in_(filter_shop_ids),
                InventoryBatch.available_qty > 0,
                InventoryBatch.status == "ACTIVE",
            )
            .group_by("horizon")
            .all()
        )

        by_horizon = [
            {
                "horizon": r[0],
                "quantity": int(r[1]),
                "valuation": _to_float(r[2]),
            }
            for r in horizon_rows
        ]

        return {
            "by_category": by_cat,
            "by_status": by_status,
            "by_expiry_horizon": by_horizon,
        }

    # -------------------------------------------------------------------------
    # 6. Analytics: Transfer Activity
    # -------------------------------------------------------------------------
    @staticmethod
    def get_transfer_activity(
        actor: User,
        shop_id: int | None = None,
    ) -> dict[str, Any]:
        """Aggregate inter-store transfer counts, throughput, and status distribution."""
        if shop_id is not None:
            shop, _ = verify_shop_access(
                user=actor,
                shop_id=shop_id,
                allow_inactive=True,
                required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
            )
            filter_q = (TransferRequest.source_shop_id == shop.id) | (TransferRequest.recipient_shop_id == shop.id)
        else:
            if actor.role != "SUPER_ADMIN":
                raise AuthorizationError("Only Super Admins can query organization-wide transfer activity.")
            filter_q = TransferRequest.organization_id == actor.organization_id

        status_counts = (
            db.session.query(
                TransferRequest.status,
                func.count(TransferRequest.id).label("cnt"),
                func.coalesce(func.sum(TransferRequest.quantity), 0).label("qty"),
            )
            .filter(filter_q)
            .group_by(TransferRequest.status)
            .all()
        )

        by_status = {r[0]: {"count": int(r[1]), "units": int(r[2])} for r in status_counts}

        completed = by_status.get("COMPLETED", {"count": 0, "units": 0})
        in_transit = by_status.get("DISPATCHED_IN_TRANSIT", {"count": 0, "units": 0})
        approved = by_status.get("APPROVED_RESERVED", {"count": 0, "units": 0})
        pending_src = by_status.get("PENDING_SOURCE_APPROVAL", {"count": 0, "units": 0})
        pending_rec = by_status.get("PENDING_RECIPIENT_APPROVAL", {"count": 0, "units": 0})
        rejected = by_status.get("REJECTED", {"count": 0, "units": 0})
        cancelled = by_status.get("CANCELLED", {"count": 0, "units": 0})

        total_transfers = sum(item["count"] for item in by_status.values())
        total_units = sum(item["units"] for item in by_status.values())

        return {
            "total_transfers": total_transfers,
            "total_units_transferred": total_units,
            "completed": completed,
            "in_transit": in_transit,
            "pending": {
                "count": approved["count"] + pending_src["count"] + pending_rec["count"],
                "units": approved["units"] + pending_src["units"] + pending_rec["units"],
            },
            "rejected_or_cancelled": {
                "count": rejected["count"] + cancelled["count"],
                "units": rejected["units"] + cancelled["units"],
            },
            "breakdown": [
                {"status": k, "count": v["count"], "units": v["units"]}
                for k, v in by_status.items()
            ],
        }

    # -------------------------------------------------------------------------
    # 7. Tabular Report Generation (SALES, INVENTORY, NEAR_EXPIRY, TRANSFERS)
    # -------------------------------------------------------------------------
    @staticmethod
    def generate_report(
        actor: User,
        report_type: str,
        start_date: date | None = None,
        end_date: date | None = None,
        shop_id: int | None = None,
        category: str | None = None,
        product_id: int | None = None,
    ) -> dict[str, Any]:
        """Generate structured tabular report with metadata and summary totals.

        Supported types: SALES, INVENTORY, NEAR_EXPIRY, TRANSFERS.
        Validates date ranges and strictly isolates shops.
        """
        valid_types = {"SALES", "INVENTORY", "NEAR_EXPIRY", "TRANSFERS"}
        normalized_type = str(report_type).strip().upper()
        if normalized_type not in valid_types:
            raise ValidationError(
                f"Invalid report type '{report_type}'. Allowed types: {', '.join(sorted(valid_types))}."
            )

        # Enforce date range
        start_d, end_d = validate_report_date_range(start_date, end_date)

        # Shop filter & isolation
        if shop_id is not None:
            shop, _ = verify_shop_access(
                user=actor,
                shop_id=shop_id,
                allow_inactive=True,
                required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
            )
            filter_shop_ids = [shop.id]
            scope_desc = f"Shop: {shop.name} ({shop.code})"
        else:
            if actor.role != "SUPER_ADMIN":
                # For shop owner/staff without shop_id param, default to all assigned shops
                shops = Shop.query.filter_by(
                    organization_id=actor.organization_id
                ).all()
                accessible_shops = []
                for s in shops:
                    try:
                        verify_shop_access(actor, s.id, allow_inactive=True)
                        accessible_shops.append(s)
                    except AuthorizationError:
                        pass
                if not accessible_shops:
                    raise AuthorizationError("User has no accessible shops for report generation.")
                filter_shop_ids = [s.id for s in accessible_shops]
                scope_desc = f"{len(accessible_shops)} Assigned Shops"
            else:
                shops = Shop.query.filter_by(organization_id=actor.organization_id).all()
                filter_shop_ids = [s.id for s in shops]
                scope_desc = "Organization-Wide (All Shops)"

        today = get_business_today()

        # =====================================================================
        # REPORT TYPE: SALES
        # =====================================================================
        if normalized_type == "SALES":
            columns = [
                "Invoice Number",
                "Date & Time",
                "Shop Name",
                "Customer",
                "Payment Mode",
                "Items Count",
                "Subtotal (INR)",
                "Discount (INR)",
                "Total (INR)",
                "COGS (INR)",
                "Gross Profit (INR)",
                "Status",
            ]

            q = (
                Sale.query.join(Shop, Sale.shop_id == Shop.id)
                .filter(
                    Sale.shop_id.in_(filter_shop_ids),
                    Sale.created_at >= datetime.combine(start_d, datetime.min.time(), tzinfo=timezone.utc),
                    Sale.created_at <= datetime.combine(end_d, datetime.max.time(), tzinfo=timezone.utc),
                )
                .order_by(Sale.created_at.desc())
            )

            sales_records = q.all()

            rows = []
            total_gross = 0.0
            total_cost = 0.0
            total_gp = 0.0

            for s in sales_records:
                items_cnt = len(s.items) if s.items else 0
                tot = _to_float(s.total_amount)
                cogs_val = _to_float(s.total_cost_amount)
                gp_val = _to_float(s.total_amount - s.total_cost_amount)

                if s.status == "COMPLETED":
                    total_gross += tot
                    total_cost += cogs_val
                    total_gp += gp_val

                rows.append(
                    {
                        "invoice_number": s.invoice_number,
                        "date_time": s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else "—",
                        "shop_name": s.shop.name if s.shop else "—",
                        "customer": s.customer_name or "Walk-in Customer",
                        "payment_mode": s.payment_mode or "—",
                        "items_count": items_cnt,
                        "subtotal": _to_float(s.subtotal_amount),
                        "discount": _to_float(s.discount_amount),
                        "total": tot,
                        "cogs": cogs_val,
                        "gross_profit": gp_val,
                        "status": s.status,
                    }
                )

            summary = {
                "total_records": len(rows),
                "completed_sales_count": len([r for r in rows if r["status"] == "COMPLETED"]),
                "total_gross_revenue": round(total_gross, 2),
                "total_cogs": round(total_cost, 2),
                "total_gross_profit": round(total_gp, 2),
                "gross_margin_pct": round((total_gp / total_gross) * 100, 2) if total_gross > 0 else 0.0,
            }

        # =====================================================================
        # REPORT TYPE: INVENTORY
        # =====================================================================
        elif normalized_type == "INVENTORY":
            columns = [
                "Shop Name",
                "Product SKU",
                "Product Name",
                "Category",
                "Unit",
                "Sellable Stock",
                "Reserved Stock",
                "Damaged Stock",
                "Total Stock",
                "Reorder Level",
                "Avg Purchase Cost (INR)",
                "Selling Price (INR)",
                "Valuation (INR)",
                "Status",
            ]

            q = (
                db.session.query(
                    Shop.name.label("shop_name"),
                    Product.sku,
                    Product.name.label("product_name"),
                    Product.category,
                    Product.unit,
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    (InventoryBatch.status == "ACTIVE") & (InventoryBatch.expiry_date > today),
                                    InventoryBatch.available_qty,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("sellable_qty"),
                    func.coalesce(func.sum(InventoryBatch.reserved_qty), 0).label("reserved_qty"),
                    func.coalesce(func.sum(InventoryBatch.damaged_qty), 0).label("damaged_qty"),
                    func.coalesce(func.sum(InventoryBatch.available_qty + InventoryBatch.reserved_qty), 0).label("total_qty"),
                    ShopProduct.low_stock_threshold,
                    func.coalesce(func.avg(InventoryBatch.unit_cost), 0).label("avg_cost"),
                    func.coalesce(func.max(ShopProduct.selling_price), 0).label("unit_price"),
                    func.coalesce(
                        func.sum(InventoryBatch.available_qty * InventoryBatch.unit_cost), 0
                    ).label("valuation"),
                    ShopProduct.is_active,
                )
                .join(ShopProduct, Shop.id == ShopProduct.shop_id)
                .join(Product, ShopProduct.product_id == Product.id)
                .outerjoin(InventoryBatch, ShopProduct.id == InventoryBatch.shop_product_id)
                .filter(Shop.id.in_(filter_shop_ids))
            )

            if category:
                q = q.filter(Product.category.ilike(f"%{category.strip()}%"))
            if product_id:
                q = q.filter(Product.id == int(product_id))

            q = q.group_by(
                Shop.name,
                Product.sku,
                Product.name,
                Product.category,
                Product.unit,
                ShopProduct.low_stock_threshold,
                ShopProduct.is_active,
            ).order_by(Shop.name.asc(), Product.name.asc())

            inv_records = q.all()

            rows = []
            total_stock_all = 0
            total_valuation_all = 0.0

            for r in inv_records:
                t_qty = int(r.total_qty or 0)
                val = _to_float(r.valuation)
                total_stock_all += t_qty
                total_valuation_all += val

                rows.append(
                    {
                        "shop_name": r.shop_name,
                        "sku": r.sku,
                        "product_name": r.product_name,
                        "category": r.category or "—",
                        "unit": r.unit or "units",
                        "sellable_stock": int(r.sellable_qty or 0),
                        "reserved_stock": int(r.reserved_qty or 0),
                        "damaged_stock": int(r.damaged_qty or 0),
                        "total_stock": t_qty,
                        "reorder_level": r.low_stock_threshold,
                        "avg_cost": _to_float(r.avg_cost),
                        "selling_price": _to_float(r.unit_price),
                        "valuation": val,
                        "status": "ACTIVE" if r.is_active else "INACTIVE",
                    }
                )

            summary = {
                "total_records": len(rows),
                "total_stock_units": total_stock_all,
                "total_inventory_valuation": round(total_valuation_all, 2),
            }

        # =====================================================================
        # REPORT TYPE: NEAR_EXPIRY
        # =====================================================================
        elif normalized_type == "NEAR_EXPIRY":
            columns = [
                "Shop Name",
                "Product SKU",
                "Product Name",
                "Category",
                "Batch Code",
                "Expiry Date",
                "Days Left",
                "Quantity",
                "Purchase Cost (INR)",
                "Valuation At Risk (INR)",
                "Batch Status",
            ]

            q = (
                db.session.query(
                    Shop.name.label("shop_name"),
                    Product.sku,
                    Product.name.label("product_name"),
                    Product.category,
                    InventoryBatch.batch_code,
                    InventoryBatch.expiry_date,
                    InventoryBatch.available_qty,
                    InventoryBatch.unit_cost,
                    (InventoryBatch.available_qty * InventoryBatch.unit_cost).label("risk_val"),
                    InventoryBatch.status,
                )
                .join(ShopProduct, Shop.id == ShopProduct.shop_id)
                .join(Product, ShopProduct.product_id == Product.id)
                .join(InventoryBatch, ShopProduct.id == InventoryBatch.shop_product_id)
                .filter(
                    Shop.id.in_(filter_shop_ids),
                    InventoryBatch.available_qty > 0,
                    InventoryBatch.expiry_date <= end_d,
                )
            )

            if category:
                q = q.filter(Product.category.ilike(f"%{category.strip()}%"))

            q = q.order_by(InventoryBatch.expiry_date.asc())

            exp_records = q.all()

            rows = []
            total_at_risk_qty = 0
            total_at_risk_val = 0.0

            for r in exp_records:
                qty = int(r.available_qty or 0)
                val = _to_float(r.risk_val)
                total_at_risk_qty += qty
                total_at_risk_val += val
                days_left = (r.expiry_date - today).days if r.expiry_date else 0

                rows.append(
                    {
                        "shop_name": r.shop_name,
                        "sku": r.sku,
                        "product_name": r.product_name,
                        "category": r.category or "—",
                        "batch_code": r.batch_code,
                        "expiry_date": r.expiry_date.isoformat() if r.expiry_date else "—",
                        "days_left": days_left,
                        "quantity": qty,
                        "purchase_cost": _to_float(r.unit_cost),
                        "valuation_at_risk": val,
                        "batch_status": r.status,
                    }
                )

            summary = {
                "total_records": len(rows),
                "total_at_risk_units": total_at_risk_qty,
                "total_at_risk_valuation": round(total_at_risk_val, 2),
            }

        # =====================================================================
        # REPORT TYPE: TRANSFERS
        # =====================================================================
        else:  # TRANSFERS
            columns = [
                "Transfer Code",
                "Created Date",
                "Source Shop",
                "Recipient Shop",
                "Product SKU",
                "Product Name",
                "Quantity",
                "Unit Transfer Price (INR)",
                "Total Value (INR)",
                "Status",
                "Updated Date",
            ]

            src_shop = db.aliased(Shop)
            rec_shop = db.aliased(Shop)

            q = (
                db.session.query(
                    TransferRequest.transfer_code,
                    TransferRequest.created_at,
                    src_shop.name.label("source_shop_name"),
                    rec_shop.name.label("recipient_shop_name"),
                    Product.sku,
                    Product.name.label("product_name"),
                    TransferRequest.quantity,
                    TransferRequest.unit_transfer_price,
                    (TransferRequest.quantity * TransferRequest.unit_transfer_price).label("tot_val"),
                    TransferRequest.status,
                    TransferRequest.updated_at,
                )
                .join(src_shop, TransferRequest.source_shop_id == src_shop.id)
                .join(rec_shop, TransferRequest.recipient_shop_id == rec_shop.id)
                .join(Product, TransferRequest.product_id == Product.id)
                .filter(
                    (TransferRequest.source_shop_id.in_(filter_shop_ids))
                    | (TransferRequest.recipient_shop_id.in_(filter_shop_ids)),
                    TransferRequest.created_at >= datetime.combine(start_d, datetime.min.time(), tzinfo=timezone.utc),
                    TransferRequest.created_at <= datetime.combine(end_d, datetime.max.time(), tzinfo=timezone.utc),
                )
                .order_by(TransferRequest.created_at.desc())
            )

            trf_records = q.all()

            rows = []
            total_trf_qty = 0
            total_trf_val = 0.0

            for r in trf_records:
                qty = int(r.quantity or 0)
                val = _to_float(r.tot_val)
                total_trf_qty += qty
                total_trf_val += val

                rows.append(
                    {
                        "transfer_code": r.transfer_code,
                        "created_date": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "—",
                        "source_shop": r.source_shop_name,
                        "recipient_shop": r.recipient_shop_name,
                        "sku": r.sku,
                        "product_name": r.product_name,
                        "quantity": qty,
                        "unit_price": _to_float(r.unit_transfer_price),
                        "total_value": val,
                        "status": r.status,
                        "updated_date": r.updated_at.strftime("%Y-%m-%d %H:%M") if r.updated_at else "—",
                    }
                )

            summary = {
                "total_records": len(rows),
                "total_transfer_units": total_trf_qty,
                "total_transfer_value": round(total_trf_val, 2),
                "completed_count": len([r for r in rows if r["status"] == "COMPLETED"]),
            }

        return {
            "report_type": normalized_type,
            "scope": scope_desc,
            "filters": {
                "start_date": start_d.isoformat(),
                "end_date": end_d.isoformat(),
                "shop_id": shop_id,
                "category": category,
                "product_id": product_id,
            },
            "columns": columns,
            "rows": rows,
            "summary": summary,
            "generated_at": _utc_now().isoformat(),
        }

    # -------------------------------------------------------------------------
    # 8. RFC 4180 Streaming CSV Export
    # -------------------------------------------------------------------------
    @staticmethod
    def generate_csv_export(
        actor: User,
        report_type: str,
        start_date: date | None = None,
        end_date: date | None = None,
        shop_id: int | None = None,
        category: str | None = None,
        product_id: int | None = None,
    ) -> tuple[str, str]:
        """Generate RFC 4180 compliant CSV string and filename.

        Returns (csv_content, filename).
        """
        report = ReportService.generate_report(
            actor=actor,
            report_type=report_type,
            start_date=start_date,
            end_date=end_date,
            shop_id=shop_id,
            category=category,
            product_id=product_id,
        )

        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

        # Write header
        writer.writerow(report["columns"])

        # Write rows
        for row in report["rows"]:
            writer.writerow(list(row.values()))

        csv_content = output.getvalue()
        output.close()

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"sanchay_report_{report['report_type'].lower()}_{timestamp_str}.csv"

        return csv_content, filename
