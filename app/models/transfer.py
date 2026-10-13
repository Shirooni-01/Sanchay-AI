"""SQLAlchemy models for Inter-Store Stock Exchange / Transfers (Phase 6)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.extensions import db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TransferRequest(db.Model):
    """Inter-store stock transfer request and lifecycle tracking (ADR-004, BR-008, BR-009, BR-010, BR-011)."""

    __tablename__ = "transfer_requests"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    transfer_code = db.Column(db.String(48), nullable=False, unique=True)
    organization_id = db.Column(
        db.BigInteger,
        db.ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_shop_id = db.Column(
        db.BigInteger,
        db.ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    recipient_shop_id = db.Column(
        db.BigInteger,
        db.ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    product_id = db.Column(
        db.BigInteger,
        db.ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_batch_id = db.Column(
        db.BigInteger,
        db.ForeignKey("inventory_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    recipient_batch_id = db.Column(
        db.BigInteger,
        db.ForeignKey("inventory_batches.id", ondelete="RESTRICT"),
        nullable=True,
    )
    quantity = db.Column(db.Integer, nullable=False)
    received_good_qty = db.Column(db.Integer, nullable=True)
    transit_damaged_qty = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default=db.text("0"),
    )
    unit_transfer_price = db.Column(db.Numeric(12, 2), nullable=False)
    estimated_distance_km = db.Column(db.Numeric(8, 2), nullable=True)
    transfer_logistics_cost = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=db.text("0.00"),
    )
    batch_expiry_date_snapshot = db.Column(db.Date, nullable=True)
    status = db.Column(
        db.String(32),
        nullable=False,
        default="PENDING_RECIPIENT_APPROVAL",
    )
    initiated_by_shop_id = db.Column(
        db.BigInteger,
        db.ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    initiated_by_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_approved_by_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_approved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    recipient_approved_by_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    recipient_approved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    reserved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    offer_expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    dispatched_by_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    dispatched_at = db.Column(db.DateTime(timezone=True), nullable=True)
    dispatch_notes = db.Column(db.Text, nullable=True)
    received_by_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    received_at = db.Column(db.DateTime(timezone=True), nullable=True)
    receipt_notes = db.Column(db.Text, nullable=True)
    rejection_or_cancel_reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=db.func.now(),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        server_default=db.func.now(),
    )

    organization = db.relationship("Organization")
    source_shop = db.relationship("Shop", foreign_keys=[source_shop_id])
    recipient_shop = db.relationship("Shop", foreign_keys=[recipient_shop_id])
    product = db.relationship("Product", foreign_keys=[product_id])
    source_batch = db.relationship("InventoryBatch", foreign_keys=[source_batch_id])
    recipient_batch = db.relationship("InventoryBatch", foreign_keys=[recipient_batch_id])
    initiated_by_shop = db.relationship("Shop", foreign_keys=[initiated_by_shop_id])
    initiated_by_user = db.relationship("User", foreign_keys=[initiated_by_user_id])
    source_approved_by = db.relationship("User", foreign_keys=[source_approved_by_user_id])
    recipient_approved_by = db.relationship("User", foreign_keys=[recipient_approved_by_user_id])
    dispatched_by = db.relationship("User", foreign_keys=[dispatched_by_user_id])
    received_by = db.relationship("User", foreign_keys=[received_by_user_id])

    status_logs = db.relationship(
        "TransferStatusLog",
        back_populates="transfer",
        cascade="all, delete-orphan",
        order_by="TransferStatusLog.created_at.asc()",
    )

    __table_args__ = (
        db.UniqueConstraint("transfer_code", name="uq_transfers_transfer_code"),
        db.CheckConstraint("quantity > 0", name="ck_transfers_quantity_positive"),
        db.CheckConstraint(
            "received_good_qty IS NULL OR received_good_qty >= 0",
            name="ck_transfers_received_good_nonnegative",
        ),
        db.CheckConstraint(
            "transit_damaged_qty >= 0",
            name="ck_transfers_transit_damaged_nonnegative",
        ),
        db.CheckConstraint(
            "unit_transfer_price >= 0",
            name="ck_transfers_unit_price_nonnegative",
        ),
        db.CheckConstraint(
            "estimated_distance_km IS NULL OR estimated_distance_km >= 0",
            name="ck_transfers_distance_nonnegative",
        ),
        db.CheckConstraint(
            "transfer_logistics_cost >= 0",
            name="ck_transfers_logistics_cost_nonnegative",
        ),
        db.CheckConstraint(
            "source_shop_id != recipient_shop_id",
            name="chk_transfer_distinct_shops",
        ),
        db.CheckConstraint(
            "status IN ("
            "'PENDING_SOURCE_APPROVAL', 'PENDING_RECIPIENT_APPROVAL', 'APPROVED_RESERVED', "
            "'DISPATCHED_IN_TRANSIT', 'COMPLETED', 'REJECTED', 'CANCELLED', 'EXPIRED_OFFER', "
            "'DISPUTED', 'RETURNED_TO_SOURCE'"
            ")",
            name="ck_transfers_status_valid",
        ),
        db.Index("idx_transfers_source_status", "source_shop_id", "status"),
        db.Index("idx_transfers_recipient_status", "recipient_shop_id", "status"),
        db.Index("idx_transfers_source_batch_active", "source_batch_id", "status"),
    )

    @property
    def total_transfer_value(self) -> Decimal:
        """Total inventory value of the transfer offer."""
        return Decimal(str(self.unit_transfer_price or 0)) * Decimal(self.quantity or 0)

    def is_expired(self, ref_time: datetime | None = None) -> bool:
        """Check if pending offer has passed its offer_expires_at."""
        now = ref_time or _utc_now()
        if self.status in ("PENDING_SOURCE_APPROVAL", "PENDING_RECIPIENT_APPROVAL"):
            return self.offer_expires_at <= now
        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialize transfer details for REST API and JSON responses."""
        prod = self.product
        src_batch = self.source_batch
        return {
            "id": self.id,
            "transfer_id": self.id,
            "transfer_code": self.transfer_code,
            "organization_id": self.organization_id,
            "source_shop_id": self.source_shop_id,
            "source_shop_name": self.source_shop.name if self.source_shop else None,
            "recipient_shop_id": self.recipient_shop_id,
            "recipient_shop_name": self.recipient_shop.name if self.recipient_shop else None,
            "product_id": self.product_id,
            "product_sku": prod.sku if prod else None,
            "product_name": prod.name if prod else None,
            "product_unit": prod.unit if prod else None,
            "source_batch_id": self.source_batch_id,
            "source_batch_code": src_batch.batch_code if src_batch else None,
            "recipient_batch_id": self.recipient_batch_id,
            "quantity": self.quantity,
            "received_good_qty": self.received_good_qty,
            "transit_damaged_qty": self.transit_damaged_qty,
            "unit_transfer_price": float(self.unit_transfer_price or 0),
            "total_value": float(self.total_transfer_value),
            "estimated_distance_km": (
                float(self.estimated_distance_km) if self.estimated_distance_km is not None else None
            ),
            "transfer_logistics_cost": float(self.transfer_logistics_cost or 0),
            "batch_expiry_date": (
                self.batch_expiry_date_snapshot.isoformat()
                if self.batch_expiry_date_snapshot
                else (src_batch.expiry_date.isoformat() if src_batch and src_batch.expiry_date else None)
            ),
            "status": self.status,
            "initiated_by_shop_id": self.initiated_by_shop_id,
            "initiated_by_user_id": self.initiated_by_user_id,
            "initiated_by_username": self.initiated_by_user.username if self.initiated_by_user else None,
            "source_approved_by_user_id": self.source_approved_by_user_id,
            "source_approved_at": (
                self.source_approved_at.isoformat() if self.source_approved_at else None
            ),
            "recipient_approved_by_user_id": self.recipient_approved_by_user_id,
            "recipient_approved_at": (
                self.recipient_approved_at.isoformat() if self.recipient_approved_at else None
            ),
            "reserved_at": self.reserved_at.isoformat() if self.reserved_at else None,
            "offer_expires_at": self.offer_expires_at.isoformat() if self.offer_expires_at else None,
            "dispatched_by_user_id": self.dispatched_by_user_id,
            "dispatched_at": self.dispatched_at.isoformat() if self.dispatched_at else None,
            "dispatch_notes": self.dispatch_notes,
            "received_by_user_id": self.received_by_user_id,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "receipt_notes": self.receipt_notes,
            "rejection_or_cancel_reason": self.rejection_or_cancel_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<TransferRequest id={self.id} code={self.transfer_code!r} "
            f"src={self.source_shop_id} dst={self.recipient_shop_id} "
            f"qty={self.quantity} status={self.status!r}>"
        )


class TransferStatusLog(db.Model):
    """Immutable status transition audit trail for transfer requests (ADR-004, BR-010)."""

    __tablename__ = "transfer_status_logs"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    transfer_id = db.Column(
        db.BigInteger,
        db.ForeignKey("transfer_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status = db.Column(db.String(32), nullable=True)
    to_status = db.Column(db.String(32), nullable=False)
    actor_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=db.func.now(),
    )

    transfer = db.relationship("TransferRequest", back_populates="status_logs")
    actor = db.relationship("User", foreign_keys=[actor_user_id])

    __table_args__ = (
        db.Index("idx_transfer_status_logs_transfer", "transfer_id", "created_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize status log entry."""
        return {
            "id": self.id,
            "transfer_id": self.transfer_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "actor_user_id": self.actor_user_id,
            "actor_username": self.actor.username if self.actor else "SYSTEM",
            "actor_name": self.actor.full_name if self.actor else "System Event",
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<TransferStatusLog id={self.id} transfer_id={self.transfer_id} "
            f"{self.from_status} -> {self.to_status}>"
        )
