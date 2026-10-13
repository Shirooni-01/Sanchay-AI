"""Deterministic Rule-Based Stock Analysis Engine (Phase 5).

Implements:
- Historical sales velocity calculation over an explicit lookback window (default 14 days),
  accounting for completed sales, item returns, and excluding cancelled/voided invoices.
- Safe handling of missing and sparse sales history (never treating missing history as
  proof of zero demand) and explicit stockout censoring documentation.
- FEFO queue-aware batch expiry-risk estimation accounting for earlier-expiring batches
  of the same product and excluding reserved, damaged, and expired stock.
- Deterministic surplus stock detection that respects estimated demand before expiry,
  earlier-expiring batches, existing reservations, minimum safety stock, and reorder
  thresholds without recommending that a shop transfer stock it needs to retain.
- Low-stock and replenishment analysis against configured reorder and safety thresholds.
- Rule-based recommendations with full numerical drivers, calculation assumptions,
  rule version, and analysis timestamp.
- Fingerprint-based freshness/staleness detection and alert deduplication without
  mutating any inventory or stock movement records.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, has_app_context
from sqlalchemy import case, func

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch, StockMovement
from app.models.organization import Shop
from app.models.sales import Sale, SaleItem, SaleReturn
from app.models.user import User
from app.services.inventory_service import get_business_today
from app.utils.decorators import verify_shop_access
from app.utils.errors import AuthorizationError, ResourceNotFoundError
from app.utils.validators import validate_analysis_params


# Process-scoped snapshot store keyed by (shop_id, config_signature).
# Automatically invalidated whenever PostgreSQL state fingerprint changes or TTL expires.
_ANALYSIS_SNAPSHOT_STORE: dict[tuple[int, str], dict[str, Any]] = {}


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


def _get_config_int(key: str, default: int) -> int:
    if has_app_context():
        return int(current_app.config.get(key, default))
    return default


def _get_rule_version() -> str:
    if has_app_context():
        return str(
            current_app.config.get(
                "ANALYSIS_RULE_VERSION", "v1.0.0-phase5-deterministic"
            )
        )
    return "v1.0.0-phase5-deterministic"


class StockAnalysisService:
    """Deterministic, read-only Rule-Based Stock Analysis Engine (Phase 5)."""

    @staticmethod
    def clear_snapshot_cache(shop_id: int | None = None) -> None:
        """Clear cached analysis snapshots (all or for a specific shop_id)."""
        if shop_id is None:
            _ANALYSIS_SNAPSHOT_STORE.clear()
            return
        keys_to_remove = [
            key for key in _ANALYSIS_SNAPSHOT_STORE if key[0] == int(shop_id)
        ]
        for key in keys_to_remove:
            _ANALYSIS_SNAPSHOT_STORE.pop(key, None)

    @staticmethod
    def compute_shop_data_fingerprint(
        shop_id: int,
        ref_date: date | None = None,
    ) -> str:
        """Compute a deterministic SHA-256 fingerprint of all PostgreSQL state affecting shop analysis.

        Includes shop status/thresholds, shop_products, inventory_batches buckets,
        stock_movements count/max_id, sales, sale_items, and sale_returns.
        Any stock receipt, adjustment, POS sale, return, cancellation, or threshold edit
        immediately changes this fingerprint and marks cached results stale.
        """
        today = ref_date or get_business_today()
        shop = db.session.get(Shop, int(shop_id))
        shop_token = (
            f"{shop.id}:{shop.status}:{shop.low_stock_default_threshold}:"
            f"{shop.near_expiry_days_threshold}:{shop.updated_at.isoformat() if shop and shop.updated_at else ''}"
            if shop
            else "none"
        )

        sp_agg = (
            db.session.query(
                func.count(ShopProduct.id),
                func.max(ShopProduct.updated_at),
                func.coalesce(func.sum(ShopProduct.low_stock_threshold), 0),
                func.coalesce(func.sum(ShopProduct.near_expiry_days), 0),
                func.coalesce(func.sum(ShopProduct.surplus_threshold), 0),
            )
            .filter(ShopProduct.shop_id == int(shop_id))
            .first()
        )

        batch_agg = (
            db.session.query(
                func.count(InventoryBatch.id),
                func.max(InventoryBatch.updated_at),
                func.coalesce(func.sum(InventoryBatch.available_qty), 0),
                func.coalesce(func.sum(InventoryBatch.reserved_qty), 0),
                func.coalesce(func.sum(InventoryBatch.damaged_qty), 0),
                func.coalesce(func.sum(InventoryBatch.expired_qty), 0),
            )
            .filter(InventoryBatch.shop_id == int(shop_id))
            .first()
        )

        mov_agg = (
            db.session.query(
                func.count(StockMovement.id),
                func.coalesce(func.max(StockMovement.id), 0),
            )
            .filter(StockMovement.shop_id == int(shop_id))
            .first()
        )

        sale_agg = (
            db.session.query(
                func.count(Sale.id),
                func.max(Sale.updated_at),
                func.coalesce(func.sum(Sale.total_amount), Decimal("0.00")),
                func.coalesce(func.sum(Sale.returned_amount), Decimal("0.00")),
            )
            .filter(Sale.shop_id == int(shop_id))
            .first()
        )

        cancelled_sales_count = (
            db.session.query(func.count(Sale.id))
            .filter(Sale.shop_id == int(shop_id), Sale.status == "CANCELLED")
            .scalar()
            or 0
        )

        item_agg = (
            db.session.query(
                func.count(SaleItem.id),
                func.coalesce(func.sum(SaleItem.quantity), 0),
                func.coalesce(func.sum(SaleItem.returned_quantity), 0),
            )
            .filter(SaleItem.shop_id == int(shop_id))
            .first()
        )

        ret_agg = (
            db.session.query(
                func.count(SaleReturn.id),
                func.coalesce(func.max(SaleReturn.id), 0),
            )
            .filter(SaleReturn.shop_id == int(shop_id))
            .first()
        )

        raw_state = {
            "business_date": today.isoformat(),
            "shop": shop_token,
            "shop_products": [
                int(sp_agg[0] or 0),
                sp_agg[1].isoformat() if sp_agg and sp_agg[1] else "",
                int(sp_agg[2] or 0),
                int(sp_agg[3] or 0),
                int(sp_agg[4] or 0),
            ],
            "batches": [
                int(batch_agg[0] or 0),
                batch_agg[1].isoformat() if batch_agg and batch_agg[1] else "",
                int(batch_agg[2] or 0),
                int(batch_agg[3] or 0),
                int(batch_agg[4] or 0),
                int(batch_agg[5] or 0),
            ],
            "movements": [int(mov_agg[0] or 0), int(mov_agg[1] or 0)],
            "sales": [
                int(sale_agg[0] or 0),
                sale_agg[1].isoformat() if sale_agg and sale_agg[1] else "",
                str(sale_agg[2] or "0.00"),
                str(sale_agg[3] or "0.00"),
                int(cancelled_sales_count),
            ],
            "sale_items": [
                int(item_agg[0] or 0),
                int(item_agg[1] or 0),
                int(item_agg[2] or 0),
            ],
            "sale_returns": [int(ret_agg[0] or 0), int(ret_agg[1] or 0)],
        }
        canonical_json = json.dumps(raw_state, sort_keys=True)
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _build_config_signature(params: dict[str, Any], ref_date: date) -> str:
        payload = {
            "lookback_days": params["lookback_days"],
            "safety_stock_days": params["safety_stock_days"],
            "safety_stock_qty": params["safety_stock_qty"],
            "surplus_coverage_days": params["surplus_coverage_days"],
            "ref_date": ref_date.isoformat(),
            "rule_version": _get_rule_version(),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

    @staticmethod
    def check_snapshot_freshness(
        shop_id: int,
        lookback_days: int | None = None,
        safety_stock_days: int | None = None,
        safety_stock_qty: int | None = None,
        surplus_coverage_days: int | None = None,
        ref_date: date | str | None = None,
    ) -> dict[str, Any]:
        """Evaluate whether a stored analysis snapshot for shop_id is fresh or stale."""
        cleaned = validate_analysis_params(
            lookback_days_raw=lookback_days,
            safety_stock_days_raw=safety_stock_days,
            safety_stock_qty_raw=safety_stock_qty,
            surplus_coverage_days_raw=surplus_coverage_days,
            ref_date_raw=ref_date,
            default_lookback_days=_get_config_int("ANALYSIS_LOOKBACK_DAYS", 14),
            default_safety_stock_days=_get_config_int(
                "ANALYSIS_DEFAULT_SAFETY_STOCK_DAYS", 2
            ),
            default_surplus_coverage_days=_get_config_int(
                "ANALYSIS_SURPLUS_COVERAGE_DAYS", 30
            ),
        )
        today = cleaned["ref_date"] or get_business_today()
        sig = StockAnalysisService._build_config_signature(cleaned, today)
        cache_key = (int(shop_id), sig)
        stored = _ANALYSIS_SNAPSHOT_STORE.get(cache_key)
        current_fp = StockAnalysisService.compute_shop_data_fingerprint(
            shop_id=int(shop_id), ref_date=today
        )
        rule_version = _get_rule_version()
        ttl_seconds = _get_config_int("ANALYSIS_STALE_AFTER_SECONDS", 300)

        if stored is None:
            return {
                "has_snapshot": False,
                "is_stale": True,
                "stale_reason": "NO_CACHED_SNAPSHOT",
                "current_data_fingerprint": current_fp,
                "cached_data_fingerprint": None,
                "rule_version": rule_version,
                "stale_after_seconds": ttl_seconds,
            }

        now_utc = _utc_now()
        age_seconds = max(
            0.0, (now_utc - stored["analyzed_at_dt"]).total_seconds()
        )
        if stored["rule_version"] != rule_version:
            reason = "RULE_VERSION_CHANGED"
            is_stale = True
        elif stored["business_date"] != today.isoformat():
            reason = "DATE_CHANGED"
            is_stale = True
        elif stored["data_fingerprint"] != current_fp:
            reason = "DATA_CHANGED"
            is_stale = True
        elif age_seconds > ttl_seconds:
            reason = "TTL_EXPIRED"
            is_stale = True
        else:
            reason = "FRESH"
            is_stale = False

        return {
            "has_snapshot": True,
            "is_stale": is_stale,
            "stale_reason": reason,
            "age_seconds": round(age_seconds, 2),
            "stale_after_seconds": ttl_seconds,
            "analyzed_at": stored["analyzed_at"],
            "evaluation_count": stored["evaluation_count"],
            "current_data_fingerprint": current_fp,
            "cached_data_fingerprint": stored["data_fingerprint"],
            "rule_version": rule_version,
        }

    @staticmethod
    def calculate_product_sales_history(
        shop_id: int,
        product_id: int,
        lookback_days: int = 14,
        ref_date: date | None = None,
        current_sellable_qty: int = 0,
        is_low_stock: bool = False,
        shop_window_total_sales_count: int | None = None,
    ) -> dict[str, Any]:
        """Calculate historical sales velocity and data sufficiency for (shop_id, product_id).

        Enforces Section 3 requirements:
        - Uses only non-cancelled sales in the target shop (Sale.status != 'CANCELLED').
        - Deducts returned quantities (net_qty = max(0, quantity - returned_quantity)).
        - Tracks cancelled sales separately to confirm voided transactions are excluded.
        - Safely classifies missing vs. sparse vs. sufficient sales history.
        - Never treats missing history as proof of zero demand.
        - Documents stockout censoring effects when inventory is depleted or low.
        """
        today = ref_date or get_business_today()
        tz = _get_business_tz()
        window_start_date = today - timedelta(days=lookback_days - 1)
        window_end_date = today

        start_dt_utc = datetime.combine(
            window_start_date, time.min, tzinfo=tz
        ).astimezone(timezone.utc)
        end_dt_utc = datetime.combine(
            window_end_date, time.max, tzinfo=tz
        ).astimezone(timezone.utc)

        min_history_days = min(
            lookback_days, _get_config_int("ANALYSIS_MIN_HISTORY_DAYS", 7)
        )
        min_active_sale_days = _get_config_int("ANALYSIS_MIN_ACTIVE_SALE_DAYS", 2)

        # Query all SaleItem + Sale rows for this shop & product within the window
        window_rows = (
            db.session.query(SaleItem, Sale)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .filter(
                Sale.shop_id == int(shop_id),
                SaleItem.shop_id == int(shop_id),
                SaleItem.product_id == int(product_id),
                Sale.created_at >= start_dt_utc,
                Sale.created_at <= end_dt_utc,
            )
            .all()
        )

        gross_units_sold = 0
        returned_units = 0
        net_units_sold = 0
        cancelled_units_excluded = 0
        valid_sale_ids: set[int] = set()
        cancelled_sale_ids: set[int] = set()
        active_sale_dates: set[str] = set()

        for s_item, sale in window_rows:
            qty = int(s_item.quantity or 0)
            ret = int(s_item.returned_quantity or 0)
            if sale.status == "CANCELLED":
                cancelled_units_excluded += qty
                cancelled_sale_ids.add(sale.id)
                continue

            valid_sale_ids.add(sale.id)
            gross_units_sold += qty
            returned_units += ret
            net_line = max(0, qty - ret)
            net_units_sold += net_line
            if net_line > 0 and sale.created_at is not None:
                local_sale_date = sale.created_at.astimezone(tz).date().isoformat()
                active_sale_dates.add(local_sale_date)

        # Check all-time non-cancelled sales and earliest stock date for this (shop_id, product_id)
        all_time_sale_agg = (
            db.session.query(
                func.count(func.distinct(Sale.id)),
                func.min(Sale.created_at),
                func.coalesce(
                    func.sum(SaleItem.quantity - SaleItem.returned_quantity), 0
                ),
            )
            .join(SaleItem, SaleItem.sale_id == Sale.id)
            .filter(
                Sale.shop_id == int(shop_id),
                SaleItem.shop_id == int(shop_id),
                SaleItem.product_id == int(product_id),
                Sale.status != "CANCELLED",
            )
            .first()
        )
        all_time_sales_count = int(all_time_sale_agg[0] or 0) if all_time_sale_agg else 0
        earliest_sale_dt = all_time_sale_agg[1] if all_time_sale_agg else None
        all_time_net_units = int(all_time_sale_agg[2] or 0) if all_time_sale_agg else 0

        batch_time_agg = (
            db.session.query(
                func.min(InventoryBatch.received_at),
                func.min(InventoryBatch.manufactured_date),
                func.count(InventoryBatch.id),
            )
            .filter(
                InventoryBatch.shop_id == int(shop_id),
                InventoryBatch.product_id == int(product_id),
            )
            .first()
        )
        earliest_batch_dt = batch_time_agg[0] if batch_time_agg else None
        total_batches_ever = int(batch_time_agg[2] or 0) if batch_time_agg else 0

        candidate_start_dates: list[date] = []
        if earliest_batch_dt is not None:
            candidate_start_dates.append(earliest_batch_dt.astimezone(tz).date())
        if earliest_sale_dt is not None:
            candidate_start_dates.append(earliest_sale_dt.astimezone(tz).date())

        if candidate_start_dates:
            first_observed_date = min(candidate_start_dates)
            days_stocked = max(1, (today - first_observed_date).days + 1)
        else:
            first_observed_date = None
            days_stocked = 0

        if shop_window_total_sales_count is None:
            shop_window_total_sales_count = (
                db.session.query(func.count(Sale.id))
                .filter(
                    Sale.shop_id == int(shop_id),
                    Sale.status != "CANCELLED",
                    Sale.created_at >= start_dt_utc,
                    Sale.created_at <= end_dt_utc,
                )
                .scalar()
                or 0
            )

        distinct_sale_days = len(active_sale_dates)
        window_sales_count = len(valid_sale_ids)

        # Canonical window velocity (units / lookback_days) per BR-015 & BUSINESS_RULES.md 2.3
        daily_sales_velocity = round(float(net_units_sold) / float(lookback_days), 4)
        effective_obs_days = (
            max(1, min(lookback_days, days_stocked))
            if days_stocked > 0
            else lookback_days
        )
        tenure_adjusted_velocity = round(
            float(net_units_sold) / float(effective_obs_days), 4
        )

        # Classify data sufficiency without treating missing history as proof of zero demand
        if net_units_sold == 0 and all_time_net_units == 0:
            if (
                days_stocked >= lookback_days
                and current_sellable_qty > 0
                and int(shop_window_total_sales_count) >= 3
            ):
                history_status = "ESTABLISHED_ZERO_DEMAND"
                is_demand_reliable = True
                insufficient_data = False
                zero_demand_proven = True
                demand_confidence = "MODERATE_ZERO_OBSERVED"
            else:
                history_status = "NO_SALES_HISTORY"
                is_demand_reliable = False
                insufficient_data = True
                zero_demand_proven = False
                demand_confidence = "INSUFFICIENT_DATA"
        elif (
            net_units_sold == 0
            and all_time_net_units > 0
            and current_sellable_qty == 0
        ):
            # Product sold previously, but is currently out of stock (stockout censoring)
            history_status = "SPARSE_HISTORY"
            is_demand_reliable = False
            insufficient_data = True
            zero_demand_proven = False
            demand_confidence = "INSUFFICIENT_STOCKOUT_CENSORED"
        elif (
            distinct_sale_days < min_active_sale_days
            and window_sales_count < min_active_sale_days
        ) or (0 < days_stocked < min_history_days):
            history_status = "SPARSE_HISTORY"
            # When positive sales exist in the window (net_units_sold > 0), we can compute
            # provisional deterministic estimates while flagging sparse-history caution.
            is_demand_reliable = net_units_sold > 0
            insufficient_data = True
            zero_demand_proven = False
            demand_confidence = "LOW_SPARSE_HISTORY"
        else:
            history_status = "SUFFICIENT_HISTORY"
            is_demand_reliable = True
            insufficient_data = False
            zero_demand_proven = net_units_sold == 0
            demand_confidence = (
                "HIGH" if distinct_sale_days >= 5 else "MODERATE"
            )

        stockout_censoring_note: str | None = None
        if current_sellable_qty == 0:
            stockout_censoring_note = (
                "Product is currently OUT OF STOCK (0 sellable units). Historical POS sales "
                "capture only realized transactions and may understate true customer demand "
                "due to stockout censoring."
            )
        elif is_low_stock:
            stockout_censoring_note = (
                "Product is currently in LOW STOCK. If shelf availability was constrained during "
                "the lookback window, observed sales velocity may be a conservative lower bound."
            )

        if history_status == "NO_SALES_HISTORY":
            history_explanation = (
                f"No completed POS sales exist for this product in Shop #{shop_id} within the "
                f"{lookback_days}-day window ({window_start_date.isoformat()} to {window_end_date.isoformat()}). "
                "Missing sales history is NOT treated as proof of zero demand."
            )
        elif history_status == "SPARSE_HISTORY":
            history_explanation = (
                f"Sparse sales history detected: {net_units_sold} net unit(s) sold across "
                f"{window_sales_count} completed invoice(s) ({distinct_sale_days} active sale day(s)) "
                f"over the {lookback_days}-day window ({daily_sales_velocity:.2f} units/day). "
                "Treat projections as provisional until at least "
                f"{min_history_days} stocked days and {min_active_sale_days} sale days accumulate."
            )
        elif history_status == "ESTABLISHED_ZERO_DEMAND":
            history_explanation = (
                f"Product has been stocked for {days_stocked} days with {current_sellable_qty} sellable "
                f"units while the shop processed {shop_window_total_sales_count} sales, resulting in "
                f"0 net units sold over the {lookback_days}-day window."
            )
        else:
            history_explanation = (
                f"Sufficient sales history: {net_units_sold} net unit(s) sold "
                f"({gross_units_sold} gross sold minus {returned_units} returned; "
                f"{cancelled_units_excluded} cancelled unit(s) excluded) across "
                f"{distinct_sale_days} sale day(s) in the {lookback_days}-day window "
                f"= {daily_sales_velocity:.2f} units/day."
            )

        return {
            "shop_id": int(shop_id),
            "product_id": int(product_id),
            "window_days": lookback_days,
            "lookback_days": lookback_days,
            "window_start_date": window_start_date.isoformat(),
            "window_end_date": window_end_date.isoformat(),
            "gross_units_sold": gross_units_sold,
            "returned_units": returned_units,
            "net_units_sold": net_units_sold,
            "cancelled_units_excluded": cancelled_units_excluded,
            "completed_sales_count": window_sales_count,
            "cancelled_sales_excluded_count": len(cancelled_sale_ids),
            "distinct_sale_days": distinct_sale_days,
            "all_time_completed_sales_count": all_time_sales_count,
            "all_time_net_units_sold": all_time_net_units,
            "first_observed_stock_or_sale_date": (
                first_observed_date.isoformat() if first_observed_date else None
            ),
            "days_stocked": days_stocked,
            "total_batches_received": total_batches_ever,
            "daily_sales_velocity": daily_sales_velocity,
            "active_day_velocity": (
                round(float(net_units_sold) / float(distinct_sale_days), 4)
                if distinct_sale_days > 0
                else 0.0
            ),
            "tenure_adjusted_daily_velocity": tenure_adjusted_velocity,
            "history_status": history_status,
            "data_sufficiency": history_status,
            "is_demand_reliable": is_demand_reliable,
            "insufficient_data": insufficient_data,
            "zero_demand_proven": zero_demand_proven,
            "demand_confidence": demand_confidence,
            "stockout_censoring_note": stockout_censoring_note,
            "history_explanation": history_explanation,
            "assumptions": {
                "lookback_window_days": lookback_days,
                "min_history_days_threshold": min_history_days,
                "min_active_sale_days_threshold": min_active_sale_days,
                "included_sale_statuses": [
                    "COMPLETED",
                    "PARTIALLY_RETURNED",
                    "RETURNED",
                ],
                "excluded_sale_statuses": ["CANCELLED"],
                "returns_handling": "net_units_sold = sum(max(0, sale_item.quantity - sale_item.returned_quantity))",
                "missing_history_policy": "Missing sales history is reported as INSUFFICIENT_DATA and never treated as proof of zero demand.",
                "stockout_policy": (
                    "Observed POS sales are bounded by shelf availability; periods of zero or low stock "
                    "censor unobserved customer demand."
                ),
            },
        }

    @staticmethod
    def calculate_product_sales_velocity(
        shop_id: int,
        product_id: int,
        lookback_days: int = 14,
        today: date | None = None,
        ref_date: date | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Compute historical sales velocity for (shop_id, product_id) over lookback_days."""
        return StockAnalysisService.calculate_product_sales_history(
            shop_id=shop_id,
            product_id=product_id,
            lookback_days=lookback_days,
            ref_date=today or ref_date,
            **kwargs,
        )

    @staticmethod
    def _analyze_single_shop_product(
        shop: Shop,
        sp: ShopProduct,
        batches: list[InventoryBatch],
        today: date,
        lookback_days: int,
        safety_stock_days: int,
        explicit_safety_stock_qty: int | None,
        surplus_coverage_days: int,
        shop_window_total_sales_count: int,
        rule_version: str,
        analyzed_at_iso: str,
    ) -> dict[str, Any]:
        """Run deterministic FEFO expiry-risk, surplus, replenishment, and recommendation rules for one product."""
        prod = sp.product
        sku = prod.sku if prod else f"PROD-{sp.product_id}"
        product_name = prod.name if prod else f"Product #{sp.product_id}"
        unit = prod.unit if prod else "pcs"
        is_perishable = bool(prod.is_perishable) if prod else True
        selling_price = Decimal(str(sp.selling_price or 0)).quantize(Decimal("0.01"))
        purchase_price = Decimal(str(sp.purchase_price or 0)).quantize(Decimal("0.01"))

        reorder_threshold = max(0, int(sp.low_stock_threshold or 0))
        near_expiry_days = max(
            1, int(sp.near_expiry_days or shop.near_expiry_days_threshold or 15)
        )
        surplus_threshold = max(1, int(sp.surplus_threshold or 40))

        # 1. Aggregate current batch buckets (purely read-only, no DB mutations)
        total_sellable_qty = 0
        total_reserved_qty = 0
        total_damaged_qty = 0
        total_expired_qty = 0
        total_physical_qty = 0

        for b in batches:
            b_sellable = b.effective_sellable_qty_on(today)
            b_reserved = max(0, int(b.reserved_qty or 0))
            b_damaged = max(0, int(b.damaged_qty or 0))
            b_expired = max(0, int(b.expired_qty or 0))
            if b.is_expired_on(today) and int(b.available_qty or 0) > 0:
                # Past-due available units are treated as expired/unsellable without mutating DB
                b_expired += int(b.available_qty or 0)

            total_sellable_qty += b_sellable
            total_reserved_qty += b_reserved
            total_damaged_qty += b_damaged
            total_expired_qty += b_expired
            total_physical_qty += b_sellable + b_reserved + b_damaged + b_expired

        is_out_of_stock = total_sellable_qty == 0
        is_low_stock = total_sellable_qty <= reorder_threshold

        # 2. Historical sales velocity & data sufficiency
        sales_history = StockAnalysisService.calculate_product_sales_history(
            shop_id=shop.id,
            product_id=sp.product_id,
            lookback_days=lookback_days,
            ref_date=today,
            current_sellable_qty=total_sellable_qty,
            is_low_stock=is_low_stock,
            shop_window_total_sales_count=shop_window_total_sales_count,
        )
        velocity = float(sales_history["daily_sales_velocity"])
        is_demand_reliable = bool(sales_history["is_demand_reliable"])
        history_status = str(sales_history["history_status"])
        insufficient_data = bool(sales_history["insufficient_data"])

        # 3. Safety stock & shop retention floor
        velocity_safety_qty = int(math.ceil(velocity * float(safety_stock_days)))
        if explicit_safety_stock_qty is not None:
            safety_stock_qty = max(int(explicit_safety_stock_qty), velocity_safety_qty)
        else:
            safety_stock_qty = velocity_safety_qty

        # Minimum stock the shop must retain across the product so it never transfers
        # units needed to stay above its reorder threshold or safety stock buffer.
        min_product_retention_qty = max(reorder_threshold, safety_stock_qty)
        max_product_surplus_pool = max(
            0, total_sellable_qty - min_product_retention_qty
        )

        # Coverage-based retention need for non-expiring / long-dated overstock (Condition B)
        coverage_demand_qty = int(math.ceil(velocity * float(surplus_coverage_days)))
        overstock_retention_floor = max(
            surplus_threshold,
            min_product_retention_qty,
            coverage_demand_qty + safety_stock_qty,
        )
        meets_condition_b_overstock = (
            is_demand_reliable
            and total_sellable_qty > surplus_threshold
            and total_sellable_qty > overstock_retention_floor
        )

        # 4. FEFO Queue-Aware Batch Analysis (Expiry Risk + Surplus Detection)
        batch_analyses: list[dict[str, Any]] = []
        recommendations: list[dict[str, Any]] = []

        cum_sellable_before = 0
        cum_retained_before = 0
        remaining_surplus_pool = max_product_surplus_pool
        fefo_active_rank = 0

        total_estimated_unsold_qty = 0
        total_potential_surplus_qty = 0
        total_at_risk_cost = Decimal("0.00")
        total_at_risk_retail = Decimal("0.00")
        total_surplus_cost = Decimal("0.00")
        total_surplus_retail = Decimal("0.00")

        for b in batches:
            days_left = b.days_to_expiry_on(today)
            is_expired = b.is_expired_on(today)
            eligible_qty = b.effective_sellable_qty_on(today)
            reserved_excluded = max(0, int(b.reserved_qty or 0))
            damaged_excluded = max(0, int(b.damaged_qty or 0))
            expired_excluded = max(0, int(b.expired_qty or 0)) + (
                max(0, int(b.available_qty or 0)) if is_expired else 0
            )
            unit_cost = Decimal(str(b.unit_cost or purchase_price)).quantize(
                Decimal("0.01")
            )

            earlier_sellable_snapshot = cum_sellable_before
            earlier_retained_snapshot = cum_retained_before

            if eligible_qty > 0:
                fefo_active_rank += 1
                current_fefo_rank: int | None = fefo_active_rank
            else:
                current_fefo_rank = None

            # Default per-batch metrics
            est_product_demand_window: float | None = None
            est_product_demand_units: int | None = None
            est_batch_demand_units: int | None = None
            est_sell_through_qty: int | None = None
            est_unsold_qty: int | None = None
            unverified_at_risk_qty = 0
            potential_surplus_qty = 0
            retained_qty = eligible_qty
            required_cum_retention = min_product_retention_qty
            surplus_reason: str | None = None
            is_surplus_candidate = False

            if is_expired:
                expiry_risk_level = "EXPIRED"
                explanation = (
                    f"Batch '{b.batch_code}' expired on {b.expiry_date.isoformat() if b.expiry_date else 'past date'} "
                    f"({abs(days_left) if days_left is not None else 0} day(s) ago). "
                    f"All {expired_excluded} expired unit(s) are excluded from sellable inventory and surplus."
                )
                if expired_excluded > 0:
                    rec_key = f"REC:EXPIRED_BATCH:shop:{shop.id}:batch:{b.id}"
                    recommendations.append(
                        {
                            "recommendation_key": rec_key,
                            "recommendation_type": "QUARANTINE_EXPIRED",
                            "severity": "CRITICAL",
                            "shop_id": shop.id,
                            "product_id": sp.product_id,
                            "batch_id": b.id,
                            "sku": sku,
                            "product_name": product_name,
                            "batch_code": b.batch_code,
                            "title": f"Expired batch {b.batch_code} ({sku}) requires quarantine review",
                            "reason": (
                                f"Batch '{b.batch_code}' passed its expiry date "
                                f"({b.expiry_date.isoformat() if b.expiry_date else 'N/A'}) "
                                f"with {expired_excluded} expired {unit}."
                            ),
                            "explanation": explanation,
                            "relevant_values": {
                                "batch_id": b.id,
                                "batch_code": b.batch_code,
                                "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
                                "days_to_expiry": days_left,
                                "expired_qty": expired_excluded,
                                "eligible_sellable_qty": 0,
                                "unit_cost": float(unit_cost),
                            },
                            "calculation_assumptions": sales_history["assumptions"],
                            "rule_version": rule_version,
                            "analyzed_at": analyzed_at_iso,
                        }
                    )
            elif eligible_qty == 0:
                expiry_risk_level = "DEPLETED"
                explanation = (
                    f"Batch '{b.batch_code}' has 0 eligible sellable units "
                    f"(reserved={reserved_excluded}, damaged={damaged_excluded}, expired={expired_excluded})."
                )
            elif days_left is None:
                # Non-perishable active batch
                expiry_risk_level = "NON_PERISHABLE"
                est_unsold_qty = 0
                est_sell_through_qty = eligible_qty
                if meets_condition_b_overstock and remaining_surplus_pool > 0:
                    required_cum_retention = overstock_retention_floor
                    needed_from_batch = max(
                        0, required_cum_retention - earlier_retained_snapshot
                    )
                    batch_excess = max(0, eligible_qty - needed_from_batch)
                    potential_surplus_qty = min(batch_excess, remaining_surplus_pool)
                    retained_qty = eligible_qty - potential_surplus_qty
                    if potential_surplus_qty > 0:
                        is_surplus_candidate = True
                        surplus_reason = "OVERSTOCK_COVERAGE"
                explanation = (
                    f"Non-perishable batch '{b.batch_code}' has {eligible_qty} sellable {unit}. "
                    + (
                        f"Flagged {potential_surplus_qty} {unit} as overstock surplus above "
                        f"{surplus_coverage_days}-day coverage and safety retention floor ({required_cum_retention} {unit})."
                        if is_surplus_candidate
                        else f"Retained {retained_qty} {unit} within shop coverage/safety thresholds."
                    )
                )
            else:
                # Perishable active batch with days_left >= 0
                effective_days = max(0, int(days_left))
                is_within_near_expiry = 0 <= effective_days <= near_expiry_days

                if is_demand_reliable:
                    est_product_demand_window = round(
                        velocity * float(effective_days), 2
                    )
                    est_product_demand_units = int(
                        math.floor(velocity * float(effective_days))
                    )
                    est_batch_demand_units = max(
                        0, est_product_demand_units - earlier_sellable_snapshot
                    )
                    est_sell_through_qty = min(eligible_qty, est_batch_demand_units)
                    est_unsold_qty = max(0, eligible_qty - est_batch_demand_units)

                    # Surplus calculation respecting demand before expiry, earlier retained stock,
                    # safety stock, and product reorder retention floor:
                    if is_within_near_expiry:
                        required_cum_retention = max(
                            min_product_retention_qty,
                            est_product_demand_units + safety_stock_qty,
                        )
                        needed_from_batch = max(
                            0, required_cum_retention - earlier_retained_snapshot
                        )
                        batch_excess = max(0, eligible_qty - needed_from_batch)
                        potential_surplus_qty = min(
                            batch_excess, remaining_surplus_pool
                        )
                        if potential_surplus_qty > 0:
                            is_surplus_candidate = True
                            surplus_reason = "NEAR_EXPIRY_SURPLUS"
                    elif meets_condition_b_overstock:
                        required_cum_retention = max(
                            overstock_retention_floor,
                            est_product_demand_units + safety_stock_qty,
                        )
                        needed_from_batch = max(
                            0, required_cum_retention - earlier_retained_snapshot
                        )
                        batch_excess = max(0, eligible_qty - needed_from_batch)
                        potential_surplus_qty = min(
                            batch_excess, remaining_surplus_pool
                        )
                        if potential_surplus_qty > 0:
                            is_surplus_candidate = True
                            surplus_reason = "OVERSTOCK_COVERAGE"

                    retained_qty = eligible_qty - potential_surplus_qty

                    # Assign deterministic expiry-risk level
                    unsold_ratio = (
                        float(est_unsold_qty) / float(eligible_qty)
                        if eligible_qty > 0
                        else 0.0
                    )
                    if effective_days <= min(3, near_expiry_days) or (
                        is_within_near_expiry and unsold_ratio >= 0.50
                    ):
                        expiry_risk_level = "CRITICAL"
                    elif is_within_near_expiry and est_unsold_qty > 0:
                        expiry_risk_level = "HIGH"
                    elif is_within_near_expiry and est_unsold_qty == 0:
                        expiry_risk_level = "MEDIUM"
                    elif not is_within_near_expiry and est_unsold_qty > 0:
                        expiry_risk_level = "MEDIUM"
                    else:
                        expiry_risk_level = "LOW"

                    retention_note = ""
                    if (
                        est_unsold_qty > potential_surplus_qty
                        and potential_surplus_qty == 0
                        and est_unsold_qty > 0
                    ):
                        retention_note = (
                            f" However, total shop sellable stock ({total_sellable_qty} {unit}) is at or near "
                            f"the required shop retention floor (max(reorder={reorder_threshold}, "
                            f"safety={safety_stock_qty}) = {min_product_retention_qty} {unit}), "
                            f"so 0 {unit} are recommended for outbound transfer."
                        )
                    elif est_unsold_qty != potential_surplus_qty and potential_surplus_qty > 0:
                        retention_note = (
                            f" After retaining {retained_qty} {unit} for expected demand and safety stock "
                            f"(floor={required_cum_retention} {unit}), {potential_surplus_qty} {unit} "
                            f"are identified as transferable surplus."
                        )

                    explanation = (
                        f"Batch '{b.batch_code}' (FEFO #{current_fefo_rank}) expires in {effective_days} day(s) "
                        f"on {b.expiry_date.isoformat()} with {eligible_qty} sellable {unit} "
                        f"(earlier FEFO batches hold {earlier_sellable_snapshot} {unit}). "
                        f"At {velocity:.2f} {unit}/day ({lookback_days}d window), total product demand over "
                        f"{effective_days} day(s) is {est_product_demand_units} {unit}, leaving "
                        f"{est_batch_demand_units} {unit} of demand for this batch, "
                        f"{est_sell_through_qty} {unit} projected sold, and "
                        f"{est_unsold_qty} {unit} potentially remaining at expiry."
                        + retention_note
                    )
                else:
                    # Missing sales history (NO_SALES_HISTORY or zero-sale stockout censored):
                    # Do NOT treat missing history as proof of zero demand!
                    unverified_at_risk_qty = (
                        eligible_qty if is_within_near_expiry else 0
                    )
                    if effective_days <= min(3, near_expiry_days):
                        expiry_risk_level = "CRITICAL"
                    elif is_within_near_expiry:
                        expiry_risk_level = "HIGH"
                    else:
                        expiry_risk_level = "INSUFFICIENT_DATA"

                    explanation = (
                        f"Batch '{b.batch_code}' (FEFO #{current_fefo_rank}) expires in {effective_days} day(s) "
                        f"on {b.expiry_date.isoformat()} with {eligible_qty} sellable {unit}. "
                        f"Insufficient historical sales data ({history_status}) prevents a reliable demand "
                        f"or surplus estimate before expiry; missing history is not treated as zero demand."
                    )

                # Generate batch-level deterministic recommendations
                if is_within_near_expiry or (
                    est_unsold_qty is not None and est_unsold_qty > 0
                ):
                    mon_key = f"REC:NEAR_EXPIRY:shop:{shop.id}:batch:{b.id}"
                    mon_severity = (
                        "CRITICAL"
                        if expiry_risk_level == "CRITICAL"
                        else ("WARNING" if expiry_risk_level == "HIGH" else "INFO")
                    )
                    recommendations.append(
                        {
                            "recommendation_key": mon_key,
                            "recommendation_type": "MONITOR_NEAR_EXPIRY",
                            "severity": mon_severity,
                            "shop_id": shop.id,
                            "product_id": sp.product_id,
                            "batch_id": b.id,
                            "sku": sku,
                            "product_name": product_name,
                            "batch_code": b.batch_code,
                            "title": (
                                f"Monitor batch {b.batch_code} ({sku}) — expires in {effective_days} day(s)"
                            ),
                            "reason": (
                                f"Monitor this batch because expiry is approaching "
                                f"({b.expiry_date.isoformat()}, {effective_days} day(s) remaining; "
                                f"threshold={near_expiry_days} days)."
                                if is_within_near_expiry
                                else f"Monitor batch {b.batch_code} because estimated demand before expiry "
                                f"({est_batch_demand_units} {unit}) is below batch stock ({eligible_qty} {unit})."
                            ),
                            "explanation": explanation,
                            "relevant_values": {
                                "batch_id": b.id,
                                "batch_code": b.batch_code,
                                "expiry_date": b.expiry_date.isoformat(),
                                "days_to_expiry": effective_days,
                                "near_expiry_threshold_days": near_expiry_days,
                                "eligible_sellable_qty": eligible_qty,
                                "earlier_batches_sellable_qty": earlier_sellable_snapshot,
                                "daily_sales_velocity": velocity,
                                "estimated_batch_demand_before_expiry": est_batch_demand_units,
                                "estimated_unsold_at_expiry_qty": est_unsold_qty,
                                "expiry_risk_level": expiry_risk_level,
                                "insufficient_data": insufficient_data,
                            },
                            "calculation_assumptions": {
                                **sales_history["assumptions"],
                                "fefo_queue_rule": (
                                    "Earlier-expiring sellable batches are deducted first before demand reaches this batch."
                                ),
                            },
                            "rule_version": rule_version,
                            "analyzed_at": analyzed_at_iso,
                        }
                    )

            if is_surplus_candidate and potential_surplus_qty > 0:
                sur_key = f"REC:SURPLUS_BATCH:shop:{shop.id}:batch:{b.id}"
                recommendations.append(
                    {
                        "recommendation_key": sur_key,
                        "recommendation_type": "REVIEW_SURPLUS",
                        "severity": (
                            "CRITICAL"
                            if expiry_risk_level == "CRITICAL"
                            else "WARNING"
                        ),
                        "shop_id": shop.id,
                        "product_id": sp.product_id,
                        "batch_id": b.id,
                        "sku": sku,
                        "product_name": product_name,
                        "batch_code": b.batch_code,
                        "title": (
                            f"Review possible surplus on batch {b.batch_code} ({sku}): "
                            f"{potential_surplus_qty} {unit}"
                        ),
                        "reason": (
                            f"Review possible surplus because estimated demand and safety retention "
                            f"({required_cum_retention} {unit} cumulative) is below eligible stock, leaving "
                            f"{potential_surplus_qty} {unit} of surplus in batch '{b.batch_code}'."
                        ),
                        "explanation": explanation,
                        "relevant_values": {
                            "batch_id": b.id,
                            "batch_code": b.batch_code,
                            "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
                            "days_to_expiry": days_left,
                            "eligible_sellable_qty": eligible_qty,
                            "retained_qty": retained_qty,
                            "potential_surplus_qty": potential_surplus_qty,
                            "estimated_unsold_at_expiry_qty": est_unsold_qty,
                            "safety_stock_qty": safety_stock_qty,
                            "reorder_threshold": reorder_threshold,
                            "surplus_reason": surplus_reason,
                        },
                        "calculation_assumptions": {
                            **sales_history["assumptions"],
                            "safety_stock_days": safety_stock_days,
                            "safety_stock_qty": safety_stock_qty,
                            "reorder_threshold": reorder_threshold,
                            "retention_policy": (
                                "Shop always retains at least max(reorder_threshold, safety_stock_qty) "
                                "and never double-counts retained or surplus units across batches."
                            ),
                        },
                        "rule_version": rule_version,
                        "analyzed_at": analyzed_at_iso,
                    }
                )

            batch_unsold_for_totals = int(est_unsold_qty or 0)
            batch_at_risk_cost = (
                Decimal(batch_unsold_for_totals) * unit_cost
            ).quantize(Decimal("0.01"))
            batch_at_risk_retail = (
                Decimal(batch_unsold_for_totals) * selling_price
            ).quantize(Decimal("0.01"))
            batch_surplus_cost = (
                Decimal(potential_surplus_qty) * unit_cost
            ).quantize(Decimal("0.01"))
            batch_surplus_retail = (
                Decimal(potential_surplus_qty) * selling_price
            ).quantize(Decimal("0.01"))

            total_estimated_unsold_qty += batch_unsold_for_totals
            total_potential_surplus_qty += potential_surplus_qty
            total_at_risk_cost += batch_at_risk_cost
            total_at_risk_retail += batch_at_risk_retail
            total_surplus_cost += batch_surplus_cost
            total_surplus_retail += batch_surplus_retail

            batch_analyses.append(
                {
                    "batch_id": b.id,
                    "id": b.id,
                    "shop_id": shop.id,
                    "shop_name": shop.name,
                    "shop_code": shop.code,
                    "product_id": sp.product_id,
                    "shop_product_id": sp.id,
                    "sku": sku,
                    "product_name": product_name,
                    "category": prod.category if prod else None,
                    "unit": unit,
                    "is_perishable": is_perishable,
                    "batch_code": b.batch_code,
                    "status": b.status,
                    "manufactured_date": (
                        b.manufactured_date.isoformat() if b.manufactured_date else None
                    ),
                    "received_at": (
                        b.received_at.isoformat() if b.received_at else None
                    ),
                    "expiry_date": (
                        b.expiry_date.isoformat() if b.expiry_date else None
                    ),
                    "days_to_expiry": days_left,
                    "near_expiry_threshold_days": near_expiry_days,
                    "is_expired": is_expired,
                    "is_near_expiry": (
                        days_left is not None
                        and 0 <= days_left <= near_expiry_days
                        and eligible_qty > 0
                    ),
                    "fefo_rank": current_fefo_rank,
                    "initial_qty": int(b.initial_qty or 0),
                    "available_qty": int(b.available_qty or 0),
                    "eligible_sellable_qty": eligible_qty,
                    "reserved_qty_excluded": reserved_excluded,
                    "damaged_qty_excluded": damaged_excluded,
                    "expired_qty_excluded": expired_excluded,
                    "earlier_batches_sellable_qty": earlier_sellable_snapshot,
                    "earlier_batches_retained_qty": earlier_retained_snapshot,
                    "daily_sales_velocity": velocity,
                    "estimated_product_demand_in_window": est_product_demand_window,
                    "estimated_product_demand_before_expiry": est_product_demand_units,
                    "estimated_batch_demand_before_expiry": est_batch_demand_units,
                    "estimated_sell_through_qty": est_sell_through_qty,
                    "estimated_unsold_at_expiry_qty": est_unsold_qty,
                    "unverified_at_risk_qty": unverified_at_risk_qty,
                    "safety_stock_qty": safety_stock_qty,
                    "reorder_threshold": reorder_threshold,
                    "required_cumulative_retention_qty": required_cum_retention,
                    "retained_qty": retained_qty,
                    "potential_surplus_qty": potential_surplus_qty,
                    "is_surplus_candidate": is_surplus_candidate,
                    "surplus_reason": surplus_reason,
                    "unit_cost": float(unit_cost),
                    "selling_price": float(selling_price),
                    "at_risk_cost_value": float(batch_at_risk_cost),
                    "at_risk_retail_value": float(batch_at_risk_retail),
                    "surplus_cost_value": float(batch_surplus_cost),
                    "surplus_retail_value": float(batch_surplus_retail),
                    "expiry_risk_level": expiry_risk_level,
                    "history_status": history_status,
                    "is_demand_reliable": is_demand_reliable,
                    "insufficient_data": insufficient_data,
                    "demand_confidence": sales_history["demand_confidence"],
                    "explanation": explanation,
                    "rule_version": rule_version,
                    "analyzed_at": analyzed_at_iso,
                }
            )

            cum_sellable_before += eligible_qty
            cum_retained_before += retained_qty
            remaining_surplus_pool = max(
                0, remaining_surplus_pool - potential_surplus_qty
            )

        # 5. Low-Stock & Replenishment Analysis (Section 6)
        reorder_deficit_qty = max(0, reorder_threshold - total_sellable_qty)
        target_stock_level = max(
            reorder_threshold + safety_stock_qty,
            int(math.ceil(velocity * float(lookback_days)))
            if is_demand_reliable
            else reorder_threshold,
        )
        recommended_replenishment_qty = (
            max(reorder_deficit_qty, target_stock_level - total_sellable_qty)
            if is_low_stock
            else 0
        )
        days_of_supply: float | None = (
            round(float(total_sellable_qty) / velocity, 2)
            if velocity > 0
            else None
        )

        if is_out_of_stock:
            replenishment_status = "OUT_OF_STOCK"
            replenishment_severity = "CRITICAL"
            replenishment_reason = (
                f"Replenishment is urgently needed because available sellable stock is 0 {unit} "
                f"(reorder threshold is {reorder_threshold} {unit}; deficit is {reorder_deficit_qty} {unit})."
            )
        elif is_low_stock:
            replenishment_status = "LOW_STOCK"
            replenishment_severity = "WARNING"
            replenishment_reason = (
                f"Replenishment may be needed because available sellable stock ({total_sellable_qty} {unit}) "
                f"is at or below the reorder threshold ({reorder_threshold} {unit}; "
                f"deficit is {reorder_deficit_qty} {unit})."
            )
        elif total_sellable_qty < min_product_retention_qty:
            replenishment_status = "BELOW_SAFETY_STOCK"
            replenishment_severity = "INFO"
            replenishment_reason = (
                f"Available sellable stock ({total_sellable_qty} {unit}) is above reorder threshold "
                f"({reorder_threshold} {unit}) but below the dynamic safety stock floor "
                f"({min_product_retention_qty} {unit} for {safety_stock_days} safety days)."
            )
        else:
            replenishment_status = "ADEQUATE"
            replenishment_severity = "NONE"
            replenishment_reason = (
                f"Available sellable stock ({total_sellable_qty} {unit}) exceeds both the reorder "
                f"threshold ({reorder_threshold} {unit}) and safety stock floor ({min_product_retention_qty} {unit})."
            )

        if is_low_stock and sp.effective_is_active:
            rep_key = f"REC:REPLENISH_LOW_STOCK:shop:{shop.id}:product:{sp.product_id}"
            recommendations.append(
                {
                    "recommendation_key": rep_key,
                    "recommendation_type": "REPLENISHMENT_NEEDED",
                    "severity": replenishment_severity,
                    "shop_id": shop.id,
                    "product_id": sp.product_id,
                    "batch_id": None,
                    "sku": sku,
                    "product_name": product_name,
                    "batch_code": None,
                    "title": (
                        f"Replenishment needed for {sku} ({product_name}) — "
                        f"{total_sellable_qty}/{reorder_threshold} {unit} available"
                    ),
                    "reason": replenishment_reason,
                    "explanation": (
                        f"{replenishment_reason} Reserved ({total_reserved_qty} {unit}), "
                        f"damaged ({total_damaged_qty} {unit}), and expired ({total_expired_qty} {unit}) "
                        f"units are excluded from available sellable stock."
                    ),
                    "relevant_values": {
                        "product_id": sp.product_id,
                        "sku": sku,
                        "available_qty": total_sellable_qty,
                        "sellable_qty": total_sellable_qty,
                        "reserved_qty_excluded": total_reserved_qty,
                        "damaged_qty_excluded": total_damaged_qty,
                        "expired_qty_excluded": total_expired_qty,
                        "reorder_threshold": reorder_threshold,
                        "safety_stock_qty": safety_stock_qty,
                        "deficit_qty": reorder_deficit_qty,
                        "recommended_replenishment_qty": recommended_replenishment_qty,
                        "daily_sales_velocity": velocity,
                        "days_of_supply": days_of_supply,
                        "replenishment_status": replenishment_status,
                    },
                    "calculation_assumptions": sales_history["assumptions"],
                    "rule_version": rule_version,
                    "analyzed_at": analyzed_at_iso,
                }
            )

        # 6. Insufficient Sales History Recommendation (Section 7)
        if insufficient_data and sp.effective_is_active:
            ins_key = f"REC:INSUFFICIENT_HISTORY:shop:{shop.id}:product:{sp.product_id}"
            recommendations.append(
                {
                    "recommendation_key": ins_key,
                    "recommendation_type": "INSUFFICIENT_SALES_HISTORY",
                    "severity": "INFO",
                    "shop_id": shop.id,
                    "product_id": sp.product_id,
                    "batch_id": None,
                    "sku": sku,
                    "product_name": product_name,
                    "batch_code": None,
                    "title": f"Insufficient sales history for {sku} ({product_name})",
                    "reason": (
                        "Insufficient sales history prevents a reliable estimate: "
                        f"{sales_history['history_explanation']}"
                    ),
                    "explanation": sales_history["history_explanation"],
                    "relevant_values": {
                        "product_id": sp.product_id,
                        "sku": sku,
                        "history_status": history_status,
                        "completed_sales_count": sales_history["completed_sales_count"],
                        "distinct_sale_days": sales_history["distinct_sale_days"],
                        "net_units_sold": sales_history["net_units_sold"],
                        "days_stocked": sales_history["days_stocked"],
                        "lookback_days": lookback_days,
                        "available_qty": total_sellable_qty,
                    },
                    "calculation_assumptions": sales_history["assumptions"],
                    "rule_version": rule_version,
                    "analyzed_at": analyzed_at_iso,
                }
            )

        low_stock_analysis = {
            "shop_id": shop.id,
            "product_id": sp.product_id,
            "shop_product_id": sp.id,
            "sku": sku,
            "product_name": product_name,
            "category": prod.category if prod else None,
            "unit": unit,
            "is_active": sp.effective_is_active,
            "available_qty": total_sellable_qty,
            "sellable_qty": total_sellable_qty,
            "reserved_qty": total_reserved_qty,
            "damaged_qty": total_damaged_qty,
            "expired_qty": total_expired_qty,
            "reorder_threshold": reorder_threshold,
            "low_stock_threshold": reorder_threshold,
            "safety_stock_qty": safety_stock_qty,
            "min_retention_qty": min_product_retention_qty,
            "deficit_qty": reorder_deficit_qty,
            "stock_deficit": reorder_deficit_qty,
            "recommended_replenishment_qty": recommended_replenishment_qty,
            "daily_sales_velocity": velocity,
            "days_of_supply": days_of_supply,
            "is_low_stock": is_low_stock,
            "is_out_of_stock": is_out_of_stock,
            "replenishment_status": replenishment_status,
            "severity": replenishment_severity,
            "reason": replenishment_reason,
            "history_status": history_status,
            "insufficient_data": insufficient_data,
            "analyzed_at": analyzed_at_iso,
            "rule_version": rule_version,
        }

        return {
            "shop_id": shop.id,
            "product_id": sp.product_id,
            "shop_product_id": sp.id,
            "sku": sku,
            "product_name": product_name,
            "category": prod.category if prod else None,
            "unit": unit,
            "is_perishable": is_perishable,
            "is_active": sp.effective_is_active,
            "purchase_price": float(purchase_price),
            "selling_price": float(selling_price),
            "thresholds": {
                "reorder_threshold": reorder_threshold,
                "low_stock_threshold": reorder_threshold,
                "near_expiry_days": near_expiry_days,
                "surplus_threshold": surplus_threshold,
                "safety_stock_days": safety_stock_days,
                "safety_stock_qty": safety_stock_qty,
                "min_product_retention_qty": min_product_retention_qty,
                "surplus_coverage_days": surplus_coverage_days,
                "overstock_retention_floor": overstock_retention_floor,
            },
            "stock_summary": {
                "available_qty": total_sellable_qty,
                "sellable_qty": total_sellable_qty,
                "reserved_qty": total_reserved_qty,
                "damaged_qty": total_damaged_qty,
                "expired_qty": total_expired_qty,
                "physical_on_hand_qty": total_physical_qty,
                "retained_qty": total_sellable_qty - total_potential_surplus_qty,
                "potential_surplus_qty": total_potential_surplus_qty,
                "estimated_unsold_at_expiry_qty": total_estimated_unsold_qty,
                "at_risk_cost_value": float(total_at_risk_cost),
                "at_risk_retail_value": float(total_at_risk_retail),
                "surplus_cost_value": float(total_surplus_cost),
                "surplus_retail_value": float(total_surplus_retail),
            },
            "sales_history": sales_history,
            "low_stock_analysis": low_stock_analysis,
            "batches": batch_analyses,
            "recommendations": recommendations,
            "rule_version": rule_version,
            "analyzed_at": analyzed_at_iso,
        }

    @staticmethod
    def analyze_shop(
        actor: User,
        shop_id: int,
        lookback_days: int | str | None = None,
        safety_stock_days: int | str | None = None,
        safety_stock_qty: int | str | None = None,
        surplus_coverage_days: int | str | None = None,
        ref_date: date | str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Execute or retrieve a fresh deterministic stock analysis for shop_id.

        Guarantees:
        - Strict shop & organization isolation via verify_shop_access.
        - Zero stock mutations (read-only analysis).
        - Automatic stale-snapshot invalidation when PostgreSQL data changes.
        - 100% deduplicated active recommendations keyed by recommendation_key.
        """
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=True,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        cleaned = validate_analysis_params(
            lookback_days_raw=lookback_days,
            safety_stock_days_raw=safety_stock_days,
            safety_stock_qty_raw=safety_stock_qty,
            surplus_coverage_days_raw=surplus_coverage_days,
            ref_date_raw=ref_date,
            default_lookback_days=_get_config_int("ANALYSIS_LOOKBACK_DAYS", 14),
            default_safety_stock_days=_get_config_int(
                "ANALYSIS_DEFAULT_SAFETY_STOCK_DAYS", 2
            ),
            default_surplus_coverage_days=_get_config_int(
                "ANALYSIS_SURPLUS_COVERAGE_DAYS", 30
            ),
        )
        today = cleaned["ref_date"] or get_business_today()
        rule_version = _get_rule_version()
        ttl_seconds = _get_config_int("ANALYSIS_STALE_AFTER_SECONDS", 300)

        freshness = StockAnalysisService.check_snapshot_freshness(
            shop_id=shop.id,
            lookback_days=cleaned["lookback_days"],
            safety_stock_days=cleaned["safety_stock_days"],
            safety_stock_qty=cleaned["safety_stock_qty"],
            surplus_coverage_days=cleaned["surplus_coverage_days"],
            ref_date=today,
        )
        sig = StockAnalysisService._build_config_signature(cleaned, today)
        cache_key = (shop.id, sig)

        if not force_refresh and not freshness["is_stale"]:
            cached_entry = _ANALYSIS_SNAPSHOT_STORE[cache_key]
            cached_entry["evaluation_count"] += 1
            cached_entry["last_evaluated_at"] = _utc_now().isoformat()
            result_payload = cached_entry["payload"]
            result_payload["freshness"] = {
                "was_cached": True,
                "is_stale": False,
                "stale_reason": "FRESH",
                "recalculated_reason": None,
                "analyzed_at": cached_entry["analyzed_at"],
                "last_evaluated_at": cached_entry["last_evaluated_at"],
                "evaluation_count": cached_entry["evaluation_count"],
                "stale_after_seconds": ttl_seconds,
                "data_fingerprint": cached_entry["data_fingerprint"],
                "rule_version": rule_version,
            }
            return result_payload

        # Compute fresh analysis from PostgreSQL
        now_utc = _utc_now()
        analyzed_at_iso = now_utc.isoformat()
        current_fp = freshness["current_data_fingerprint"]
        recalculated_reason = (
            "FORCED_REFRESH" if force_refresh else freshness["stale_reason"]
        )

        tz = _get_business_tz()
        w_days = int(cleaned["lookback_days"])
        w_start = today - timedelta(days=w_days - 1)
        start_dt_utc = datetime.combine(
            w_start, time.min, tzinfo=tz
        ).astimezone(timezone.utc)
        end_dt_utc = datetime.combine(
            today, time.max, tzinfo=tz
        ).astimezone(timezone.utc)

        shop_window_total_sales_count = (
            db.session.query(func.count(Sale.id))
            .filter(
                Sale.shop_id == shop.id,
                Sale.status != "CANCELLED",
                Sale.created_at >= start_dt_utc,
                Sale.created_at <= end_dt_utc,
            )
            .scalar()
            or 0
        )

        shop_products = (
            db.session.query(ShopProduct)
            .join(Product, ShopProduct.product_id == Product.id)
            .filter(ShopProduct.shop_id == shop.id)
            .order_by(Product.name.asc(), Product.id.asc())
            .all()
        )

        product_analyses: list[dict[str, Any]] = []
        all_batch_analyses: list[dict[str, Any]] = []
        low_stock_items: list[dict[str, Any]] = []
        dedup_recommendations: dict[str, dict[str, Any]] = {}

        for sp in shop_products:
            batches = (
                db.session.query(InventoryBatch)
                .filter(
                    InventoryBatch.shop_id == shop.id,
                    InventoryBatch.product_id == sp.product_id,
                )
                .order_by(
                    case((InventoryBatch.expiry_date.is_(None), 1), else_=0).asc(),
                    InventoryBatch.expiry_date.asc(),
                    InventoryBatch.received_at.asc(),
                    InventoryBatch.id.asc(),
                )
                .all()
            )
            p_result = StockAnalysisService._analyze_single_shop_product(
                shop=shop,
                sp=sp,
                batches=batches,
                today=today,
                lookback_days=w_days,
                safety_stock_days=int(cleaned["safety_stock_days"]),
                explicit_safety_stock_qty=cleaned["safety_stock_qty"],
                surplus_coverage_days=int(cleaned["surplus_coverage_days"]),
                shop_window_total_sales_count=int(shop_window_total_sales_count),
                rule_version=rule_version,
                analyzed_at_iso=analyzed_at_iso,
            )
            product_analyses.append(p_result)
            all_batch_analyses.extend(p_result["batches"])
            low_stock_items.append(p_result["low_stock_analysis"])

            for rec in p_result["recommendations"]:
                # Deduplicate by deterministic recommendation_key
                dedup_recommendations[rec["recommendation_key"]] = rec

        near_expiry_batches = [
            b
            for b in all_batch_analyses
            if b["expiry_risk_level"] in ("EXPIRED", "CRITICAL", "HIGH", "MEDIUM")
            and (b["eligible_sellable_qty"] > 0 or b["is_expired"])
        ]
        near_expiry_batches.sort(
            key=lambda x: (
                x["days_to_expiry"] if x["days_to_expiry"] is not None else 999999,
                x["batch_id"],
            )
        )

        surplus_batches = [
            b
            for b in all_batch_analyses
            if b["is_surplus_candidate"] and b["potential_surplus_qty"] > 0
        ]
        surplus_batches.sort(
            key=lambda x: (
                x["days_to_expiry"] if x["days_to_expiry"] is not None else 999999,
                -x["potential_surplus_qty"],
                x["batch_id"],
            )
        )

        flagged_low_stock = [
            item
            for item in low_stock_items
            if item["replenishment_status"]
            in ("OUT_OF_STOCK", "LOW_STOCK", "BELOW_SAFETY_STOCK")
        ]
        flagged_low_stock.sort(
            key=lambda x: (
                0
                if x["replenishment_status"] == "OUT_OF_STOCK"
                else (1 if x["replenishment_status"] == "LOW_STOCK" else 2),
                -x["deficit_qty"],
                x["product_id"],
            )
        )

        severity_rank = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
        recommendations_list = sorted(
            dedup_recommendations.values(),
            key=lambda r: (
                severity_rank.get(r["severity"], 9),
                r["recommendation_key"],
            ),
        )

        kpis = {
            "total_catalog_products": len(product_analyses),
            "active_products": sum(1 for p in product_analyses if p["is_active"]),
            "total_batches_analyzed": len(all_batch_analyses),
            "total_sellable_units": sum(
                p["stock_summary"]["sellable_qty"] for p in product_analyses
            ),
            "total_reserved_units_excluded": sum(
                p["stock_summary"]["reserved_qty"] for p in product_analyses
            ),
            "total_damaged_units_excluded": sum(
                p["stock_summary"]["damaged_qty"] for p in product_analyses
            ),
            "total_expired_units_excluded": sum(
                p["stock_summary"]["expired_qty"] for p in product_analyses
            ),
            "expired_batches_count": sum(
                1 for b in all_batch_analyses if b["expiry_risk_level"] == "EXPIRED"
            ),
            "critical_expiry_batches_count": sum(
                1 for b in all_batch_analyses if b["expiry_risk_level"] == "CRITICAL"
            ),
            "high_expiry_batches_count": sum(
                1 for b in all_batch_analyses if b["expiry_risk_level"] == "HIGH"
            ),
            "medium_expiry_batches_count": sum(
                1 for b in all_batch_analyses if b["expiry_risk_level"] == "MEDIUM"
            ),
            "near_expiry_batches_count": sum(
                1 for b in all_batch_analyses if b["is_near_expiry"]
            ),
            "total_estimated_unsold_units": sum(
                p["stock_summary"]["estimated_unsold_at_expiry_qty"]
                for p in product_analyses
            ),
            "total_at_risk_cost_value": round(
                sum(
                    p["stock_summary"]["at_risk_cost_value"]
                    for p in product_analyses
                ),
                2,
            ),
            "surplus_batches_count": len(surplus_batches),
            "surplus_products_count": sum(
                1
                for p in product_analyses
                if p["stock_summary"]["potential_surplus_qty"] > 0
            ),
            "total_potential_surplus_units": sum(
                p["stock_summary"]["potential_surplus_qty"]
                for p in product_analyses
            ),
            "total_surplus_cost_value": round(
                sum(
                    p["stock_summary"]["surplus_cost_value"]
                    for p in product_analyses
                ),
                2,
            ),
            "low_stock_products_count": sum(
                1 for item in low_stock_items if item["is_low_stock"]
            ),
            "out_of_stock_products_count": sum(
                1 for item in low_stock_items if item["is_out_of_stock"]
            ),
            "insufficient_history_products_count": sum(
                1 for p in product_analyses if p["sales_history"]["insufficient_data"]
            ),
            "active_recommendations_count": len(recommendations_list),
        }

        payload: dict[str, Any] = {
            "shop_id": shop.id,
            "shop_name": shop.name,
            "shop_code": shop.code,
            "organization_id": shop.organization_id,
            "business_date": today.isoformat(),
            "timezone": (
                current_app.config.get("APP_TIMEZONE", "Asia/Kolkata")
                if has_app_context()
                else "Asia/Kolkata"
            ),
            "rule_version": rule_version,
            "analyzed_at": analyzed_at_iso,
            "configuration": {
                "lookback_days": w_days,
                "window_start_date": w_start.isoformat(),
                "window_end_date": today.isoformat(),
                "safety_stock_days": int(cleaned["safety_stock_days"]),
                "safety_stock_qty_override": cleaned["safety_stock_qty"],
                "surplus_coverage_days": int(cleaned["surplus_coverage_days"]),
                "stale_after_seconds": ttl_seconds,
            },
            "freshness": {
                "was_cached": False,
                "is_stale": False,
                "stale_reason": "FRESH",
                "recalculated_reason": recalculated_reason,
                "analyzed_at": analyzed_at_iso,
                "last_evaluated_at": analyzed_at_iso,
                "evaluation_count": 1,
                "stale_after_seconds": ttl_seconds,
                "data_fingerprint": current_fp,
                "rule_version": rule_version,
            },
            "kpis": kpis,
            "products": product_analyses,
            "batches": all_batch_analyses,
            "near_expiry_batches": near_expiry_batches,
            "surplus_batches": surplus_batches,
            "low_stock_items": flagged_low_stock,
            "all_low_stock_evaluations": low_stock_items,
            "recommendations": recommendations_list,
        }

        _ANALYSIS_SNAPSHOT_STORE[cache_key] = {
            "shop_id": shop.id,
            "config_signature": sig,
            "analyzed_at_dt": now_utc,
            "analyzed_at": analyzed_at_iso,
            "last_evaluated_at": analyzed_at_iso,
            "evaluation_count": 1,
            "business_date": today.isoformat(),
            "rule_version": rule_version,
            "data_fingerprint": current_fp,
            "payload": payload,
        }
        return payload

    @staticmethod
    def get_product_analysis(
        actor: User,
        shop_id: int,
        product_id: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return detailed deterministic stock analysis for a single product in shop_id."""
        shop_result = StockAnalysisService.analyze_shop(
            actor=actor,
            shop_id=shop_id,
            **kwargs,
        )
        prod = db.session.get(Product, int(product_id))
        if prod is None:
            raise ResourceNotFoundError(
                message=f"Product #{product_id} was not found."
            )
        if prod.organization_id != shop_result["organization_id"]:
            raise AuthorizationError(
                message="Product belongs to another organization.",
                code="UNAUTHORIZED_ORG_ACCESS",
            )

        for p_item in shop_result["products"]:
            if int(p_item["product_id"]) == int(product_id):
                return {
                    "shop_id": shop_result["shop_id"],
                    "shop_name": shop_result["shop_name"],
                    "shop_code": shop_result["shop_code"],
                    "business_date": shop_result["business_date"],
                    "timezone": shop_result["timezone"],
                    "rule_version": shop_result["rule_version"],
                    "analyzed_at": shop_result["analyzed_at"],
                    "freshness": shop_result["freshness"],
                    "configuration": shop_result["configuration"],
                    "product_analysis": p_item,
                }

        raise ResourceNotFoundError(
            message=f"Product #{product_id} is not enrolled in shop #{shop_id}."
        )

    @staticmethod
    def get_batch_analysis(
        actor: User,
        shop_id: int,
        batch_id: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return detailed deterministic FEFO, expiry-risk, and surplus analysis for a single batch."""
        shop_result = StockAnalysisService.analyze_shop(
            actor=actor,
            shop_id=shop_id,
            **kwargs,
        )
        batch = db.session.get(InventoryBatch, int(batch_id))
        if batch is None:
            raise ResourceNotFoundError(message=f"Batch #{batch_id} was not found.")
        if batch.shop_id != int(shop_id):
            raise AuthorizationError(
                message="Batch belongs to another shop.",
                code="UNAUTHORIZED_SHOP_ACCESS",
            )

        for b_item in shop_result["batches"]:
            if int(b_item["batch_id"]) == int(batch_id):
                parent_product = next(
                    (
                        p
                        for p in shop_result["products"]
                        if int(p["product_id"]) == int(b_item["product_id"])
                    ),
                    None,
                )
                batch_recs = [
                    r
                    for r in shop_result["recommendations"]
                    if r.get("batch_id") == int(batch_id)
                    or (
                        r.get("batch_id") is None
                        and r.get("product_id") == int(b_item["product_id"])
                    )
                ]
                return {
                    "shop_id": shop_result["shop_id"],
                    "shop_name": shop_result["shop_name"],
                    "shop_code": shop_result["shop_code"],
                    "business_date": shop_result["business_date"],
                    "timezone": shop_result["timezone"],
                    "rule_version": shop_result["rule_version"],
                    "analyzed_at": shop_result["analyzed_at"],
                    "freshness": shop_result["freshness"],
                    "configuration": shop_result["configuration"],
                    "batch_analysis": b_item,
                    "product_sales_history": (
                        parent_product["sales_history"] if parent_product else None
                    ),
                    "product_thresholds": (
                        parent_product["thresholds"] if parent_product else None
                    ),
                    "recommendations": batch_recs,
                }

        raise ResourceNotFoundError(
            message=f"Batch #{batch_id} was not found in shop #{shop_id}."
        )


# Canonical alias matching ARCHITECTURE.md reference (`SurplusService`)
SurplusService = StockAnalysisService
