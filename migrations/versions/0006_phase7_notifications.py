"""Phase 7 schema: notifications table.

Revision ID: 0006_phase7_notifications
Revises: 0005_phase6_transfers
Create Date: 2026-10-13 17:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006_phase7_notifications"
down_revision = "0005_phase6_transfers"
branch_labels = None
depends_on = None

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


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("recipient_user_id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=True),
        sa.Column("notification_type", sa.String(length=48), nullable=False),
        sa.Column(
            "severity",
            sa.String(length=16),
            server_default="INFO",
            nullable=False,
        ),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "is_read",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("related_entity_type", sa.String(length=32), nullable=True),
        sa.Column("related_entity_id", sa.BigInteger(), nullable=True),
        sa.Column("dedup_key", sa.String(length=160), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.id"],
            name="fk_notifications_recipient_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_notifications_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["shops.id"],
            name="fk_notifications_shop_id_shops",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.CheckConstraint(
            f"severity IN {VALID_NOTIFICATION_SEVERITIES!r}",
            name="ck_notifications_severity_valid",
        ),
        sa.CheckConstraint(
            f"notification_type IN {VALID_NOTIFICATION_TYPES!r}",
            name="ck_notifications_type_valid",
        ),
        sa.UniqueConstraint(
            "recipient_user_id",
            "dedup_key",
            name="uq_notifications_user_dedup",
        ),
    )

    op.create_index(
        "idx_notifications_user_unread",
        "notifications",
        ["recipient_user_id", "is_read", "created_at"],
    )
    op.create_index(
        "idx_notifications_user_created",
        "notifications",
        ["recipient_user_id", "created_at"],
    )
    op.create_index(
        "idx_notifications_shop_type",
        "notifications",
        ["shop_id", "notification_type"],
    )


def downgrade() -> None:
    op.drop_index("idx_notifications_shop_type", table_name="notifications")
    op.drop_index("idx_notifications_user_created", table_name="notifications")
    op.drop_index("idx_notifications_user_unread", table_name="notifications")
    op.drop_table("notifications")
