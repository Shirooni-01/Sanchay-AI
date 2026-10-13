"""Phase 3 schema: products, shop_products, inventory_batches, and stock_movements.

Revision ID: 0003_phase3_catalog_inventory
Revises: 0002_phase2_org_shops
Create Date: 2026-10-13 00:18:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_phase3_catalog_inventory"
down_revision = "0002_phase2_org_shops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_perishable",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "default_purchase_price",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "default_selling_price",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "reorder_threshold",
            sa.Integer(),
            server_default=sa.text("10"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
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
            "unit IN ('pcs', 'pack', 'kg', 'g', 'litre', 'ml', 'box')",
            name="ck_products_unit_valid",
        ),
        sa.CheckConstraint(
            "default_purchase_price >= 0",
            name="ck_products_default_purchase_nonnegative",
        ),
        sa.CheckConstraint(
            "default_selling_price >= 0",
            name="ck_products_default_selling_nonnegative",
        ),
        sa.CheckConstraint(
            "reorder_threshold >= 0",
            name="ck_products_reorder_threshold_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_products_organization_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_products_created_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "sku", name="uq_products_org_sku"),
    )
    op.create_index(
        "idx_products_org_category",
        "products",
        ["organization_id", "category"],
        unique=False,
    )
    op.create_index(
        "idx_products_org_active",
        "products",
        ["organization_id", "is_active"],
        unique=False,
    )

    op.create_table(
        "shop_products",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("purchase_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("selling_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "low_stock_threshold",
            sa.Integer(),
            server_default=sa.text("10"),
            nullable=False,
        ),
        sa.Column(
            "near_expiry_days",
            sa.Integer(),
            server_default=sa.text("15"),
            nullable=False,
        ),
        sa.Column(
            "surplus_threshold",
            sa.Integer(),
            server_default=sa.text("40"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
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
            "purchase_price >= 0",
            name="ck_shop_products_purchase_price_nonnegative",
        ),
        sa.CheckConstraint(
            "selling_price >= 0",
            name="ck_shop_products_selling_price_nonnegative",
        ),
        sa.CheckConstraint(
            "low_stock_threshold >= 0",
            name="ck_shop_products_low_stock_nonnegative",
        ),
        sa.CheckConstraint(
            "near_expiry_days >= 1",
            name="ck_shop_products_near_expiry_positive",
        ),
        sa.CheckConstraint(
            "surplus_threshold >= 1",
            name="ck_shop_products_surplus_positive",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["shops.id"],
            name="fk_shop_products_shop_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_shop_products_product_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shop_id",
            "product_id",
            name="uq_shop_products_shop_product",
        ),
    )
    op.create_index(
        "idx_shop_products_shop_active",
        "shop_products",
        ["shop_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "idx_shop_products_product",
        "shop_products",
        ["product_id"],
        unique=False,
    )

    op.create_table(
        "inventory_batches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_product_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_code", sa.String(length=64), nullable=False),
        sa.Column("manufactured_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("unit_cost", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("initial_qty", sa.Integer(), nullable=False),
        sa.Column("available_qty", sa.Integer(), nullable=False),
        sa.Column(
            "reserved_qty",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "damaged_qty",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "expired_qty",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("source_transfer_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
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
            "unit_cost >= 0",
            name="ck_batches_unit_cost_nonnegative",
        ),
        sa.CheckConstraint(
            "initial_qty > 0",
            name="ck_batches_initial_qty_positive",
        ),
        sa.CheckConstraint(
            "available_qty >= 0",
            name="ck_batches_available_qty_nonnegative",
        ),
        sa.CheckConstraint(
            "reserved_qty >= 0",
            name="ck_batches_reserved_qty_nonnegative",
        ),
        sa.CheckConstraint(
            "damaged_qty >= 0",
            name="ck_batches_damaged_qty_nonnegative",
        ),
        sa.CheckConstraint(
            "expired_qty >= 0",
            name="ck_batches_expired_qty_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DEPLETED', 'EXPIRED', 'ARCHIVED')",
            name="ck_batches_status_valid",
        ),
        sa.CheckConstraint(
            "manufactured_date IS NULL OR expiry_date IS NULL OR expiry_date >= manufactured_date",
            name="chk_batch_dates",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["shops.id"],
            name="fk_batches_shop_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_batches_product_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shop_product_id"],
            ["shop_products.id"],
            name="fk_batches_shop_product_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shop_id",
            "product_id",
            "batch_code",
            name="uq_batches_shop_product_code",
        ),
    )
    op.create_index(
        "idx_batches_fefo",
        "inventory_batches",
        ["shop_id", "product_id", "status", "expiry_date", "received_at"],
        unique=False,
    )
    op.create_index(
        "idx_batches_expiry",
        "inventory_batches",
        ["shop_id", "expiry_date"],
        unique=False,
    )

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("movement_type", sa.String(length=32), nullable=False),
        sa.Column("bucket_affected", sa.String(length=20), nullable=False),
        sa.Column("quantity_change", sa.Integer(), nullable=False),
        sa.Column("balance_before", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column(
            "unit_cost_snapshot",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column("reference_type", sa.String(length=32), nullable=False),
        sa.Column("reference_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("performed_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "movement_type IN ("
            "'RECEIPT', 'SALE', 'SALE_RETURN', 'SALE_CANCELLATION', "
            "'ADJUSTMENT_ADD', 'ADJUSTMENT_SUBTRACT', 'MARK_DAMAGED', 'MARK_EXPIRED', "
            "'TRANSFER_RESERVE', 'TRANSFER_RELEASE', 'TRANSFER_OUT', 'TRANSFER_IN'"
            ")",
            name="ck_movements_type_valid",
        ),
        sa.CheckConstraint(
            "bucket_affected IN ('AVAILABLE', 'RESERVED', 'DAMAGED', 'EXPIRED')",
            name="ck_movements_bucket_valid",
        ),
        sa.CheckConstraint(
            "quantity_change != 0",
            name="ck_movements_qty_nonzero",
        ),
        sa.CheckConstraint(
            "balance_before >= 0",
            name="ck_movements_balance_before_nonnegative",
        ),
        sa.CheckConstraint(
            "balance_after >= 0",
            name="ck_movements_balance_after_nonnegative",
        ),
        sa.CheckConstraint(
            "balance_before + quantity_change = balance_after",
            name="ck_movements_balance_math",
        ),
        sa.CheckConstraint(
            "unit_cost_snapshot >= 0",
            name="ck_movements_unit_cost_nonnegative",
        ),
        sa.CheckConstraint(
            "reference_type IN ("
            "'INITIAL_RECEIPT', 'SALE', 'SALE_RETURN', 'TRANSFER', "
            "'MANUAL_ADJUSTMENT', 'EXPIRY_SWEEP'"
            ")",
            name="ck_movements_ref_type_valid",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_movements_organization_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["shops.id"],
            name="fk_movements_shop_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_movements_product_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["inventory_batches.id"],
            name="fk_movements_batch_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["performed_by_user_id"],
            ["users.id"],
            name="fk_movements_performed_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_movements_shop_created",
        "stock_movements",
        ["shop_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_movements_batch",
        "stock_movements",
        ["batch_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_movements_product",
        "stock_movements",
        ["shop_id", "product_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_movements_product", table_name="stock_movements")
    op.drop_index("idx_movements_batch", table_name="stock_movements")
    op.drop_index("idx_movements_shop_created", table_name="stock_movements")
    op.drop_table("stock_movements")

    op.drop_index("idx_batches_expiry", table_name="inventory_batches")
    op.drop_index("idx_batches_fefo", table_name="inventory_batches")
    op.drop_table("inventory_batches")

    op.drop_index("idx_shop_products_product", table_name="shop_products")
    op.drop_index("idx_shop_products_shop_active", table_name="shop_products")
    op.drop_table("shop_products")

    op.drop_index("idx_products_org_active", table_name="products")
    op.drop_index("idx_products_org_category", table_name="products")
    op.drop_table("products")
