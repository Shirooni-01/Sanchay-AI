"""Transactional Inter-Store Stock Exchange & Transfer Service (Phase 6).

Enforces:
- BR-008: Dual shop-owner approval required before any stock is reserved or transferred.
- BR-009: Row-level locking (SELECT ... FOR UPDATE) and atomic reservation and receipt.
- BR-010: Full status audit trail in transfer_status_logs; reservation release on reject/cancel.
- BR-011: Preservation of original batch codes, expiry dates, and unit costs.
- BR-013: Organization and shop multi-tenant data isolation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from flask import current_app, has_app_context
from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError, OperationalError

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch, StockMovement
from app.models.organization import Organization, Shop
from app.models.transfer import TransferRequest, TransferStatusLog
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.inventory_service import InventoryService, get_business_today
from app.utils.decorators import verify_shop_access
from app.utils.errors import (
    AuthorizationError,
    BatchExpiredError,
    DatabaseConflictError,
    DomainError,
    InsufficientStockError,
    ResourceConflictError,
    ResourceNotFoundError,
    ValidationError,
)
from app.utils.validators import (
    VALID_TRANSFER_STATUSES,
    validate_pagination_params,
    validate_transfer_action_input,
    validate_transfer_create_input,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TransferService:
    """Core domain service for inter-store stock transfers and reservation lifecycle."""

    @staticmethod
    def generate_transfer_code(source_shop_id: int) -> str:
        """Generate human-readable unique transfer code formatted as TRF-YYYYMMDD-XXXX."""
        today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        prefix = f"TRF-{today_str}-"

        # Count existing transfers today
        count = (
            db.session.query(func.count(TransferRequest.id))
            .filter(TransferRequest.transfer_code.like(f"{prefix}%"))
            .scalar()
            or 0
        )
        return f"{prefix}{count + 1:04d}"

    @classmethod
    def get_eligible_recipients(
        cls,
        actor: User,
        source_shop_id: int,
        product_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """List eligible recipient shops within the same organization with stock/demand indicators.

        Excludes source_shop_id. Never exposes proprietary sales figures or profit margins.
        """
        source_shop, _ = verify_shop_access(
            user=actor,
            shop_id=source_shop_id,
            allow_inactive=False,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
        )

        peer_shops = (
            db.session.query(Shop)
            .filter(
                Shop.organization_id == source_shop.organization_id,
                Shop.id != source_shop.id,
                Shop.status == "ACTIVE",
            )
            .order_by(Shop.name.asc())
            .all()
        )

        results: list[dict[str, Any]] = []
        today = get_business_today()

        for peer in peer_shops:
            shop_info: dict[str, Any] = {
                "shop_id": peer.id,
                "name": peer.name,
                "code": peer.code,
                "city": peer.city,
                "address": peer.address,
                "contact_phone": peer.contact_phone,
                "contact_email": peer.contact_email,
                "is_eligible": True,
            }

            if product_id is not None:
                sp = (
                    db.session.query(ShopProduct)
                    .filter(
                        ShopProduct.shop_id == peer.id,
                        ShopProduct.product_id == product_id,
                    )
                    .first()
                )
                low_stock_threshold = (
                    int(sp.low_stock_threshold)
                    if sp and sp.low_stock_threshold is not None
                    else int(peer.low_stock_default_threshold or 10)
                )

                # Compute peer's sellable stock for this product
                peer_batches = (
                    db.session.query(InventoryBatch)
                    .filter(
                        InventoryBatch.shop_id == peer.id,
                        InventoryBatch.product_id == product_id,
                        InventoryBatch.status == "ACTIVE",
                    )
                    .all()
                )
                peer_sellable_qty = sum(
                    b.effective_sellable_qty_on(today) for b in peer_batches
                )

                shop_info["product_configured"] = sp is not None
                shop_info["current_stock"] = peer_sellable_qty
                shop_info["low_stock_threshold"] = low_stock_threshold
                shop_info["is_out_of_stock"] = peer_sellable_qty == 0
                shop_info["is_low_stock"] = peer_sellable_qty <= low_stock_threshold
                shop_info["needs_replenishment"] = (
                    peer_sellable_qty <= low_stock_threshold
                )
            else:
                shop_info["product_configured"] = None
                shop_info["current_stock"] = None
                shop_info["low_stock_threshold"] = int(peer.low_stock_default_threshold or 10)
                shop_info["needs_replenishment"] = False

            results.append(shop_info)

        return results

    @classmethod
    def get_open_pending_quantity(cls, source_batch_id: int) -> int:
        """Calculate total units currently committed in pending unreserved proposals for a batch."""
        res = (
            db.session.query(func.coalesce(func.sum(TransferRequest.quantity), 0))
            .filter(
                TransferRequest.source_batch_id == source_batch_id,
                TransferRequest.status.in_(
                    ["PENDING_RECIPIENT_APPROVAL", "PENDING_SOURCE_APPROVAL"]
                ),
            )
            .scalar()
        )
        return int(res or 0)

    @classmethod
    def create_transfer_proposal(
        cls,
        actor: User,
        payload: dict[str, Any],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> TransferRequest:
        """Create a new inter-store transfer proposal (API-027).

        If created by source shop owner: status is PENDING_RECIPIENT_APPROVAL.
        If created by recipient shop owner: status is PENDING_SOURCE_APPROVAL.
        """
        cleaned = validate_transfer_create_input(payload)
        source_shop_id = cleaned["source_shop_id"]
        recipient_shop_id = cleaned["recipient_shop_id"]
        product_id = cleaned["product_id"]
        source_batch_id = cleaned["source_batch_id"]
        qty = cleaned["quantity"]
        unit_price = cleaned["unit_transfer_price"]
        offer_expires_at = cleaned["offer_expires_at"]
        notes = cleaned["notes"]

        # Validate caller authorization: must be SHOP_OWNER of source or recipient, or SUPER_ADMIN
        is_admin = actor.role == "SUPER_ADMIN"
        source_shop = (
            db.session.query(Shop)
            .filter(Shop.id == source_shop_id, Shop.status == "ACTIVE")
            .first()
        )
        if not source_shop:
            raise ResourceNotFoundError(
                message="Source shop not found or inactive.",
                code="SOURCE_SHOP_NOT_FOUND",
            )
        recipient_shop = (
            db.session.query(Shop)
            .filter(Shop.id == recipient_shop_id, Shop.status == "ACTIVE")
            .first()
        )
        if not recipient_shop:
            raise ResourceNotFoundError(
                message="Recipient shop not found or inactive.",
                code="RECIPIENT_SHOP_NOT_FOUND",
            )

        if source_shop.organization_id != recipient_shop.organization_id:
            raise ValidationError(
                message="Cannot transfer stock between different organizations.",
                details={"organization": "Shops belong to different organizations."},
            )

        # Determine initiating shop and party
        is_source_owner = False
        is_recipient_owner = False
        if is_admin:
            is_source_owner = True
        else:
            try:
                _, src_assignment = verify_shop_access(
                    user=actor,
                    shop_id=source_shop_id,
                    allow_inactive=False,
                    required_shop_roles={"SHOP_OWNER"},
                )
                is_source_owner = src_assignment is not None
            except AuthorizationError:
                is_source_owner = False

            try:
                _, dst_assignment = verify_shop_access(
                    user=actor,
                    shop_id=recipient_shop_id,
                    allow_inactive=False,
                    required_shop_roles={"SHOP_OWNER"},
                )
                is_recipient_owner = dst_assignment is not None
            except AuthorizationError:
                is_recipient_owner = False

        if not is_source_owner and not is_recipient_owner:
            raise AuthorizationError(
                message="You must be an assigned SHOP_OWNER of the source or recipient shop to propose a transfer.",
                code="FORBIDDEN_TRANSFER_INITIATION",
            )

        # Validate product
        product = (
            db.session.query(Product)
            .filter(
                Product.id == product_id,
                Product.organization_id == source_shop.organization_id,
                Product.is_active.is_(True),
            )
            .first()
        )
        if not product:
            raise ResourceNotFoundError(
                message="Product not found or inactive in this organization.",
                code="PRODUCT_NOT_FOUND",
            )

        # Validate source batch
        today = get_business_today()
        batch = (
            db.session.query(InventoryBatch)
            .filter(
                InventoryBatch.id == source_batch_id,
                InventoryBatch.shop_id == source_shop.id,
                InventoryBatch.product_id == product.id,
            )
            .first()
        )
        if not batch:
            raise ResourceNotFoundError(
                message="Source inventory batch not found in source shop.",
                code="SOURCE_BATCH_NOT_FOUND",
            )

        if batch.status != "ACTIVE":
            raise ValidationError(
                message=f"Cannot transfer from batch with status '{batch.status}'.",
                details={"status": "Source batch must be ACTIVE."},
            )

        if batch.is_expired_on(today):
            raise BatchExpiredError(
                message=f"Cannot transfer expired stock (batch '{batch.batch_code}' expired on {batch.expiry_date}).",
                details={
                    "batch_id": batch.id,
                    "expiry_date": (
                        batch.expiry_date.isoformat() if batch.expiry_date else None
                    ),
                },
            )

        # Validate available stock against open pending offers
        open_pending_qty = cls.get_open_pending_quantity(batch.id)
        available_unoffered = max(0, int(batch.available_qty or 0) - open_pending_qty)
        if qty > available_unoffered:
            raise InsufficientStockError(
                message=(
                    f"Requested transfer quantity ({qty}) exceeds available unreserved stock "
                    f"in batch '{batch.batch_code}'. "
                    f"Available: {batch.available_qty}, already committed in open proposals: {open_pending_qty}."
                ),
                code="INSUFFICIENT_STOCK_FOR_PROPOSAL",
                details={
                    "requested_qty": qty,
                    "available_qty": batch.available_qty,
                    "open_pending_qty": open_pending_qty,
                    "unoffered_qty": available_unoffered,
                },
            )

        now = _utc_now()
        transfer_code = cls.generate_transfer_code(source_shop.id)

        # If source initiated:
        if is_source_owner:
            initial_status = "PENDING_RECIPIENT_APPROVAL"
            initiated_shop_id = source_shop.id
            src_approved_user = actor.id
            src_approved_at = now
            dst_approved_user = None
            dst_approved_at = None
        else:
            initial_status = "PENDING_SOURCE_APPROVAL"
            initiated_shop_id = recipient_shop.id
            src_approved_user = None
            src_approved_at = None
            dst_approved_user = actor.id
            dst_approved_at = now

        try:
            transfer = TransferRequest(
                transfer_code=transfer_code,
                organization_id=source_shop.organization_id,
                source_shop_id=source_shop.id,
                recipient_shop_id=recipient_shop.id,
                product_id=product.id,
                source_batch_id=batch.id,
                recipient_batch_id=None,
                quantity=qty,
                received_good_qty=None,
                transit_damaged_qty=0,
                unit_transfer_price=unit_price,
                estimated_distance_km=cleaned["estimated_distance_km"],
                transfer_logistics_cost=cleaned["transfer_logistics_cost"],
                batch_expiry_date_snapshot=batch.expiry_date,
                status=initial_status,
                initiated_by_shop_id=initiated_shop_id,
                initiated_by_user_id=actor.id,
                source_approved_by_user_id=src_approved_user,
                source_approved_at=src_approved_at,
                recipient_approved_by_user_id=dst_approved_user,
                recipient_approved_at=dst_approved_at,
                reserved_at=None,
                offer_expires_at=offer_expires_at,
                dispatch_notes=notes,
            )
            db.session.add(transfer)
            db.session.flush()

            status_log = TransferStatusLog(
                transfer_id=transfer.id,
                from_status=None,
                to_status=initial_status,
                actor_user_id=actor.id,
                notes=f"Transfer proposal created. Awaiting counterparty approval. {notes or ''}".strip(),
            )
            db.session.add(status_log)

            AuditService.record(
                action="TRANSFER_PROPOSED",
                entity_type="TRANSFER_REQUEST",
                entity_id=transfer.id,
                actor=actor,
                organization_id=source_shop.organization_id,
                shop_id=initiated_shop_id,
                after_state=transfer.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )

            # Phase 7: In-app notification integration (in-transaction)
            from app.services.notification_service import NotificationService
            if initial_status == "PENDING_RECIPIENT_APPROVAL":
                NotificationService.notify_shop_users(
                    shop_id=recipient_shop.id,
                    notification_type="TRANSFER_OFFER_RECEIVED",
                    title=f"New Transfer Offer: {transfer.transfer_code}",
                    message=f"Shop '{source_shop.name}' offered {transfer.quantity} units of '{product.name}'.",
                    roles={"SHOP_OWNER"},
                    related_entity_type="transfer",
                    related_entity_id=transfer.id,
                    dedup_key_prefix=f"transfer:{transfer.id}:PENDING_RECIPIENT_APPROVAL",
                    auto_commit=False,
                )
            else:
                NotificationService.notify_shop_users(
                    shop_id=source_shop.id,
                    notification_type="TRANSFER_REQUEST_RECEIVED",
                    title=f"Transfer Request: {transfer.transfer_code}",
                    message=f"Shop '{recipient_shop.name}' requested {transfer.quantity} units of '{product.name}'.",
                    roles={"SHOP_OWNER"},
                    related_entity_type="transfer",
                    related_entity_id=transfer.id,
                    dedup_key_prefix=f"transfer:{transfer.id}:PENDING_SOURCE_APPROVAL",
                    auto_commit=False,
                )

            db.session.commit()

            # Automatic sustainability credit evaluation
            try:
                from app.services.sustainability_service import SustainabilityService
                SustainabilityService.process_transfer_sustainability_event(actor=actor, transfer_id=transfer.id)
            except Exception as sust_err:
                if has_app_context():
                    current_app.logger.info(f"Sustainability notice: {sust_err}")

            return transfer

        except DomainError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Integrity error creating transfer proposal.",
                code="DATABASE_INTEGRITY_ERROR",
            ) from exc
        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def get_transfer(
        cls,
        actor: User,
        transfer_id: int,
    ) -> TransferRequest:
        """Fetch a single transfer request, enforcing tenant authorization."""
        transfer = (
            db.session.query(TransferRequest)
            .filter(TransferRequest.id == transfer_id)
            .first()
        )
        if not transfer:
            raise ResourceNotFoundError(
                message=f"Transfer request with id {transfer_id} not found.",
                code="TRANSFER_NOT_FOUND",
            )

        if actor.role != "SUPER_ADMIN":
            # Check user access to either source or recipient shop
            accessible_shop_ids = {a.shop_id for a in actor.shop_assignments if a.is_active}
            if (
                transfer.source_shop_id not in accessible_shop_ids
                and transfer.recipient_shop_id not in accessible_shop_ids
            ):
                raise AuthorizationError(
                    message="You do not have access to view this transfer.",
                    code="FORBIDDEN_TRANSFER_ACCESS",
                )

        return transfer

    @classmethod
    def list_transfers(
        cls,
        actor: User,
        shop_id: int | None = None,
        direction: str = "all",
        status: str | None = None,
        product_id: int | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[TransferRequest], int]:
        """List transfers scoped to the user's authorized shops with filtering."""
        query = db.session.query(TransferRequest)

        if actor.role == "SUPER_ADMIN":
            if shop_id is not None:
                if direction == "incoming":
                    query = query.filter(TransferRequest.recipient_shop_id == shop_id)
                elif direction == "outgoing":
                    query = query.filter(TransferRequest.source_shop_id == shop_id)
                else:
                    query = query.filter(
                        or_(
                            TransferRequest.source_shop_id == shop_id,
                            TransferRequest.recipient_shop_id == shop_id,
                        )
                    )
        else:
            accessible_shop_ids = {a.shop_id for a in actor.shop_assignments if a.is_active}
            if shop_id is not None:
                if shop_id not in accessible_shop_ids:
                    raise AuthorizationError(
                        message="You do not have access to this shop's transfers.",
                        code="FORBIDDEN_SHOP_ACCESS",
                    )
                if direction == "incoming":
                    query = query.filter(TransferRequest.recipient_shop_id == shop_id)
                elif direction == "outgoing":
                    query = query.filter(TransferRequest.source_shop_id == shop_id)
                else:
                    query = query.filter(
                        or_(
                            TransferRequest.source_shop_id == shop_id,
                            TransferRequest.recipient_shop_id == shop_id,
                        )
                    )
            else:
                # All accessible shops
                query = query.filter(
                    or_(
                        TransferRequest.source_shop_id.in_(accessible_shop_ids),
                        TransferRequest.recipient_shop_id.in_(accessible_shop_ids),
                    )
                )

        if status:
            st_clean = status.strip().upper()
            if st_clean in VALID_TRANSFER_STATUSES:
                query = query.filter(TransferRequest.status == st_clean)

        if product_id:
            query = query.filter(TransferRequest.product_id == product_id)

        total = query.count()
        transfers = (
            query.order_by(TransferRequest.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        return transfers, total

    @classmethod
    def approve_transfer(
        cls,
        actor: User,
        transfer_id: int,
        payload: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> TransferRequest:
        """Record second shop owner approval, transition to APPROVED_RESERVED, and lock stock (API-030)."""
        cleaned = validate_transfer_action_input(payload, action="approve")
        now = _utc_now()
        is_admin = actor.role == "SUPER_ADMIN"

        try:
            # Row lock transfer
            transfer = (
                db.session.query(TransferRequest)
                .filter(TransferRequest.id == transfer_id)
                .with_for_update()
                .one_or_none()
            )
            if not transfer:
                raise ResourceNotFoundError(
                    message=f"Transfer request {transfer_id} not found.",
                    code="TRANSFER_NOT_FOUND",
                )

            # Check expiration
            if transfer.offer_expires_at <= now:
                transfer.status = "EXPIRED_OFFER"
                db.session.commit()
                raise ResourceConflictError(
                    message="Cannot approve this transfer proposal because the offer has expired.",
                    code="TRANSFER_OFFER_EXPIRED",
                )

            # Authorization and state transition
            if transfer.status == "PENDING_RECIPIENT_APPROVAL":
                # Recipient must approve
                if not is_admin:
                    verify_shop_access(
                        user=actor,
                        shop_id=transfer.recipient_shop_id,
                        allow_inactive=False,
                        required_shop_roles={"SHOP_OWNER"},
                    )
                transfer.recipient_approved_by_user_id = actor.id
                transfer.recipient_approved_at = now

            elif transfer.status == "PENDING_SOURCE_APPROVAL":
                # Source must approve
                if not is_admin:
                    verify_shop_access(
                        user=actor,
                        shop_id=transfer.source_shop_id,
                        allow_inactive=False,
                        required_shop_roles={"SHOP_OWNER"},
                    )
                transfer.source_approved_by_user_id = actor.id
                transfer.source_approved_at = now

            elif transfer.status == "APPROVED_RESERVED":
                raise ResourceConflictError(
                    message="Transfer has already been dual-approved and reserved.",
                    code="TRANSFER_ALREADY_APPROVED",
                )
            else:
                raise ResourceConflictError(
                    message=f"Cannot approve transfer with current status '{transfer.status}'.",
                    code="INVALID_TRANSFER_STATUS",
                )

            # Dual approval complete! Transition to APPROVED_RESERVED and reserve stock
            old_status = transfer.status
            transfer.status = "APPROVED_RESERVED"
            transfer.reserved_at = now

            # ROW LOCK SOURCE BATCH with SELECT ... FOR UPDATE
            source_batch = (
                db.session.query(InventoryBatch)
                .filter(InventoryBatch.id == transfer.source_batch_id)
                .with_for_update()
                .one()
            )

            old_avail = int(source_batch.available_qty or 0)
            old_res = int(source_batch.reserved_qty or 0)
            qty = transfer.quantity

            if old_avail < qty:
                raise InsufficientStockError(
                    message=(
                        f"Cannot reserve transfer: available stock in batch '{source_batch.batch_code}' "
                        f"is {old_avail}, but transfer requires {qty} units."
                    ),
                    code="INSUFFICIENT_STOCK_FOR_RESERVATION",
                    details={
                        "available_qty": old_avail,
                        "required_qty": qty,
                        "batch_id": source_batch.id,
                    },
                )

            # Atomically move stock: AVAILABLE -> RESERVED
            new_avail = old_avail - qty
            new_res = old_res + qty
            source_batch.available_qty = new_avail
            source_batch.reserved_qty = new_res

            # Immutable ledger movements (BR-002, BR-007)
            # 1. AVAILABLE bucket decremented
            m_avail = StockMovement(
                organization_id=transfer.organization_id,
                shop_id=transfer.source_shop_id,
                product_id=transfer.product_id,
                batch_id=source_batch.id,
                movement_type="TRANSFER_RESERVE",
                bucket_affected="AVAILABLE",
                quantity_change=-qty,
                balance_before=old_avail,
                balance_after=new_avail,
                unit_cost_snapshot=source_batch.unit_cost,
                reference_type="TRANSFER",
                reference_id=transfer.id,
                reason=f"Transfer reservation for {transfer.transfer_code}",
                performed_by_user_id=actor.id,
            )
            # 2. RESERVED bucket incremented
            m_res = StockMovement(
                organization_id=transfer.organization_id,
                shop_id=transfer.source_shop_id,
                product_id=transfer.product_id,
                batch_id=source_batch.id,
                movement_type="TRANSFER_RESERVE",
                bucket_affected="RESERVED",
                quantity_change=qty,
                balance_before=old_res,
                balance_after=new_res,
                unit_cost_snapshot=source_batch.unit_cost,
                reference_type="TRANSFER",
                reference_id=transfer.id,
                reason=f"Transfer reservation hold for {transfer.transfer_code}",
                performed_by_user_id=actor.id,
            )
            db.session.add(m_avail)
            db.session.add(m_res)

            status_log = TransferStatusLog(
                transfer_id=transfer.id,
                from_status=old_status,
                to_status="APPROVED_RESERVED",
                actor_user_id=actor.id,
                notes=f"Dual approval complete. {qty} units reserved in source batch. {cleaned.get('notes') or ''}".strip(),
            )
            db.session.add(status_log)

            AuditService.record(
                action="TRANSFER_APPROVED_RESERVED",
                entity_type="TRANSFER_REQUEST",
                entity_id=transfer.id,
                actor=actor,
                organization_id=transfer.organization_id,
                shop_id=transfer.source_shop_id,
                after_state={
                    "transfer": transfer.to_dict(),
                    "source_batch_balance": {
                        "batch_id": source_batch.id,
                        "available_qty": source_batch.available_qty,
                        "reserved_qty": source_batch.reserved_qty,
                    },
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )

            # Phase 7: In-app notification integration (in-transaction)
            from app.services.notification_service import NotificationService
            product = db.session.get(Product, transfer.product_id)
            prod_name = product.name if product else f"Product #{transfer.product_id}"
            NotificationService.notify_shop_users(
                shop_id=transfer.source_shop_id,
                notification_type="TRANSFER_APPROVED",
                title=f"Transfer Approved: {transfer.transfer_code}",
                message=f"Transfer {transfer.transfer_code} ({transfer.quantity} units of '{prod_name}') has been approved and stock is reserved.",
                roles={"SHOP_OWNER"},
                related_entity_type="transfer",
                related_entity_id=transfer.id,
                dedup_key_prefix=f"transfer:{transfer.id}:APPROVED_RESERVED:src",
                auto_commit=False,
            )
            NotificationService.notify_shop_users(
                shop_id=transfer.recipient_shop_id,
                notification_type="TRANSFER_APPROVED",
                title=f"Transfer Approved: {transfer.transfer_code}",
                message=f"Transfer {transfer.transfer_code} ({transfer.quantity} units of '{prod_name}') has been approved and stock is reserved.",
                roles={"SHOP_OWNER"},
                related_entity_type="transfer",
                related_entity_id=transfer.id,
                dedup_key_prefix=f"transfer:{transfer.id}:APPROVED_RESERVED:rec",
                auto_commit=False,
            )

            db.session.commit()
            return transfer

        except DomainError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while approving transfer.",
                code="DATABASE_INTEGRITY_ERROR",
            ) from exc
        except OperationalError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Concurrency conflict while reserving transfer stock. Please retry.",
                code="CONCURRENCY_CONFLICT",
            ) from exc
        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def reject_transfer(
        cls,
        actor: User,
        transfer_id: int,
        payload: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> TransferRequest:
        """Reject a pending or reserved transfer proposal and release any reserved stock (API-031)."""
        cleaned = validate_transfer_action_input(payload, action="reject")
        reason = cleaned["reason"]
        is_admin = actor.role == "SUPER_ADMIN"

        try:
            transfer = (
                db.session.query(TransferRequest)
                .filter(TransferRequest.id == transfer_id)
                .with_for_update()
                .one_or_none()
            )
            if not transfer:
                raise ResourceNotFoundError(
                    message=f"Transfer request {transfer_id} not found.",
                    code="TRANSFER_NOT_FOUND",
                )

            if transfer.status in ("COMPLETED", "REJECTED", "CANCELLED", "DISPATCHED_IN_TRANSIT"):
                raise ResourceConflictError(
                    message=f"Cannot reject transfer with status '{transfer.status}'.",
                    code="INVALID_TRANSFER_STATUS",
                )

            # Authorization: must be participating shop owner or super admin
            if not is_admin:
                accessible_owner_shop_ids = {
                    a.shop_id
                    for a in actor.shop_assignments
                    if a.is_active and a.assignment_role == "SHOP_OWNER"
                }
                if (
                    transfer.source_shop_id not in accessible_owner_shop_ids
                    and transfer.recipient_shop_id not in accessible_owner_shop_ids
                ):
                    raise AuthorizationError(
                        message="Only an assigned SHOP_OWNER of a participating shop can reject this transfer.",
                        code="FORBIDDEN_TRANSFER_REJECTION",
                    )

            old_status = transfer.status
            transfer.status = "REJECTED"
            transfer.rejection_or_cancel_reason = reason

            # If stock was reserved in APPROVED_RESERVED, release it!
            if old_status == "APPROVED_RESERVED":
                source_batch = (
                    db.session.query(InventoryBatch)
                    .filter(InventoryBatch.id == transfer.source_batch_id)
                    .with_for_update()
                    .one()
                )
                qty = transfer.quantity
                old_res = int(source_batch.reserved_qty or 0)
                old_avail = int(source_batch.available_qty or 0)

                new_res = max(0, old_res - qty)
                new_avail = old_avail + qty
                source_batch.reserved_qty = new_res
                source_batch.available_qty = new_avail

                m_res = StockMovement(
                    organization_id=transfer.organization_id,
                    shop_id=transfer.source_shop_id,
                    product_id=transfer.product_id,
                    batch_id=source_batch.id,
                    movement_type="TRANSFER_RELEASE",
                    bucket_affected="RESERVED",
                    quantity_change=-qty,
                    balance_before=old_res,
                    balance_after=new_res,
                    unit_cost_snapshot=source_batch.unit_cost,
                    reference_type="TRANSFER",
                    reference_id=transfer.id,
                    reason=f"Reservation release upon rejection of {transfer.transfer_code}: {reason}",
                    performed_by_user_id=actor.id,
                )
                m_avail = StockMovement(
                    organization_id=transfer.organization_id,
                    shop_id=transfer.source_shop_id,
                    product_id=transfer.product_id,
                    batch_id=source_batch.id,
                    movement_type="TRANSFER_RELEASE",
                    bucket_affected="AVAILABLE",
                    quantity_change=qty,
                    balance_before=old_avail,
                    balance_after=new_avail,
                    unit_cost_snapshot=source_batch.unit_cost,
                    reference_type="TRANSFER",
                    reference_id=transfer.id,
                    reason=f"Stock restored to sellable upon rejection of {transfer.transfer_code}",
                    performed_by_user_id=actor.id,
                )
                db.session.add(m_res)
                db.session.add(m_avail)

            status_log = TransferStatusLog(
                transfer_id=transfer.id,
                from_status=old_status,
                to_status="REJECTED",
                actor_user_id=actor.id,
                notes=f"Transfer rejected: {reason}",
            )
            db.session.add(status_log)

            AuditService.record(
                action="TRANSFER_REJECTED",
                entity_type="TRANSFER_REQUEST",
                entity_id=transfer.id,
                actor=actor,
                organization_id=transfer.organization_id,
                shop_id=transfer.source_shop_id,
                after_state=transfer.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )

            # Phase 7: In-app notification integration (in-transaction)
            from app.services.notification_service import NotificationService
            other_shop_id = (
                transfer.source_shop_id
                if any(a.shop_id == transfer.recipient_shop_id for a in actor.shop_assignments)
                else transfer.recipient_shop_id
            )
            NotificationService.notify_shop_users(
                shop_id=other_shop_id,
                notification_type="TRANSFER_REJECTED",
                title=f"Transfer Rejected: {transfer.transfer_code}",
                message=f"Transfer {transfer.transfer_code} was rejected. Reason: {reason}",
                roles={"SHOP_OWNER"},
                related_entity_type="transfer",
                related_entity_id=transfer.id,
                dedup_key_prefix=f"transfer:{transfer.id}:REJECTED",
                auto_commit=False,
            )

            db.session.commit()
            return transfer

        except DomainError:
            db.session.rollback()
            raise
        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def cancel_transfer(
        cls,
        actor: User,
        transfer_id: int,
        payload: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> TransferRequest:
        """Cancel a transfer prior to physical dispatch and release any stock reservation (API-032)."""
        cleaned = validate_transfer_action_input(payload, action="cancel")
        reason = cleaned["reason"]
        is_admin = actor.role == "SUPER_ADMIN"

        try:
            transfer = (
                db.session.query(TransferRequest)
                .filter(TransferRequest.id == transfer_id)
                .with_for_update()
                .one_or_none()
            )
            if not transfer:
                raise ResourceNotFoundError(
                    message=f"Transfer request {transfer_id} not found.",
                    code="TRANSFER_NOT_FOUND",
                )

            if transfer.status in ("COMPLETED", "REJECTED", "CANCELLED", "DISPATCHED_IN_TRANSIT"):
                raise ResourceConflictError(
                    message=f"Cannot cancel transfer with status '{transfer.status}'. Transfers in transit or completed cannot be cancelled.",
                    code="INVALID_TRANSFER_STATUS",
                )

            # Authorization: Initiator, or participating Shop Owner, or Super Admin
            if not is_admin and transfer.initiated_by_user_id != actor.id:
                accessible_owner_shop_ids = {
                    a.shop_id
                    for a in actor.shop_assignments
                    if a.is_active and a.assignment_role == "SHOP_OWNER"
                }
                if (
                    transfer.source_shop_id not in accessible_owner_shop_ids
                    and transfer.recipient_shop_id not in accessible_owner_shop_ids
                ):
                    raise AuthorizationError(
                        message="Only the proposal initiator or a participating SHOP_OWNER can cancel this transfer.",
                        code="FORBIDDEN_TRANSFER_CANCELLATION",
                    )

            old_status = transfer.status
            transfer.status = "CANCELLED"
            transfer.rejection_or_cancel_reason = reason

            # If stock was reserved in APPROVED_RESERVED, release it!
            if old_status == "APPROVED_RESERVED":
                source_batch = (
                    db.session.query(InventoryBatch)
                    .filter(InventoryBatch.id == transfer.source_batch_id)
                    .with_for_update()
                    .one()
                )
                qty = transfer.quantity
                old_res = int(source_batch.reserved_qty or 0)
                old_avail = int(source_batch.available_qty or 0)

                new_res = max(0, old_res - qty)
                new_avail = old_avail + qty
                source_batch.reserved_qty = new_res
                source_batch.available_qty = new_avail

                m_res = StockMovement(
                    organization_id=transfer.organization_id,
                    shop_id=transfer.source_shop_id,
                    product_id=transfer.product_id,
                    batch_id=source_batch.id,
                    movement_type="TRANSFER_RELEASE",
                    bucket_affected="RESERVED",
                    quantity_change=-qty,
                    balance_before=old_res,
                    balance_after=new_res,
                    unit_cost_snapshot=source_batch.unit_cost,
                    reference_type="TRANSFER",
                    reference_id=transfer.id,
                    reason=f"Reservation release upon cancellation of {transfer.transfer_code}: {reason}",
                    performed_by_user_id=actor.id,
                )
                m_avail = StockMovement(
                    organization_id=transfer.organization_id,
                    shop_id=transfer.source_shop_id,
                    product_id=transfer.product_id,
                    batch_id=source_batch.id,
                    movement_type="TRANSFER_RELEASE",
                    bucket_affected="AVAILABLE",
                    quantity_change=qty,
                    balance_before=old_avail,
                    balance_after=new_avail,
                    unit_cost_snapshot=source_batch.unit_cost,
                    reference_type="TRANSFER",
                    reference_id=transfer.id,
                    reason=f"Stock restored to sellable upon cancellation of {transfer.transfer_code}",
                    performed_by_user_id=actor.id,
                )
                db.session.add(m_res)
                db.session.add(m_avail)

            status_log = TransferStatusLog(
                transfer_id=transfer.id,
                from_status=old_status,
                to_status="CANCELLED",
                actor_user_id=actor.id,
                notes=f"Transfer cancelled: {reason}",
            )
            db.session.add(status_log)

            AuditService.record(
                action="TRANSFER_CANCELLED",
                entity_type="TRANSFER_REQUEST",
                entity_id=transfer.id,
                actor=actor,
                organization_id=transfer.organization_id,
                after_state=transfer.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )

            # Phase 7: In-app notification integration (in-transaction)
            from app.services.notification_service import NotificationService
            other_shop_id = (
                transfer.source_shop_id
                if any(a.shop_id == transfer.recipient_shop_id for a in actor.shop_assignments)
                else transfer.recipient_shop_id
            )
            NotificationService.notify_shop_users(
                shop_id=other_shop_id,
                notification_type="TRANSFER_CANCELLED",
                title=f"Transfer Cancelled: {transfer.transfer_code}",
                message=f"Transfer {transfer.transfer_code} was cancelled. Reason: {reason}",
                roles={"SHOP_OWNER"},
                related_entity_type="transfer",
                related_entity_id=transfer.id,
                dedup_key_prefix=f"transfer:{transfer.id}:CANCELLED",
                auto_commit=False,
            )

            db.session.commit()
            return transfer

        except DomainError:
            db.session.rollback()
            raise
        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def dispatch_transfer(
        cls,
        actor: User,
        transfer_id: int,
        payload: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> TransferRequest:
        """Confirm physical dispatch of reserved stock (APPROVED_RESERVED -> DISPATCHED_IN_TRANSIT) (API-033)."""
        cleaned = validate_transfer_action_input(payload, action="dispatch")
        dispatch_notes = cleaned.get("dispatch_notes")
        carrier_ref = cleaned.get("carrier_reference")
        combined_notes = (
            f"{dispatch_notes or ''} [Carrier: {carrier_ref}]".strip()
            if carrier_ref
            else dispatch_notes
        )
        is_admin = actor.role == "SUPER_ADMIN"

        try:
            transfer = (
                db.session.query(TransferRequest)
                .filter(TransferRequest.id == transfer_id)
                .with_for_update()
                .one_or_none()
            )
            if not transfer:
                raise ResourceNotFoundError(
                    message=f"Transfer request {transfer_id} not found.",
                    code="TRANSFER_NOT_FOUND",
                )

            if transfer.status != "APPROVED_RESERVED":
                raise ResourceConflictError(
                    message=f"Cannot dispatch transfer with status '{transfer.status}'. Only APPROVED_RESERVED transfers can be dispatched.",
                    code="TRANSFER_NOT_APPROVED_RESERVED",
                )

            # Authorization: Source SHOP_OWNER or SUPER_ADMIN
            if not is_admin:
                verify_shop_access(
                    user=actor,
                    shop_id=transfer.source_shop_id,
                    allow_inactive=False,
                    required_shop_roles={"SHOP_OWNER"},
                )

            now = _utc_now()
            transfer.status = "DISPATCHED_IN_TRANSIT"
            transfer.dispatched_by_user_id = actor.id
            transfer.dispatched_at = now
            if combined_notes:
                transfer.dispatch_notes = (
                    f"{transfer.dispatch_notes or ''}\nDispatch: {combined_notes}".strip()
                )

            status_log = TransferStatusLog(
                transfer_id=transfer.id,
                from_status="APPROVED_RESERVED",
                to_status="DISPATCHED_IN_TRANSIT",
                actor_user_id=actor.id,
                notes=f"Stock handed over / dispatched in transit. {combined_notes or ''}".strip(),
            )
            db.session.add(status_log)

            AuditService.record(
                action="TRANSFER_DISPATCHED",
                entity_type="TRANSFER_REQUEST",
                entity_id=transfer.id,
                actor=actor,
                organization_id=transfer.organization_id,
                shop_id=transfer.source_shop_id,
                after_state=transfer.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )

            # Phase 7: In-app notification integration (in-transaction)
            from app.services.notification_service import NotificationService
            product = db.session.get(Product, transfer.product_id)
            source_shop = db.session.get(Shop, transfer.source_shop_id)
            prod_name = product.name if product else f"Product #{transfer.product_id}"
            src_name = source_shop.name if source_shop else f"Shop #{transfer.source_shop_id}"
            NotificationService.notify_shop_users(
                shop_id=transfer.recipient_shop_id,
                notification_type="TRANSFER_DISPATCHED",
                title=f"Transfer Dispatched: {transfer.transfer_code}",
                message=f"Shop '{src_name}' has dispatched {transfer.quantity} units of '{prod_name}'. Notes: {combined_notes or 'In transit'}",
                roles={"SHOP_OWNER"},
                related_entity_type="transfer",
                related_entity_id=transfer.id,
                dedup_key_prefix=f"transfer:{transfer.id}:DISPATCHED_IN_TRANSIT",
                auto_commit=False,
            )

            db.session.commit()
            return transfer

        except DomainError:
            db.session.rollback()
            raise
        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def confirm_receipt(
        cls,
        actor: User,
        transfer_id: int,
        payload: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> TransferRequest:
        """Confirm physical receipt, atomically moving stock from source reservation to recipient batch (API-034).

        Executes:
        - Source batch: RESERVED bucket decremented by quantity (TRANSFER_OUT).
        - Recipient shop: Finds or creates matching batch preserving original expiry date,
          and increments AVAILABLE bucket (TRANSFER_IN).
        - Single ACID transaction with row-level locks.
        """
        cleaned = validate_transfer_action_input(payload, action="receive")
        receipt_notes = cleaned.get("receipt_notes")
        is_admin = actor.role == "SUPER_ADMIN"
        now = _utc_now()

        try:
            transfer = (
                db.session.query(TransferRequest)
                .filter(TransferRequest.id == transfer_id)
                .with_for_update()
                .one_or_none()
            )
            if not transfer:
                raise ResourceNotFoundError(
                    message=f"Transfer request {transfer_id} not found.",
                    code="TRANSFER_NOT_FOUND",
                )

            if transfer.status != "DISPATCHED_IN_TRANSIT":
                raise ResourceConflictError(
                    message=f"Cannot confirm receipt of transfer with status '{transfer.status}'. Only DISPATCHED_IN_TRANSIT transfers can be received.",
                    code="TRANSFER_NOT_IN_TRANSIT",
                )

            # Authorization: Recipient SHOP_OWNER or SUPER_ADMIN
            if not is_admin:
                verify_shop_access(
                    user=actor,
                    shop_id=transfer.recipient_shop_id,
                    allow_inactive=False,
                    required_shop_roles={"SHOP_OWNER"},
                )

            total_qty = transfer.quantity
            rec_good = cleaned.get("received_good_qty")
            rec_damaged = cleaned.get("transit_damaged_qty", 0)

            if rec_good is None:
                # Default to full quantity received in good condition
                rec_good = max(0, total_qty - rec_damaged)
            else:
                if rec_good + rec_damaged != total_qty:
                    raise ValidationError(
                        message=f"Total units (received good: {rec_good} + damaged: {rec_damaged}) must equal transfer quantity ({total_qty}).",
                        details={
                            "quantity": f"Sum must be {total_qty}.",
                            "received_good_qty": str(rec_good),
                            "transit_damaged_qty": str(rec_damaged),
                        },
                    )

            # ROW LOCK SOURCE BATCH
            source_batch = (
                db.session.query(InventoryBatch)
                .filter(InventoryBatch.id == transfer.source_batch_id)
                .with_for_update()
                .one()
            )

            old_src_res = int(source_batch.reserved_qty or 0)
            if old_src_res < total_qty:
                raise InsufficientStockError(
                    message=(
                        f"Database integrity anomaly: Source batch '{source_batch.batch_code}' has "
                        f"{old_src_res} reserved units, but transfer requires {total_qty}."
                    ),
                    code="SOURCE_RESERVATION_CORRUPTED",
                )

            # 1. Deduct source reservation (TRANSFER_OUT)
            new_src_res = old_src_res - total_qty
            source_batch.reserved_qty = new_src_res
            if source_batch.on_hand_qty == 0:
                source_batch.status = "DEPLETED"

            m_out = StockMovement(
                organization_id=transfer.organization_id,
                shop_id=transfer.source_shop_id,
                product_id=transfer.product_id,
                batch_id=source_batch.id,
                movement_type="TRANSFER_OUT",
                bucket_affected="RESERVED",
                quantity_change=-total_qty,
                balance_before=old_src_res,
                balance_after=new_src_res,
                unit_cost_snapshot=source_batch.unit_cost,
                reference_type="TRANSFER",
                reference_id=transfer.id,
                reason=f"Inter-store transfer out to shop {transfer.recipient_shop_id} ({transfer.transfer_code})",
                performed_by_user_id=actor.id,
            )
            db.session.add(m_out)

            # 2. Ensure ShopProduct exists in recipient shop
            recipient_sp = (
                db.session.query(ShopProduct)
                .filter(
                    ShopProduct.shop_id == transfer.recipient_shop_id,
                    ShopProduct.product_id == transfer.product_id,
                )
                .first()
            )
            if not recipient_sp:
                # Auto-link catalog product to recipient shop
                prod = transfer.product
                recipient_sp = ShopProduct(
                    shop_id=transfer.recipient_shop_id,
                    product_id=transfer.product_id,
                    selling_price=prod.default_selling_price,
                    purchase_price=transfer.unit_transfer_price,
                    low_stock_threshold=10,
                    near_expiry_days=15,
                    surplus_threshold=40,
                    is_active=True,
                )
                db.session.add(recipient_sp)
                db.session.flush()

            # 3. Find or create matching batch in recipient shop (preserving expiry date & batch details)
            # Check if matching batch exists with same batch_code and expiry_date
            matching_batch = (
                db.session.query(InventoryBatch)
                .filter(
                    InventoryBatch.shop_id == transfer.recipient_shop_id,
                    InventoryBatch.product_id == transfer.product_id,
                    InventoryBatch.batch_code == source_batch.batch_code,
                )
                .with_for_update()
                .first()
            )

            # If matching batch exists with the same expiry date, merge into it
            if matching_batch and matching_batch.expiry_date == source_batch.expiry_date:
                recipient_batch = matching_batch
                old_dst_avail = int(recipient_batch.available_qty or 0)
                old_dst_dmg = int(recipient_batch.damaged_qty or 0)
                recipient_batch.available_qty = old_dst_avail + rec_good
                recipient_batch.damaged_qty = old_dst_dmg + rec_damaged
                if recipient_batch.status in ("DEPLETED", "ARCHIVED"):
                    recipient_batch.status = "ACTIVE"
            else:
                # Create a new batch in recipient shop
                target_code = source_batch.batch_code
                if matching_batch is not None:
                    # Same code already used with different expiry; disambiguate
                    target_code = f"{source_batch.batch_code}-T{transfer.id}"

                recipient_batch = InventoryBatch(
                    shop_id=transfer.recipient_shop_id,
                    product_id=transfer.product_id,
                    shop_product_id=recipient_sp.id,
                    batch_code=target_code,
                    manufactured_date=source_batch.manufactured_date,
                    expiry_date=source_batch.expiry_date,  # EXACT EXPIRY PRESERVED
                    unit_cost=transfer.unit_transfer_price,
                    initial_qty=total_qty,
                    available_qty=rec_good,
                    reserved_qty=0,
                    damaged_qty=rec_damaged,
                    expired_qty=0,
                    source_transfer_id=transfer.id,
                    status="ACTIVE",
                    received_at=now,
                )
                db.session.add(recipient_batch)
                db.session.flush()
                old_dst_avail = 0
                old_dst_dmg = 0

            # 4. Ledger movements on recipient side (TRANSFER_IN)
            if rec_good > 0:
                m_in_avail = StockMovement(
                    organization_id=transfer.organization_id,
                    shop_id=transfer.recipient_shop_id,
                    product_id=transfer.product_id,
                    batch_id=recipient_batch.id,
                    movement_type="TRANSFER_IN",
                    bucket_affected="AVAILABLE",
                    quantity_change=rec_good,
                    balance_before=old_dst_avail,
                    balance_after=old_dst_avail + rec_good,
                    unit_cost_snapshot=transfer.unit_transfer_price,
                    reference_type="TRANSFER",
                    reference_id=transfer.id,
                    reason=f"Inter-store transfer received from shop {transfer.source_shop_id} ({transfer.transfer_code})",
                    performed_by_user_id=actor.id,
                )
                db.session.add(m_in_avail)

            if rec_damaged > 0:
                m_in_dmg = StockMovement(
                    organization_id=transfer.organization_id,
                    shop_id=transfer.recipient_shop_id,
                    product_id=transfer.product_id,
                    batch_id=recipient_batch.id,
                    movement_type="TRANSFER_IN",
                    bucket_affected="DAMAGED",
                    quantity_change=rec_damaged,
                    balance_before=old_dst_dmg,
                    balance_after=old_dst_dmg + rec_damaged,
                    unit_cost_snapshot=transfer.unit_transfer_price,
                    reference_type="TRANSFER",
                    reference_id=transfer.id,
                    reason=f"Inter-store transfer transit damage ({transfer.transfer_code})",
                    performed_by_user_id=actor.id,
                )
                db.session.add(m_in_dmg)

            # 5. Complete transfer
            transfer.recipient_batch_id = recipient_batch.id
            transfer.received_good_qty = rec_good
            transfer.transit_damaged_qty = rec_damaged
            transfer.received_by_user_id = actor.id
            transfer.received_at = now
            transfer.receipt_notes = receipt_notes
            transfer.status = "COMPLETED"

            status_log = TransferStatusLog(
                transfer_id=transfer.id,
                from_status="DISPATCHED_IN_TRANSIT",
                to_status="COMPLETED",
                actor_user_id=actor.id,
                notes=f"Physical receipt confirmed. Good units: {rec_good}, Damaged units: {rec_damaged}. {receipt_notes or ''}".strip(),
            )
            db.session.add(status_log)

            AuditService.record(
                action="TRANSFER_COMPLETED",
                entity_type="TRANSFER_REQUEST",
                entity_id=transfer.id,
                actor=actor,
                organization_id=transfer.organization_id,
                shop_id=transfer.recipient_shop_id,
                after_state={
                    "transfer": transfer.to_dict(),
                    "recipient_batch_id": recipient_batch.id,
                    "recipient_batch_code": recipient_batch.batch_code,
                    "source_batch_id": source_batch.id,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )

            # Phase 7: In-app notification integration (in-transaction)
            from app.services.notification_service import NotificationService
            product = db.session.get(Product, transfer.product_id)
            rec_shop = db.session.get(Shop, transfer.recipient_shop_id)
            prod_name = product.name if product else f"Product #{transfer.product_id}"
            rec_name = rec_shop.name if rec_shop else f"Shop #{transfer.recipient_shop_id}"
            NotificationService.notify_shop_users(
                shop_id=transfer.source_shop_id,
                notification_type="TRANSFER_RECEIVED",
                title=f"Transfer Completed: {transfer.transfer_code}",
                message=f"Shop '{rec_name}' confirmed receipt of {rec_good} units of '{prod_name}'.",
                roles={"SHOP_OWNER"},
                related_entity_type="transfer",
                related_entity_id=transfer.id,
                dedup_key_prefix=f"transfer:{transfer.id}:COMPLETED",
                auto_commit=False,
            )

            db.session.commit()
            return transfer

        except DomainError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Integrity conflict while confirming transfer receipt.",
                code="DATABASE_INTEGRITY_ERROR",
            ) from exc
        except OperationalError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Concurrency conflict during transfer receipt. Please retry.",
                code="CONCURRENCY_CONFLICT",
            ) from exc
        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def get_redistribution_recommendations(
        cls,
        actor: User,
        shop_id: int | None = None,
        max_distance_km: float = 100.0,
        min_net_salvage: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Multi-shop automated stock redistribution recommendation engine (PRD Module F).

        Pairs surplus batches with deficit or high-velocity sister shops within the organization.
        Balances Haversine transit costs against gross profit to maximize net salvage.
        Zero autonomous mutations: returns recommendations for human Shop Owner review and proposal.
        """
        from app.utils.geo import haversine_distance_km

        today = get_business_today()
        recommendations: list[dict[str, Any]] = []

        # Determine target shops based on actor role and shop_id filter
        if shop_id is not None:
            scoped_shop, _ = verify_shop_access(
                user=actor,
                shop_id=shop_id,
                allow_inactive=False,
                required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
            )
            org_id = scoped_shop.organization_id
            source_shops = [scoped_shop]
        else:
            if actor.role != "SUPER_ADMIN":
                accessible = actor.get_accessible_shops(include_inactive=False)
                if not accessible:
                    return []
                org_id = accessible[0].organization_id
                source_shops = accessible
            else:
                org_id = actor.organization_id
                source_shops = Shop.query.filter_by(organization_id=org_id, status="ACTIVE").all()

        if not org_id or not source_shops:
            return []

        # All active shops in the organization
        all_org_shops = {
            s.id: s
            for s in Shop.query.filter_by(organization_id=org_id, status="ACTIVE").all()
        }

        for src_shop in source_shops:
            # Query active batches in source shop with available stock
            surplus_batches = (
                InventoryBatch.query.filter_by(shop_id=src_shop.id, status="ACTIVE")
                .filter(InventoryBatch.available_qty > 0)
                .order_by(InventoryBatch.expiry_date.asc())
                .all()
            )

            for batch in surplus_batches:
                if batch.is_expired_on(today):
                    continue
                days_left = batch.days_to_expiry_on(today)
                product = db.session.get(Product, batch.product_id)
                if not product or not product.is_active:
                    continue

                src_sp = ShopProduct.query.filter_by(shop_id=src_shop.id, product_id=product.id).first()
                selling_price = float(
                    src_sp.selling_price
                    if (src_sp and src_sp.selling_price)
                    else (product.default_selling_price or 0.0)
                )
                unit_cost = float(batch.unit_cost or product.default_purchase_price or 0.0)
                unit_margin = max(0.0, selling_price - unit_cost)

                # Find candidate recipient sister shops in the same organization
                for dest_id, dest_shop in all_org_shops.items():
                    if dest_id == src_shop.id:
                        continue

                    dest_sp = ShopProduct.query.filter_by(shop_id=dest_id, product_id=product.id).first()
                    if not dest_sp or not dest_sp.is_active:
                        continue

                    # Compute recipient current sellable stock and low stock threshold
                    dest_batches = (
                        InventoryBatch.query.filter_by(
                            shop_id=dest_id, product_id=product.id, status="ACTIVE"
                        ).all()
                    )
                    dest_sellable_qty = sum(
                        b.effective_sellable_qty_on(today) for b in dest_batches
                    )
                    dest_threshold = int(
                        dest_sp.low_stock_threshold
                        or dest_shop.low_stock_default_threshold
                        or 10
                    )

                    # Destination is candidate if out of stock, low stock, or can absorb stock
                    is_dest_deficit = dest_sellable_qty <= dest_threshold
                    deficit_target = (
                        max(1, (dest_threshold * 2) - dest_sellable_qty)
                        if is_dest_deficit
                        else dest_threshold
                    )
                    recommended_qty = min(int(batch.available_qty), int(deficit_target))
                    if recommended_qty <= 0:
                        continue

                    # Geographic distance and transit cost
                    dist_km = 5.0
                    if (
                        src_shop.latitude
                        and src_shop.longitude
                        and dest_shop.latitude
                        and dest_shop.longitude
                    ):
                        calculated_dist = haversine_distance_km(
                            float(src_shop.latitude),
                            float(src_shop.longitude),
                            float(dest_shop.latitude),
                            float(dest_shop.longitude),
                        )
                        if calculated_dist is not None:
                            dist_km = calculated_dist

                    if dist_km > max_distance_km:
                        continue

                    # Logistics cost: Rs 15 per km, minimum Rs 50
                    est_transport_cost = round(max(50.0, dist_km * 15.0), 2)
                    gross_margin_salvaged = round(recommended_qty * unit_margin, 2)
                    net_salvage_profit = round(gross_margin_salvaged - est_transport_cost, 2)

                    if net_salvage_profit < min_net_salvage:
                        continue

                    # Priority classification
                    if dest_sellable_qty == 0:
                        priority = "CRITICAL"
                        rationale = (
                            f"Recipient '{dest_shop.name}' is completely OUT OF STOCK. "
                            f"Transferring {recommended_qty} units prevents stockouts and yields ₹{net_salvage_profit:,.2f} net profit."
                        )
                    elif days_left is not None and days_left <= 7:
                        priority = "HIGH"
                        rationale = (
                            f"Source batch '{batch.batch_code}' expires in {days_left} days. "
                            f"Urgent inter-store transfer salvages ₹{net_salvage_profit:,.2f} before spoilage."
                        )
                    elif is_dest_deficit:
                        priority = "MEDIUM"
                        rationale = (
                            f"Recipient stock ({dest_sellable_qty} units) is below threshold ({dest_threshold}). "
                            f"Rebalancing {recommended_qty} units optimizes network inventory."
                        )
                    else:
                        priority = "LOW"
                        rationale = (
                            f"Redistributes {recommended_qty} units from surplus to active sister shop "
                            f"at ₹{net_salvage_profit:,.2f} net salvage."
                        )

                    rec_item = {
                        "source_shop_id": src_shop.id,
                        "source_shop_name": src_shop.name,
                        "source_shop_code": src_shop.code,
                        "recipient_shop_id": dest_shop.id,
                        "recipient_shop_name": dest_shop.name,
                        "recipient_shop_code": dest_shop.code,
                        "product_id": product.id,
                        "product_name": product.name,
                        "sku": product.sku,
                        "category": product.category,
                        "unit": product.unit,
                        "source_batch_id": batch.id,
                        "batch_code": batch.batch_code,
                        "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else "N/A",
                        "days_to_expiry": days_left,
                        "source_available_qty": batch.available_qty,
                        "recipient_current_stock": dest_sellable_qty,
                        "recipient_low_stock_threshold": dest_threshold,
                        "recommended_transfer_qty": recommended_qty,
                        "distance_km": dist_km,
                        "estimated_transport_cost": est_transport_cost,
                        "gross_margin_salvaged": gross_margin_salvaged,
                        "net_salvage_profit": net_salvage_profit,
                        "priority": priority,
                        "rationale": rationale,
                    }
                    recommendations.append(rec_item)

        # Priority order ranking: CRITICAL -> HIGH -> MEDIUM -> LOW, then net_salvage_profit descending
        priority_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        recommendations.sort(
            key=lambda r: (priority_rank.get(r["priority"], 4), -r["net_salvage_profit"])
        )

        return recommendations

