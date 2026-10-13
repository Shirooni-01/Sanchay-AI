"""SQLAlchemy models for Organizations, Shops, and User-Shop Assignments (Phase 2)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.extensions import db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Organization(db.Model):
    """Represents a retail organization operating one or more shops."""

    __tablename__ = "organizations"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    name = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(32), unique=True, nullable=False)
    contact_email = db.Column(db.String(150), nullable=True)
    contact_phone = db.Column(db.String(20), nullable=True)
    address = db.Column(db.Text, nullable=True)
    low_stock_default_threshold = db.Column(
        db.Integer,
        nullable=False,
        default=10,
        server_default=db.text("10"),
    )
    near_expiry_days_threshold = db.Column(
        db.Integer,
        nullable=False,
        default=15,
        server_default=db.text("15"),
    )
    status = db.Column(
        db.String(20),
        nullable=False,
        default="ACTIVE",
        server_default="ACTIVE",
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

    shops = db.relationship(
        "Shop",
        back_populates="organization",
        lazy="dynamic",
        cascade="save-update, merge",
    )
    users = db.relationship(
        "User",
        back_populates="organization",
        lazy="dynamic",
        cascade="save-update, merge",
    )

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name="ck_organizations_status_valid",
        ),
        db.CheckConstraint(
            "low_stock_default_threshold >= 0",
            name="ck_organizations_low_stock_nonnegative",
        ),
        db.CheckConstraint(
            "near_expiry_days_threshold >= 1",
            name="ck_organizations_near_expiry_positive",
        ),
        db.Index("idx_organizations_status", "status"),
    )

    @property
    def is_active(self) -> bool:
        """Return True when the organization is active."""
        return self.status == "ACTIVE"

    def to_dict(self, include_counts: bool = False) -> dict[str, Any]:
        """Serialize organization details for REST API responses."""
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "contact_email": self.contact_email,
            "contact_phone": self.contact_phone,
            "address": self.address,
            "low_stock_default_threshold": self.low_stock_default_threshold,
            "near_expiry_days_threshold": self.near_expiry_days_threshold,
            "status": self.status,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_counts:
            data["shops_count"] = self.shops.count()
            data["active_shops_count"] = self.shops.filter_by(status="ACTIVE").count()
            data["users_count"] = self.users.count()
        return data

    def __repr__(self) -> str:
        return f"<Organization id={self.id} code={self.code!r} status={self.status!r}>"


class Shop(db.Model):
    """Represents an individual physical store branch within an Organization."""

    __tablename__ = "shops"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    organization_id = db.Column(
        db.BigInteger,
        db.ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(32), nullable=False)
    address = db.Column(db.Text, nullable=False)
    city = db.Column(db.String(80), nullable=False)
    pincode = db.Column(db.String(12), nullable=False)
    latitude = db.Column(db.Numeric(9, 6), nullable=True)
    longitude = db.Column(db.Numeric(9, 6), nullable=True)
    contact_phone = db.Column(db.String(20), nullable=False)
    contact_email = db.Column(db.String(150), nullable=True)
    low_stock_default_threshold = db.Column(
        db.Integer,
        nullable=False,
        default=10,
        server_default=db.text("10"),
    )
    near_expiry_days_threshold = db.Column(
        db.Integer,
        nullable=False,
        default=15,
        server_default=db.text("15"),
    )
    status = db.Column(
        db.String(20),
        nullable=False,
        default="ACTIVE",
        server_default="ACTIVE",
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

    organization = db.relationship("Organization", back_populates="shops")
    user_assignments = db.relationship(
        "UserShopAssignment",
        back_populates="shop",
        lazy="dynamic",
        cascade="save-update, merge",
    )

    __table_args__ = (
        db.UniqueConstraint("organization_id", "code", name="uq_shops_org_code"),
        db.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')",
            name="ck_shops_status_valid",
        ),
        db.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_shops_latitude_bounds",
        ),
        db.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_shops_longitude_bounds",
        ),
        db.CheckConstraint(
            "low_stock_default_threshold >= 0",
            name="ck_shops_low_stock_nonnegative",
        ),
        db.CheckConstraint(
            "near_expiry_days_threshold >= 1",
            name="ck_shops_near_expiry_positive",
        ),
        db.Index("idx_shops_org_status", "organization_id", "status"),
    )

    @property
    def is_operational(self) -> bool:
        """Return True if both the shop and its parent organization are ACTIVE."""
        org_active = self.organization.status == "ACTIVE" if self.organization else True
        return self.status == "ACTIVE" and org_active

    def to_dict(self, include_assignments: bool = False) -> dict[str, Any]:
        """Serialize shop details for REST API responses."""
        lat_val = (
            float(self.latitude)
            if isinstance(self.latitude, (Decimal, float, int))
            else None
        )
        lon_val = (
            float(self.longitude)
            if isinstance(self.longitude, (Decimal, float, int))
            else None
        )
        data: dict[str, Any] = {
            "id": self.id,
            "organization_id": self.organization_id,
            "name": self.name,
            "code": self.code,
            "address": self.address,
            "city": self.city,
            "pincode": self.pincode,
            "latitude": lat_val,
            "longitude": lon_val,
            "contact_phone": self.contact_phone,
            "contact_email": self.contact_email,
            "low_stock_default_threshold": self.low_stock_default_threshold,
            "near_expiry_days_threshold": self.near_expiry_days_threshold,
            "status": self.status,
            "is_operational": self.is_operational,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_assignments:
            assignments = (
                self.user_assignments.order_by(UserShopAssignment.id.asc()).all()
            )
            data["assignments"] = [a.to_dict(include_user=True) for a in assignments]
        return data

    def __repr__(self) -> str:
        return f"<Shop id={self.id} org_id={self.organization_id} code={self.code!r} status={self.status!r}>"


class UserShopAssignment(db.Model):
    """Maps a User (SHOP_OWNER or STAFF) to a Shop with a shop-scoped role."""

    __tablename__ = "user_shop_assignments"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    shop_id = db.Column(
        db.BigInteger,
        db.ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    assignment_role = db.Column(db.String(20), nullable=False)
    is_primary = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.text("true"),
    )
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.text("true"),
    )
    assigned_by_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    assigned_at = db.Column(
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

    user = db.relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="shop_assignments",
    )
    assigned_by = db.relationship(
        "User",
        foreign_keys=[assigned_by_user_id],
    )
    shop = db.relationship("Shop", back_populates="user_assignments")

    __table_args__ = (
        db.UniqueConstraint("user_id", "shop_id", name="uq_user_shop"),
        db.CheckConstraint(
            "assignment_role IN ('SHOP_OWNER', 'STAFF')",
            name="ck_user_shop_assignments_role_valid",
        ),
        db.Index(
            "idx_usa_shop_role_active",
            "shop_id",
            "assignment_role",
            "is_active",
        ),
    )

    def to_dict(
        self,
        include_shop: bool = False,
        include_user: bool = False,
    ) -> dict[str, Any]:
        """Serialize assignment details for REST API responses."""
        data: dict[str, Any] = {
            "id": self.id,
            "user_id": self.user_id,
            "shop_id": self.shop_id,
            "assignment_role": self.assignment_role,
            "is_primary": self.is_primary,
            "is_active": self.is_active,
            "assigned_by_user_id": self.assigned_by_user_id,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_shop and self.shop is not None:
            data["shop_name"] = self.shop.name
            data["shop_code"] = self.shop.code
            data["shop_status"] = self.shop.status
            data["organization_id"] = self.shop.organization_id
        if include_user and self.user is not None:
            data["username"] = self.user.username
            data["full_name"] = self.user.full_name
            data["email"] = self.user.email
            data["user_is_active"] = self.user.is_active
        return data

    def __repr__(self) -> str:
        return (
            f"<UserShopAssignment id={self.id} user_id={self.user_id} "
            f"shop_id={self.shop_id} role={self.assignment_role!r} active={self.is_active}>"
        )
