"""Phase 4 schema: sales, sale_items, and sale_returns.

Revision ID: 0004_phase4_sales_billing
Revises: 0003_phase3_catalog_inventory
Create Date: 2026-10-13 07:35:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_phase4_sales_billing"
down_revision = "0003_phase3_catalog_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("invoice_number", sa.String(length=48), nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("customer_name", sa.String(length=120), nullable=True),
        sa.Column("customer_phone", sa.String(length=20), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="COMPLETED",
            nullable=False,
        ),
        sa.Column(
            "subtotal_amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "discount_amount",
            sa.Numeric(precision=12, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "total_amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "total_cost_amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "returned_amount",
            sa.Numeric(precision=12, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "payment_mode",
            sa.String(length=20),
            server_default="CASH",
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("sold_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("cancelled_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('COMPLETED', 'PARTIALLY_RETURNED', 'RETURNED', 'CANCELLED')",
            name="ck_sales_status_valid",
        ),
        sa.CheckConstraint(
            "payment_mode IN ('CASH', 'UPI_MANUAL', 'CARD_MANUAL', 'OTHER')",
            name="ck_sales_payment_mode_valid",
        ),
        sa.CheckConstraint(
            "subtotal_amount >= 0 AND discount_amount >= 0 AND total_amount >= 0 "
            "AND total_cost_amount >= 0 AND returned_amount >= 0",
            name="ck_sales_amounts_nonnegative",
        ),
        sa.CheckConstraint(
            "discount_amount <= subtotal_amount AND total_amount = subtotal_amount - discount_amount",
            name="ck_sales_discount_bounds",
        ),
        sa.CheckConstraint(
            "returned_amount <= total_amount",
            name="ck_sales_returned_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["shops.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sold_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shop_id", "invoice_number", name="uq_sales_shop_invoice"),
        sa.UniqueConstraint("shop_id", "idempotency_key", name="uq_sales_shop_idempotency"),
    )
    op.create_index("idx_sales_shop_created", "sales", ["shop_id", "created_at"], unique=False)
    op.create_index("idx_sales_shop_status", "sales", ["shop_id", "status"], unique=False)

    op.create_table(
        "sale_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sale_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_product_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "returned_quantity",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "unit_selling_price",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "unit_cost_price",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "line_discount",
            sa.Numeric(precision=12, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "line_total",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("quantity > 0", name="ck_sale_items_qty_positive"),
        sa.CheckConstraint(
            "returned_quantity >= 0 AND returned_quantity <= quantity",
            name="ck_sale_items_returned_qty_bounds",
        ),
        sa.CheckConstraint(
            "unit_selling_price >= 0 AND unit_cost_price >= 0 "
            "AND line_discount >= 0 AND line_total >= 0",
            name="ck_sale_items_prices_nonnegative",
        ),
        sa.CheckConstraint(
            "line_total = (quantity * unit_selling_price) - line_discount",
            name="ck_sale_items_line_total_math",
        ),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["shop_product_id"], ["shop_products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["batch_id"], ["inventory_batches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sale_items_sale", "sale_items", ["sale_id"], unique=False)
    op.create_index("idx_sale_items_shop_product", "sale_items", ["shop_id", "product_id"], unique=False)
    op.create_index("idx_sale_items_batch", "sale_items", ["batch_id"], unique=False)

    op.create_table(
        "sale_returns",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sale_id", sa.BigInteger(), nullable=False),
        sa.Column("sale_item_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity_returned", sa.Integer(), nullable=False),
        sa.Column("return_condition", sa.String(length=20), nullable=False),
        sa.Column("stock_bucket_restored", sa.String(length=20), nullable=False),
        sa.Column("refund_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("processed_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("quantity_returned > 0", name="ck_sale_returns_qty_positive"),
        sa.CheckConstraint(
            "return_condition IN ('RESELLABLE', 'DAMAGED', 'EXPIRED')",
            name="ck_sale_returns_condition_valid",
        ),
        sa.CheckConstraint(
            "stock_bucket_restored IN ('AVAILABLE', 'DAMAGED', 'EXPIRED')",
            name="ck_sale_returns_bucket_valid",
        ),
        sa.CheckConstraint("refund_amount >= 0", name="ck_sale_returns_refund_nonnegative"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sale_item_id"], ["sale_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["batch_id"], ["inventory_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["processed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shop_id", "idempotency_key", name="uq_sale_returns_shop_idempotency"),
    )
    op.create_index("idx_sale_returns_sale", "sale_returns", ["sale_id"], unique=False)
    op.create_index("idx_sale_returns_shop_created", "sale_returns", ["shop_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_sale_returns_shop_created", table_name="sale_returns")
    op.drop_index("idx_sale_returns_sale", table_name="sale_returns")
    op.drop_table("sale_returns")

    op.drop_index("idx_sale_items_batch", table_name="sale_items")
    op.drop_index("idx_sale_items_shop_product", table_name="sale_items")
    op.drop_index("idx_sale_items_sale", table_name="sale_items")
    op.drop_table("sale_items")

    op.drop_index("idx_sales_shop_status", table_name="sales")
    op.drop_index("idx_sales_shop_created", table_name="sales")
    op.drop_table("sales")
