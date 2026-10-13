"""Transactional Batch Inventory, Stock Movement Ledger & Deterministic Alert Service (Phase 3).

Enforces:
- BR-001: Multi-batch per product with distinct batch codes, unit costs, and expiry dates.
- BR-002: 100% reconciliation between shop product totals, batch buckets, and stock_movements.
- BR-003: Non-negative buckets (available_qty, reserved_qty, damaged_qty, expired_qty).
- BR-006: Row-level locking (SELECT ... FOR UPDATE) to prevent race conditions and negative stock.
- BR-007: Immutable stock_movements and audit_logs within the same ACID transaction.
- BR-013: Strict shop and organization isolation.
- BR-015: Deterministic low-stock, near-expiry, and expired-stock detection.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, has_app_context
from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError, OperationalError

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch, StockMovement
from app.models.organization import Shop
from app.models.user import User
from app.services.audit_service import AuditService
from app.utils.decorators import verify_shop_access
from app.utils.errors import (
    AuthorizationError,
    BatchExpiredError,
    DatabaseConflictError,
    DomainError,
    DuplicateResourceError,
    InsufficientStockError,
    ResourceConflictError,
    ResourceNotFoundError,
    ValidationError,
)
from app.utils.validators import (
    validate_batch_adjustment_input,
    validate_batch_receive_input,
    validate_pagination_params,
)

IST_FALLBACK_TZ = timezone(timedelta(hours=5, minutes=30), name="IST")


def get_business_today(tz_name: str | None = None) -> date:
    """Return the current calendar date in the configured business timezone (default Asia/Kolkata).

    Provides a deterministic fallback to UTC+05:30 on Windows environments where
    OS tzdata is not installed.
    """
    target_tz_name = tz_name
    if not target_tz_name and has_app_context():
        target_tz_name = current_app.config.get("APP_TIMEZONE", "Asia/Kolkata")
    target_tz_name = (target_tz_name or "Asia/Kolkata").strip()

    if target_tz_name.upper() == "UTC":
        return datetime.now(timezone.utc).date()

    try:
        tz = ZoneInfo(target_tz_name)
        return datetime.now(tz).date()
    except (ZoneInfoNotFoundError, Exception):
        return datetime.now(IST_FALLBACK_TZ).date()


class InventoryService:
    """Centralized, transactional service for all batch and stock-changing operations."""

    @staticmethod
    def sweep_expired_batches(
        shop_id: int,
        product_id: int | None = None,
        ref_date: date | None = None,
        commit: bool = True,
    ) -> list[InventoryBatch]:
        """Automatically transition available_qty on past-due batches (expiry_date < today) to expired_qty.

        Writes paired MARK_EXPIRED entries to stock_movements and an audit_logs entry
        within the same transaction (BR-002, BR-003, BR-007).
        """
        today = ref_date or get_business_today()
        shop = db.session.get(Shop, int(shop_id))
        if shop is None:
            return []

        query = (
            db.session.query(InventoryBatch)
            .filter(
                InventoryBatch.shop_id == shop.id,
                InventoryBatch.expiry_date.isnot(None),
                InventoryBatch.expiry_date < today,
                or_(
                    InventoryBatch.available_qty > 0,
                    InventoryBatch.status == "ACTIVE",
                ),
            )
            .order_by(InventoryBatch.id.asc())
            .with_for_update()
        )
        if product_id is not None:
            query = query.filter(InventoryBatch.product_id == int(product_id))

        candidates = query.all()
        if not candidates:
            return []

        swept: list[InventoryBatch] = []
        for batch in candidates:
            before_state = {
                "available_qty": batch.available_qty,
                "expired_qty": batch.expired_qty,
                "status": batch.status,
            }
            changed = False

            if batch.available_qty > 0:
                delta = int(batch.available_qty)
                old_expired = int(batch.expired_qty or 0)
                batch.available_qty = 0
                batch.expired_qty = old_expired + delta

                reason_msg = (
                    f"Automatic expiry sweep on {today.isoformat()}: "
                    f"batch expired on {batch.expiry_date.isoformat()}."
                )
                db.session.add(
                    StockMovement(
                        organization_id=shop.organization_id,
                        shop_id=shop.id,
                        product_id=batch.product_id,
                        batch_id=batch.id,
                        movement_type="MARK_EXPIRED",
                        bucket_affected="AVAILABLE",
                        quantity_change=-delta,
                        balance_before=delta,
                        balance_after=0,
                        unit_cost_snapshot=batch.unit_cost,
                        reference_type="EXPIRY_SWEEP",
                        reference_id=batch.id,
                        reason=reason_msg,
                        performed_by_user_id=None,
                    )
                )
                db.session.add(
                    StockMovement(
                        organization_id=shop.organization_id,
                        shop_id=shop.id,
                        product_id=batch.product_id,
                        batch_id=batch.id,
                        movement_type="MARK_EXPIRED",
                        bucket_affected="EXPIRED",
                        quantity_change=delta,
                        balance_before=old_expired,
                        balance_after=old_expired + delta,
                        unit_cost_snapshot=batch.unit_cost,
                        reference_type="EXPIRY_SWEEP",
                        reference_id=batch.id,
                        reason=reason_msg,
                        performed_by_user_id=None,
                    )
                )
                changed = True

            if batch.reserved_qty == 0 and batch.status != "EXPIRED":
                batch.status = "EXPIRED"
                changed = True

            if changed:
                swept.append(batch)
                AuditService.record(
                    action="BATCH_EXPIRED_SWEEP",
                    entity_type="INVENTORY_BATCH",
                    entity_id=batch.id,
                    actor=None,
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    before_state=before_state,
                    after_state={
                        "available_qty": batch.available_qty,
                        "expired_qty": batch.expired_qty,
                        "status": batch.status,
                    },
                )

        if swept and commit:
            db.session.commit()

        return swept

    @staticmethod
    def compute_shop_product_stock_summary(
        shop_product: ShopProduct,
        ref_date: date | None = None,
        batches: list[InventoryBatch] | None = None,
    ) -> dict[str, Any]:
        """Compute reconciled stock bucket totals, valuation, and deterministic alert flags."""
        today = ref_date or get_business_today()
        batch_rows = (
            batches
            if batches is not None
            else (
                db.session.query(InventoryBatch)
                .filter(
                    InventoryBatch.shop_id == shop_product.shop_id,
                    InventoryBatch.product_id == shop_product.product_id,
                )
                .order_by(
                    case((InventoryBatch.expiry_date.is_(None), 1), else_=0).asc(),
                    InventoryBatch.expiry_date.asc(),
                    InventoryBatch.received_at.asc(),
                    InventoryBatch.id.asc(),
                )
                .all()
            )
        )

        sellable_qty = 0
        available_qty = 0
        reserved_qty = 0
        damaged_qty = 0
        expired_qty = 0
        on_hand_qty = 0
        active_batch_count = 0
        near_expiry_batch_count = 0
        near_expiry_qty = 0
        expired_batch_count = 0
        earliest_expiry: date | None = None
        cost_value = Decimal("0.00")

        warn_days = int(shop_product.near_expiry_days or 15)

        for b in batch_rows:
            b_sellable = b.effective_sellable_qty_on(today)
            b_avail = int(b.available_qty or 0)
            b_res = int(b.reserved_qty or 0)
            b_dmg = int(b.damaged_qty or 0)
            b_exp = int(b.expired_qty or 0)

            sellable_qty += b_sellable
            available_qty += b_avail
            reserved_qty += b_res
            damaged_qty += b_dmg
            expired_qty += b_exp
            on_hand_qty += b.on_hand_qty

            if b_sellable > 0:
                active_batch_count += 1
                cost_value += Decimal(str(b.unit_cost)) * Decimal(b_sellable)
                if b.expiry_date is not None:
                    if earliest_expiry is None or b.expiry_date < earliest_expiry:
                        earliest_expiry = b.expiry_date
                if b.is_near_expiry_on(today, warn_days):
                    near_expiry_batch_count += 1
                    near_expiry_qty += b_sellable

            if b.is_expired_on(today) or b_exp > 0:
                expired_batch_count += 1

        selling_price_dec = Decimal(str(shop_product.selling_price or "0.00"))
        retail_value = (selling_price_dec * Decimal(sellable_qty)).quantize(Decimal("0.01"))
        cost_value = cost_value.quantize(Decimal("0.01"))

        threshold = int(shop_product.low_stock_threshold or 0)
        eff_active = shop_product.effective_is_active

        if not eff_active:
            stock_status = "INACTIVE"
            is_low_stock = False
            is_out_of_stock = False
        elif sellable_qty == 0:
            stock_status = "OUT_OF_STOCK"
            is_low_stock = True
            is_out_of_stock = True
        elif sellable_qty <= threshold:
            stock_status = "LOW_STOCK"
            is_low_stock = True
            is_out_of_stock = False
        else:
            stock_status = "IN_STOCK"
            is_low_stock = False
            is_out_of_stock = False

        return {
            "sellable_qty": sellable_qty,
            "effective_sellable_qty": sellable_qty,
            "available_qty": sellable_qty,
            "raw_available_qty": available_qty,
            "reserved_qty": reserved_qty,
            "damaged_qty": damaged_qty,
            "expired_qty": expired_qty,
            "on_hand_qty": on_hand_qty,
            "total_stock": on_hand_qty,
            "active_batch_count": active_batch_count,
            "total_batch_count": len(batch_rows),
            "near_expiry_batch_count": near_expiry_batch_count,
            "has_near_expiry": near_expiry_batch_count > 0,
            "near_expiry_qty": near_expiry_qty,
            "expired_batch_count": expired_batch_count,
            "earliest_expiry_date": earliest_expiry.isoformat() if earliest_expiry else None,
            "is_low_stock": is_low_stock,
            "is_out_of_stock": is_out_of_stock,
            "stock_status": stock_status,
            "inventory_cost_value": f"{cost_value:.2f}",
            "inventory_retail_value": f"{retail_value:.2f}",
        }

    @staticmethod
    def _resolve_product_and_shop_product_for_receipt(
        shop: Shop,
        payload: dict[str, Any],
    ) -> tuple[Product, ShopProduct]:
        """Resolve and row-lock the target ShopProduct for receiving a batch."""
        raw_shop_product_id = payload.get("shop_product_id")
        raw_product_id = payload.get("product_id")

        shop_product: ShopProduct | None = None
        product: Product | None = None

        if raw_shop_product_id is not None and str(raw_shop_product_id).strip() != "":
            try:
                sp_id = int(raw_shop_product_id)
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    message="Invalid shop_product_id.",
                    details={"shop_product_id": "Must be a positive integer."},
                ) from exc

            shop_product = (
                db.session.query(ShopProduct)
                .filter(
                    ShopProduct.id == sp_id,
                    ShopProduct.shop_id == shop.id,
                )
                .with_for_update()
                .first()
            )
            if shop_product is None:
                raise ResourceNotFoundError(
                    message=f"Shop product #{sp_id} was not found in shop '{shop.name}'."
                )
            product = db.session.get(Product, shop_product.product_id)

        elif raw_product_id is not None and str(raw_product_id).strip() != "":
            try:
                prod_id = int(raw_product_id)
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    message="Invalid product_id.",
                    details={"product_id": "Must be a positive integer."},
                ) from exc

            product = db.session.get(Product, prod_id)
            if product is None:
                # Also check if caller passed shop_product.id in product_id field
                shop_product = (
                    db.session.query(ShopProduct)
                    .filter(
                        ShopProduct.id == prod_id,
                        ShopProduct.shop_id == shop.id,
                    )
                    .with_for_update()
                    .first()
                )
                if shop_product is not None:
                    product = db.session.get(Product, shop_product.product_id)
                else:
                    raise ResourceNotFoundError(
                        message=f"Product #{prod_id} was not found."
                    )

            if product.organization_id != shop.organization_id:
                raise AuthorizationError(
                    message="Cannot receive stock for a product belonging to another organization.",
                    code="UNAUTHORIZED_ORG_ACCESS",
                )

            if shop_product is None:
                shop_product = (
                    db.session.query(ShopProduct)
                    .filter(
                        ShopProduct.shop_id == shop.id,
                        ShopProduct.product_id == product.id,
                    )
                    .with_for_update()
                    .first()
                )
                if shop_product is None:
                    if not product.is_active:
                        raise ResourceConflictError(
                            message=f"Product '{product.sku}' is deactivated and cannot receive new stock.",
                            code="PRODUCT_INACTIVE",
                        )
                    # Auto-enroll active organization product into this shop's catalog
                    shop_product = ShopProduct(
                        shop_id=shop.id,
                        product_id=product.id,
                        purchase_price=product.default_purchase_price,
                        selling_price=product.default_selling_price,
                        low_stock_threshold=product.reorder_threshold,
                        near_expiry_days=shop.near_expiry_days_threshold,
                        surplus_threshold=40,
                        is_active=True,
                    )
                    db.session.add(shop_product)
                    db.session.flush()
        else:
            raise ValidationError(
                message="product_id is required to receive a batch.",
                details={"product_id": "Select a valid product."},
            )

        if product is None or not product.is_active or not shop_product.is_active:
            raise ResourceConflictError(
                message="Cannot receive stock for a deactivated product.",
                code="PRODUCT_INACTIVE",
            )

        return product, shop_product

    @staticmethod
    def receive_batch(
        actor: User,
        shop_id: int,
        payload: dict[str, Any],
        allow_past_expiry: bool = False,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[InventoryBatch, StockMovement]:
        """Receive new stock into a batch (API-017) inside an ACID transaction."""
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=False,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        today = get_business_today()

        try:
            product, shop_product = InventoryService._resolve_product_and_shop_product_for_receipt(
                shop=shop,
                payload=payload,
            )

            cleaned = validate_batch_receive_input(
                payload=payload,
                is_perishable=product.is_perishable,
                today=today,
                default_unit_cost=Decimal(str(shop_product.purchase_price)),
                allow_past_expiry=allow_past_expiry,
            )

            existing = (
                db.session.query(InventoryBatch)
                .filter(
                    InventoryBatch.shop_id == shop.id,
                    InventoryBatch.product_id == product.id,
                    func.upper(InventoryBatch.batch_code) == cleaned["batch_code"],
                )
                .first()
            )
            if existing is not None:
                raise DuplicateResourceError(
                    message=(
                        f"Batch code '{cleaned['batch_code']}' already exists for product "
                        f"'{product.sku}' in shop '{shop.name}'."
                    ),
                    code="DUPLICATE_BATCH_CODE",
                    details={
                        "batch_code": cleaned["batch_code"],
                        "product_id": product.id,
                        "shop_id": shop.id,
                    },
                )

            initial_qty = cleaned["initial_qty"]
            batch = InventoryBatch(
                shop_id=shop.id,
                product_id=product.id,
                shop_product_id=shop_product.id,
                batch_code=cleaned["batch_code"],
                manufactured_date=cleaned["manufactured_date"],
                expiry_date=cleaned["expiry_date"],
                unit_cost=cleaned["unit_cost"],
                initial_qty=initial_qty,
                available_qty=initial_qty,
                reserved_qty=0,
                damaged_qty=0,
                expired_qty=0,
                status="ACTIVE",
            )
            db.session.add(batch)
            db.session.flush()

            movement = StockMovement(
                organization_id=shop.organization_id,
                shop_id=shop.id,
                product_id=product.id,
                batch_id=batch.id,
                movement_type="RECEIPT",
                bucket_affected="AVAILABLE",
                quantity_change=initial_qty,
                balance_before=0,
                balance_after=initial_qty,
                unit_cost_snapshot=batch.unit_cost,
                reference_type="INITIAL_RECEIPT",
                reference_id=batch.id,
                reason=cleaned["reason"] or f"Initial batch receipt ({batch.batch_code})",
                performed_by_user_id=actor.id,
            )
            db.session.add(movement)
            db.session.flush()

            AuditService.record(
                action="BATCH_RECEIVED",
                entity_type="INVENTORY_BATCH",
                entity_id=batch.id,
                actor=actor,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                after_state={
                    "batch_code": batch.batch_code,
                    "product_id": product.id,
                    "sku": product.sku,
                    "initial_qty": batch.initial_qty,
                    "available_qty": batch.available_qty,
                    "unit_cost": str(batch.unit_cost),
                    "expiry_date": (
                        batch.expiry_date.isoformat() if batch.expiry_date else None
                    ),
                    "movement_id": movement.id,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )

            db.session.commit()
            return batch, movement

        except DomainError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="Batch code already exists for this product in this shop.",
                code="DUPLICATE_BATCH_CODE",
            ) from exc
        except OperationalError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database lock or connection error while receiving batch.",
                code="DATABASE_LOCK_OR_CONNECTION_ERROR",
            ) from exc
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def adjust_batch(
        actor: User,
        shop_id: int,
        batch_id: int,
        payload: dict[str, Any],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[InventoryBatch, list[StockMovement]]:
        """Perform an authorized manual stock adjustment (API-018) with row-level locking."""
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=False,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
        )
        cleaned = validate_batch_adjustment_input(payload)
        movement_type = cleaned["movement_type"]
        qty = cleaned["quantity"]
        reason = cleaned["reason"]
        today = get_business_today()

        try:
            batch = (
                db.session.query(InventoryBatch)
                .filter(InventoryBatch.id == int(batch_id))
                .with_for_update()
                .first()
            )
            if batch is None:
                raise ResourceNotFoundError(message=f"Batch #{batch_id} was not found.")

            if batch.shop_id != shop.id:
                AuditService.record(
                    action="UNAUTHORIZED_SHOP_BATCH_ACCESS",
                    entity_type="INVENTORY_BATCH",
                    entity_id=batch.id,
                    actor=actor,
                    organization_id=actor.organization_id,
                    shop_id=shop.id,
                    after_state={
                        "target_batch_shop_id": batch.shop_id,
                        "requested_shop_id": shop.id,
                    },
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                db.session.commit()
                raise AuthorizationError(
                    message="Batch does not belong to the authorized shop.",
                    code="UNAUTHORIZED_SHOP_ACCESS",
                )

            before_state = {
                "available_qty": batch.available_qty,
                "reserved_qty": batch.reserved_qty,
                "damaged_qty": batch.damaged_qty,
                "expired_qty": batch.expired_qty,
                "status": batch.status,
            }

            created_movements: list[StockMovement] = []

            if movement_type == "ADJUSTMENT_ADD":
                if batch.is_expired_on(today):
                    raise BatchExpiredError(
                        message=(
                            f"Cannot add sellable stock to batch '{batch.batch_code}' "
                            "because it is already expired."
                        ),
                        details={
                            "batch_id": batch.id,
                            "expiry_date": (
                                batch.expiry_date.isoformat() if batch.expiry_date else None
                            ),
                        },
                    )
                old_avail = int(batch.available_qty or 0)
                new_avail = old_avail + qty
                batch.available_qty = new_avail
                if batch.status in ("DEPLETED", "ARCHIVED"):
                    batch.status = "ACTIVE"

                m_add = StockMovement(
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    product_id=batch.product_id,
                    batch_id=batch.id,
                    movement_type="ADJUSTMENT_ADD",
                    bucket_affected="AVAILABLE",
                    quantity_change=qty,
                    balance_before=old_avail,
                    balance_after=new_avail,
                    unit_cost_snapshot=batch.unit_cost,
                    reference_type="MANUAL_ADJUSTMENT",
                    reference_id=batch.id,
                    reason=reason,
                    performed_by_user_id=actor.id,
                )
                db.session.add(m_add)
                created_movements.append(m_add)

            elif movement_type == "ADJUSTMENT_SUBTRACT":
                old_avail = int(batch.available_qty or 0)
                if qty > old_avail:
                    raise InsufficientStockError(
                        message=(
                            f"Cannot subtract {qty} units from batch '{batch.batch_code}'; "
                            f"only {old_avail} available units remain."
                        ),
                        code="INSUFFICIENT_AVAILABLE_STOCK",
                        details={
                            "batch_id": batch.id,
                            "batch_code": batch.batch_code,
                            "requested_qty": qty,
                            "available_qty": old_avail,
                        },
                    )
                new_avail = old_avail - qty
                batch.available_qty = new_avail
                if batch.on_hand_qty == 0:
                    batch.status = "DEPLETED"

                m_sub = StockMovement(
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    product_id=batch.product_id,
                    batch_id=batch.id,
                    movement_type="ADJUSTMENT_SUBTRACT",
                    bucket_affected="AVAILABLE",
                    quantity_change=-qty,
                    balance_before=old_avail,
                    balance_after=new_avail,
                    unit_cost_snapshot=batch.unit_cost,
                    reference_type="MANUAL_ADJUSTMENT",
                    reference_id=batch.id,
                    reason=reason,
                    performed_by_user_id=actor.id,
                )
                db.session.add(m_sub)
                created_movements.append(m_sub)

            elif movement_type == "MARK_DAMAGED":
                old_avail = int(batch.available_qty or 0)
                if qty > old_avail:
                    raise InsufficientStockError(
                        message=(
                            f"Cannot mark {qty} units as damaged in batch '{batch.batch_code}'; "
                            f"only {old_avail} available units remain."
                        ),
                        code="INSUFFICIENT_AVAILABLE_STOCK",
                        details={
                            "batch_id": batch.id,
                            "batch_code": batch.batch_code,
                            "requested_qty": qty,
                            "available_qty": old_avail,
                        },
                    )
                old_damaged = int(batch.damaged_qty or 0)
                new_avail = old_avail - qty
                new_damaged = old_damaged + qty
                batch.available_qty = new_avail
                batch.damaged_qty = new_damaged

                m_avail = StockMovement(
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    product_id=batch.product_id,
                    batch_id=batch.id,
                    movement_type="MARK_DAMAGED",
                    bucket_affected="AVAILABLE",
                    quantity_change=-qty,
                    balance_before=old_avail,
                    balance_after=new_avail,
                    unit_cost_snapshot=batch.unit_cost,
                    reference_type="MANUAL_ADJUSTMENT",
                    reference_id=batch.id,
                    reason=reason,
                    performed_by_user_id=actor.id,
                )
                m_dmg = StockMovement(
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    product_id=batch.product_id,
                    batch_id=batch.id,
                    movement_type="MARK_DAMAGED",
                    bucket_affected="DAMAGED",
                    quantity_change=qty,
                    balance_before=old_damaged,
                    balance_after=new_damaged,
                    unit_cost_snapshot=batch.unit_cost,
                    reference_type="MANUAL_ADJUSTMENT",
                    reference_id=batch.id,
                    reason=reason,
                    performed_by_user_id=actor.id,
                )
                db.session.add(m_avail)
                db.session.add(m_dmg)
                created_movements.extend([m_avail, m_dmg])

            elif movement_type == "MARK_EXPIRED":
                old_avail = int(batch.available_qty or 0)
                if qty > old_avail:
                    raise InsufficientStockError(
                        message=(
                            f"Cannot mark {qty} units as expired in batch '{batch.batch_code}'; "
                            f"only {old_avail} available units remain."
                        ),
                        code="INSUFFICIENT_AVAILABLE_STOCK",
                        details={
                            "batch_id": batch.id,
                            "batch_code": batch.batch_code,
                            "requested_qty": qty,
                            "available_qty": old_avail,
                        },
                    )
                old_expired = int(batch.expired_qty or 0)
                new_avail = old_avail - qty
                new_expired = old_expired + qty
                batch.available_qty = new_avail
                batch.expired_qty = new_expired
                if batch.available_qty == 0 and batch.reserved_qty == 0:
                    batch.status = "EXPIRED"

                m_avail = StockMovement(
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    product_id=batch.product_id,
                    batch_id=batch.id,
                    movement_type="MARK_EXPIRED",
                    bucket_affected="AVAILABLE",
                    quantity_change=-qty,
                    balance_before=old_avail,
                    balance_after=new_avail,
                    unit_cost_snapshot=batch.unit_cost,
                    reference_type="MANUAL_ADJUSTMENT",
                    reference_id=batch.id,
                    reason=reason,
                    performed_by_user_id=actor.id,
                )
                m_exp = StockMovement(
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    product_id=batch.product_id,
                    batch_id=batch.id,
                    movement_type="MARK_EXPIRED",
                    bucket_affected="EXPIRED",
                    quantity_change=qty,
                    balance_before=old_expired,
                    balance_after=new_expired,
                    unit_cost_snapshot=batch.unit_cost,
                    reference_type="MANUAL_ADJUSTMENT",
                    reference_id=batch.id,
                    reason=reason,
                    performed_by_user_id=actor.id,
                )
                db.session.add(m_avail)
                db.session.add(m_exp)
                created_movements.extend([m_avail, m_exp])

            db.session.flush()

            AuditService.record(
                action="BATCH_STOCK_ADJUSTED",
                entity_type="INVENTORY_BATCH",
                entity_id=batch.id,
                actor=actor,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                before_state=before_state,
                after_state={
                    "movement_type": movement_type,
                    "quantity": qty,
                    "reason": reason,
                    "available_qty": batch.available_qty,
                    "reserved_qty": batch.reserved_qty,
                    "damaged_qty": batch.damaged_qty,
                    "expired_qty": batch.expired_qty,
                    "status": batch.status,
                    "movement_ids": [m.id for m in created_movements],
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )

            db.session.commit()
            return batch, created_movements

        except DomainError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Stock adjustment violated a database integrity constraint.",
                code="DATABASE_INTEGRITY_CONFLICT",
                http_status=409,
            ) from exc
        except OperationalError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database lock or connection error during stock adjustment.",
                code="DATABASE_LOCK_OR_CONNECTION_ERROR",
            ) from exc
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def list_batches(
        actor: User,
        shop_id: int,
        product_id: int | None = None,
        status: str | None = None,
        near_expiry: bool = False,
        expired_only: bool = False,
        search_query: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """List shop batches in FEFO order with deterministic expiry evaluation (API-016)."""
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=True,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        page, per_page = validate_pagination_params(page, per_page)
        today = get_business_today()

        # Run automatic expiry sweep first so expired stock is never shown as sellable
        InventoryService.sweep_expired_batches(shop_id=shop.id, product_id=product_id, ref_date=today)

        query = (
            db.session.query(InventoryBatch)
            .join(Product, InventoryBatch.product_id == Product.id)
            .outerjoin(ShopProduct, InventoryBatch.shop_product_id == ShopProduct.id)
            .filter(InventoryBatch.shop_id == shop.id)
        )

        if product_id is not None:
            query = query.filter(InventoryBatch.product_id == int(product_id))

        if status:
            clean_status = status.strip().upper()
            query = query.filter(InventoryBatch.status == clean_status)

        if search_query and search_query.strip():
            term = f"%{search_query.strip()}%"
            query = query.filter(
                or_(
                    InventoryBatch.batch_code.ilike(term),
                    Product.sku.ilike(term),
                    Product.name.ilike(term),
                    Product.category.ilike(term),
                )
            )

        # FEFO Ordering: earliest expiry first, non-perishables (NULL) last, then FIFO by received_at & id
        ordered_rows = query.order_by(
            case((InventoryBatch.expiry_date.is_(None), 1), else_=0).asc(),
            InventoryBatch.expiry_date.asc(),
            InventoryBatch.received_at.asc(),
            InventoryBatch.id.asc(),
        ).all()

        filtered_rows: list[InventoryBatch] = []
        for b in ordered_rows:
            warn_days = (
                b.shop_product.near_expiry_days
                if b.shop_product is not None
                else shop.near_expiry_days_threshold
            )
            if near_expiry and not b.is_near_expiry_on(today, warn_days):
                continue
            if expired_only and not (b.is_expired_on(today) or int(b.expired_qty or 0) > 0):
                continue
            filtered_rows.append(b)

        total_items = len(filtered_rows)
        total_pages = max(1, (total_items + per_page - 1) // per_page)
        start_idx = (page - 1) * per_page
        page_rows = filtered_rows[start_idx : start_idx + per_page]

        items = [
            b.to_dict(
                ref_date=today,
                near_expiry_days=(
                    b.shop_product.near_expiry_days
                    if b.shop_product is not None
                    else shop.near_expiry_days_threshold
                ),
            )
            for b in page_rows
        ]

        return {
            "items": items,
            "meta": {
                "page": page,
                "per_page": per_page,
                "total": total_items,
                "total_items": total_items,
                "total_pages": total_pages,
                "business_date": today.isoformat(),
                "timezone": (
                    current_app.config.get("APP_TIMEZONE", "Asia/Kolkata")
                    if has_app_context()
                    else "Asia/Kolkata"
                ),
            },
        }

    @staticmethod
    def get_batch_detail(
        actor: User,
        shop_id: int,
        batch_id: int,
    ) -> dict[str, Any]:
        """Retrieve a single batch and its complete chronological stock movements."""
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=True,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        today = get_business_today()
        InventoryService.sweep_expired_batches(shop_id=shop.id, ref_date=today)

        batch = db.session.get(InventoryBatch, int(batch_id))
        if batch is None:
            raise ResourceNotFoundError(message=f"Batch #{batch_id} was not found.")
        if batch.shop_id != shop.id:
            raise AuthorizationError(
                message="Batch does not belong to the authorized shop.",
                code="UNAUTHORIZED_SHOP_ACCESS",
            )

        movements = (
            db.session.query(StockMovement)
            .filter(
                StockMovement.batch_id == batch.id,
                StockMovement.shop_id == shop.id,
            )
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
            .all()
        )
        warn_days = (
            batch.shop_product.near_expiry_days
            if batch.shop_product is not None
            else shop.near_expiry_days_threshold
        )
        batch_dict = batch.to_dict(ref_date=today, near_expiry_days=warn_days)
        batch_dict["movements"] = [m.to_dict() for m in movements]
        return batch_dict

    @staticmethod
    def list_stock_movements(
        actor: User,
        shop_id: int,
        product_id: int | None = None,
        batch_id: int | None = None,
        movement_type: str | None = None,
        bucket_affected: str | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict[str, Any]:
        """Return paginated immutable stock movement history for a shop (API-019)."""
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=True,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        page, per_page = validate_pagination_params(page, per_page, default_per_page=25)
        today = get_business_today()
        InventoryService.sweep_expired_batches(shop_id=shop.id, ref_date=today)

        query = db.session.query(StockMovement).filter(StockMovement.shop_id == shop.id)

        if product_id is not None:
            query = query.filter(StockMovement.product_id == int(product_id))
        if batch_id is not None:
            query = query.filter(StockMovement.batch_id == int(batch_id))
        if movement_type and movement_type.strip():
            query = query.filter(StockMovement.movement_type == movement_type.strip().upper())
        if bucket_affected and bucket_affected.strip():
            query = query.filter(StockMovement.bucket_affected == bucket_affected.strip().upper())

        total_items = query.count()
        total_pages = max(1, (total_items + per_page - 1) // per_page)

        rows = (
            query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        return {
            "items": [m.to_dict() for m in rows],
            "meta": {
                "page": page,
                "per_page": per_page,
                "total": total_items,
                "total_items": total_items,
                "total_pages": total_pages,
            },
        }

    @staticmethod
    def evaluate_shop_inventory_alerts(
        actor: User,
        shop_id: int,
    ) -> dict[str, Any]:
        """Evaluate deterministic low-stock, near-expiry, and expired-stock rules.

        Deduplicates alerts by canonical alert_key so repeated evaluations of the
        same condition never produce duplicate alert records.
        """
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=True,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        today = get_business_today()
        InventoryService.sweep_expired_batches(shop_id=shop.id, ref_date=today)

        shop_products = (
            db.session.query(ShopProduct)
            .join(Product, ShopProduct.product_id == Product.id)
            .filter(ShopProduct.shop_id == shop.id)
            .order_by(Product.name.asc())
            .all()
        )

        low_stock_alerts: dict[str, dict[str, Any]] = {}
        near_expiry_alerts: dict[str, dict[str, Any]] = {}
        expired_batch_alerts: dict[str, dict[str, Any]] = {}

        total_sellable_units = 0
        total_reserved_units = 0
        total_damaged_units = 0
        total_expired_units = 0
        total_cost_valuation = Decimal("0.00")
        total_retail_valuation = Decimal("0.00")

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
            summary = InventoryService.compute_shop_product_stock_summary(
                shop_product=sp,
                ref_date=today,
                batches=batches,
            )

            total_sellable_units += summary["sellable_qty"]
            total_reserved_units += summary["reserved_qty"]
            total_damaged_units += summary["damaged_qty"]
            total_expired_units += summary["expired_qty"]
            total_cost_valuation += Decimal(summary["inventory_cost_value"])
            total_retail_valuation += Decimal(summary["inventory_retail_value"])

            if summary["is_low_stock"]:
                dedup_key = f"LOW_STOCK:{shop.id}:{sp.product_id}"
                alert_type_label = (
                    "OUT_OF_STOCK" if summary["is_out_of_stock"] else "LOW_STOCK"
                )
                low_stock_alerts[dedup_key] = {
                    "alert_key": dedup_key,
                    "alert_type": alert_type_label,
                    "severity": alert_type_label,
                    "severity_level": "CRITICAL" if summary["is_out_of_stock"] else "WARNING",
                    "shop_id": shop.id,
                    "shop_product_id": sp.id,
                    "product_id": sp.product_id,
                    "sku": sp.product.sku if sp.product else None,
                    "product_sku": sp.product.sku if sp.product else None,
                    "product_name": sp.product.name if sp.product else None,
                    "category": sp.product.category if sp.product else None,
                    "unit": sp.product.unit if sp.product else None,
                    "sellable_qty": summary["sellable_qty"],
                    "available_qty": summary["sellable_qty"],
                    "reorder_threshold": sp.low_stock_threshold,
                    "reorder_level": sp.low_stock_threshold,
                    "low_stock_threshold": sp.low_stock_threshold,
                    "deficit_qty": max(0, sp.low_stock_threshold - summary["sellable_qty"]),
                    "stock_status": summary["stock_status"],
                }

            warn_days = int(sp.near_expiry_days or shop.near_expiry_days_threshold)
            for b in batches:
                b_dict = b.to_dict(ref_date=today, near_expiry_days=warn_days)
                if b.is_near_expiry_on(today, warn_days):
                    ne_key = f"NEAR_EXPIRY:{shop.id}:{b.id}"
                    near_expiry_alerts[ne_key] = {
                        "alert_key": ne_key,
                        "alert_type": "NEAR_EXPIRY",
                        "severity": (
                            "CRITICAL"
                            if b_dict["expiry_risk_band"] in ("EXPIRES_TODAY", "CRITICAL")
                            else "WARNING"
                        ),
                        "near_expiry_window_days": warn_days,
                        "near_expiry_threshold_days": warn_days,
                        **b_dict,
                    }
                if b.is_expired_on(today) or int(b.expired_qty or 0) > 0:
                    exp_key = f"EXPIRED_BATCH:{shop.id}:{b.id}"
                    expired_batch_alerts[exp_key] = {
                        "alert_key": exp_key,
                        "alert_type": "EXPIRED_BATCH",
                        "severity": "CRITICAL",
                        "near_expiry_threshold_days": warn_days,
                        **b_dict,
                    }

        cost_val_quantized = total_cost_valuation.quantize(Decimal("0.01"))
        retail_val_quantized = total_retail_valuation.quantize(Decimal("0.01"))

        return {
            "shop_id": shop.id,
            "shop_name": shop.name,
            "shop_code": shop.code,
            "business_date": today.isoformat(),
            "timezone": (
                current_app.config.get("APP_TIMEZONE", "Asia/Kolkata")
                if has_app_context()
                else "Asia/Kolkata"
            ),
            "kpis": {
                "total_products": len(shop_products),
                "total_catalog_products": len(shop_products),
                "active_products": sum(1 for sp in shop_products if sp.effective_is_active),
                "active_catalog_products": sum(1 for sp in shop_products if sp.effective_is_active),
                "total_available_units": total_sellable_units,
                "total_sellable_units": total_sellable_units,
                "total_reserved_units": total_reserved_units,
                "total_damaged_units": total_damaged_units,
                "total_expired_units": total_expired_units,
                "low_stock_count": len(low_stock_alerts),
                "out_of_stock_count": sum(
                    1 for a in low_stock_alerts.values() if a["alert_type"] == "OUT_OF_STOCK"
                ),
                "near_expiry_batch_count": len(near_expiry_alerts),
                "expired_batch_count": len(expired_batch_alerts),
                "inventory_valuation_cost": float(cost_val_quantized),
                "inventory_cost_valuation": f"{cost_val_quantized:.2f}",
                "inventory_retail_valuation": f"{retail_val_quantized:.2f}",
            },
            "low_stock_items": list(low_stock_alerts.values()),
            "near_expiry_batches": sorted(
                near_expiry_alerts.values(),
                key=lambda x: (x["days_to_expiry"] if x["days_to_expiry"] is not None else 999999, x["id"]),
            ),
            "expired_batches": list(expired_batch_alerts.values()),
        }

    @staticmethod
    def verify_shop_ledger_reconciliation(shop_id: int) -> dict[str, Any]:
        """Verify BR-002 invariant: every batch bucket equals the sum of its stock_movements."""
        batches = (
            db.session.query(InventoryBatch)
            .filter(InventoryBatch.shop_id == int(shop_id))
            .all()
        )
        discrepancies: list[dict[str, Any]] = []
        bucket_map = {
            "AVAILABLE": "available_qty",
            "RESERVED": "reserved_qty",
            "DAMAGED": "damaged_qty",
            "EXPIRED": "expired_qty",
        }

        for batch in batches:
            for bucket_name, attr_name in bucket_map.items():
                actual_balance = int(getattr(batch, attr_name) or 0)
                ledger_sum = (
                    db.session.query(
                        func.coalesce(func.sum(StockMovement.quantity_change), 0)
                    )
                    .filter(
                        StockMovement.batch_id == batch.id,
                        StockMovement.bucket_affected == bucket_name,
                    )
                    .scalar()
                )
                ledger_sum_int = int(ledger_sum or 0)
                if actual_balance < 0 or actual_balance != ledger_sum_int:
                    discrepancies.append(
                        {
                            "batch_id": batch.id,
                            "batch_code": batch.batch_code,
                            "bucket": bucket_name,
                            "batch_balance": actual_balance,
                            "ledger_sum": ledger_sum_int,
                        }
                    )

        return {
            "shop_id": int(shop_id),
            "batches_checked": len(batches),
            "is_reconciled": len(discrepancies) == 0,
            "discrepancies": discrepancies,
        }
