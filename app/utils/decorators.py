"""Backend-enforced RBAC, organization isolation, and shop-scope decorators (Phase 2).

Every organization- and shop-scoped request verifies on the server:
1. The user is authenticated and active.
2. The user has the required role or permission.
3. The target organization belongs to the user's authorized scope.
4. The target shop belongs to that organization.
5. The user is authorized to access that shop.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from flask import g, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models.organization import Organization, Shop, UserShopAssignment
from app.models.user import User
from app.services.audit_service import AuditService
from app.utils.errors import (
    AuthenticationError,
    AuthorizationError,
    ResourceNotFoundError,
)


def _record_security_audit(
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: str | int | None = None,
    organization_id: int | None = None,
    shop_id: int | None = None,
    reason: str | None = None,
) -> None:
    """Persist an immutable security audit event for rejected authorization checks."""
    try:
        AuditService.record(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor=actor,
            organization_id=organization_id,
            shop_id=shop_id,
            after_state={
                "reason": reason or action,
                "path": request.path,
                "method": request.method,
            },
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


def verify_authenticated_user() -> User:
    """Verify the current user is authenticated and active."""
    if not current_user.is_authenticated:
        raise AuthenticationError(
            message="Authentication is required to access this resource.",
            code="AUTHENTICATION_REQUIRED",
        )
    user: User = current_user._get_current_object()
    if not user.is_active:
        raise AuthenticationError(
            message="Your account is inactive.",
            code="ACCOUNT_INACTIVE",
        )
    return user


def verify_organization_access(
    user: User,
    target_organization_id: int | None = None,
    require_active: bool = False,
) -> Organization:
    """Verify that the user belongs to an organization and matches target_organization_id."""
    if user.organization_id is None:
        _record_security_audit(
            actor=user,
            action="UNAUTHORIZED_ORG_ACCESS",
            entity_type="ORGANIZATION",
            entity_id=target_organization_id,
            reason="user_has_no_organization",
        )
        raise AuthorizationError(
            message="User is not associated with an active organization.",
            code="ORGANIZATION_MEMBERSHIP_REQUIRED",
        )

    if (
        target_organization_id is not None
        and int(target_organization_id) != int(user.organization_id)
    ):
        _record_security_audit(
            actor=user,
            action="UNAUTHORIZED_ORG_ACCESS",
            entity_type="ORGANIZATION",
            entity_id=target_organization_id,
            organization_id=user.organization_id,
            reason="cross_organization_access_attempt",
        )
        raise AuthorizationError(
            message="Cross-organization access is strictly forbidden.",
            code="UNAUTHORIZED_ORG_ACCESS",
        )

    org = db.session.get(Organization, user.organization_id)
    if org is None:
        raise ResourceNotFoundError(message="Organization not found.")

    if require_active and org.status != "ACTIVE":
        _record_security_audit(
            actor=user,
            action="INACTIVE_ORGANIZATION_BLOCKED",
            entity_type="ORGANIZATION",
            entity_id=org.id,
            organization_id=org.id,
            reason="organization_inactive",
        )
        raise AuthorizationError(
            message="This organization is currently deactivated and cannot perform operational changes.",
            code="INACTIVE_ORGANIZATION",
        )

    return org


def verify_shop_access(
    user: User,
    shop_id: int,
    allow_inactive: bool = False,
    required_shop_roles: set[str] | list[str] | tuple[str, ...] | None = None,
) -> tuple[Shop, UserShopAssignment | None]:
    """Verify all 5 tenant & shop isolation requirements for a target shop_id.

    1. User is authenticated & active.
    2. User has required global/shop role.
    3. User belongs to an organization (and it is active when allow_inactive=False).
    4. Target shop exists and belongs to the user's organization.
    5. User is authorized for that specific shop (SUPER_ADMIN of that org, or active UserShopAssignment).
    """
    if user.organization_id is None:
        _record_security_audit(
            actor=user,
            action="UNAUTHORIZED_SHOP_ACCESS",
            entity_type="SHOP",
            entity_id=shop_id,
            reason="user_has_no_organization",
        )
        raise AuthorizationError(
            message="You do not belong to an organization.",
            code="UNAUTHORIZED_ORG_ACCESS",
        )

    shop = db.session.get(Shop, int(shop_id))
    if shop is None:
        raise ResourceNotFoundError(message=f"Shop #{shop_id} was not found.")

    # Check 3 & 4: Target shop's organization must match the user's organization
    if shop.organization_id != user.organization_id:
        _record_security_audit(
            actor=user,
            action="UNAUTHORIZED_ORG_ACCESS",
            entity_type="SHOP",
            entity_id=shop.id,
            organization_id=user.organization_id,
            shop_id=shop.id,
            reason="cross_organization_shop_access_attempt",
        )
        raise AuthorizationError(
            message="You cannot access a shop belonging to another organization.",
            code="UNAUTHORIZED_ORG_ACCESS",
        )

    org = shop.organization or db.session.get(Organization, shop.organization_id)
    if not allow_inactive and org is not None and org.status != "ACTIVE":
        _record_security_audit(
            actor=user,
            action="INACTIVE_ORGANIZATION_BLOCKED",
            entity_type="SHOP",
            entity_id=shop.id,
            organization_id=shop.organization_id,
            shop_id=shop.id,
            reason="organization_inactive",
        )
        raise AuthorizationError(
            message="Parent organization is deactivated; operational shop actions are disabled.",
            code="INACTIVE_ORGANIZATION",
        )

    assignment: UserShopAssignment | None = None

    # Check 5: Verify shop-level membership & role
    if user.role == "SUPER_ADMIN":
        if required_shop_roles and "SUPER_ADMIN" not in required_shop_roles:
            _record_security_audit(
                actor=user,
                action="INSUFFICIENT_ROLE_PERMISSION",
                entity_type="SHOP",
                entity_id=shop.id,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                reason="role_not_in_required_shop_roles",
            )
            raise AuthorizationError(
                message="Your role is not permitted to perform this shop action.",
                code="INSUFFICIENT_ROLE_PERMISSION",
            )
    else:
        assignment = (
            db.session.query(UserShopAssignment)
            .filter(
                UserShopAssignment.user_id == user.id,
                UserShopAssignment.shop_id == shop.id,
                UserShopAssignment.is_active.is_(True),
            )
            .first()
        )
        if assignment is None:
            _record_security_audit(
                actor=user,
                action="UNAUTHORIZED_SHOP_ACCESS",
                entity_type="SHOP",
                entity_id=shop.id,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                reason="no_active_shop_assignment",
            )
            raise AuthorizationError(
                message="You are not authorized to access this shop.",
                code="UNAUTHORIZED_SHOP_ACCESS",
            )

        if required_shop_roles and assignment.assignment_role not in required_shop_roles:
            _record_security_audit(
                actor=user,
                action="INSUFFICIENT_ROLE_PERMISSION",
                entity_type="SHOP",
                entity_id=shop.id,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                reason=f"assignment_role_{assignment.assignment_role}_insufficient",
            )
            raise AuthorizationError(
                message="You do not have sufficient shop permissions for this action.",
                code="INSUFFICIENT_ROLE_PERMISSION",
            )

    if not allow_inactive and shop.status != "ACTIVE":
        _record_security_audit(
            actor=user,
            action="INACTIVE_SHOP_OPERATION_BLOCKED",
            entity_type="SHOP",
            entity_id=shop.id,
            organization_id=shop.organization_id,
            shop_id=shop.id,
            reason=f"shop_status_{shop.status}",
        )
        raise AuthorizationError(
            message=f"Shop '{shop.name}' is currently {shop.status} and cannot perform operational activities.",
            code="INACTIVE_SHOP",
        )

    return shop, assignment


def require_role(*allowed_roles: str) -> Callable:
    """Decorator enforcing that the authenticated user has one of allowed_roles."""
    normalized_roles = {r.upper() for r in allowed_roles}

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        @login_required
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            user = verify_authenticated_user()
            if user.role not in normalized_roles:
                _record_security_audit(
                    actor=user,
                    action="INSUFFICIENT_ROLE_PERMISSION",
                    entity_type="ENDPOINT",
                    entity_id=request.endpoint,
                    organization_id=user.organization_id,
                    reason=f"required_one_of_{sorted(normalized_roles)}_got_{user.role}",
                )
                raise AuthorizationError(
                    message="You do not have permission to perform this action.",
                    code="INSUFFICIENT_ROLE_PERMISSION",
                )
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_organization_access(require_active: bool = False) -> Callable:
    """Decorator verifying the user belongs to an organization (and stores it on g.current_organization)."""

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        @login_required
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            user = verify_authenticated_user()
            target_org_id = kwargs.get("organization_id") or request.args.get("organization_id")
            if target_org_id is None and request.is_json:
                body = request.get_json(silent=True) or {}
                target_org_id = body.get("organization_id")
            if target_org_id is not None:
                try:
                    target_org_id = int(target_org_id)
                except (ValueError, TypeError):
                    target_org_id = None

            org = verify_organization_access(
                user=user,
                target_organization_id=target_org_id,
                require_active=require_active,
            )
            g.current_organization = org
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_shop_access(
    allow_inactive: bool = False,
    required_shop_roles: set[str] | list[str] | tuple[str, ...] | None = None,
) -> Callable:
    """Decorator verifying shop-level access for routes taking shop_id."""

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        @login_required
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            user = verify_authenticated_user()
            raw_shop_id = kwargs.get("shop_id") or request.args.get("shop_id")
            if raw_shop_id is None and request.is_json:
                body = request.get_json(silent=True) or {}
                raw_shop_id = body.get("shop_id")

            if raw_shop_id is None:
                raise AuthorizationError(
                    message="A valid shop_id is required for this request.",
                    code="SHOP_SCOPE_REQUIRED",
                )
            try:
                shop_id = int(raw_shop_id)
            except (ValueError, TypeError) as exc:
                raise ResourceNotFoundError(message="Invalid shop identifier.") from exc

            shop, assignment = verify_shop_access(
                user=user,
                shop_id=shop_id,
                allow_inactive=allow_inactive,
                required_shop_roles=required_shop_roles,
            )
            g.current_shop = shop
            g.current_shop_assignment = assignment
            return fn(*args, **kwargs)

        return wrapper

    return decorator
