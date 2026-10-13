"""SQLAlchemy models for Point-of-Sale (POS) Sales, Line Item Batch Allocations, and Returns (Phase 4)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.extensions import db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Sale(db.Model):
    """Completed, returned, or cancelled POS sale invoice record (DATABASE_SCHEMA.md 2.9, BR-004, BR-017)."""

    __tablename__ = "sales"

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
    invoice_number = db.Column(db.String(48), nullable=False)
    idempotency_key = db.Column(db.String(80), nullable=True)
    customer_name = db.Column(db.String(120), nullable=True)
    customer_phone = db.Column(db.String(20), nullable=True)
    status = db.Column(
        db.String(24),
        nullable=False,
        default="COMPLETED",
        server_default="COMPLETED",
    )
    subtotal_amount = db.Column(db.Numeric(12, 2), nullable=False)
    discount_amount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=db.text("0.00"),
    )
    total_amount = db.Column(db.Numeric(12, 2), nullable=False)
    total_cost_amount = db.Column(db.Numeric(12, 2), nullable=False)
    returned_amount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=db.text("0.00"),
    )
    payment_mode = db.Column(
        db.String(20),
        nullable=False,
        default="CASH",
        server_default="CASH",
    )
    notes = db.Column(db.Text, nullable=True)
    sold_by_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    cancelled_by_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    cancelled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    cancellation_reason = db.Column(db.Text, nullable=True)
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
    shop = db.relationship("Shop", backref=db.backref("sales", lazy="dynamic"))
    sold_by = db.relationship("User", foreign_keys=[sold_by_user_id])
    cancelled_by = db.relationship("User", foreign_keys=[cancelled_by_user_id])

    items = db.relationship(
        "SaleItem",
        back_populates="sale",
        order_by="SaleItem.id.asc()",
        cascade="save-update, merge",
    )
    returns = db.relationship(
        "SaleReturn",
        back_populates="sale",
        order_by="SaleReturn.id.asc()",
        cascade="save-update, merge",
    )

    __table_args__ = (
        db.UniqueConstraint("shop_id", "invoice_number", name="uq_sales_shop_invoice"),
        db.UniqueConstraint("shop_id", "idempotency_key", name="uq_sales_shop_idempotency"),
        db.CheckConstraint(
            "status IN ('COMPLETED', 'PARTIALLY_RETURNED', 'RETURNED', 'CANCELLED')",
            name="ck_sales_status_valid",
        ),
        db.CheckConstraint(
            "payment_mode IN ('CASH', 'UPI_MANUAL', 'CARD_MANUAL', 'OTHER')",
            name="ck_sales_payment_mode_valid",
        ),
        db.CheckConstraint(
            "subtotal_amount >= 0 AND discount_amount >= 0 AND total_amount >= 0 "
            "AND total_cost_amount >= 0 AND returned_amount >= 0",
            name="ck_sales_amounts_nonnegative",
        ),
        db.CheckConstraint(
            "discount_amount <= subtotal_amount AND total_amount = subtotal_amount - discount_amount",
            name="ck_sales_discount_bounds",
        ),
        db.CheckConstraint(
            "returned_amount <= total_amount",
            name="ck_sales_returned_bounds",
        ),
        db.Index("idx_sales_shop_created", "shop_id", "created_at"),
        db.Index("idx_sales_shop_status", "shop_id", "status"),
    )

    @property
    def receipt_number(self) -> str:
        """Alias for invoice_number."""
        return self.invoice_number

    @property
    def net_revenue_decimal(self) -> Decimal:
        """Net revenue after returns/cancellations (BR-017)."""
        if self.status == "CANCELLED":
            return Decimal("0.00")
        total = Decimal(str(self.total_amount or 0))
        returned = Decimal(str(self.returned_amount or 0))
        return max(Decimal("0.00"), (total - returned).quantize(Decimal("0.01")))

    @property
    def net_cogs_decimal(self) -> Decimal:
        """Net COGS for unreturned units (BR-017)."""
        if self.status == "CANCELLED":
            return Decimal("0.00")
        net_cost = Decimal("0.00")
        for item in self.items:
            net_qty = max(0, int(item.quantity or 0) - int(item.returned_quantity or 0))
            net_cost += Decimal(net_qty) * Decimal(str(item.unit_cost_price or 0))
        return net_cost.quantize(Decimal("0.01"))

    @property
    def gross_profit_decimal(self) -> Decimal:
        """Net Gross Profit = Net Revenue - Net COGS (BR-017)."""
        return (self.net_revenue_decimal - self.net_cogs_decimal).quantize(Decimal("0.01"))

    def to_dict(self, include_details: bool = True) -> dict[str, Any]:
        """Serialize sale transaction with FEFO batch allocations and return history."""
        subtotal = Decimal(str(self.subtotal_amount or 0)).quantize(Decimal("0.01"))
        discount = Decimal(str(self.discount_amount or 0)).quantize(Decimal("0.01"))
        total = Decimal(str(self.total_amount or 0)).quantize(Decimal("0.01"))
        cost = Decimal(str(self.total_cost_amount or 0)).quantize(Decimal("0.01"))
        returned = Decimal(str(self.returned_amount or 0)).quantize(Decimal("0.01"))
        net_rev = self.net_revenue_decimal
        net_cogs = self.net_cogs_decimal
        gross_profit = self.gross_profit_decimal

        total_qty = sum(int(i.quantity or 0) for i in self.items)
        returned_qty = sum(int(i.returned_quantity or 0) for i in self.items)
        net_qty = 0 if self.status == "CANCELLED" else max(0, total_qty - returned_qty)

        payload: dict[str, Any] = {
            "id": self.id,
            "sale_id": self.id,
            "organization_id": self.organization_id,
            "shop_id": self.shop_id,
            "shop_name": self.shop.name if self.shop else None,
            "shop_code": self.shop.code if self.shop else None,
            "invoice_number": self.invoice_number,
            "receipt_number": self.invoice_number,
            "idempotency_key": self.idempotency_key,
            "customer_name": self.customer_name,
            "customer_phone": self.customer_phone,
            "status": self.status,
            "payment_mode": self.payment_mode,
            "payment_method": self.payment_mode,
            "subtotal_amount": float(subtotal),
            "subtotal": float(subtotal),
            "discount_amount": float(discount),
            "discount": float(discount),
            "total_amount": float(total),
            "final_total": float(total),
            "total": float(total),
            "total_cost_amount": float(cost),
            "returned_amount": float(returned),
            "net_revenue_amount": float(net_rev),
            "net_cogs_amount": float(net_cogs),
            "gross_profit_amount": float(gross_profit),
            "total_quantity_sold": total_qty,
            "returned_quantity": returned_qty,
            "net_quantity_sold": net_qty,
            "notes": self.notes,
            "sold_by_user_id": self.sold_by_user_id,
            "sold_by_username": self.sold_by.username if self.sold_by else None,
            "sold_by_name": self.sold_by.full_name if self.sold_by else None,
            "cancelled_by_user_id": self.cancelled_by_user_id,
            "cancelled_by_name": self.cancelled_by.full_name if self.cancelled_by else None,
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
            "cancellation_reason": self.cancellation_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_details:
            serialized_items = [item.to_dict() for item in self.items]
            payload["items"] = serialized_items
            payload["allocations"] = serialized_items
            payload["returns"] = [ret.to_dict() for ret in self.returns]

            # Group by product_id for receipt display
            grouped: dict[int, dict[str, Any]] = {}
            for s_item in serialized_items:
                pid = int(s_item["product_id"])
                if pid not in grouped:
                    grouped[pid] = {
                        "product_id": pid,
                        "shop_product_id": s_item["shop_product_id"],
                        "sku": s_item["sku"],
                        "product_name": s_item["product_name"],
                        "category": s_item["category"],
                        "unit": s_item["unit"],
                        "unit_selling_price": s_item["unit_selling_price"],
                        "quantity": 0,
                        "returned_quantity": 0,
                        "line_discount": 0.0,
                        "line_total": 0.0,
                        "batch_allocations": [],
                    }
                grouped[pid]["quantity"] += s_item["quantity"]
                grouped[pid]["returned_quantity"] += s_item["returned_quantity"]
                grouped[pid]["line_discount"] = round(
                    grouped[pid]["line_discount"] + s_item["line_discount"], 2
                )
                grouped[pid]["line_total"] = round(
                    grouped[pid]["line_total"] + s_item["line_total"], 2
                )
                grouped[pid]["batch_allocations"].append(s_item)

            payload["product_lines"] = list(grouped.values())

        return payload

    def __repr__(self) -> str:
        return (
            f"<Sale id={self.id} shop_id={self.shop_id} invoice={self.invoice_number!r} "
            f"status={self.status!r} total={self.total_amount}>"
        )


class SaleItem(db.Model):
    """Batch-allocated POS sale line item (DATABASE_SCHEMA.md 2.9, BR-004, BR-017)."""

    __tablename__ = "sale_items"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    sale_id = db.Column(
        db.BigInteger,
        db.ForeignKey("sales.id", ondelete="RESTRICT"),
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
    shop_product_id = db.Column(
        db.BigInteger,
        db.ForeignKey("shop_products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id = db.Column(
        db.BigInteger,
        db.ForeignKey("inventory_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity = db.Column(db.Integer, nullable=False)
    returned_quantity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default=db.text("0"),
    )
    unit_selling_price = db.Column(db.Numeric(12, 2), nullable=False)
    unit_cost_price = db.Column(db.Numeric(12, 2), nullable=False)
    line_discount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=db.text("0.00"),
    )
    line_total = db.Column(db.Numeric(12, 2), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=db.func.now(),
    )

    sale = db.relationship("Sale", back_populates="items")
    shop = db.relationship("Shop")
    product = db.relationship("Product")
    shop_product = db.relationship("ShopProduct")
    batch = db.relationship("InventoryBatch")
    returns = db.relationship(
        "SaleReturn",
        back_populates="sale_item",
        order_by="SaleReturn.id.asc()",
        cascade="save-update, merge",
    )

    __table_args__ = (
        db.CheckConstraint("quantity > 0", name="ck_sale_items_qty_positive"),
        db.CheckConstraint(
            "returned_quantity >= 0 AND returned_quantity <= quantity",
            name="ck_sale_items_returned_qty_bounds",
        ),
        db.CheckConstraint(
            "unit_selling_price >= 0 AND unit_cost_price >= 0 "
            "AND line_discount >= 0 AND line_total >= 0",
            name="ck_sale_items_prices_nonnegative",
        ),
        db.CheckConstraint(
            "line_total = (quantity * unit_selling_price) - line_discount",
            name="ck_sale_items_line_total_math",
        ),
        db.Index("idx_sale_items_sale", "sale_id"),
        db.Index("idx_sale_items_shop_product", "shop_id", "product_id"),
        db.Index("idx_sale_items_batch", "batch_id"),
    )

    @property
    def remaining_returnable_qty(self) -> int:
        """Units still eligible for return on this batch allocation."""
        return max(0, int(self.quantity or 0) - int(self.returned_quantity or 0))

    def to_dict(self) -> dict[str, Any]:
        """Serialize sale item / batch allocation."""
        prod = self.product
        batch = self.batch
        qty = int(self.quantity or 0)
        ret_qty = int(self.returned_quantity or 0)
        unit_price = Decimal(str(self.unit_selling_price or 0)).quantize(Decimal("0.01"))
        unit_cost = Decimal(str(self.unit_cost_price or 0)).quantize(Decimal("0.01"))
        discount = Decimal(str(self.line_discount or 0)).quantize(Decimal("0.01"))
        total = Decimal(str(self.line_total or 0)).quantize(Decimal("0.01"))
        gross_line = (Decimal(qty) * unit_price).quantize(Decimal("0.01"))

        return {
            "id": self.id,
            "sale_item_id": self.id,
            "sale_id": self.sale_id,
            "shop_id": self.shop_id,
            "product_id": self.product_id,
            "shop_product_id": self.shop_product_id,
            "sku": prod.sku if prod else None,
            "product_sku": prod.sku if prod else None,
            "product_name": prod.name if prod else None,
            "category": prod.category if prod else None,
            "unit": prod.unit if prod else None,
            "batch_id": self.batch_id,
            "batch_code": batch.batch_code if batch else None,
            "expiry_date": (
                batch.expiry_date.isoformat() if batch and batch.expiry_date else None
            ),
            "quantity": qty,
            "quantity_sold": qty,
            "quantity_deducted": qty,
            "returned_quantity": ret_qty,
            "remaining_returnable_qty": max(0, qty - ret_qty),
            "unit_selling_price": float(unit_price),
            "unit_price": float(unit_price),
            "unit_cost_price": float(unit_cost),
            "gross_line_amount": float(gross_line),
            "line_discount": float(discount),
            "line_total": float(total),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<SaleItem id={self.id} sale_id={self.sale_id} product_id={self.product_id} "
            f"batch_id={self.batch_id} qty={self.quantity} total={self.line_total}>"
        )


class SaleReturn(db.Model):
    """Customer item return record linked to original Sale and SaleItem (DATABASE_SCHEMA.md 2.9, OD-006)."""

    __tablename__ = "sale_returns"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    sale_id = db.Column(
        db.BigInteger,
        db.ForeignKey("sales.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sale_item_id = db.Column(
        db.BigInteger,
        db.ForeignKey("sale_items.id", ondelete="RESTRICT"),
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
    quantity_returned = db.Column(db.Integer, nullable=False)
    return_condition = db.Column(db.String(20), nullable=False)
    stock_bucket_restored = db.Column(db.String(20), nullable=False)
    refund_amount = db.Column(db.Numeric(12, 2), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    idempotency_key = db.Column(db.String(80), nullable=True)
    processed_by_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=db.func.now(),
    )

    sale = db.relationship("Sale", back_populates="returns")
    sale_item = db.relationship("SaleItem", back_populates="returns")
    shop = db.relationship("Shop")
    product = db.relationship("Product")
    batch = db.relationship("InventoryBatch")
    processed_by = db.relationship("User", foreign_keys=[processed_by_user_id])

    __table_args__ = (
        db.UniqueConstraint("shop_id", "idempotency_key", name="uq_sale_returns_shop_idempotency"),
        db.CheckConstraint("quantity_returned > 0", name="ck_sale_returns_qty_positive"),
        db.CheckConstraint(
            "return_condition IN ('RESELLABLE', 'DAMAGED', 'EXPIRED')",
            name="ck_sale_returns_condition_valid",
        ),
        db.CheckConstraint(
            "stock_bucket_restored IN ('AVAILABLE', 'DAMAGED', 'EXPIRED')",
            name="ck_sale_returns_bucket_valid",
        ),
        db.CheckConstraint("refund_amount >= 0", name="ck_sale_returns_refund_nonnegative"),
        db.Index("idx_sale_returns_sale", "sale_id"),
        db.Index("idx_sale_returns_shop_created", "shop_id", "created_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize return record."""
        prod = self.product
        batch = self.batch
        actor = self.processed_by
        return {
            "id": self.id,
            "return_id": self.id,
            "sale_id": self.sale_id,
            "sale_item_id": self.sale_item_id,
            "shop_id": self.shop_id,
            "product_id": self.product_id,
            "sku": prod.sku if prod else None,
            "product_name": prod.name if prod else None,
            "unit": prod.unit if prod else None,
            "batch_id": self.batch_id,
            "batch_code": batch.batch_code if batch else None,
            "quantity_returned": self.quantity_returned,
            "return_condition": self.return_condition,
            "stock_bucket_restored": self.stock_bucket_restored,
            "refund_amount": float(Decimal(str(self.refund_amount or 0)).quantize(Decimal("0.01"))),
            "reason": self.reason,
            "idempotency_key": self.idempotency_key,
            "processed_by_user_id": self.processed_by_user_id,
            "processed_by_username": actor.username if actor else None,
            "processed_by_name": actor.full_name if actor else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<SaleReturn id={self.id} sale_id={self.sale_id} item_id={self.sale_item_id} "
            f"qty={self.quantity_returned} condition={self.return_condition!r}>"
        )
