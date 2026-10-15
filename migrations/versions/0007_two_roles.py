"""Simplify user roles to two roles: SUPER_ADMIN and SHOP_OWNER.

Revision ID: 0007_two_roles
Revises: 0006_phase7_notifications
Create Date: 2026-10-15 20:45:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0007_two_roles"
down_revision = "0006_phase7_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Migrate any existing 'STAFF' users to 'SHOP_OWNER'
    op.execute("UPDATE users SET role = 'SHOP_OWNER' WHERE role = 'STAFF'")
    op.execute("UPDATE user_shop_assignments SET assignment_role = 'SHOP_OWNER' WHERE assignment_role = 'STAFF'")

    # 2. Update users.role server default
    op.alter_column(
        "users",
        "role",
        server_default="SHOP_OWNER",
        existing_type=sa.String(length=20),
        existing_nullable=False,
    )

    # 3. Update users table check constraint
    op.drop_constraint("chk_users_role", "users", type_="check")
    op.create_check_constraint(
        "chk_users_role",
        "users",
        "role IN ('SUPER_ADMIN', 'SHOP_OWNER')",
    )

    # 4. Update user_shop_assignments table check constraint
    op.drop_constraint("ck_user_shop_assignments_role_valid", "user_shop_assignments", type_="check")
    op.create_check_constraint(
        "ck_user_shop_assignments_role_valid",
        "user_shop_assignments",
        "assignment_role IN ('SHOP_OWNER')",
    )


def downgrade() -> None:
    # Restore users.role server default
    op.alter_column(
        "users",
        "role",
        server_default="STAFF",
        existing_type=sa.String(length=20),
        existing_nullable=False,
    )

    # Restore users check constraint
    op.drop_constraint("chk_users_role", "users", type_="check")
    op.create_check_constraint(
        "chk_users_role",
        "users",
        "role IN ('SUPER_ADMIN', 'SHOP_OWNER', 'STAFF')",
    )

    # Restore user_shop_assignments check constraint
    op.drop_constraint("ck_user_shop_assignments_role_valid", "user_shop_assignments", type_="check")
    op.create_check_constraint(
        "ck_user_shop_assignments_role_valid",
        "user_shop_assignments",
        "assignment_role IN ('SHOP_OWNER', 'STAFF')",
    )
