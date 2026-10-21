"""Add Carbon Credits and Sustainability Ledger tables (Phase C).

Revision ID: 0008_carbon_sustainability
Revises: 0007_two_roles
Create Date: 2026-10-21 21:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0008_carbon_sustainability"
down_revision = "0007_two_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create emission_factors table
    op.create_table(
        "emission_factors",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("subcategory", sa.String(length=80), nullable=True),
        sa.Column("product_name_pattern", sa.String(length=150), nullable=True),
        sa.Column("product_id", sa.BigInteger(), nullable=True),
        sa.Column("factor_value", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("factor_unit", sa.String(length=32), server_default="kg_co2e_per_kg", nullable=False),
        sa.Column("factor_scope", sa.String(length=40), server_default="WASTE_PREVENTION", nullable=False),
        sa.Column("geographic_scope", sa.String(length=32), server_default="IN", nullable=False),
        sa.Column("source_name", sa.String(length=150), nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=True),
        sa.Column("source_year", sa.Integer(), nullable=True),
        sa.Column("uncertainty_percent", sa.Numeric(precision=5, scale=2), server_default="15.00", nullable=True),
        sa.Column("methodology_version", sa.String(length=32), server_default="v1.0.0-fao-warm", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("factor_value >= 0", name="ck_emission_factor_value_positive"),
        sa.CheckConstraint(
            "factor_scope IN ('WASTE_PREVENTION', 'LIFECYCLE_PRODUCTION', 'TRANSPORT_ROAD', 'TRANSPORT_COLD_CHAIN')",
            name="ck_emission_factor_scope_valid",
        ),
    )
    op.create_index("idx_emission_factors_lookup", "emission_factors", ["category", "subcategory", "is_active"])
    op.create_index("idx_emission_factors_product", "emission_factors", ["product_id", "is_active"])

    # 2. Create sustainability_events table
    op.create_table(
        "sustainability_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("source_shop_id", sa.BigInteger(), nullable=False),
        sa.Column("destination_shop_id", sa.BigInteger(), nullable=True),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=True),
        sa.Column("stock_transfer_id", sa.BigInteger(), nullable=True),
        sa.Column("sale_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(length=40), server_default="TRANSFER_REDISTRIBUTION", nullable=False),
        sa.Column("eligible_saved_quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("quantity_unit", sa.String(length=20), nullable=False),
        sa.Column("normalized_weight_kg", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("applicable_factor_id", sa.BigInteger(), nullable=True),
        sa.Column("factor_selection_tier", sa.String(length=32), server_default="CATEGORY_FALLBACK", nullable=False),
        sa.Column("estimated_avoided_emissions", sa.Numeric(precision=12, scale=4), server_default="0.0000", nullable=False),
        sa.Column("additional_transport_emissions", sa.Numeric(precision=12, scale=4), server_default="0.0000", nullable=False),
        sa.Column("net_estimated_impact", sa.Numeric(precision=12, scale=4), server_default="0.0000", nullable=False),
        sa.Column("confidence_level", sa.String(length=32), server_default="MEDIUM_ESTIMATE", nullable=False),
        sa.Column("event_status", sa.String(length=32), server_default="FINALIZED", nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.Column("methodology_version", sa.String(length=32), server_default="v1.0.0-fao-warm", nullable=False),
        sa.Column("reward_points_awarded", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_shop_id"], ["shops.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["destination_shop_id"], ["shops.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["batch_id"], ["inventory_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["stock_transfer_id"], ["transfer_requests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["applicable_factor_id"], ["emission_factors.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("eligible_saved_quantity >= 0", name="ck_sustainability_qty_nonnegative"),
        sa.CheckConstraint("normalized_weight_kg >= 0", name="ck_sustainability_norm_kg_nonnegative"),
        sa.CheckConstraint(
            "event_status IN ('FINALIZED', 'PENDING_REVIEW', 'INELIGIBLE', 'REVERSED')",
            name="ck_sustainability_event_status",
        ),
    )
    op.create_index("idx_sustainability_events_org", "sustainability_events", ["organization_id", "created_at"])
    op.create_index("idx_sustainability_events_source_shop", "sustainability_events", ["source_shop_id", "created_at"])
    op.create_index("idx_sustainability_events_transfer", "sustainability_events", ["stock_transfer_id"])
    op.create_index("idx_sustainability_events_product", "sustainability_events", ["product_id"])

    # 3. Create sustainability_ledger table
    op.create_table(
        "sustainability_ledger",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("sustainability_event_id", sa.BigInteger(), nullable=False),
        sa.Column("transaction_type", sa.String(length=40), nullable=False),
        sa.Column("points_delta", sa.BigInteger(), nullable=False),
        sa.Column("attributed_impact_kg_co2e", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("reward_rule_version", sa.String(length=32), server_default="v1.0.0-10pts-per-kg", nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sustainability_event_id"], ["sustainability_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_sustainability_ledger_idempotency"),
    )
    op.create_index("idx_sustainability_ledger_shop_points", "sustainability_ledger", ["shop_id", "created_at"])
    op.create_index("idx_sustainability_ledger_org", "sustainability_ledger", ["organization_id", "created_at"])
    op.create_index("idx_sustainability_ledger_event", "sustainability_ledger", ["sustainability_event_id"])


def downgrade() -> None:
    op.drop_table("sustainability_ledger")
    op.drop_table("sustainability_events")
    op.drop_table("emission_factors")
