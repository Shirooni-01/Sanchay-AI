"""Phase 6 schema: transfer_requests and transfer_status_logs.

Revision ID: 0005_phase6_transfers
Revises: 0004_phase4_sales_billing
Create Date: 2026-10-13 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_phase6_transfers"
down_revision = "0004_phase4_sales_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transfer_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transfer_code", sa.String(length=48), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("source_shop_id", sa.BigInteger(), nullable=False),
        sa.Column("recipient_shop_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("source_batch_id", sa.BigInteger(), nullable=False),
        sa.Column("recipient_batch_id", sa.BigInteger(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("received_good_qty", sa.Integer(), nullable=True),
        sa.Column(
            "transit_damaged_qty",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "unit_transfer_price",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "estimated_distance_km",
            sa.Numeric(precision=8, scale=2),
            nullable=True,
        ),
        sa.Column(
            "transfer_logistics_cost",
            sa.Numeric(precision=12, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column("batch_expiry_date_snapshot", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="PENDING_RECIPIENT_APPROVAL",
            nullable=False,
        ),
        sa.Column("initiated_by_shop_id", sa.BigInteger(), nullable=False),
        sa.Column("initiated_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_approved_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("source_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recipient_approved_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("recipient_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("offer_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_notes", sa.Text(), nullable=True),
        sa.Column("received_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_notes", sa.Text(), nullable=True),
        sa.Column("rejection_or_cancel_reason", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_transfers_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_shop_id"],
            ["shops.id"],
            name="fk_transfers_source_shop",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_shop_id"],
            ["shops.id"],
            name="fk_transfers_recipient_shop",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_transfers_product",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_batch_id"],
            ["inventory_batches.id"],
            name="fk_transfers_source_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_batch_id"],
            ["inventory_batches.id"],
            name="fk_transfers_recipient_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by_shop_id"],
            ["shops.id"],
            name="fk_transfers_initiated_by_shop",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by_user_id"],
            ["users.id"],
            name="fk_transfers_initiated_by_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_approved_by_user_id"],
            ["users.id"],
            name="fk_transfers_source_approved_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_approved_by_user_id"],
            ["users.id"],
            name="fk_transfers_recipient_approved_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dispatched_by_user_id"],
            ["users.id"],
            name="fk_transfers_dispatched_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["received_by_user_id"],
            ["users.id"],
            name="fk_transfers_received_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transfer_requests"),
        sa.UniqueConstraint("transfer_code", name="uq_transfers_transfer_code"),
        sa.CheckConstraint("quantity > 0", name="ck_transfers_quantity_positive"),
        sa.CheckConstraint(
            "received_good_qty IS NULL OR received_good_qty >= 0",
            name="ck_transfers_received_good_nonnegative",
        ),
        sa.CheckConstraint(
            "transit_damaged_qty >= 0",
            name="ck_transfers_transit_damaged_nonnegative",
        ),
        sa.CheckConstraint(
            "unit_transfer_price >= 0",
            name="ck_transfers_unit_price_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_distance_km IS NULL OR estimated_distance_km >= 0",
            name="ck_transfers_distance_nonnegative",
        ),
        sa.CheckConstraint(
            "transfer_logistics_cost >= 0",
            name="ck_transfers_logistics_cost_nonnegative",
        ),
        sa.CheckConstraint(
            "source_shop_id != recipient_shop_id",
            name="chk_transfer_distinct_shops",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'PENDING_SOURCE_APPROVAL', 'PENDING_RECIPIENT_APPROVAL', 'APPROVED_RESERVED', "
            "'DISPATCHED_IN_TRANSIT', 'COMPLETED', 'REJECTED', 'CANCELLED', 'EXPIRED_OFFER', "
            "'DISPUTED', 'RETURNED_TO_SOURCE'"
            ")",
            name="ck_transfers_status_valid",
        ),
    )

    op.create_index(
        "idx_transfers_source_status",
        "transfer_requests",
        ["source_shop_id", "status"],
    )
    op.create_index(
        "idx_transfers_recipient_status",
        "transfer_requests",
        ["recipient_shop_id", "status"],
    )
    op.create_index(
        "idx_transfers_source_batch_active",
        "transfer_requests",
        ["source_batch_id", "status"],
    )

    op.create_table(
        "transfer_status_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transfer_id", sa.BigInteger(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["transfer_id"],
            ["transfer_requests.id"],
            name="fk_transfer_status_logs_transfer",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_transfer_status_logs_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transfer_status_logs"),
    )

    op.create_index(
        "idx_transfer_status_logs_transfer",
        "transfer_status_logs",
        ["transfer_id", "created_at"],
    )

    op.create_foreign_key(
        "fk_inventory_batches_source_transfer_id",
        "inventory_batches",
        "transfer_requests",
        ["source_transfer_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_inventory_batches_source_transfer_id",
        "inventory_batches",
        type_="foreignkey",
    )
    op.drop_index("idx_transfer_status_logs_transfer", table_name="transfer_status_logs")
    op.drop_table("transfer_status_logs")
    op.drop_index("idx_transfers_source_batch_active", table_name="transfer_requests")
    op.drop_index("idx_transfers_recipient_status", table_name="transfer_requests")
    op.drop_index("idx_transfers_source_status", table_name="transfer_requests")
    op.drop_table("transfer_requests")
