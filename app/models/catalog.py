"""SQLAlchemy models for Product Catalog and Shop-Scoped Product Configuration (Phase 3)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.extensions import db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_money(val: Decimal | float | int | None) -> str:
    if val is None:
        return "0.00"
    return f"{Decimal(str(val)):.2f}"


class Product(db.Model):
    """Organization-scoped canonical product master entry (ADR-006)."""

    __tablename__ = "products"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    organization_id = db.Column(
        db.BigInteger,
        db.ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    unit = db.Column(db.String(20), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_perishable = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.text("true"),
    )
    default_purchase_price = db.Column(db.Numeric(12, 2), nullable=False)
    default_selling_price = db.Column(db.Numeric(12, 2), nullable=False)
    reorder_threshold = db.Column(
        db.Integer,
        nullable=False,
        default=10,
        server_default=db.text("10"),
    )
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.text("true"),
    )
    created_by_user_id = db.Column(
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
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        server_default=db.func.now(),
    )

    organization = db.relationship("Organization", backref=db.backref("products", lazy="dynamic"))
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    shop_products = db.relationship(
        "ShopProduct",
        back_populates="product",
        lazy="dynamic",
        cascade="save-update, merge",
    )
    batches = db.relationship(
        "InventoryBatch",
        back_populates="product",
        lazy="dynamic",
        cascade="save-update, merge",
    )

    __table_args__ = (
        db.UniqueConstraint("organization_id", "sku", name="uq_products_org_sku"),
        db.CheckConstraint(
            "unit IN ('pcs', 'pack', 'kg', 'g', 'litre', 'ml', 'box')",
            name="ck_products_unit_valid",
        ),
        db.CheckConstraint(
            "default_purchase_price >= 0",
            name="ck_products_default_purchase_nonnegative",
        ),
        db.CheckConstraint(
            "default_selling_price >= 0",
            name="ck_products_default_selling_nonnegative",
        ),
        db.CheckConstraint(
            "reorder_threshold >= 0",
            name="ck_products_reorder_threshold_nonnegative",
        ),
        db.Index("idx_products_org_category", "organization_id", "category"),
        db.Index("idx_products_org_active", "organization_id", "is_active"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize canonical product master record."""
        return {
            "id": self.id,
            "product_id": self.id,
            "organization_id": self.organization_id,
            "sku": self.sku,
            "name": self.name,
            "category": self.category,
            "unit": self.unit,
            "description": self.description,
            "is_perishable": self.is_perishable,
            "default_purchase_price": float(self.default_purchase_price or 0),
            "default_selling_price": float(self.default_selling_price or 0),
            "reorder_threshold": self.reorder_threshold,
            "reorder_level": self.reorder_threshold,
            "is_active": self.is_active,
            "created_by_user_id": self.created_by_user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<Product id={self.id} org_id={self.organization_id} sku={self.sku!r} active={self.is_active}>"


class ShopProduct(db.Model):
    """Shop-specific catalog configuration, local pricing, and alert thresholds (FR-014)."""

    __tablename__ = "shop_products"

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
    purchase_price = db.Column(db.Numeric(12, 2), nullable=False)
    selling_price = db.Column(db.Numeric(12, 2), nullable=False)
    low_stock_threshold = db.Column(
        db.Integer,
        nullable=False,
        default=10,
        server_default=db.text("10"),
    )
    near_expiry_days = db.Column(
        db.Integer,
        nullable=False,
        default=15,
        server_default=db.text("15"),
    )
    surplus_threshold = db.Column(
        db.Integer,
        nullable=False,
        default=40,
        server_default=db.text("40"),
    )
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.text("true"),
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

    shop = db.relationship("Shop", backref=db.backref("shop_products", lazy="dynamic"))
    product = db.relationship("Product", back_populates="shop_products")
    batches = db.relationship(
        "InventoryBatch",
        back_populates="shop_product",
        lazy="dynamic",
        cascade="save-update, merge",
    )

    __table_args__ = (
        db.UniqueConstraint("shop_id", "product_id", name="uq_shop_products_shop_product"),
        db.CheckConstraint(
            "purchase_price >= 0",
            name="ck_shop_products_purchase_price_nonnegative",
        ),
        db.CheckConstraint(
            "selling_price >= 0",
            name="ck_shop_products_selling_price_nonnegative",
        ),
        db.CheckConstraint(
            "low_stock_threshold >= 0",
            name="ck_shop_products_low_stock_nonnegative",
        ),
        db.CheckConstraint(
            "near_expiry_days >= 1",
            name="ck_shop_products_near_expiry_positive",
        ),
        db.CheckConstraint(
            "surplus_threshold >= 1",
            name="ck_shop_products_surplus_positive",
        ),
        db.Index("idx_shop_products_shop_active", "shop_id", "is_active"),
        db.Index("idx_shop_products_product", "product_id"),
    )

    @property
    def reorder_threshold(self) -> int:
        """Alias for low_stock_threshold."""
        return self.low_stock_threshold

    @reorder_threshold.setter
    def reorder_threshold(self, value: int) -> None:
        self.low_stock_threshold = value

    @property
    def effective_is_active(self) -> bool:
        """Return True if both the shop configuration and master product are active."""
        prod_active = self.product.is_active if self.product else True
        return bool(self.is_active and prod_active)

    def to_dict(self, stock_summary: dict[str, Any] | None = None) -> dict[str, Any]:
        """Serialize shop product with canonical product attributes and optional stock summary."""
        prod = self.product
        data: dict[str, Any] = {
            "id": self.id,
            "shop_product_id": self.id,
            "shop_id": self.shop_id,
            "product_id": self.product_id,
            "organization_id": prod.organization_id if prod else None,
            "sku": prod.sku if prod else None,
            "name": prod.name if prod else None,
            "category": prod.category if prod else None,
            "unit": prod.unit if prod else None,
            "description": prod.description if prod else None,
            "is_perishable": prod.is_perishable if prod else True,
            "default_purchase_price": float(prod.default_purchase_price or 0) if prod else 0.0,
            "default_selling_price": float(prod.default_selling_price or 0) if prod else 0.0,
            "purchase_price": float(self.purchase_price or 0),
            "selling_price": float(self.selling_price or 0),
            "low_stock_threshold": self.low_stock_threshold,
            "reorder_threshold": self.low_stock_threshold,
            "reorder_level": self.low_stock_threshold,
            "near_expiry_days": self.near_expiry_days,
            "near_expiry_threshold_days": self.near_expiry_days,
            "surplus_threshold": self.surplus_threshold,
            "is_active": self.effective_is_active,
            "shop_product_is_active": self.is_active,
            "product_is_active": prod.is_active if prod else True,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if stock_summary is not None:
            data.update(stock_summary)
        return data

    def __repr__(self) -> str:
        return (
            f"<ShopProduct id={self.id} shop_id={self.shop_id} "
            f"product_id={self.product_id} active={self.is_active}>"
        )
