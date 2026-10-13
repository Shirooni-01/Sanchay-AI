"""User model for authentication, session management, and Phase 2 RBAC."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager

if TYPE_CHECKING:
    from app.models.organization import Shop, UserShopAssignment

PASSWORD_HASH_METHOD = "scrypt"
DUMMY_PASSWORD_HASH = generate_password_hash(
    "sanchay-constant-time-dummy-password-987654321",
    method=PASSWORD_HASH_METHOD,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_session_token() -> str:
    return secrets.token_hex(32)


class User(UserMixin, db.Model):
    """Represents an authenticated user account in Sanchay AI."""

    __tablename__ = "users"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    organization_id = db.Column(
        db.BigInteger,
        db.ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    username = db.Column(db.String(64), nullable=False, unique=True)
    email = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    role = db.Column(
        db.String(20),
        nullable=False,
        default="STAFF",
        server_default="STAFF",
    )
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.text("true"),
    )
    session_token = db.Column(
        db.String(64),
        nullable=False,
        default=_generate_session_token,
    )
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)
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

    organization = db.relationship("Organization", back_populates="users")
    shop_assignments = db.relationship(
        "UserShopAssignment",
        foreign_keys="UserShopAssignment.user_id",
        back_populates="user",
        lazy="dynamic",
        cascade="save-update, merge",
    )

    __table_args__ = (
        db.CheckConstraint(
            "role IN ('SUPER_ADMIN', 'SHOP_OWNER', 'STAFF')",
            name="chk_users_role",
        ),
        db.Index("idx_users_role_active", "role", "is_active"),
        db.Index("idx_users_org_role", "organization_id", "role"),
    )

    def set_password(self, raw_password: str) -> None:
        """Hash and store the user's password using scrypt."""
        if not raw_password:
            raise ValueError("Password cannot be empty.")
        self.password_hash = generate_password_hash(
            raw_password,
            method=PASSWORD_HASH_METHOD,
        )

    def check_password(self, raw_password: str) -> bool:
        """Verify a candidate plaintext password against the stored scrypt hash."""
        if not raw_password or not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw_password)

    def rotate_session_token(self) -> str:
        """Rotate the server-side session token to invalidate prior session cookies."""
        self.session_token = _generate_session_token()
        return self.session_token

    def get_id(self) -> str:
        """Return composite user ID and session token for Flask-Login verification."""
        return f"{self.id}:{self.session_token}"

    def get_active_shop_assignments(self) -> list[UserShopAssignment]:
        """Return active shop assignments for this user within their organization."""
        from app.models.organization import Shop, UserShopAssignment

        query = (
            self.shop_assignments.join(Shop, UserShopAssignment.shop_id == Shop.id)
            .filter(UserShopAssignment.is_active.is_(True))
        )
        if self.organization_id is not None:
            query = query.filter(Shop.organization_id == self.organization_id)
        return query.order_by(
            UserShopAssignment.is_primary.desc(),
            UserShopAssignment.id.asc(),
        ).all()

    def get_primary_assignment(self) -> UserShopAssignment | None:
        """Return the user's primary active shop assignment, if any."""
        assignments = self.get_active_shop_assignments()
        return assignments[0] if assignments else None

    def get_accessible_shops(self, include_inactive: bool = True) -> list[Shop]:
        """Return all shops in the user's organization that the user is allowed to access."""
        from app.models.organization import Shop, UserShopAssignment

        if not self.is_active or self.organization_id is None:
            return []

        if self.role == "SUPER_ADMIN":
            query = db.session.query(Shop).filter(
                Shop.organization_id == self.organization_id
            )
            if not include_inactive:
                query = query.filter(Shop.status == "ACTIVE")
            return query.order_by(Shop.id.asc()).all()

        query = (
            db.session.query(Shop)
            .join(UserShopAssignment, UserShopAssignment.shop_id == Shop.id)
            .filter(
                Shop.organization_id == self.organization_id,
                UserShopAssignment.user_id == self.id,
                UserShopAssignment.is_active.is_(True),
            )
        )
        if not include_inactive:
            query = query.filter(Shop.status == "ACTIVE")
        return query.order_by(Shop.id.asc()).all()

    def get_shop_assignment(
        self,
        shop_id: int,
        require_active: bool = True,
    ) -> UserShopAssignment | None:
        """Return the UserShopAssignment for the given shop_id if present."""
        from app.models.organization import UserShopAssignment

        query = self.shop_assignments.filter(UserShopAssignment.shop_id == shop_id)
        if require_active:
            query = query.filter(UserShopAssignment.is_active.is_(True))
        return query.first()

    def to_dict(self, include_assignments: bool = True) -> dict[str, Any]:
        """Serialize safe public user fields (never exposes password_hash or session_token)."""
        data: dict[str, Any] = {
            "id": self.id,
            "organization_id": self.organization_id,
            "organization_name": (
                self.organization.name if self.organization is not None else None
            ),
            "organization_code": (
                self.organization.code if self.organization is not None else None
            ),
            "organization_status": (
                self.organization.status if self.organization is not None else None
            ),
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "phone": self.phone,
            "role": self.role,
            "is_active": self.is_active,
            "last_login_at": (
                self.last_login_at.isoformat() if self.last_login_at else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_assignments:
            active_assignments = self.get_active_shop_assignments()
            data["assigned_shops"] = [
                {
                    "assignment_id": a.id,
                    "shop_id": a.shop_id,
                    "shop_name": a.shop.name if a.shop else None,
                    "shop_code": a.shop.code if a.shop else None,
                    "shop_status": a.shop.status if a.shop else None,
                    "assignment_role": a.assignment_role,
                    "is_primary": a.is_primary,
                    "is_active": a.is_active,
                }
                for a in active_assignments
            ]
        return data


@login_manager.user_loader
def load_user(composite_id: str) -> User | None:
    """Load an active user and verify their server-side session token."""
    if not composite_id or ":" not in str(composite_id):
        return None
    raw_uid, token = str(composite_id).split(":", 1)
    try:
        user_id = int(raw_uid)
    except ValueError:
        return None

    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    if not user.session_token or not secrets.compare_digest(user.session_token, token):
        return None
    return user
