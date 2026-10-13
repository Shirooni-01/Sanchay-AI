"""Authentication and user lifecycle service for Phase 1."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from werkzeug.security import check_password_hash

from app.extensions import db
from app.models.user import DUMMY_PASSWORD_HASH, User
from app.services.audit_service import AuditService
from app.utils.errors import (
    AuthenticationError,
    DatabaseConflictError,
    DuplicateResourceError,
    ValidationError,
)
from app.utils.validators import (
    validate_login_input,
    validate_password_strength,
    validate_user_creation_input,
)

GENERIC_AUTH_ERROR_MESSAGE = "Invalid username/email or password."


class AuthService:
    """Encapsulates transactional authentication, session rotation, and user creation."""

    @staticmethod
    def authenticate_user(
        identifier: str | None,
        password: str | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> User:
        """Validate credentials and return the authenticated active User.

        Always returns a generic error message when credentials are invalid or
        when the account does not exist or is deactivated, preventing account enumeration.
        """
        clean_identifier, raw_password = validate_login_input(identifier, password)

        try:
            user: User | None = (
                db.session.query(User)
                .filter(
                    or_(
                        func.lower(User.username) == clean_identifier,
                        func.lower(User.email) == clean_identifier,
                    )
                )
                .first()
            )

            if user is None:
                # Perform dummy hash verification to mitigate timing-based account enumeration
                check_password_hash(DUMMY_PASSWORD_HASH, raw_password)
                AuditService.record(
                    action="USER_LOGIN_FAILED",
                    entity_type="USER",
                    entity_id=None,
                    actor=None,
                    after_state={"reason": "unknown_identifier"},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                db.session.commit()
                raise AuthenticationError(message=GENERIC_AUTH_ERROR_MESSAGE)

            password_valid = user.check_password(raw_password)
            if not password_valid:
                AuditService.record(
                    action="USER_LOGIN_FAILED",
                    entity_type="USER",
                    entity_id=user.id,
                    actor=user,
                    after_state={"reason": "invalid_password"},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                db.session.commit()
                raise AuthenticationError(message=GENERIC_AUTH_ERROR_MESSAGE)

            if not user.is_active:
                AuditService.record(
                    action="USER_LOGIN_BLOCKED_INACTIVE",
                    entity_type="USER",
                    entity_id=user.id,
                    actor=user,
                    after_state={"reason": "inactive_account"},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                db.session.commit()
                raise AuthenticationError(message=GENERIC_AUTH_ERROR_MESSAGE)

            # Rotate server-side session token on login (mitigates session fixation)
            user.rotate_session_token()
            user.last_login_at = datetime.now(timezone.utc)

            AuditService.record(
                action="USER_LOGIN_SUCCESS",
                entity_type="USER",
                entity_id=user.id,
                actor=user,
                after_state={"username": user.username, "role": user.role},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return user

        except AuthenticationError:
            raise
        except OperationalError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database connection error during authentication.",
                code="DATABASE_UNAVAILABLE",
                http_status=503,
            ) from exc
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error during authentication.",
                code="DATABASE_ERROR",
                http_status=500,
            ) from exc

    @staticmethod
    def invalidate_user_session(
        user: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Rotate the user's server-side session token on logout and record audit log."""
        try:
            user.rotate_session_token()
            AuditService.record(
                action="USER_LOGOUT",
                entity_type="USER",
                entity_id=user.id,
                actor=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Failed to invalidate session in database.",
                code="DATABASE_ERROR",
                http_status=500,
            ) from exc

    @staticmethod
    def change_password(
        user: User,
        current_password: str | None,
        new_password: str | None,
        confirm_password: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> User:
        """Verify current password, enforce strength, update hash, and rotate session token."""
        if not current_password or not isinstance(current_password, str):
            raise ValidationError(
                message="Current password is required.",
                details={"current_password": "Current password cannot be empty."},
            )

        if not user.check_password(current_password):
            AuditService.record(
                action="USER_PASSWORD_CHANGE_FAILED",
                entity_type="USER",
                entity_id=user.id,
                actor=user,
                after_state={"reason": "incorrect_current_password"},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            raise AuthenticationError(
                message="Current password is incorrect.",
                code="INVALID_CURRENT_PASSWORD",
            )

        validated_new = validate_password_strength(new_password, field_name="new_password")

        if confirm_password is not None and validated_new != confirm_password:
            raise ValidationError(
                message="New password and confirmation do not match.",
                details={"confirm_password": "Passwords must match."},
            )

        if current_password == validated_new:
            raise ValidationError(
                message="New password must be different from your current password.",
                details={"new_password": "Choose a different password."},
            )

        try:
            user.set_password(validated_new)
            user.rotate_session_token()
            AuditService.record(
                action="USER_PASSWORD_CHANGED",
                entity_type="USER",
                entity_id=user.id,
                actor=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return user
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while updating password.",
                code="DATABASE_ERROR",
                http_status=500,
            ) from exc

    @staticmethod
    def create_user(
        username: str | None,
        email: str | None,
        password: str | None,
        full_name: str | None,
        role: str = "STAFF",
        phone: str | None = None,
        is_active: bool = True,
        organization_id: int | None = None,
        actor: User | None = None,
    ) -> User:
        """Create a validated user account with a hashed password and audit record."""
        cleaned = validate_user_creation_input(
            username=username,
            email=email,
            password=password,
            full_name=full_name,
            role=role,
            phone=phone,
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

            resolved_org_id = (
                organization_id
                if organization_id is not None
                else (actor.organization_id if actor is not None else None)
            )

            user = User(
                organization_id=resolved_org_id,
                username=str(cleaned["username"]),
                email=str(cleaned["email"]),
                full_name=str(cleaned["full_name"]),
                role=str(cleaned["role"]),
                phone=cleaned["phone"],
                is_active=bool(is_active),
            )
            user.set_password(str(cleaned["password"]))
            db.session.add(user)
            db.session.flush()

            AuditService.record(
                action="USER_CREATED",
                entity_type="USER",
                entity_id=user.id,
                actor=actor,
                organization_id=user.organization_id,
                after_state={
                    "username": user.username,
                    "email": user.email,
                    "role": user.role,
                    "organization_id": user.organization_id,
                    "is_active": user.is_active,
                },
            )
            db.session.commit()
            return user

        except DuplicateResourceError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="A user with that username or email already exists."
            ) from exc
        except OperationalError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database connection error while creating user.",
                code="DATABASE_UNAVAILABLE",
                http_status=503,
            ) from exc
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while creating user.",
                code="DATABASE_ERROR",
                http_status=500,
            ) from exc
