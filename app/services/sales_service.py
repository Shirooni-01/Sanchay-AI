"""Transactional Point-of-Sale (POS) Billing, FEFO Batch Allocation, Returns & Cancellations Service (Phase 4).

Enforces:
- BR-002: 100% reconciliation between batch buckets and stock_movements ledger.
- BR-004: POS sale cannot exceed currently available, non-expired sellable stock; deducts in FEFO order.
- BR-006: Row-level locking (SELECT ... FOR UPDATE) prevents concurrent overselling and negative stock.
- BR-007: Every sale, cancellation, and return writes stock_movements and audit_logs in the same ACID transaction.
- BR-013: Strict shop and organization isolation.
- BR-017: Accurate separation of Gross Sales Revenue, Returns, Net Revenue, COGS, and Gross Profit.
- OD-006: Same-day sale cancellation vs. condition-based (RESELLABLE / DAMAGED / EXPIRED) item returns.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError, OperationalError

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch, StockMovement
from app.models.organization import Shop
from app.models.sales import Sale, SaleItem, SaleReturn
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.inventory_service import InventoryService, get_business_today
from app.utils.decorators import verify_shop_access
from app.utils.errors import (
    AuthorizationError,
    DatabaseConflictError,
    DomainError,
    DuplicateResourceError,
    InsufficientStockError,
    ResourceConflictError,
    ResourceNotFoundError,
    ValidationError,
)
from app.utils.validators import (
    _parse_date,
    validate_pagination_params,
    validate_sale_cancel_input,
    validate_sale_create_input,
    validate_sale_return_input,
)


class SalesService:
    """Centralized, transactional service for POS sales, FEFO allocation, returns, and cancellations."""

    @staticmethod
    def _generate_next_invoice_number(shop: Shop, business_date: date) -> str:
        """Generate sequential invoice number per shop per business day (must be called under shop row lock)."""
        date_token = business_date.strftime("%Y%m%d")
        prefix = f"INV-{shop.code.upper()}-{date_token}-"

        latest_invoice = (
            db.session.query(Sale.invoice_number)
            .filter(
                Sale.shop_id == shop.id,
                Sale.invoice_number.like(f"{prefix}%"),
            )
            .order_by(Sale.id.desc())
            .first()
        )
        next_seq = 1
        if latest_invoice and latest_invoice[0]:
            suffix = latest_invoice[0][len(prefix) :]
            if suffix.isdigit():
                next_seq = int(suffix) + 1
            else:
                count_today = (
                    db.session.query(func.count(Sale.id))
                    .filter(
                        Sale.shop_id == shop.id,
                        Sale.invoice_number.like(f"{prefix}%"),
                    )
                    .scalar()
                    or 0
                )
                next_seq = int(count_today) + 1

        return f"{prefix}{next_seq:04d}"

    @staticmethod
    def _resolve_shop_product_for_sale(
        shop: Shop,
        product_id: int | None,
        shop_product_id: int | None,
    ) -> ShopProduct:
        """Load and validate ShopProduct belonging to shop_id."""
        sp: ShopProduct | None = None
        if shop_product_id is not None:
            sp = db.session.get(ShopProduct, int(shop_product_id))
            if sp is None:
                raise ResourceNotFoundError(
                    message=f"Shop product #{shop_product_id} was not found."
                )
            if sp.shop_id != shop.id:
                raise AuthorizationError(
                    message="Cannot sell a product configuration belonging to another shop.",
                    code="UNAUTHORIZED_SHOP_ACCESS",
                )
            if product_id is not None and sp.product_id != int(product_id):
                raise ValidationError(
                    message="Mismatched product_id and shop_product_id in cart item.",
                    details={"product_id": "Does not match shop_product_id."},
                )
        elif product_id is not None:
            prod = db.session.get(Product, int(product_id))
            if prod is None:
                raise ResourceNotFoundError(
                    message=f"Product #{product_id} was not found."
                )
            if prod.organization_id != shop.organization_id:
                raise AuthorizationError(
                    message="Product belongs to another organization.",
                    code="UNAUTHORIZED_ORG_ACCESS",
                )
            sp = (
                db.session.query(ShopProduct)
                .filter(
                    ShopProduct.shop_id == shop.id,
                    ShopProduct.product_id == prod.id,
                )
                .first()
            )
            if sp is None:
                raise ResourceNotFoundError(
                    message=f"Product '{prod.sku}' is not enrolled in shop '{shop.name}'."
                )
        else:
            raise ValidationError(message="Cart item requires product_id.")

        if not sp.effective_is_active:
            sku_label = sp.product.sku if sp.product else f"#{sp.product_id}"
            raise ResourceConflictError(
                message=f"Product '{sku_label}' is deactivated and cannot be sold.",
                code="PRODUCT_INACTIVE",
                details={"product_id": sp.product_id, "shop_product_id": sp.id},
            )
        return sp

    @staticmethod
    def create_sale(
        actor: User,
        shop_id: int,
        payload: dict[str, Any],
        header_idempotency_key: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        simulate_failure_after_allocation: bool = False,
    ) -> Sale:
        """Create a POS sale transaction with atomic FEFO batch deduction (API-020, BR-004, BR-006).

        All steps execute inside a single ACID database transaction after sweeping expired batches.
        If any validation, stock allocation, or simulated error occurs, the entire sale and all
        batch deductions are rolled back.
        """
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=False,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        cleaned = validate_sale_create_input(
            payload=payload,
            header_idempotency_key=header_idempotency_key,
        )
        today = get_business_today()

        # Step 1: Sweep any past-due batches in this shop so expired stock is quarantined
        InventoryService.sweep_expired_batches(
            shop_id=shop.id,
            ref_date=today,
            commit=True,
        )

        try:
            # Step 2: Lock Shop row to serialize invoice generation and idempotency check
            locked_shop = (
                db.session.query(Shop)
                .filter(Shop.id == shop.id)
                .with_for_update()
                .first()
            )
            if locked_shop is None or locked_shop.status != "ACTIVE":
                raise AuthorizationError(
                    message="Cannot process sales for a deactivated or suspended shop.",
                    code="INACTIVE_SHOP",
                )

            idem_key = cleaned["idempotency_key"]
            if idem_key:
                existing_sale = (
                    db.session.query(Sale)
                    .filter(
                        Sale.shop_id == locked_shop.id,
                        Sale.idempotency_key == idem_key,
                    )
                    .first()
                )
                if existing_sale is not None:
                    raise DuplicateResourceError(
                        message=f"Duplicate sale submission detected (invoice '{existing_sale.invoice_number}').",
                        code="DUPLICATE_SALE_SUBMISSION",
                        details={
                            "idempotency_key": idem_key,
                            "existing_sale_id": existing_sale.id,
                            "invoice_number": existing_sale.invoice_number,
                        },
                    )

            # Step 3: Resolve authoritative ShopProduct rows and merge duplicate cart lines by product_id
            merged_lines: dict[int, dict[str, Any]] = {}
            for raw_line in cleaned["items"]:
                sp = SalesService._resolve_shop_product_for_sale(
                    shop=locked_shop,
                    product_id=raw_line["product_id"],
                    shop_product_id=raw_line["shop_product_id"],
                )
                pid = int(sp.product_id)
                if pid not in merged_lines:
                    merged_lines[pid] = {
                        "shop_product": sp,
                        "product_id": pid,
                        "quantity": 0,
                        "line_discount": Decimal("0.00"),
                    }
                merged_lines[pid]["quantity"] += int(raw_line["quantity"])
                merged_lines[pid]["line_discount"] += Decimal(str(raw_line["line_discount"]))

            # Sort by product_id ASC to prevent multi-item deadlocks under concurrent checkouts
            ordered_lines = [
                merged_lines[pid] for pid in sorted(merged_lines.keys())
            ]

            # Step 4: Compute authoritative gross subtotal and validate discounts
            subtotal_amount = Decimal("0.00")
            total_item_discounts = Decimal("0.00")
            for line in ordered_lines:
                sp = line["shop_product"]
                unit_price = Decimal(str(sp.selling_price)).quantize(Decimal("0.01"))
                qty = int(line["quantity"])
                line_gross = (unit_price * Decimal(qty)).quantize(Decimal("0.01"))
                line_disc = line["line_discount"].quantize(Decimal("0.01"))

                if line_disc > line_gross:
                    raise ValidationError(
                        message=(
                            f"Line discount ({line_disc:.2f}) cannot exceed line subtotal "
                            f"({line_gross:.2f}) for SKU '{sp.product.sku}'."
                        ),
                        details={"line_discount": "Cannot exceed line subtotal."},
                    )
                line["unit_selling_price"] = unit_price
                line["line_gross"] = line_gross
                line["line_discount"] = line_disc
                subtotal_amount += line_gross
                total_item_discounts += line_disc

            header_discount = cleaned["discount_amount"].quantize(Decimal("0.01"))
            if header_discount > Decimal("0.00") and total_item_discounts == Decimal("0.00"):
                if header_discount > subtotal_amount:
                    raise ValidationError(
                        message=(
                            f"Discount amount ({header_discount:.2f}) cannot exceed "
                            f"subtotal amount ({subtotal_amount:.2f})."
                        ),
                        details={"discount_amount": "Cannot exceed subtotal amount."},
                    )
                # Prorate bill-level discount across product lines so line_totals sum to total_amount
                rem_header_disc = header_discount
                for idx, line in enumerate(ordered_lines):
                    if idx == len(ordered_lines) - 1 or subtotal_amount == Decimal("0.00"):
                        share = min(line["line_gross"], rem_header_disc)
                    else:
                        share = (
                            (header_discount * line["line_gross"]) / subtotal_amount
                        ).quantize(Decimal("0.01"))
                        share = min(line["line_gross"], share, rem_header_disc)
                    line["line_discount"] = share
                    rem_header_disc -= share
                total_discount_amount = header_discount - rem_header_disc
            elif header_discount > Decimal("0.00") and header_discount != total_item_discounts:
                raise ValidationError(
                    message="Specify either per-item line_discount or a matching bill discount_amount, not conflicting values.",
                    details={"discount_amount": "Conflicts with sum of item line_discounts."},
                )
            else:
                total_discount_amount = total_item_discounts

            total_amount = (subtotal_amount - total_discount_amount).quantize(Decimal("0.01"))

            # Step 5: Create Sale header (flushed to obtain sale.id for FK references)
            invoice_number = SalesService._generate_next_invoice_number(
                shop=locked_shop,
                business_date=today,
            )
            sale = Sale(
                organization_id=locked_shop.organization_id,
                shop_id=locked_shop.id,
                invoice_number=invoice_number,
                idempotency_key=idem_key,
                customer_name=cleaned["customer_name"],
                customer_phone=cleaned["customer_phone"],
                status="COMPLETED",
                subtotal_amount=subtotal_amount,
                discount_amount=total_discount_amount,
                total_amount=total_amount,
                total_cost_amount=Decimal("0.00"),
                returned_amount=Decimal("0.00"),
                payment_mode=cleaned["payment_mode"],
                notes=cleaned["notes"],
                sold_by_user_id=actor.id,
            )
            db.session.add(sale)
            db.session.flush()

            # Step 6: Lock eligible batches in FEFO order and allocate stock
            total_cost_amount = Decimal("0.00")
            created_movements: list[StockMovement] = []

            for line in ordered_lines:
                sp = line["shop_product"]
                pid = int(line["product_id"])
                req_qty = int(line["quantity"])
                unit_price = line["unit_selling_price"]
                line_disc = line["line_discount"]

                eligible_batches = (
                    db.session.query(InventoryBatch)
                    .filter(
                        InventoryBatch.shop_id == locked_shop.id,
                        InventoryBatch.product_id == pid,
                        InventoryBatch.status == "ACTIVE",
                        InventoryBatch.available_qty > 0,
                        or_(
                            InventoryBatch.expiry_date.is_(None),
                            InventoryBatch.expiry_date >= today,
                        ),
                    )
                    .order_by(
                        case((InventoryBatch.expiry_date.is_(None), 1), else_=0).asc(),
                        InventoryBatch.expiry_date.asc(),
                        InventoryBatch.received_at.asc(),
                        InventoryBatch.id.asc(),
                    )
                    .with_for_update()
                    .all()
                )

                avail_sellable = sum(int(b.available_qty or 0) for b in eligible_batches)
                if avail_sellable < req_qty:
                    sku_str = sp.product.sku if sp.product else f"#{pid}"
                    raise InsufficientStockError(
                        message=(
                            f"Requested quantity ({req_qty}) exceeds available sellable stock "
                            f"({avail_sellable}) for {sku_str}."
                        ),
                        code="INSUFFICIENT_SELLABLE_STOCK",
                        details={
                            "product_id": pid,
                            "shop_product_id": sp.id,
                            "sku": sku_str,
                            "requested_qty": req_qty,
                            "available_sellable_qty": avail_sellable,
                            "available_qty": avail_sellable,
                        },
                    )

                # Plan batch takes first so we can prorate line_discount accurately across batch splits
                batch_takes: list[tuple[InventoryBatch, int]] = []
                rem_qty = req_qty
                for batch in eligible_batches:
                    if rem_qty <= 0:
                        break
                    b_avail = int(batch.available_qty or 0)
                    if b_avail <= 0:
                        continue
                    q_take = min(b_avail, rem_qty)
                    batch_takes.append((batch, q_take))
                    rem_qty -= q_take

                rem_line_disc = line_disc
                for alloc_idx, (batch, q_take) in enumerate(batch_takes):
                    old_avail = int(batch.available_qty or 0)
                    new_avail = old_avail - q_take
                    batch.available_qty = new_avail
                    if batch.on_hand_qty == 0:
                        batch.status = "DEPLETED"

                    if alloc_idx == len(batch_takes) - 1:
                        alloc_disc = rem_line_disc
                    else:
                        alloc_disc = (
                            (line_disc * Decimal(q_take)) / Decimal(req_qty)
                        ).quantize(Decimal("0.01"))
                        alloc_disc = min(rem_line_disc, alloc_disc)
                    rem_line_disc -= alloc_disc

                    unit_cost = Decimal(str(batch.unit_cost or 0)).quantize(Decimal("0.01"))
                    alloc_gross = (Decimal(q_take) * unit_price).quantize(Decimal("0.01"))
                    alloc_total = (alloc_gross - alloc_disc).quantize(Decimal("0.01"))
                    alloc_cost = (Decimal(q_take) * unit_cost).quantize(Decimal("0.01"))
                    total_cost_amount += alloc_cost

                    sale_item = SaleItem(
                        sale_id=sale.id,
                        shop_id=locked_shop.id,
                        product_id=pid,
                        shop_product_id=sp.id,
                        batch_id=batch.id,
                        quantity=q_take,
                        returned_quantity=0,
                        unit_selling_price=unit_price,
                        unit_cost_price=unit_cost,
                        line_discount=alloc_disc,
                        line_total=alloc_total,
                    )
                    db.session.add(sale_item)

                    movement = StockMovement(
                        organization_id=locked_shop.organization_id,
                        shop_id=locked_shop.id,
                        product_id=pid,
                        batch_id=batch.id,
                        movement_type="SALE",
                        bucket_affected="AVAILABLE",
                        quantity_change=-q_take,
                        balance_before=old_avail,
                        balance_after=new_avail,
                        unit_cost_snapshot=unit_cost,
                        reference_type="SALE",
                        reference_id=sale.id,
                        reason=f"POS Sale {invoice_number}",
                        performed_by_user_id=actor.id,
                    )
                    db.session.add(movement)
                    created_movements.append(movement)

            sale.total_cost_amount = total_cost_amount.quantize(Decimal("0.01"))
            db.session.flush()

            if simulate_failure_after_allocation:
                raise RuntimeError("Simulated failure after FEFO batch allocation to verify rollback.")

            AuditService.record(
                action="SALE_COMPLETED",
                entity_type="SALE",
                entity_id=sale.id,
                actor=actor,
                organization_id=locked_shop.organization_id,
                shop_id=locked_shop.id,
                before_state=None,
                after_state={
                    "invoice_number": sale.invoice_number,
                    "subtotal_amount": f"{sale.subtotal_amount:.2f}",
                    "discount_amount": f"{sale.discount_amount:.2f}",
                    "total_amount": f"{sale.total_amount:.2f}",
                    "total_cost_amount": f"{sale.total_cost_amount:.2f}",
                    "payment_mode": sale.payment_mode,
                    "movement_ids": [m.id for m in created_movements],
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )

            db.session.commit()
            return sale

        except DomainError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="Sale submission conflicted with an existing invoice or idempotency key.",
                code="DUPLICATE_SALE_SUBMISSION",
            ) from exc
        except OperationalError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database lock or connection error while processing POS sale.",
                code="DATABASE_LOCK_OR_CONNECTION_ERROR",
            ) from exc
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def cancel_sale(
        actor: User,
        shop_id: int,
        sale_id: int,
        payload: dict[str, Any] | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Sale:
        """Cancel a same-day completed POS sale and restore stock to original batches (API-023, OD-006)."""
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=False,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        cleaned = validate_sale_cancel_input(payload)
        reason = cleaned["reason"]
        today = get_business_today()

        try:
            sale = (
                db.session.query(Sale)
                .filter(Sale.id == int(sale_id))
                .with_for_update()
                .first()
            )
            if sale is None:
                raise ResourceNotFoundError(message=f"Sale #{sale_id} was not found.")
            if sale.shop_id != shop.id:
                raise AuthorizationError(
                    message="Sale does not belong to the authorized shop.",
                    code="UNAUTHORIZED_SHOP_ACCESS",
                )

            if sale.status == "CANCELLED":
                raise ResourceConflictError(
                    message=f"Invoice '{sale.invoice_number}' is already cancelled.",
                    code="SALE_ALREADY_CANCELLED",
                    details={"sale_id": sale.id, "status": sale.status},
                )
            if sale.status != "COMPLETED":
                raise ResourceConflictError(
                    message=(
                        f"Invoice '{sale.invoice_number}' has status '{sale.status}' and cannot be "
                        "cancelled as a whole. Use item returns for remaining items."
                    ),
                    code="INVALID_SALE_STATE_FOR_CANCELLATION",
                    details={"sale_id": sale.id, "status": sale.status},
                )

            sale_date = sale.created_at.date() if sale.created_at else today
            if sale_date != today and actor.role == "STAFF":
                raise AuthorizationError(
                    message="Staff can only cancel sales created on the same business day.",
                    code="INSUFFICIENT_ROLE_PERMISSION",
                )

            before_state = {
                "status": sale.status,
                "returned_amount": f"{sale.returned_amount:.2f}",
            }

            movement_ids: list[int] = []
            for item in sorted(sale.items, key=lambda x: x.batch_id):
                batch = (
                    db.session.query(InventoryBatch)
                    .filter(InventoryBatch.id == item.batch_id)
                    .with_for_update()
                    .first()
                )
                if batch is None:
                    raise ResourceNotFoundError(
                        message=f"Batch #{item.batch_id} referenced by sale item was not found."
                    )

                qty_to_restore = max(0, int(item.quantity or 0) - int(item.returned_quantity or 0))
                if qty_to_restore <= 0:
                    continue

                if batch.is_expired_on(today):
                    old_exp = int(batch.expired_qty or 0)
                    new_exp = old_exp + qty_to_restore
                    batch.expired_qty = new_exp
                    if batch.available_qty == 0 and batch.reserved_qty == 0:
                        batch.status = "EXPIRED"
                    bucket = "EXPIRED"
                    bal_before = old_exp
                    bal_after = new_exp
                else:
                    old_avail = int(batch.available_qty or 0)
                    new_avail = old_avail + qty_to_restore
                    batch.available_qty = new_avail
                    if batch.status == "DEPLETED":
                        batch.status = "ACTIVE"
                    bucket = "AVAILABLE"
                    bal_before = old_avail
                    bal_after = new_avail

                item.returned_quantity = int(item.quantity)

                mov = StockMovement(
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    product_id=item.product_id,
                    batch_id=batch.id,
                    movement_type="SALE_CANCELLATION",
                    bucket_affected=bucket,
                    quantity_change=qty_to_restore,
                    balance_before=bal_before,
                    balance_after=bal_after,
                    unit_cost_snapshot=item.unit_cost_price,
                    reference_type="SALE",
                    reference_id=sale.id,
                    reason=f"Cancelled invoice {sale.invoice_number}: {reason}",
                    performed_by_user_id=actor.id,
                )
                db.session.add(mov)
                db.session.flush()
                movement_ids.append(mov.id)

            sale.status = "CANCELLED"
            sale.returned_amount = sale.total_amount
            sale.cancelled_by_user_id = actor.id
            sale.cancelled_at = datetime.now(timezone.utc)
            sale.cancellation_reason = reason

            AuditService.record(
                action="SALE_CANCELLED",
                entity_type="SALE",
                entity_id=sale.id,
                actor=actor,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                before_state=before_state,
                after_state={
                    "status": sale.status,
                    "returned_amount": f"{sale.returned_amount:.2f}",
                    "cancellation_reason": reason,
                    "movement_ids": movement_ids,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )

            db.session.commit()
            return sale

        except DomainError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database constraint conflict while cancelling sale.",
                code="DATABASE_INTEGRITY_CONFLICT",
                http_status=409,
            ) from exc
        except OperationalError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database lock error while cancelling sale.",
                code="DATABASE_LOCK_OR_CONNECTION_ERROR",
            ) from exc
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def process_return(
        actor: User,
        shop_id: int,
        sale_id: int,
        payload: dict[str, Any],
        header_idempotency_key: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[Sale, list[SaleReturn]]:
        """Process partial or full item returns against an existing sale (API-024, OD-006).

        Restores stock to:
        - AVAILABLE bucket if return_condition == 'RESELLABLE' and the batch is not expired.
        - DAMAGED bucket if return_condition == 'DAMAGED'.
        - EXPIRED bucket if return_condition == 'EXPIRED' or the batch has expired.
        """
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=False,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        cleaned = validate_sale_return_input(
            payload=payload,
            header_idempotency_key=header_idempotency_key,
        )
        today = get_business_today()

        try:
            sale = (
                db.session.query(Sale)
                .filter(Sale.id == int(sale_id))
                .with_for_update()
                .first()
            )
            if sale is None:
                raise ResourceNotFoundError(message=f"Sale #{sale_id} was not found.")
            if sale.shop_id != shop.id:
                raise AuthorizationError(
                    message="Sale does not belong to the authorized shop.",
                    code="UNAUTHORIZED_SHOP_ACCESS",
                )

            if sale.status == "CANCELLED":
                raise ResourceConflictError(
                    message=f"Cannot process returns for cancelled invoice '{sale.invoice_number}'.",
                    code="SALE_CANCELLED",
                    details={"sale_id": sale.id, "status": sale.status},
                )
            if sale.status == "RETURNED":
                raise ResourceConflictError(
                    message=f"All items on invoice '{sale.invoice_number}' have already been returned.",
                    code="SALE_ALREADY_FULLY_RETURNED",
                    details={"sale_id": sale.id, "status": sale.status},
                )

            idem_key = cleaned["idempotency_key"]
            if idem_key:
                existing_ret = (
                    db.session.query(SaleReturn)
                    .filter(
                        SaleReturn.shop_id == shop.id,
                        SaleReturn.idempotency_key == idem_key,
                    )
                    .first()
                )
                if existing_ret is not None:
                    raise DuplicateResourceError(
                        message="Duplicate return submission detected.",
                        code="DUPLICATE_RETURN_SUBMISSION",
                        details={
                            "idempotency_key": idem_key,
                            "existing_return_id": existing_ret.id,
                        },
                    )

            before_state = {
                "status": sale.status,
                "returned_amount": f"{sale.returned_amount:.2f}",
            }

            created_returns: list[SaleReturn] = []
            for idx, req_item in enumerate(cleaned["items"]):
                sale_item: SaleItem | None = None
                if req_item["sale_item_id"] is not None:
                    sale_item = (
                        db.session.query(SaleItem)
                        .filter(
                            SaleItem.id == int(req_item["sale_item_id"]),
                            SaleItem.sale_id == sale.id,
                        )
                        .with_for_update()
                        .first()
                    )
                else:
                    q_item = db.session.query(SaleItem).filter(
                        SaleItem.sale_id == sale.id,
                        SaleItem.product_id == int(req_item["product_id"]),
                    )
                    if req_item["batch_id"] is not None:
                        q_item = q_item.filter(SaleItem.batch_id == int(req_item["batch_id"]))
                    sale_item = (
                        q_item.order_by(SaleItem.id.asc())
                        .with_for_update()
                        .first()
                    )

                if sale_item is None:
                    raise ResourceNotFoundError(
                        message="Specified sale line item was not found on this invoice."
                    )

                eligible_qty = int(sale_item.quantity or 0) - int(sale_item.returned_quantity or 0)
                qty_returned = int(req_item["quantity_returned"])
                if qty_returned > eligible_qty:
                    raise ResourceConflictError(
                        message=(
                            f"Cannot return {qty_returned} units for line item #{sale_item.id}; "
                            f"only {eligible_qty} units remain eligible for return."
                        ),
                        code="RETURN_EXCEEDS_SOLD_QUANTITY",
                        details={
                            "sale_item_id": sale_item.id,
                            "sold_quantity": sale_item.quantity,
                            "already_returned_quantity": sale_item.returned_quantity,
                            "eligible_return_quantity": eligible_qty,
                            "requested_return_quantity": qty_returned,
                        },
                    )

                batch = (
                    db.session.query(InventoryBatch)
                    .filter(InventoryBatch.id == sale_item.batch_id)
                    .with_for_update()
                    .first()
                )
                if batch is None:
                    raise ResourceNotFoundError(
                        message=f"Batch #{sale_item.batch_id} was not found."
                    )

                cond = req_item["return_condition"]
                if cond == "RESELLABLE" and not batch.is_expired_on(today):
                    old_bal = int(batch.available_qty or 0)
                    new_bal = old_bal + qty_returned
                    batch.available_qty = new_bal
                    if batch.status == "DEPLETED":
                        batch.status = "ACTIVE"
                    bucket_restored = "AVAILABLE"
                elif cond == "DAMAGED":
                    old_bal = int(batch.damaged_qty or 0)
                    new_bal = old_bal + qty_returned
                    batch.damaged_qty = new_bal
                    bucket_restored = "DAMAGED"
                else:
                    # EXPIRED condition or RESELLABLE item whose batch has now expired
                    old_bal = int(batch.expired_qty or 0)
                    new_bal = old_bal + qty_returned
                    batch.expired_qty = new_bal
                    if batch.available_qty == 0 and batch.reserved_qty == 0:
                        batch.status = "EXPIRED"
                    bucket_restored = "EXPIRED"

                # Calculate proportional refund amount on this sale_item
                prev_refunds_on_item = (
                    db.session.query(func.coalesce(func.sum(SaleReturn.refund_amount), 0))
                    .filter(SaleReturn.sale_item_id == sale_item.id)
                    .scalar()
                )
                prev_refund_dec = Decimal(str(prev_refunds_on_item or 0)).quantize(Decimal("0.01"))
                item_line_total = Decimal(str(sale_item.line_total or 0)).quantize(Decimal("0.01"))

                if int(sale_item.returned_quantity or 0) + qty_returned == int(sale_item.quantity):
                    refund_amount = max(Decimal("0.00"), item_line_total - prev_refund_dec)
                else:
                    refund_amount = (
                        (item_line_total * Decimal(qty_returned)) / Decimal(sale_item.quantity)
                    ).quantize(Decimal("0.01"))

                item_idem_key = (
                    idem_key
                    if (idem_key and len(cleaned["items"]) == 1)
                    else (f"{idem_key}:{idx}" if idem_key else None)
                )

                sale_return = SaleReturn(
                    sale_id=sale.id,
                    sale_item_id=sale_item.id,
                    shop_id=shop.id,
                    product_id=sale_item.product_id,
                    batch_id=batch.id,
                    quantity_returned=qty_returned,
                    return_condition=cond,
                    stock_bucket_restored=bucket_restored,
                    refund_amount=refund_amount,
                    reason=req_item["reason"],
                    idempotency_key=item_idem_key,
                    processed_by_user_id=actor.id,
                )
                db.session.add(sale_return)
                db.session.flush()
                created_returns.append(sale_return)

                sale_item.returned_quantity = int(sale_item.returned_quantity or 0) + qty_returned
                sale.returned_amount = (
                    Decimal(str(sale.returned_amount or 0)) + refund_amount
                ).quantize(Decimal("0.01"))

                mov = StockMovement(
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    product_id=sale_item.product_id,
                    batch_id=batch.id,
                    movement_type="SALE_RETURN",
                    bucket_affected=bucket_restored,
                    quantity_change=qty_returned,
                    balance_before=old_bal,
                    balance_after=new_bal,
                    unit_cost_snapshot=sale_item.unit_cost_price,
                    reference_type="SALE_RETURN",
                    reference_id=sale_return.id,
                    reason=f"Return on {sale.invoice_number} ({cond}): {req_item['reason']}",
                    performed_by_user_id=actor.id,
                )
                db.session.add(mov)

            db.session.flush()

            all_returned = all(
                int(si.returned_quantity or 0) >= int(si.quantity or 0)
                for si in sale.items
            )
            sale.status = "RETURNED" if all_returned else "PARTIALLY_RETURNED"

            AuditService.record(
                action="SALE_RETURN_PROCESSED",
                entity_type="SALE",
                entity_id=sale.id,
                actor=actor,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                before_state=before_state,
                after_state={
                    "status": sale.status,
                    "returned_amount": f"{sale.returned_amount:.2f}",
                    "return_ids": [r.id for r in created_returns],
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )

            db.session.commit()
            return sale, created_returns

        except DomainError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="Duplicate return submission detected.",
                code="DUPLICATE_RETURN_SUBMISSION",
            ) from exc
        except OperationalError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database lock error while processing sale return.",
                code="DATABASE_LOCK_OR_CONNECTION_ERROR",
            ) from exc
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def get_sale_detail(
        actor: User,
        shop_id: int,
        sale_id: int,
    ) -> dict[str, Any]:
        """Retrieve complete sale details, FEFO batch allocations, and return history (API-022)."""
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=True,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        sale = db.session.get(Sale, int(sale_id))
        if sale is None:
            raise ResourceNotFoundError(message=f"Sale #{sale_id} was not found.")
        if sale.shop_id != shop.id:
            raise AuthorizationError(
                message="Sale does not belong to the authorized shop.",
                code="UNAUTHORIZED_SHOP_ACCESS",
            )

        data = sale.to_dict(include_details=True)
        data["shop"] = {
            "id": shop.id,
            "name": shop.name,
            "code": shop.code,
            "address": shop.address,
            "city": shop.city,
            "pincode": shop.pincode,
            "contact_phone": shop.contact_phone,
            "contact_email": shop.contact_email,
            "organization_name": shop.organization.name if shop.organization else None,
        }
        return data

    @staticmethod
    def compute_sales_summary_from_rows(sales_rows: list[Sale]) -> dict[str, Any]:
        """Compute gross, returned, and net-of-returns/cancellations financial & unit metrics (BR-017)."""
        total_invoices_count = len(sales_rows)
        completed_sales_count = 0
        partially_returned_count = 0
        returned_count = 0
        cancelled_count = 0

        gross_sales_revenue = Decimal("0.00")
        total_returned_amount = Decimal("0.00")
        cancelled_amount = Decimal("0.00")
        net_sales_revenue = Decimal("0.00")
        gross_cogs = Decimal("0.00")
        net_cogs = Decimal("0.00")

        gross_units_sold = 0
        returned_units = 0
        cancelled_units = 0
        net_units_sold = 0

        for s in sales_rows:
            s_total = Decimal(str(s.total_amount or 0)).quantize(Decimal("0.01"))
            s_ret = Decimal(str(s.returned_amount or 0)).quantize(Decimal("0.01"))
            s_cost = Decimal(str(s.total_cost_amount or 0)).quantize(Decimal("0.01"))
            s_qty = sum(int(i.quantity or 0) for i in s.items)
            s_ret_qty = sum(int(i.returned_quantity or 0) for i in s.items)

            if s.status == "CANCELLED":
                cancelled_count += 1
                cancelled_amount += s_total
                cancelled_units += s_qty
                continue

            if s.status == "COMPLETED":
                completed_sales_count += 1
            elif s.status == "PARTIALLY_RETURNED":
                partially_returned_count += 1
            elif s.status == "RETURNED":
                returned_count += 1

            gross_sales_revenue += s_total
            total_returned_amount += s_ret
            net_sales_revenue += s.net_revenue_decimal
            gross_cogs += s_cost
            net_cogs += s.net_cogs_decimal

            gross_units_sold += s_qty
            returned_units += s_ret_qty
            net_units_sold += max(0, s_qty - s_ret_qty)

        net_gross_profit = (net_sales_revenue - net_cogs).quantize(Decimal("0.01"))

        return {
            "total_invoices_count": total_invoices_count,
            "completed_sales_count": completed_sales_count,
            "active_non_cancelled_sales_count": (
                completed_sales_count + partially_returned_count
            ),
            "partially_returned_count": partially_returned_count,
            "returned_count": returned_count,
            "cancelled_count": cancelled_count,
            "gross_sales_revenue": float(gross_sales_revenue.quantize(Decimal("0.01"))),
            "total_returned_amount": float(total_returned_amount.quantize(Decimal("0.01"))),
            "cancelled_amount": float(cancelled_amount.quantize(Decimal("0.01"))),
            "net_sales_revenue": float(net_sales_revenue.quantize(Decimal("0.01"))),
            "gross_cogs": float(gross_cogs.quantize(Decimal("0.01"))),
            "net_cogs": float(net_cogs.quantize(Decimal("0.01"))),
            "net_gross_profit": float(net_gross_profit),
            "gross_units_sold": gross_units_sold,
            "returned_units": returned_units,
            "cancelled_units": cancelled_units,
            "net_units_sold": net_units_sold,
            "quantities_sold": net_units_sold,
            "accounting_basis": (
                "Gross revenue sums total_amount of non-cancelled sales; "
                "net_sales_revenue subtracts returned_amount; "
                "net_cogs and net_units_sold exclude cancelled and returned units (BR-017)."
            ),
        }

    @staticmethod
    def list_sales(
        actor: User,
        shop_id: int,
        status: str | None = None,
        payment_mode: str | None = None,
        date_from: str | date | None = None,
        date_to: str | date | None = None,
        search_query: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """List shop sales with filters, pagination, and financial/quantity summary (API-021, FR-034, FR-035)."""
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=True,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        page, per_page = validate_pagination_params(page, per_page)

        query = db.session.query(Sale).filter(Sale.shop_id == shop.id)

        if status and status.strip():
            query = query.filter(Sale.status == status.strip().upper())

        if payment_mode and payment_mode.strip():
            query = query.filter(Sale.payment_mode == payment_mode.strip().upper())

        parsed_from = (
            date_from
            if isinstance(date_from, date)
            else _parse_date(date_from, field_name="date_from", required=False)
        )
        parsed_to = (
            date_to
            if isinstance(date_to, date)
            else _parse_date(date_to, field_name="date_to", required=False)
        )
        if parsed_from is not None and parsed_to is not None and parsed_to < parsed_from:
            raise ValidationError(
                message="date_to cannot be earlier than date_from.",
                details={"date_to": "Must be >= date_from."},
            )

        if parsed_from is not None:
            start_dt = datetime.combine(parsed_from, time.min, tzinfo=timezone.utc)
            query = query.filter(Sale.created_at >= start_dt)
        if parsed_to is not None:
            end_dt = datetime.combine(parsed_to, time.max, tzinfo=timezone.utc)
            query = query.filter(Sale.created_at <= end_dt)

        if search_query and search_query.strip():
            term = f"%{search_query.strip()}%"
            matching_sale_ids = (
                db.session.query(SaleItem.sale_id)
                .join(Product, SaleItem.product_id == Product.id)
                .filter(
                    SaleItem.shop_id == shop.id,
                    or_(
                        Product.sku.ilike(term),
                        Product.name.ilike(term),
                    ),
                )
            )
            query = query.filter(
                or_(
                    Sale.invoice_number.ilike(term),
                    Sale.customer_name.ilike(term),
                    Sale.customer_phone.ilike(term),
                    Sale.id.in_(matching_sale_ids),
                )
            )

        all_matching_sales = query.order_by(Sale.created_at.desc(), Sale.id.desc()).all()
        summary = SalesService.compute_sales_summary_from_rows(all_matching_sales)

        total_items = len(all_matching_sales)
        total_pages = max(1, (total_items + per_page - 1) // per_page)
        start_idx = (page - 1) * per_page
        page_rows = all_matching_sales[start_idx : start_idx + per_page]

        return {
            "items": [s.to_dict(include_details=True) for s in page_rows],
            "summary": summary,
            "meta": {
                "page": page,
                "per_page": per_page,
                "total": total_items,
                "total_items": total_items,
                "total_pages": total_pages,
            },
        }
