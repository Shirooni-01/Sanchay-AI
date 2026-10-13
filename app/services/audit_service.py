"""Audit logging service that safely records security and domain events."""

from __future__ import annotations

from typing import Any

from app.extensions import db
from app.models.audit import AuditLog
from app.models.user import User

SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "current_password",
    "new_password",
    "confirm_password",
    "secret",
    "secret_key",
    "session_token",
    "csrf_token",
}


def sanitize_audit_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip any password, token, or secret keys before writing to audit_logs."""
    if payload is None:
        return None
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if str(key).lower() in SENSITIVE_KEYS:
            cleaned[key] = "[REDACTED]"
        elif isinstance(value, dict):
            cleaned[key] = sanitize_audit_payload(value)
        else:
            cleaned[key] = value
    return cleaned


class AuditService:
    """Service for creating immutable AuditLog records within the active session."""

    @staticmethod
    def record(
        action: str,
        entity_type: str,
        entity_id: str | int | None = None,
        actor: User | None = None,
        organization_id: int | None = None,
        shop_id: int | None = None,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog:
        """Add an AuditLog entry to the current database session."""
        resolved_org_id = (
            organization_id
            if organization_id is not None
            else (actor.organization_id if actor is not None else None)
        )
        entry = AuditLog(
            organization_id=resolved_org_id,
            shop_id=shop_id,
            actor_user_id=actor.id if actor else None,
            actor_role=actor.role if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            before_state=sanitize_audit_payload(before_state),
            after_state=sanitize_audit_payload(after_state),
            ip_address=(ip_address or "")[:45] or None,
            user_agent=user_agent,
        )
        db.session.add(entry)
        return entry
