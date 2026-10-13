"""SQLAlchemy declarative model for in-app Notifications (Phase 7)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.extensions import db

VALID_NOTIFICATION_TYPES = (
    "LOW_STOCK",
    "NEAR_EXPIRY",
    "EXPIRY_RISK_CRITICAL",
    "SURPLUS_DETECTED",
    "TRANSFER_OFFER_RECEIVED",
    "TRANSFER_REQUEST_RECEIVED",
    "TRANSFER_APPROVED",
    "TRANSFER_REJECTED",
    "TRANSFER_DISPATCHED",
    "TRANSFER_RECEIVED",
    "TRANSFER_CANCELLED",
    "TRANSFER_EXPIRED",
    "TRANSFER_DISPUTED",
    "SYSTEM_ALERT",
)

VALID_NOTIFICATION_SEVERITIES = ("INFO", "WARNING", "CRITICAL")


class Notification(db.Model):
    """In-app notification delivered to an authorized recipient user."""

    __tablename__ = "notifications"

    __table_args__ = (
        CheckConstraint(
            f"severity IN {VALID_NOTIFICATION_SEVERITIES!r}",
            name="ck_notifications_severity_valid",
        ),
        CheckConstraint(
            f"notification_type IN {VALID_NOTIFICATION_TYPES!r}",
            name="ck_notifications_type_valid",
        ),
        UniqueConstraint(
            "recipient_user_id",
            "dedup_key",
            name="uq_notifications_user_dedup",
        ),
        Index(
            "idx_notifications_user_unread",
            "recipient_user_id",
            "is_read",
            "created_at",
        ),
        Index(
            "idx_notifications_user_created",
            "recipient_user_id",
            "created_at",
        ),
        Index(
            "idx_notifications_shop_type",
            "shop_id",
            "notification_type",
        ),
    )

    id = db.Column(BigInteger, primary_key=True, autoincrement=True)

    recipient_user_id = db.Column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    organization_id = db.Column(
        BigInteger,
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    shop_id = db.Column(
        BigInteger,
        ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    notification_type = db.Column(String(48), nullable=False)
    severity = db.Column(String(16), nullable=False, default="INFO")
    title = db.Column(String(160), nullable=False)
    message = db.Column(Text, nullable=False)

    is_read = db.Column(Boolean, nullable=False, default=False, index=True)
    read_at = db.Column(DateTime(timezone=True), nullable=True)

    related_entity_type = db.Column(String(32), nullable=True)
    related_entity_id = db.Column(BigInteger, nullable=True)

    dedup_key = db.Column(String(160), nullable=True, index=True)

    created_at = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # Relationships
    recipient_user = relationship("User", foreign_keys=[recipient_user_id], backref="notifications")
    organization = relationship("Organization", foreign_keys=[organization_id])
    shop = relationship("Shop", foreign_keys=[shop_id])

    def to_dict(self) -> dict[str, Any]:
        """Serialize notification to dictionary."""
        return {
            "id": self.id,
            "recipient_user_id": self.recipient_user_id,
            "organization_id": self.organization_id,
            "shop_id": self.shop_id,
            "notification_type": self.notification_type,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "is_read": self.is_read,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "related_entity_type": self.related_entity_type,
            "related_entity_id": self.related_entity_id,
            "dedup_key": self.dedup_key,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
