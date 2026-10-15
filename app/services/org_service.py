"""Organization, Shop, and Membership/RBAC services for Phase 2."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from app.extensions import db
from app.models.organization import Organization, Shop, UserShopAssignment
from app.models.user import User
from app.services.audit_service import AuditService
from app.utils.decorators import (
    _record_security_audit,
    verify_organization_access,
    verify_shop_access,
)
from app.utils.errors import (
    AuthorizationError,
    DatabaseConflictError,
    DuplicateResourceError,
    ResourceNotFoundError,
    ValidationError,
)
from app.utils.geo import haversine_distance_km
from app.utils.validators import (
    validate_organization_create_input,
    validate_organization_update_input,
    validate_shop_assignment_input,
    validate_shop_create_input,
    validate_shop_update_input,
    validate_user_creation_input,
    validate_user_update_input,
)


class OrganizationService:
    """Manages Organization lifecycle, configuration, and activation status."""

    @staticmethod
    def create_organization(
        actor: User,
        payload: dict[str, Any],
        attach_actor: bool = True,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Organization:
        """Create a new Organization (SUPER_ADMIN only)."""
        if actor.role != "SUPER_ADMIN":
            _record_security_audit(
                actor=actor,
                action="INSUFFICIENT_ROLE_PERMISSION",
                entity_type="ORGANIZATION",
                reason="non_super_admin_attempted_organization_create",
            )
            raise AuthorizationError(
                message="Only a Super Admin can create an organization.",
                code="INSUFFICIENT_ROLE_PERMISSION",
            )

        if attach_actor and actor.organization_id is not None:
            raise DuplicateResourceError(
                message="Your account is already bound to an organization.",
                details={"organization_id": "A Super Admin belongs to one organization."},
            )

        cleaned = validate_organization_create_input(payload)

        try:
            existing = (
                db.session.query(Organization)
                .filter(func.upper(Organization.code) == cleaned["code"])
                .first()
            )
            if existing is not None:
                raise DuplicateResourceError(
                    message=f"An organization with code '{cleaned['code']}' already exists.",
                    details={"code": "Duplicate organization code."},
                )

            org = Organization(
                name=cleaned["name"],
                code=cleaned["code"],
                contact_email=cleaned["contact_email"],
                contact_phone=cleaned["contact_phone"],
                address=cleaned["address"],
                low_stock_default_threshold=cleaned["low_stock_default_threshold"],
                near_expiry_days_threshold=cleaned["near_expiry_days_threshold"],
                status=cleaned["status"],
            )
            db.session.add(org)
            db.session.flush()

            if attach_actor and actor.organization_id is None:
                actor.organization_id = org.id

            AuditService.record(
                action="ORGANIZATION_CREATED",
                entity_type="ORGANIZATION",
                entity_id=org.id,
                actor=actor,
                organization_id=org.id,
                after_state=org.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return org

        except (DuplicateResourceError, ValidationError, AuthorizationError):
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="An organization with that code already exists."
            ) from exc
        except OperationalError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database connection error while creating organization.",
                code="DATABASE_UNAVAILABLE",
                http_status=503,
            ) from exc
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while creating organization.",
                code="DATABASE_ERROR",
                http_status=500,
            ) from exc

    @staticmethod
    def get_organization_for_user(
        actor: User,
        target_organization_id: int | None = None,
    ) -> Organization:
        """Return the organization accessible to the authenticated user."""
        return verify_organization_access(
            user=actor,
            target_organization_id=target_organization_id,
            require_active=False,
        )

    @staticmethod
    def update_organization(
        actor: User,
        payload: dict[str, Any],
        target_organization_id: int | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Organization:
        """Update organization settings or status (SUPER_ADMIN only)."""
        if actor.role != "SUPER_ADMIN":
            _record_security_audit(
                actor=actor,
                action="INSUFFICIENT_ROLE_PERMISSION",
                entity_type="ORGANIZATION",
                entity_id=target_organization_id or actor.organization_id,
                organization_id=actor.organization_id,
                reason="non_super_admin_attempted_organization_update",
            )
            raise AuthorizationError(
                message="Only a Super Admin can modify organization settings.",
                code="INSUFFICIENT_ROLE_PERMISSION",
            )

        org = verify_organization_access(
            user=actor,
            target_organization_id=target_organization_id,
            require_active=False,
        )

        # Reject attempts to pass a mismatched organization_id inside the payload body
        if (
            "organization_id" in payload
            and payload["organization_id"] is not None
            and int(payload["organization_id"]) != org.id
        ):
            _record_security_audit(
                actor=actor,
                action="UNAUTHORIZED_ORG_ACCESS",
                entity_type="ORGANIZATION",
                entity_id=payload["organization_id"],
                organization_id=org.id,
                reason="mismatched_payload_organization_id",
            )
            raise AuthorizationError(
                message="Cross-organization modification is strictly forbidden.",
                code="UNAUTHORIZED_ORG_ACCESS",
            )

        cleaned = validate_organization_update_input(payload)
        before_state = org.to_dict()

        try:
            if "code" in cleaned and cleaned["code"] != org.code:
                conflict = (
                    db.session.query(Organization)
                    .filter(
                        func.upper(Organization.code) == cleaned["code"],
                        Organization.id != org.id,
                    )
                    .first()
                )
                if conflict is not None:
                    raise DuplicateResourceError(
                        message=f"An organization with code '{cleaned['code']}' already exists.",
                        details={"code": "Duplicate organization code."},
                    )

            previous_status = org.status
            for field, val in cleaned.items():
                setattr(org, field, val)

            db.session.flush()

            action_name = "ORGANIZATION_UPDATED"
            if "status" in cleaned and cleaned["status"] != previous_status:
                action_name = (
                    "ORGANIZATION_DEACTIVATED"
                    if cleaned["status"] == "INACTIVE"
                    else "ORGANIZATION_ACTIVATED"
                )

            AuditService.record(
                action=action_name,
                entity_type="ORGANIZATION",
                entity_id=org.id,
                actor=actor,
                organization_id=org.id,
                before_state=before_state,
                after_state=org.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return org

        except (DuplicateResourceError, ValidationError, AuthorizationError):
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="An organization with that code already exists."
            ) from exc
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while updating organization.",
                code="DATABASE_ERROR",
                http_status=500,
            ) from exc


class ShopService:
    """Manages Shop creation, listing, filtering, editing, and status transitions."""

    @staticmethod
    def list_shops(
        actor: User,
        status: str | None = None,
        city: str | None = None,
        search: str | None = None,
        target_organization_id: int | None = None,
    ) -> list[Shop]:
        """List shops accessible to the current user within their authorized organization."""
        org = verify_organization_access(
            user=actor,
            target_organization_id=target_organization_id,
            require_active=False,
        )

        if actor.role == "SUPER_ADMIN":
            query = db.session.query(Shop).filter(Shop.organization_id == org.id)
        else:
            query = (
                db.session.query(Shop)
                .join(UserShopAssignment, UserShopAssignment.shop_id == Shop.id)
                .filter(
                    Shop.organization_id == org.id,
                    UserShopAssignment.user_id == actor.id,
                    UserShopAssignment.is_active.is_(True),
                )
            )

        if status:
            clean_status = status.strip().upper()
            query = query.filter(Shop.status == clean_status)

        if city:
            clean_city = city.strip().lower()
            query = query.filter(func.lower(Shop.city) == clean_city)

        if search:
            pattern = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    Shop.name.ilike(pattern),
                    Shop.code.ilike(pattern),
                    Shop.city.ilike(pattern),
                    Shop.pincode.ilike(pattern),
                )
            )

        return query.order_by(Shop.id.asc()).all()

    @staticmethod
    def get_shop_details(
        actor: User,
        shop_id: int,
        allow_inactive: bool = True,
    ) -> dict[str, Any]:
        """Fetch a single shop's details, assignments, and peer shop distances if authorized."""
        shop, assignment = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=allow_inactive,
        )
        include_assignments = actor.role == "SUPER_ADMIN" or (
            assignment is not None and assignment.assignment_role == "SHOP_OWNER"
        )
        payload = shop.to_dict(include_assignments=include_assignments)
        payload["my_assignment_role"] = (
            "SUPER_ADMIN"
            if actor.role == "SUPER_ADMIN"
            else (assignment.assignment_role if assignment else None)
        )

        # Compute Haversine distances to other active shops in the same organization
        peer_shops = (
            db.session.query(Shop)
            .filter(
                Shop.organization_id == shop.organization_id,
                Shop.id != shop.id,
                Shop.status == "ACTIVE",
            )
            .order_by(Shop.name.asc())
            .all()
        )
        nearby: list[dict[str, Any]] = []
        for peer in peer_shops:
            dist_km = haversine_distance_km(
                shop.latitude,
                shop.longitude,
                peer.latitude,
                peer.longitude,
            )
            nearby.append(
                {
                    "shop_id": peer.id,
                    "name": peer.name,
                    "code": peer.code,
                    "city": peer.city,
                    "distance_km": dist_km,
                }
            )
        payload["peer_shops"] = nearby
        return payload

    @staticmethod
    def create_shop(
        actor: User,
        payload: dict[str, Any],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Shop:
        """Create a new Shop within the Super Admin's active Organization."""
        if actor.role != "SUPER_ADMIN":
            _record_security_audit(
                actor=actor,
                action="INSUFFICIENT_ROLE_PERMISSION",
                entity_type="SHOP",
                organization_id=actor.organization_id,
                reason="non_super_admin_attempted_shop_create",
            )
            raise AuthorizationError(
                message="Only a Super Admin can create a new shop.",
                code="INSUFFICIENT_ROLE_PERMISSION",
            )

        target_org_id = payload.get("organization_id")
        if target_org_id is not None:
            try:
                target_org_id = int(target_org_id)
            except (ValueError, TypeError) as exc:
                raise ValidationError(
                    message="Invalid organization_id.",
                    details={"organization_id": "Must be a valid integer."},
                ) from exc

        org = verify_organization_access(
            user=actor,
            target_organization_id=target_org_id,
            require_active=True,
        )

        cleaned = validate_shop_create_input(
            payload=payload,
            default_low_stock=org.low_stock_default_threshold,
            default_near_expiry=org.near_expiry_days_threshold,
        )

        try:
            existing = (
                db.session.query(Shop)
                .filter(
                    Shop.organization_id == org.id,
                    func.upper(Shop.code) == cleaned["code"],
                )
                .first()
            )
            if existing is not None:
                raise DuplicateResourceError(
                    message=f"A shop with code '{cleaned['code']}' already exists in your organization.",
                    details={"code": "Shop code must be unique within the organization."},
                )

            shop = Shop(
                organization_id=org.id,
                name=cleaned["name"],
                code=cleaned["code"],
                address=cleaned["address"],
                city=cleaned["city"],
                pincode=cleaned["pincode"],
                latitude=cleaned["latitude"],
                longitude=cleaned["longitude"],
                contact_phone=cleaned["contact_phone"],
                contact_email=cleaned["contact_email"],
                low_stock_default_threshold=cleaned["low_stock_default_threshold"],
                near_expiry_days_threshold=cleaned["near_expiry_days_threshold"],
                status=cleaned["status"],
            )
            db.session.add(shop)
            db.session.flush()

            # Optional initial owner assignment if owner_user_id is supplied
            owner_user_id = payload.get("owner_user_id")
            if owner_user_id not in (None, ""):
                try:
                    owner_id = int(owner_user_id)
                except (ValueError, TypeError) as exc:
                    raise ValidationError(
                        message="Invalid owner_user_id.",
                        details={"owner_user_id": "Must be a valid user ID."},
                    ) from exc

                owner_user = db.session.get(User, owner_id)
                if owner_user is None:
                    raise ResourceNotFoundError(message=f"Owner user #{owner_id} not found.")
                if (
                    owner_user.organization_id is not None
                    and owner_user.organization_id != org.id
                ):
                    raise AuthorizationError(
                        message="Cannot assign a user from another organization.",
                        code="UNAUTHORIZED_ORG_ACCESS",
                    )
                if owner_user.organization_id is None:
                    owner_user.organization_id = org.id
                if owner_user.role != "SUPER_ADMIN":
                    owner_user.role = "SHOP_OWNER"

                existing_primary = (
                    db.session.query(UserShopAssignment)
                    .filter(
                        UserShopAssignment.user_id == owner_user.id,
                        UserShopAssignment.is_active.is_(True),
                        UserShopAssignment.is_primary.is_(True),
                    )
                    .first()
                )
                assignment = UserShopAssignment(
                    user_id=owner_user.id,
                    shop_id=shop.id,
                    assignment_role="SHOP_OWNER",
                    is_primary=(existing_primary is None),
                    is_active=True,
                    assigned_by_user_id=actor.id,
                )
                db.session.add(assignment)
                db.session.flush()

            AuditService.record(
                action="SHOP_CREATED",
                entity_type="SHOP",
                entity_id=shop.id,
                actor=actor,
                organization_id=org.id,
                shop_id=shop.id,
                after_state=shop.to_dict(include_assignments=True),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return shop

        except (
            DuplicateResourceError,
            ValidationError,
            AuthorizationError,
            ResourceNotFoundError,
        ):
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="A shop with that code already exists in your organization."
            ) from exc
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while creating shop.",
                code="DATABASE_ERROR",
                http_status=500,
            ) from exc

    @staticmethod
    def update_shop(
        actor: User,
        shop_id: int,
        payload: dict[str, Any],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Shop:
        """Update shop details or status (SUPER_ADMIN or assigned SHOP_OWNER)."""
        shop, _assignment = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=True,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
        )

        # Ensure parent organization is active before allowing shop modifications
        verify_organization_access(
            user=actor,
            target_organization_id=shop.organization_id,
            require_active=True,
        )

        # Reject payload tampering with organization_id
        if (
            "organization_id" in payload
            and payload["organization_id"] is not None
            and int(payload["organization_id"]) != shop.organization_id
        ):
            _record_security_audit(
                actor=actor,
                action="UNAUTHORIZED_ORG_ACCESS",
                entity_type="SHOP",
                entity_id=shop.id,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                reason="attempted_shop_organization_reassignment",
            )
            raise AuthorizationError(
                message="Cannot transfer a shop to another organization.",
                code="UNAUTHORIZED_ORG_ACCESS",
            )

        cleaned = validate_shop_update_input(payload)

        # Role-specific restrictions for SHOP_OWNER:
        # 1. Only SUPER_ADMIN can change shop status (activate/deactivate/suspend) or shop code.
        # 2. If the shop is currently INACTIVE or SUSPENDED, SHOP_OWNER cannot edit it.
        if actor.role != "SUPER_ADMIN":
            if shop.status != "ACTIVE":
                _record_security_audit(
                    actor=actor,
                    action="INACTIVE_SHOP_OPERATION_BLOCKED",
                    entity_type="SHOP",
                    entity_id=shop.id,
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    reason="shop_owner_attempted_edit_on_inactive_shop",
                )
                raise AuthorizationError(
                    message=f"Shop '{shop.name}' is {shop.status} and cannot be modified by a Shop Owner.",
                    code="INACTIVE_SHOP",
                )

            if "status" in cleaned and cleaned["status"] != shop.status:
                _record_security_audit(
                    actor=actor,
                    action="INSUFFICIENT_ROLE_PERMISSION",
                    entity_type="SHOP",
                    entity_id=shop.id,
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    reason="shop_owner_attempted_shop_status_change",
                )
                raise AuthorizationError(
                    message="Only a Super Admin can activate, deactivate, or suspend a shop.",
                    code="INSUFFICIENT_ROLE_PERMISSION",
                )

            if "code" in cleaned and cleaned["code"] != shop.code:
                _record_security_audit(
                    actor=actor,
                    action="INSUFFICIENT_ROLE_PERMISSION",
                    entity_type="SHOP",
                    entity_id=shop.id,
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    reason="shop_owner_attempted_shop_code_change",
                )
                raise AuthorizationError(
                    message="Only a Super Admin can change a shop's unique internal code.",
                    code="INSUFFICIENT_ROLE_PERMISSION",
                )

        # If status is not being changed to ACTIVE and the shop is currently INACTIVE/SUSPENDED,
        # block operational attribute edits unless the request is activating the shop.
        target_status = cleaned.get("status", shop.status)
        if shop.status != "ACTIVE" and target_status != "ACTIVE" and "status" not in cleaned:
            _record_security_audit(
                actor=actor,
                action="INACTIVE_SHOP_OPERATION_BLOCKED",
                entity_type="SHOP",
                entity_id=shop.id,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                reason="modification_blocked_while_shop_inactive",
            )
            raise AuthorizationError(
                message=f"Shop '{shop.name}' is currently {shop.status}. Reactivate the shop before editing operational details.",
                code="INACTIVE_SHOP",
            )

        before_state = shop.to_dict()

        try:
            if "code" in cleaned and cleaned["code"] != shop.code:
                conflict = (
                    db.session.query(Shop)
                    .filter(
                        Shop.organization_id == shop.organization_id,
                        func.upper(Shop.code) == cleaned["code"],
                        Shop.id != shop.id,
                    )
                    .first()
                )
                if conflict is not None:
                    raise DuplicateResourceError(
                        message=f"A shop with code '{cleaned['code']}' already exists in your organization.",
                        details={"code": "Shop code must be unique within the organization."},
                    )

            new_lat = cleaned.get("latitude", shop.latitude)
            new_lon = cleaned.get("longitude", shop.longitude)
            if (new_lat is None) != (new_lon is None):
                raise ValidationError(
                    message="Both latitude and longitude must be set together, or both cleared.",
                    details={"coordinates": "Provide both latitude and longitude."},
                )

            previous_status = shop.status
            for field, val in cleaned.items():
                setattr(shop, field, val)

            db.session.flush()

            action_name = "SHOP_UPDATED"
            if "status" in cleaned and cleaned["status"] != previous_status:
                action_name = (
                    "SHOP_ACTIVATED"
                    if cleaned["status"] == "ACTIVE"
                    else "SHOP_DEACTIVATED"
                )

            AuditService.record(
                action=action_name,
                entity_type="SHOP",
                entity_id=shop.id,
                actor=actor,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                before_state=before_state,
                after_state=shop.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return shop

        except (DuplicateResourceError, ValidationError, AuthorizationError):
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="A shop with that code already exists in your organization."
            ) from exc
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while updating shop.",
                code="DATABASE_ERROR",
                http_status=500,
            ) from exc


