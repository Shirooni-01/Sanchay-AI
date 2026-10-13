"""Phase 2 schema: organizations, shops, user_shop_assignments, and foreign keys on users/audit_logs.

Revision ID: 0002_phase2_org_shops
Revises: 0001_phase1_users_audit
Create Date: 2026-10-12 23:45:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_phase2_org_shops"
down_revision = "0001_phase1_users_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("contact_email", sa.String(length=150), nullable=True),
        sa.Column("contact_phone", sa.String(length=20), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column(
            "low_stock_default_threshold",
            sa.Integer(),
            server_default=sa.text("10"),
            nullable=False,
        ),
        sa.Column(
            "near_expiry_days_threshold",
            sa.Integer(),
            server_default=sa.text("15"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name="ck_organizations_status_valid",
        ),
        sa.CheckConstraint(
            "low_stock_default_threshold >= 0",
            name="ck_organizations_low_stock_nonnegative",
        ),
        sa.CheckConstraint(
            "near_expiry_days_threshold >= 1",
            name="ck_organizations_near_expiry_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_organizations_code"),
    )
    op.create_index(
        "idx_organizations_status",
        "organizations",
        ["status"],
        unique=False,
    )

    op.create_table(
        "shops",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=False),
        sa.Column("pincode", sa.String(length=12), nullable=False),
        sa.Column("latitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("contact_phone", sa.String(length=20), nullable=False),
        sa.Column("contact_email", sa.String(length=150), nullable=True),
        sa.Column(
            "low_stock_default_threshold",
            sa.Integer(),
            server_default=sa.text("10"),
            nullable=False,
        ),
        sa.Column(
            "near_expiry_days_threshold",
            sa.Integer(),
            server_default=sa.text("15"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')",
            name="ck_shops_status_valid",
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_shops_latitude_bounds",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_shops_longitude_bounds",
        ),
        sa.CheckConstraint(
            "low_stock_default_threshold >= 0",
            name="ck_shops_low_stock_nonnegative",
        ),
        sa.CheckConstraint(
            "near_expiry_days_threshold >= 1",
            name="ck_shops_near_expiry_positive",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_shops_organization_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "code", name="uq_shops_org_code"),
    )
    op.create_index(
        "idx_shops_org_status",
        "shops",
        ["organization_id", "status"],
        unique=False,
    )

    op.create_table(
        "user_shop_assignments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("assignment_role", sa.String(length=20), nullable=False),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("assigned_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "assignment_role IN ('SHOP_OWNER', 'STAFF')",
            name="ck_user_shop_assignments_role_valid",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_usa_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["shops.id"],
            name="fk_usa_shop_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"],
            ["users.id"],
            name="fk_usa_assigned_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "shop_id", name="uq_user_shop"),
    )
    op.create_index(
        "idx_usa_shop_role_active",
        "user_shop_assignments",
        ["shop_id", "assignment_role", "is_active"],
        unique=False,
    )
    op.create_index(
        "uq_user_active_primary_shop",
        "user_shop_assignments",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_active = TRUE AND is_primary = TRUE"),
    )

    # Extend existing users table without dropping data
    op.add_column(
        "users",
        sa.Column("organization_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_organization_id",
        "users",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_users_org_role",
        "users",
        ["organization_id", "role"],
        unique=False,
    )

    # Extend existing audit_logs table without dropping data
    op.add_column(
        "audit_logs",
        sa.Column("organization_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("shop_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_audit_logs_organization_id",
        "audit_logs",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_audit_logs_shop_id",
        "audit_logs",
        "shops",
        ["shop_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_audit_logs_org_created",
        "audit_logs",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_audit_logs_shop_created",
        "audit_logs",
        ["shop_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_audit_logs_shop_created", table_name="audit_logs")
    op.drop_index("idx_audit_logs_org_created", table_name="audit_logs")
    op.drop_constraint("fk_audit_logs_shop_id", "audit_logs", type_="foreignkey")
    op.drop_constraint("fk_audit_logs_organization_id", "audit_logs", type_="foreignkey")
    op.drop_column("audit_logs", "shop_id")
    op.drop_column("audit_logs", "organization_id")

    op.drop_index("idx_users_org_role", table_name="users")
    op.drop_constraint("fk_users_organization_id", "users", type_="foreignkey")
    op.drop_column("users", "organization_id")

    op.drop_index("uq_user_active_primary_shop", table_name="user_shop_assignments")
    op.drop_index("idx_usa_shop_role_active", table_name="user_shop_assignments")
    op.drop_table("user_shop_assignments")

    op.drop_index("idx_shops_org_status", table_name="shops")
    op.drop_table("shops")

    op.drop_index("idx_organizations_status", table_name="organizations")
    op.drop_table("organizations")
