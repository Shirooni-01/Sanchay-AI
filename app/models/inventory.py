"""SQLAlchemy models for Batch Inventory and Immutable Stock Movements Ledger (Phase 3)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.extensions import db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_money(val: Decimal | float | int | None) -> str:
    if val is None:
        return "0.00"
    return f"{Decimal(str(val)):.2f}"


class InventoryBatch(db.Model):
    """Canonical batch-level physical inventory record (ADR-004, BR-001, BR-002, BR-003)."""

    __tablename__ = "inventory_batches"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    shop_id = db.Column(
        db.BigInteger,
        db.ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    product_id = db.Column(
        db.BigInteger,
        db.ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    shop_product_id = db.Column(
        db.BigInteger,
        db.ForeignKey("shop_products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_code = db.Column(db.String(64), nullable=False)
    manufactured_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    unit_cost = db.Column(db.Numeric(12, 2), nullable=False)
    initial_qty = db.Column(db.Integer, nullable=False)
    available_qty = db.Column(db.Integer, nullable=False)
    reserved_qty = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default=db.text("0"),
    )
    damaged_qty = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default=db.text("0"),
    )
    expired_qty = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default=db.text("0"),
    )
    source_transfer_id = db.Column(db.BigInteger, nullable=True)
    status = db.Column(
        db.String(20),
        nullable=False,
        default="ACTIVE",
        server_default="ACTIVE",
    )
    received_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=db.func.now(),
    )
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

    shop = db.relationship("Shop", backref=db.backref("batches", lazy="dynamic"))
    product = db.relationship("Product", back_populates="batches")
    shop_product = db.relationship("ShopProduct", back_populates="batches")
    movements = db.relationship(
        "StockMovement",
        back_populates="batch",
        lazy="dynamic",
        cascade="save-update, merge",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "shop_id",
            "product_id",
            "batch_code",
            name="uq_batches_shop_product_code",
        ),
        db.CheckConstraint(
            "unit_cost >= 0",
            name="ck_batches_unit_cost_nonnegative",
        ),
        db.CheckConstraint(
            "initial_qty > 0",
            name="ck_batches_initial_qty_positive",
        ),
        db.CheckConstraint(
            "available_qty >= 0",
            name="ck_batches_available_qty_nonnegative",
        ),
        db.CheckConstraint(
            "reserved_qty >= 0",
            name="ck_batches_reserved_qty_nonnegative",
        ),
        db.CheckConstraint(
            "damaged_qty >= 0",
            name="ck_batches_damaged_qty_nonnegative",
        ),
        db.CheckConstraint(
            "expired_qty >= 0",
            name="ck_batches_expired_qty_nonnegative",
        ),
        db.CheckConstraint(
            "status IN ('ACTIVE', 'DEPLETED', 'EXPIRED', 'ARCHIVED')",
            name="ck_batches_status_valid",
        ),
        db.CheckConstraint(
            "manufactured_date IS NULL OR expiry_date IS NULL OR expiry_date >= manufactured_date",
            name="chk_batch_dates",
        ),
        db.Index(
            "idx_batches_fefo",
            "shop_id",
            "product_id",
            "status",
            "expiry_date",
            "received_at",
        ),
        db.Index("idx_batches_expiry", "shop_id", "expiry_date"),
    )

    @property
    def on_hand_qty(self) -> int:
        """Total physical units across all 4 batch buckets (BR-003)."""
        return (
            int(self.available_qty or 0)
            + int(self.reserved_qty or 0)
            + int(self.damaged_qty or 0)
            + int(self.expired_qty or 0)
        )

    @property
    def remaining_qty(self) -> int:
        """Alias for total physical on-hand quantity in the batch."""
        return self.on_hand_qty

    def days_to_expiry_on(self, ref_date: date) -> int | None:
        """Return integer days until expiry relative to ref_date, or None if non-perishable."""
        if self.expiry_date is None:
            return None
        return (self.expiry_date - ref_date).days

    def is_expired_on(self, ref_date: date) -> bool:
        """Return True if batch has passed its expiry_date or has status EXPIRED."""
        if self.status == "EXPIRED":
            return True
        if self.expiry_date is not None and self.expiry_date < ref_date:
            return True
        return False

    def effective_sellable_qty_on(self, ref_date: date) -> int:
        """Return sellable quantity on ref_date (excludes reserved, damaged, and expired stock)."""
        if self.status != "ACTIVE":
            return 0
        if self.expiry_date is not None and self.expiry_date < ref_date:
            return 0
        return max(0, int(self.available_qty or 0))

    def is_near_expiry_on(self, ref_date: date, warning_days: int = 15) -> bool:
        """Return True if batch has sellable stock and expires within 0..warning_days."""
        if self.effective_sellable_qty_on(ref_date) <= 0:
            return False
        days = self.days_to_expiry_on(ref_date)
        if days is None:
            return False
        return 0 <= days <= warning_days

    def expiry_risk_band_on(self, ref_date: date, warning_days: int = 15) -> str:
        """Classify batch expiry status deterministically."""
        if self.expiry_date is None:
            return "NON_PERISHABLE"
        days = self.days_to_expiry_on(ref_date)
        if days is not None and days < 0 or self.status == "EXPIRED":
            return "EXPIRED"
        if days == 0:
            return "EXPIRES_TODAY"
        if days is not None and 1 <= days <= min(3, warning_days):
            return "CRITICAL"
        if days is not None and days <= warning_days:
            return "WARNING"
        return "SAFE"

    def to_dict(
        self,
        ref_date: date | None = None,
        near_expiry_days: int | None = None,
    ) -> dict[str, Any]:
        """Serialize batch details for REST API responses."""
        today = ref_date or date.today()
        warn_days = (
            near_expiry_days
            if near_expiry_days is not None
            else (self.shop_product.near_expiry_days if self.shop_product else 15)
        )
        prod = self.product
        days_left = self.days_to_expiry_on(today)
        sellable = self.effective_sellable_qty_on(today)

        return {
            "id": self.id,
            "batch_id": self.id,
            "shop_id": self.shop_id,
            "product_id": self.product_id,
            "shop_product_id": self.shop_product_id,
            "sku": prod.sku if prod else None,
            "product_sku": prod.sku if prod else None,
            "product_name": prod.name if prod else None,
            "category": prod.category if prod else None,
            "unit": prod.unit if prod else None,
            "is_perishable": prod.is_perishable if prod else (self.expiry_date is not None),
            "batch_code": self.batch_code,
            "manufactured_date": (
                self.manufactured_date.isoformat() if self.manufactured_date else None
            ),
            "received_date": (
                self.manufactured_date.isoformat()
                if self.manufactured_date
                else (self.received_at.date().isoformat() if self.received_at else today.isoformat())
            ),
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "days_to_expiry": days_left,
            "days_until_expiry": days_left,
            "unit_cost": float(self.unit_cost or 0),
            "initial_qty": self.initial_qty,
            "available_qty": self.available_qty,
            "sellable_qty": sellable,
            "effective_sellable_qty": sellable,
            "reserved_qty": self.reserved_qty,
            "damaged_qty": self.damaged_qty,
            "expired_qty": self.expired_qty,
            "on_hand_qty": self.on_hand_qty,
            "remaining_qty": self.on_hand_qty,
            "status": self.status,
            "is_expired": self.is_expired_on(today),
            "is_near_expiry": self.is_near_expiry_on(today, warn_days),
            "expiry_risk_band": self.expiry_risk_band_on(today, warn_days),
            "source_transfer_id": self.source_transfer_id,
            "supplier_reference": None,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<InventoryBatch id={self.id} shop_id={self.shop_id} product_id={self.product_id} "
            f"code={self.batch_code!r} avail={self.available_qty} status={self.status!r}>"
        )


class StockMovement(db.Model):
    """Immutable ledger recording every change to any batch stock bucket (BR-002, BR-007)."""

    __tablename__ = "stock_movements"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    organization_id = db.Column(
        db.BigInteger,
        db.ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    shop_id = db.Column(
        db.BigInteger,
        db.ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    product_id = db.Column(
        db.BigInteger,
        db.ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id = db.Column(
        db.BigInteger,
        db.ForeignKey("inventory_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    movement_type = db.Column(db.String(32), nullable=False)
    bucket_affected = db.Column(db.String(20), nullable=False)
    quantity_change = db.Column(db.Integer, nullable=False)
    balance_before = db.Column(db.Integer, nullable=False)
    balance_after = db.Column(db.Integer, nullable=False)
    unit_cost_snapshot = db.Column(db.Numeric(12, 2), nullable=False)
    reference_type = db.Column(db.String(32), nullable=False)
    reference_id = db.Column(db.BigInteger, nullable=True)
    reason = db.Column(db.Text, nullable=True)
    performed_by_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=db.func.now(),
    )

    organization = db.relationship("Organization")
    shop = db.relationship("Shop")
    product = db.relationship("Product")
    batch = db.relationship("InventoryBatch", back_populates="movements")
    performed_by = db.relationship("User", foreign_keys=[performed_by_user_id])

    __table_args__ = (
        db.CheckConstraint(
            "movement_type IN ("
            "'RECEIPT', 'SALE', 'SALE_RETURN', 'SALE_CANCELLATION', "
            "'ADJUSTMENT_ADD', 'ADJUSTMENT_SUBTRACT', 'MARK_DAMAGED', 'MARK_EXPIRED', "
            "'TRANSFER_RESERVE', 'TRANSFER_RELEASE', 'TRANSFER_OUT', 'TRANSFER_IN'"
            ")",
            name="ck_movements_type_valid",
        ),
        db.CheckConstraint(
            "bucket_affected IN ('AVAILABLE', 'RESERVED', 'DAMAGED', 'EXPIRED')",
            name="ck_movements_bucket_valid",
        ),
        db.CheckConstraint(
            "quantity_change != 0",
            name="ck_movements_qty_nonzero",
        ),
        db.CheckConstraint(
            "balance_before >= 0",
            name="ck_movements_balance_before_nonnegative",
        ),
        db.CheckConstraint(
            "balance_after >= 0",
            name="ck_movements_balance_after_nonnegative",
        ),
        db.CheckConstraint(
            "balance_before + quantity_change = balance_after",
            name="ck_movements_balance_math",
        ),
        db.CheckConstraint(
            "unit_cost_snapshot >= 0",
            name="ck_movements_unit_cost_nonnegative",
        ),
        db.CheckConstraint(
            "reference_type IN ("
            "'INITIAL_RECEIPT', 'SALE', 'SALE_RETURN', 'TRANSFER', "
            "'MANUAL_ADJUSTMENT', 'EXPIRY_SWEEP'"
            ")",
            name="ck_movements_ref_type_valid",
        ),
        db.Index("idx_movements_shop_created", "shop_id", "created_at"),
        db.Index("idx_movements_batch", "batch_id", "created_at"),
        db.Index("idx_movements_product", "shop_id", "product_id", "created_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize ledger entry for REST API responses."""
        prod = self.product
        batch = self.batch
        actor = self.performed_by
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "shop_id": self.shop_id,
            "product_id": self.product_id,
            "sku": prod.sku if prod else None,
            "product_sku": prod.sku if prod else None,
            "product_name": prod.name if prod else None,
            "unit": prod.unit if prod else None,
            "batch_id": self.batch_id,
            "batch_code": batch.batch_code if batch else None,
            "movement_type": self.movement_type,
            "bucket_affected": self.bucket_affected,
            "quantity_change": self.quantity_change,
            "previous_qty": self.balance_before,
            "balance_before": self.balance_before,
            "resulting_qty": self.balance_after,
            "balance_after": self.balance_after,
            "unit_cost_snapshot": float(self.unit_cost_snapshot or 0),
            "reference_type": self.reference_type,
            "reference_id": self.reference_id,
            "reason": self.reason,
            "performed_by_user_id": self.performed_by_user_id,
            "performed_by_username": actor.username if actor else "SYSTEM",
            "performed_by_name": actor.full_name if actor else "Automated Expiry Sweep",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<StockMovement id={self.id} batch_id={self.batch_id} "
            f"type={self.movement_type!r} bucket={self.bucket_affected!r} "
            f"delta={self.quantity_change:+d} after={self.balance_after}>"
        )