class MembershipService:
    """Manages organization users, roles, and shop-scoped assignments."""

    @staticmethod
    def _get_owner_shop_ids(user: User) -> set[int]:
        """Return set of active shop IDs within user's org where user is SHOP_OWNER."""
        rows = (
            db.session.query(UserShopAssignment.shop_id)
            .join(Shop, UserShopAssignment.shop_id == Shop.id)
            .filter(
                Shop.organization_id == user.organization_id,
                UserShopAssignment.user_id == user.id,
                UserShopAssignment.assignment_role == "SHOP_OWNER",
                UserShopAssignment.is_active.is_(True),
            )
            .all()
        )
        return {r[0] for r in rows}

    @staticmethod
    def list_users(
        actor: User,
        role_filter: str | None = None,
        shop_id_filter: int | None = None,
        target_organization_id: int | None = None,
    ) -> list[User]:
        """List users within the authorized organization or shop scope."""
        org = verify_organization_access(
            user=actor,
            target_organization_id=target_organization_id,
            require_active=False,
        )

        if actor.role == "SUPER_ADMIN":
            query = db.session.query(User).filter(User.organization_id == org.id)
            if shop_id_filter is not None:
                # Verify shop belongs to this organization
                verify_shop_access(actor, shop_id_filter, allow_inactive=True)
                query = query.join(
                    UserShopAssignment, UserShopAssignment.user_id == User.id
                ).filter(UserShopAssignment.shop_id == shop_id_filter)
        else:
            # SHOP_OWNER can only view users assigned to shops they actively own
            owned_shop_ids = MembershipService._get_owner_shop_ids(actor)
            if shop_id_filter is not None:
                verify_shop_access(
                    actor,
                    shop_id_filter,
                    allow_inactive=True,
                    required_shop_roles={"SHOP_OWNER"},
                )
                target_shop_ids = {int(shop_id_filter)}
            else:
                target_shop_ids = owned_shop_ids

            if not target_shop_ids:
                return []

            query = (
                db.session.query(User)
                .join(UserShopAssignment, UserShopAssignment.user_id == User.id)
                .filter(
                    User.organization_id == org.id,
                    UserShopAssignment.shop_id.in_(target_shop_ids),
                )
                .distinct()
            )

        if role_filter:
            query = query.filter(User.role == role_filter.strip().upper())

        return query.order_by(User.id.asc()).all()

    @staticmethod
    def get_user(actor: User, target_user_id: int) -> User:
        """Retrieve a specific user within the actor's authorized scope."""
        org = verify_organization_access(user=actor, require_active=False)
        target_user = db.session.get(User, int(target_user_id))
        if target_user is None:
            raise ResourceNotFoundError(message=f"User #{target_user_id} was not found.")

        if target_user.organization_id != org.id:
            _record_security_audit(
                actor=actor,
                action="UNAUTHORIZED_ORG_ACCESS",
                entity_type="USER",
                entity_id=target_user.id,
                organization_id=org.id,
                reason="cross_organization_user_lookup",
            )
            raise AuthorizationError(
                message="Cannot access a user belonging to another organization.",
                code="UNAUTHORIZED_ORG_ACCESS",
            )

        if actor.role == "SHOP_OWNER" and target_user.id != actor.id:
            owned_shop_ids = MembershipService._get_owner_shop_ids(actor)
            shared = (
                db.session.query(UserShopAssignment)
                .filter(
                    UserShopAssignment.user_id == target_user.id,
                    UserShopAssignment.shop_id.in_(owned_shop_ids),
                )
                .first()
                if owned_shop_ids
                else None
            )
            if shared is None:
                _record_security_audit(
                    actor=actor,
                    action="UNAUTHORIZED_SHOP_ACCESS",
                    entity_type="USER",
                    entity_id=target_user.id,
                    organization_id=org.id,
                    reason="shop_owner_attempted_unassigned_user_lookup",
                )
                raise AuthorizationError(
                    message="You can only view users assigned to your authorized shops.",
                    code="UNAUTHORIZED_SHOP_ACCESS",
                )

        return target_user

    @staticmethod
    def create_member_user(
        actor: User,
        payload: dict[str, Any],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> User:
        """Create a new user within the actor's organization and optionally assign to a shop."""
        if actor.role != "SUPER_ADMIN":
            _record_security_audit(
                actor=actor,
                action="INSUFFICIENT_ROLE_PERMISSION",
                entity_type="USER",
                organization_id=actor.organization_id,
                reason="non_super_admin_attempted_user_creation",
            )
            raise AuthorizationError(
                message="Only Super Admins can create user accounts.",
                code="INSUFFICIENT_ROLE_PERMISSION",
            )

        target_org_id = payload.get("organization_id")
        if target_org_id is not None:
            try:
                target_org_id = int(target_org_id)
            except (ValueError, TypeError) as exc:
                raise ValidationError(
                    message="Invalid organization_id.",
                    details={"organization_id": "Must be a valid integer."},
                ) from exc

        org = verify_organization_access(
            user=actor,
            target_organization_id=target_org_id,
            require_active=True,
        )

        cleaned = validate_user_creation_input(
            username=payload.get("username"),
            email=payload.get("email"),
            password=payload.get("password"),
            full_name=payload.get("full_name"),
            role=payload.get("role", "SHOP_OWNER"),
            phone=payload.get("phone"),
        )
        requested_role = str(cleaned["role"])

        raw_shop_id = payload.get("shop_id")
        target_shop: Shop | None = None
        if raw_shop_id not in (None, ""):
            try:
                parsed_shop_id = int(raw_shop_id)
            except (ValueError, TypeError) as exc:
                raise ValidationError(
                    message="Invalid shop_id.",
                    details={"shop_id": "Must be a valid integer."},
                ) from exc
            target_shop, _ = verify_shop_access(
                user=actor,
                shop_id=parsed_shop_id,
                allow_inactive=False,
                required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
            )

        try:
            existing = (
                db.session.query(User)
                .filter(
                    or_(
                        func.lower(User.username) == cleaned["username"],
                        func.lower(User.email) == cleaned["email"],
                    )
                )
                .first()
            )
            if existing is not None:
                conflict_field = (
                    "username"
                    if existing.username.lower() == cleaned["username"]
                    else "email"
                )
                raise DuplicateResourceError(
                    message=f"A user with that {conflict_field} already exists.",
                    details={conflict_field: f"Duplicate {conflict_field}."},
                )

            user = User(
                organization_id=org.id,
                username=str(cleaned["username"]),
                email=str(cleaned["email"]),
                full_name=str(cleaned["full_name"]),
                role=requested_role,
                phone=cleaned["phone"],
                is_active=True,
            )
            user.set_password(str(cleaned["password"]))
            db.session.add(user)
            db.session.flush()

            AuditService.record(
                action="USER_CREATED",
                entity_type="USER",
                entity_id=user.id,
                actor=actor,
                organization_id=org.id,
                shop_id=target_shop.id if target_shop else None,
                after_state=user.to_dict(include_assignments=False),
                ip_address=ip_address,
                user_agent=user_agent,
            )

            if target_shop is not None and requested_role == "SHOP_OWNER":
                assignment = UserShopAssignment(
                    user_id=user.id,
                    shop_id=target_shop.id,
                    assignment_role="SHOP_OWNER",
                    is_primary=True,
                    is_active=True,
                    assigned_by_user_id=actor.id,
                )
                db.session.add(assignment)
                db.session.flush()

                AuditService.record(
                    action="SHOP_ASSIGNMENT_CREATED",
                    entity_type="USER_SHOP_ASSIGNMENT",
                    entity_id=assignment.id,
                    actor=actor,
                    organization_id=org.id,
                    shop_id=target_shop.id,
                    after_state=assignment.to_dict(),
                    ip_address=ip_address,
                    user_agent=user_agent,
                )

            db.session.commit()
            return user

        except (
            DuplicateResourceError,
            ValidationError,
            AuthorizationError,
            ResourceNotFoundError,
        ):
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="A user with that username or email already exists."
            ) from exc
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while creating user.",
                code="DATABASE_ERROR",
                http_status=500,
            ) from exc

    @staticmethod
    def update_member_user(
        actor: User,
        target_user_id: int,
        payload: dict[str, Any],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> User:
        """Update user profile, active status, or role within authorized boundaries."""
        org = verify_organization_access(user=actor, require_active=True)
        target_user = db.session.get(User, int(target_user_id))
        if target_user is None:
            raise ResourceNotFoundError(message=f"User #{target_user_id} was not found.")

        if target_user.organization_id != org.id:
            _record_security_audit(
                actor=actor,
                action="UNAUTHORIZED_ORG_ACCESS",
                entity_type="USER",
                entity_id=target_user.id,
                organization_id=org.id,
                reason="cross_organization_user_update_attempt",
            )
            raise AuthorizationError(
                message="Cannot modify a user belonging to another organization.",
                code="UNAUTHORIZED_ORG_ACCESS",
            )

        cleaned = validate_user_update_input(payload)

        if actor.role == "SHOP_OWNER":
            if target_user.id != actor.id:
                _record_security_audit(
                    actor=actor,
                    action="INSUFFICIENT_ROLE_PERMISSION",
                    entity_type="USER",
                    entity_id=target_user.id,
                    organization_id=org.id,
                    reason="shop_owner_attempted_to_modify_other_user",
                )
                raise AuthorizationError(
                    message="Shop Owners can only manage their own account.",
                    code="INSUFFICIENT_ROLE_PERMISSION",
                )

            if "role" in cleaned and cleaned["role"] != target_user.role:
                _record_security_audit(
                    actor=actor,
                    action="INSUFFICIENT_ROLE_PERMISSION",
                    entity_type="USER",
                    entity_id=target_user.id,
                    organization_id=org.id,
                    reason="shop_owner_attempted_role_change",
                )
                raise AuthorizationError(
                    message="Shop Owners cannot change global user roles.",
                    code="INSUFFICIENT_ROLE_PERMISSION",
                )

        # Prevent self-lockout for Super Admin
        if target_user.id == actor.id:
            if cleaned.get("is_active") is False:
                raise ValidationError(
                    message="You cannot deactivate your own active account.",
                    details={"is_active": "Self-deactivation is not permitted."},
                )
            if "role" in cleaned and cleaned["role"] != actor.role:
                raise AuthorizationError(
                    message="You cannot change your own global role.",
                    code="INSUFFICIENT_ROLE_PERMISSION",
                )

        before_state = target_user.to_dict(include_assignments=False)

        try:
            if "email" in cleaned and cleaned["email"] != target_user.email.lower():
                conflict = (
                    db.session.query(User)
                    .filter(
                        func.lower(User.email) == cleaned["email"],
                        User.id != target_user.id,
                    )
                    .first()
                )
                if conflict is not None:
                    raise DuplicateResourceError(
                        message="A user with that email address already exists.",
                        details={"email": "Duplicate email."},
                    )

            session_invalidation_needed = False
            if "is_active" in cleaned and cleaned["is_active"] != target_user.is_active:
                session_invalidation_needed = True
            if "role" in cleaned and cleaned["role"] != target_user.role:
                session_invalidation_needed = True

            for field, val in cleaned.items():
                setattr(target_user, field, val)

            if session_invalidation_needed:
                target_user.rotate_session_token()

            db.session.flush()

            AuditService.record(
                action="USER_UPDATED",
                entity_type="USER",
                entity_id=target_user.id,
                actor=actor,
                organization_id=org.id,
                before_state=before_state,
                after_state=target_user.to_dict(include_assignments=False),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return target_user

        except (DuplicateResourceError, ValidationError, AuthorizationError):
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="A user with that email already exists."
            ) from exc
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while updating user.",
                code="DATABASE_ERROR",
                http_status=500,
            ) from exc

    @staticmethod
    def assign_user_to_shop(
        actor: User,
        shop_id: int,
        payload: dict[str, Any],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> UserShopAssignment:
        """Create or update a user's assignment to a specific shop."""
        # Require both organization and shop to be ACTIVE for new assignments
        shop, _actor_assignment = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=False,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
        )

        cleaned = validate_shop_assignment_input(payload)
        target_user = db.session.get(User, cleaned["user_id"])
        if target_user is None:
            raise ResourceNotFoundError(
                message=f"User #{cleaned['user_id']} was not found."
            )

        # Enforce strict organization isolation on target user
        if (
            target_user.organization_id is not None
            and target_user.organization_id != shop.organization_id
        ):
            _record_security_audit(
                actor=actor,
                action="UNAUTHORIZED_ORG_ACCESS",
                entity_type="USER_SHOP_ASSIGNMENT",
                entity_id=target_user.id,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                reason="cross_organization_user_shop_assignment_attempt",
            )
            raise AuthorizationError(
                message="Cannot assign a user belonging to a different organization.",
                code="UNAUTHORIZED_ORG_ACCESS",
            )

        if target_user.role == "SUPER_ADMIN":
            raise ValidationError(
                message="Super Admins already have organization-wide access and do not use shop assignments.",
                details={"user_id": "Cannot create shop assignment for SUPER_ADMIN."},
            )

        assignment_role = cleaned["assignment_role"]

        # Only Super Admins can assign users to shops
        if actor.role != "SUPER_ADMIN":
            _record_security_audit(
                actor=actor,
                action="INSUFFICIENT_ROLE_PERMISSION",
                entity_type="USER_SHOP_ASSIGNMENT",
                entity_id=target_user.id,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                reason="non_super_admin_attempted_shop_assignment",
            )
            raise AuthorizationError(
                message="Only Super Admins can assign users to shops.",
                code="INSUFFICIENT_ROLE_PERMISSION",
            )

        try:
            if target_user.organization_id is None:
                target_user.organization_id = shop.organization_id

            # Synchronize global role if Super Admin assigns a user as SHOP_OWNER
            if actor.role == "SUPER_ADMIN" and assignment_role == "SHOP_OWNER":
                target_user.role = "SHOP_OWNER"

            is_primary = bool(cleaned["is_primary"])
            is_active = bool(cleaned["is_active"])

            existing = (
                db.session.query(UserShopAssignment)
                .filter(
                    UserShopAssignment.user_id == target_user.id,
                    UserShopAssignment.shop_id == shop.id,
                )
                .first()
            )

            # Ensure partial unique index uq_user_active_primary_shop is satisfied
            if is_active and is_primary:
                other_primaries = (
                    db.session.query(UserShopAssignment)
                    .filter(
                        UserShopAssignment.user_id == target_user.id,
                        UserShopAssignment.is_active.is_(True),
                        UserShopAssignment.is_primary.is_(True),
                    )
                    .all()
                )
                for other in other_primaries:
                    if existing is None or other.id != existing.id:
                        other.is_primary = False
                db.session.flush()

            if existing is not None:
                if actor.role != "SUPER_ADMIN" and existing.assignment_role == "SHOP_OWNER":
                    raise AuthorizationError(
                        message="Shop Owners cannot modify a Shop Owner assignment.",
                        code="INSUFFICIENT_ROLE_PERMISSION",
                    )
                before_state = existing.to_dict()
                existing.assignment_role = assignment_role
                existing.is_primary = is_primary
                existing.is_active = is_active
                existing.assigned_by_user_id = actor.id
                db.session.flush()

                AuditService.record(
                    action="SHOP_ASSIGNMENT_UPDATED",
                    entity_type="USER_SHOP_ASSIGNMENT",
                    entity_id=existing.id,
                    actor=actor,
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    before_state=before_state,
                    after_state=existing.to_dict(),
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                db.session.commit()
                return existing

            assignment = UserShopAssignment(
                user_id=target_user.id,
                shop_id=shop.id,
                assignment_role=assignment_role,
                is_primary=is_primary,
                is_active=is_active,
                assigned_by_user_id=actor.id,
            )
            db.session.add(assignment)
            db.session.flush()

            AuditService.record(
                action="SHOP_ASSIGNMENT_CREATED",
                entity_type="USER_SHOP_ASSIGNMENT",
                entity_id=assignment.id,
                actor=actor,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                after_state=assignment.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return assignment

        except (
            DuplicateResourceError,
            ValidationError,
            AuthorizationError,
            ResourceNotFoundError,
        ):
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="Could not save shop assignment due to a uniqueness conflict."
            ) from exc
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while saving shop assignment.",
                code="DATABASE_ERROR",
                http_status=500,
            ) from exc

    @staticmethod
    def update_shop_assignment(
        actor: User,
        shop_id: int,
        assignment_id: int,
        payload: dict[str, Any],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> UserShopAssignment:
        """Update or revoke an existing UserShopAssignment."""
        shop, _actor_assignment = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=False,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
        )

        assignment = db.session.get(UserShopAssignment, int(assignment_id))
        if assignment is None or assignment.shop_id != shop.id:
            raise ResourceNotFoundError(
                message=f"Assignment #{assignment_id} was not found for shop #{shop.id}."
            )

        if actor.role != "SUPER_ADMIN":
            _record_security_audit(
                actor=actor,
                action="INSUFFICIENT_ROLE_PERMISSION",
                entity_type="USER_SHOP_ASSIGNMENT",
                entity_id=assignment.id,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                reason="non_super_admin_attempted_to_modify_shop_assignment",
            )
            raise AuthorizationError(
                message="Only a Super Admin can modify or revoke a shop assignment.",
                code="INSUFFICIENT_ROLE_PERMISSION",
            )

        merged_payload = {
            "user_id": assignment.user_id,
            "assignment_role": payload.get("assignment_role", assignment.assignment_role),
            "is_primary": payload.get("is_primary", assignment.is_primary),
            "is_active": payload.get("is_active", assignment.is_active),
        }
        cleaned = validate_shop_assignment_input(merged_payload)

        before_state = assignment.to_dict()

        try:
            if cleaned["is_active"] and cleaned["is_primary"]:
                other_primaries = (
                    db.session.query(UserShopAssignment)
                    .filter(
                        UserShopAssignment.user_id == assignment.user_id,
                        UserShopAssignment.id != assignment.id,
                        UserShopAssignment.is_active.is_(True),
                        UserShopAssignment.is_primary.is_(True),
                    )
                    .all()
                )
                for other in other_primaries:
                    other.is_primary = False
                db.session.flush()

            assignment.assignment_role = cleaned["assignment_role"]
            assignment.is_primary = cleaned["is_primary"]
            assignment.is_active = cleaned["is_active"]
            assignment.assigned_by_user_id = actor.id

            if (
                actor.role == "SUPER_ADMIN"
                and cleaned["assignment_role"] == "SHOP_OWNER"
                and assignment.user is not None
            ):
                assignment.user.role = "SHOP_OWNER"

            db.session.flush()

            AuditService.record(
                action="SHOP_ASSIGNMENT_UPDATED",
                entity_type="USER_SHOP_ASSIGNMENT",
                entity_id=assignment.id,
                actor=actor,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                before_state=before_state,
                after_state=assignment.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return assignment

        except (ValidationError, AuthorizationError, DuplicateResourceError):
            db.session.rollback()
            raise
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while updating shop assignment.",
                code="DATABASE_ERROR",
                http_status=500,
            ) from exc
