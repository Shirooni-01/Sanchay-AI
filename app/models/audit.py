"""Immutable AuditLog model for recording security and state-changing events."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB

from app.extensions import db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(db.Model):
    """Stores an immutable audit trail entry in PostgreSQL."""

    __tablename__ = "audit_logs"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    organization_id = db.Column(
        db.BigInteger,
        db.ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    shop_id = db.Column(
        db.BigInteger,
        db.ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_role = db.Column(db.String(20), nullable=True)
    action = db.Column(db.String(64), nullable=False)
    entity_type = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.String(64), nullable=True)
    before_state = db.Column(JSONB, nullable=True)
    after_state = db.Column(JSONB, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=db.func.now(),
    )

    actor = db.relationship("User", backref=db.backref("audit_logs", lazy="dynamic"))

    __table_args__ = (
        db.Index("idx_audit_logs_created_at", "created_at"),
        db.Index("idx_audit_logs_actor_action", "actor_user_id", "action"),
        db.Index("idx_audit_logs_org_created", "organization_id", "created_at"),
        db.Index("idx_audit_logs_shop_created", "shop_id", "created_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize audit log record."""
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "shop_id": self.shop_id,
            "actor_user_id": self.actor_user_id,
            "actor_role": self.actor_role,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "ip_address": self.ip_address,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
