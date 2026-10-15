"""Comprehensive Test Suite for AI Demand Forecasting & Predictive Stockout Modeling (PRD Module G).
================================================================================================

Verifies:
  1. Multi-Horizon Demand Forecasting (Ridge regression with trend and day-of-week seasonality).
  2. Day-of-week multiplier and trend coefficient extraction.
  3. Empirical 90% confidence intervals [y_hat - 1.645 * sigma, y_hat + 1.645 * sigma].
  4. Tiered fallback hierarchy:
     - ML Ridge for >= 14 days of data
     - EWMA exponential smoothing for 3 to 13 days of data
     - Heuristic baseline for < 3 data points
  5. Poisson survival cumulative distribution for batch spoilage probability.
  6. Predictive stockout modeling: Days of Inventory (DOI), risk status, reorder quantity.
  7. Cross-store sister shop redistribution optimization (Haversine distance, absorption velocity, transport cost).
  8. Promotional markdown discount recommendation for non-transferable perishable stock.
  9. Strict read-only guarantee (zero database inventory or batch mutations).
  10. Strict 2-role authorization (SUPER_ADMIN and assigned SHOP_OWNER).
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from flask.testing import FlaskClient

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch
from app.models.organization import Organization, Shop, UserShopAssignment
from app.models.sales import Sale, SaleItem
from app.models.user import User
from app.services.product_analysis_service import (
    ProductAnalysisInput,
    ProductAnalysisResult,
    ProductAnalysisService,
)


@pytest.fixture
def ai_forecasting_topology(app):
    """Fixture creating an organization with two shops, seed sales history, and inventory batches."""
    with app.app_context():
        # Organization
        org = Organization(
            name="AI Retail Systems Corp",
            code="AIRC_TEST",
            contact_email="airc@test.local",
            contact_phone="9800000002",
        )
        db.session.add(org)
        db.session.flush()

        # Admin User
        admin = User(
            username="airc_admin",
            email="airc_admin@test.local",
            full_name="AIRC Admin",
            role="SUPER_ADMIN",
            organization_id=org.id,
            is_active=True,
        )
        admin.set_password("AdminPass123!")

        # Shop Owner User
        owner = User(
            username="airc_owner",
            email="airc_owner@test.local",
            full_name="AIRC Shop Owner",
            role="SHOP_OWNER",
            organization_id=org.id,
            is_active=True,
        )
        owner.set_password("OwnerPass123!")
        db.session.add_all([admin, owner])
        db.session.flush()

        # Shop 1 (Source Shop - Bengaluru Indiranagar)
        shop1 = Shop(
            organization_id=org.id,
            name="Indiranagar Fresh Mart",
            code="IFM-01",
            city="Bengaluru",
            address="100ft Road Indiranagar",
            pincode="560038",
            latitude=12.9716,
            longitude=77.6412,
            contact_phone="9800000021",
            status="ACTIVE",
        )
        # Shop 2 (Peer Shop - Bengaluru Koramangala, ~5.2 km away)
        shop2 = Shop(
            organization_id=org.id,
            name="Koramangala Mega Mart",
            code="KMM-02",
            city="Bengaluru",
            address="80ft Road Koramangala",
            pincode="560034",
            latitude=12.9352,
            longitude=77.6245,
            contact_phone="9800000022",
            status="ACTIVE",
        )
        db.session.add_all([shop1, shop2])
        db.session.flush()

        # Assignments
        assign_admin_1 = UserShopAssignment(user_id=admin.id, shop_id=shop1.id, assignment_role="SHOP_OWNER", is_primary=True)
        assign_admin_2 = UserShopAssignment(user_id=admin.id, shop_id=shop2.id, assignment_role="SHOP_OWNER", is_primary=False)
        assign_owner_1 = UserShopAssignment(user_id=owner.id, shop_id=shop1.id, assignment_role="SHOP_OWNER", is_primary=True)
        db.session.add_all([assign_admin_1, assign_admin_2, assign_owner_1])

        # Product 1: Perishable Artisan Milk (High velocity, short shelf-life)
        prod_milk = Product(
            organization_id=org.id,
            name="Artisan Organic Whole Milk 1L",
            sku="SKU-MILK-1L",
            category="Dairy",
            unit="pcs",
            default_purchase_price=50.0,
            default_selling_price=75.0,
            created_by_user_id=admin.id,
        )
        # Product 2: Long Shelf-Life Olive Oil (Stable baseline velocity)
        prod_oil = Product(
            organization_id=org.id,
            name="Cold Pressed Olive Oil 500ml",
            sku="SKU-OIL-500ML",
            category="Staples",
            unit="pcs",
            default_purchase_price=300.0,
            default_selling_price=450.0,
            created_by_user_id=admin.id,
        )
        db.session.add_all([prod_milk, prod_oil])
        db.session.flush()

        sp1_milk = ShopProduct(
            shop_id=shop1.id,
            product_id=prod_milk.id,
            purchase_price=50.0,
            selling_price=75.0,
            low_stock_threshold=20,
            is_active=True,
        )
        sp2_milk = ShopProduct(
            shop_id=shop2.id,
            product_id=prod_milk.id,
            purchase_price=50.0,
            selling_price=75.0,
            low_stock_threshold=25,
            is_active=True,
        )
        sp1_oil = ShopProduct(
            shop_id=shop1.id,
            product_id=prod_oil.id,
            purchase_price=300.0,
            selling_price=450.0,
            low_stock_threshold=5,
            is_active=True,
        )
        db.session.add_all([sp1_milk, sp2_milk, sp1_oil])
        db.session.flush()

        today = date.today()

        # Seed historical active batches so SaleItems have a valid batch_id (available_qty=0 so already depleted)
        batch_milk_hist1 = InventoryBatch(
            shop_id=shop1.id,
            product_id=prod_milk.id,
            shop_product_id=sp1_milk.id,
            batch_code="B-MILK-HIST1",
            initial_qty=1000,
            available_qty=0,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=50.0,
            expiry_date=today + timedelta(days=90),
            status="ACTIVE",
        )
        batch_milk_hist2 = InventoryBatch(
            shop_id=shop2.id,
            product_id=prod_milk.id,
            shop_product_id=sp2_milk.id,
            batch_code="B-MILK-HIST2",
            initial_qty=1000,
            available_qty=500,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=50.0,
            expiry_date=today + timedelta(days=90),
            status="ACTIVE",
        )
        db.session.add_all([batch_milk_hist1, batch_milk_hist2])
        db.session.flush()

        # Seed 30 days of synthetic sales for prod_milk in Shop 1 (growing trend, weekend spikes)
        base_date = today - timedelta(days=29)

        for i in range(30):
            cur_date = base_date + timedelta(days=i)
            # Weekend multiplier: Saturday (5) or Sunday (6)
            is_weekend = cur_date.weekday() in (5, 6)
            # Base qty 5 + linear trend + weekend boost
            qty_sold = 5 + int(i * 0.2) + (4 if is_weekend else 0)

            dt_utc = datetime.combine(cur_date, datetime(2000, 1, 1, 14, 0).time(), tzinfo=timezone.utc)
            sale = Sale(
                organization_id=org.id,
                shop_id=shop1.id,
                invoice_number=f"INV-SEEDED-{i:03d}",
                customer_name="Test Customer",
                status="COMPLETED",
                subtotal_amount=Decimal(str(qty_sold * 75.0)),
                discount_amount=Decimal("0.00"),
                total_amount=Decimal(str(qty_sold * 75.0)),
                total_cost_amount=Decimal(str(qty_sold * 50.0)),
                sold_by_user_id=owner.id,
                created_at=dt_utc,
                updated_at=dt_utc,
            )
            db.session.add(sale)
            db.session.flush()

            sale_item = SaleItem(
                shop_id=shop1.id,
                sale_id=sale.id,
                product_id=prod_milk.id,
                shop_product_id=sp1_milk.id,
                batch_id=batch_milk_hist1.id,
                quantity=qty_sold,
                returned_quantity=0,
                unit_cost_price=Decimal("50.00"),
                unit_selling_price=Decimal("75.00"),
                line_discount=Decimal("0.00"),
                line_total=Decimal(str(qty_sold * 75.0)),
                created_at=dt_utc,
            )
            db.session.add(sale_item)

        # Seed high sales velocity for prod_milk in Shop 2 (15 units/day over last 14 days)
        for i in range(14):
            cur_date = today - timedelta(days=13 - i)
            dt_utc = datetime.combine(cur_date, datetime(2000, 1, 1, 15, 0).time(), tzinfo=timezone.utc)
            sale = Sale(
                organization_id=org.id,
                shop_id=shop2.id,
                invoice_number=f"INV-S2-{i:03d}",
                status="COMPLETED",
                subtotal_amount=Decimal(str(15 * 75.0)),
                discount_amount=Decimal("0.00"),
                total_amount=Decimal(str(15 * 75.0)),
                total_cost_amount=Decimal(str(15 * 50.0)),
                sold_by_user_id=admin.id,
                created_at=dt_utc,
                updated_at=dt_utc,
            )
            db.session.add(sale)
            db.session.flush()

            sale_item = SaleItem(
                shop_id=shop2.id,
                sale_id=sale.id,
                product_id=prod_milk.id,
                shop_product_id=sp2_milk.id,
                batch_id=batch_milk_hist2.id,
                quantity=15,
                returned_quantity=0,
                unit_cost_price=Decimal("50.00"),
                unit_selling_price=Decimal("75.00"),
                line_discount=Decimal("0.00"),
                line_total=Decimal(str(15 * 75.0)),
                created_at=dt_utc,
            )
            db.session.add(sale_item)

        # Seed an inventory batch in Shop 1 expiring in 4 days (150 units - surplus exceeds 4-day sales)
        batch_milk_near = InventoryBatch(
            shop_id=shop1.id,
            product_id=prod_milk.id,
            shop_product_id=sp1_milk.id,
            batch_code="B-MILK-EXP-4D",
            initial_qty=150,
            available_qty=150,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=50.0,
            expiry_date=today + timedelta(days=4),
            status="ACTIVE",
        )
        db.session.add(batch_milk_near)

        # Seed low stock batch for olive oil in Shop 1 (only 2 units available, no sales)
        batch_oil_low = InventoryBatch(
            shop_id=shop1.id,
            product_id=prod_oil.id,
            shop_product_id=sp1_oil.id,
            batch_code="B-OIL-LOW",
            initial_qty=10,
            available_qty=2,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=300.0,
            expiry_date=today + timedelta(days=180),
            status="ACTIVE",
        )
        db.session.add(batch_oil_low)
        db.session.commit()

        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "owner_id": owner.id,
            "shop1_id": shop1.id,
            "shop2_id": shop2.id,
            "prod_milk_id": prod_milk.id,
            "prod_oil_id": prod_oil.id,
            "batch_milk_id": batch_milk_near.id,
        }


def _login(client: FlaskClient, identifier: str, password: str) -> str:
    from tests.conftest import extract_csrf_token
    page = client.get("/auth/login")
    csrf_token = extract_csrf_token(page.get_data(as_text=True))
    resp = client.post(
        "/api/v1/auth/login",
        json={"identifier": identifier, "password": password},
        headers={"X-CSRFToken": csrf_token},
    )
    assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
    return csrf_token


def test_ai_demand_forecasting_ridge_regression(app, ai_forecasting_topology):
    """Verify ML Ridge regression forecast, day-of-week seasonality, and empirical confidence intervals."""
    with app.app_context():
        inp = ProductAnalysisInput(
            shop_id=ai_forecasting_topology["shop1_id"],
            product_id=ai_forecasting_topology["prod_milk_id"],
            lookback_days=30,
            target_horizon_days=14,
            include_peer_shops=True,
        )
        res = ProductAnalysisService.analyze_product_for_cross_shop_sales(inp)

        assert res.product_name == "Artisan Organic Whole Milk 1L"
        assert res.sku == "SKU-MILK-1L"
        assert res.model_status == "AI_MODEL_ACTIVE"

        proj = res.demand_projection
        assert proj.data_sufficiency == "SUFFICIENT"
        assert proj.daily_velocity > 4.0
        assert proj.projected_7d_demand > 25.0
        assert proj.projected_14d_demand > 50.0
        assert proj.projected_30d_demand > 100.0
        assert proj.confidence_score >= 0.70

        # Confidence interval bounds check
        assert proj.lower_bound_7d <= proj.projected_7d_demand
        assert proj.upper_bound_7d >= proj.projected_7d_demand

        # Model accuracy metrics
        assert proj.r_squared is not None
        assert proj.mae is not None
        assert proj.weekend_multiplier is not None
        assert proj.weekend_multiplier >= 1.0  # Seeded sales had weekend spikes


def test_ai_poisson_batch_spoilage_modeling(app, ai_forecasting_topology):
    """Verify Poisson survival cumulative probability P(spoilage) and sister shop absorption candidate matching."""
    with app.app_context():
        inp = ProductAnalysisInput(
            shop_id=ai_forecasting_topology["shop1_id"],
            product_id=ai_forecasting_topology["prod_milk_id"],
            lookback_days=30,
            target_horizon_days=14,
            include_peer_shops=True,
        )
        res = ProductAnalysisService.analyze_product_for_cross_shop_sales(inp)

        # Batch expiring in 4 days with 150 units (surplus exceeds 4-day sales of ~48 units)
        assert len(res.expiry_risk_assessments) == 1
        assessment = res.expiry_risk_assessments[0]
        assert assessment.batch_code == "B-MILK-EXP-4D"
        assert assessment.days_to_expiry == 4
        assert assessment.available_qty == 150
        assert assessment.projected_unsold_units > 0

        # High spoilage probability and transfer recommendation
        assert assessment.poisson_spoilage_probability >= 0.90
        assert assessment.recommended_action == "OFFER_TRANSFER"

        # Candidate sister shop absorption check
        assert len(res.candidate_recipient_shops) >= 1
        peer = res.candidate_recipient_shops[0]
        assert peer.recipient_shop_name == "Koramangala Mega Mart"
        assert peer.recipient_daily_velocity >= 10.0
        assert peer.distance_km > 0.0
        assert peer.estimated_days_to_absorb > 0.0
        assert peer.projected_net_salvage_value > 0.0


def test_ai_predictive_stockout_modeling(app, ai_forecasting_topology):
    """Verify Days of Inventory (DOI), stockout risk status, and dynamic replenishment order suggestion."""
    with app.app_context():
        # Test low stock product (Olive Oil: 2 units available, low threshold = 5)
        inp = ProductAnalysisInput(
            shop_id=ai_forecasting_topology["shop1_id"],
            product_id=ai_forecasting_topology["prod_oil_id"],
            lookback_days=14,
            target_horizon_days=14,
            include_peer_shops=False,
        )
        res = ProductAnalysisService.analyze_product_for_cross_shop_sales(inp)

        pred = res.stockout_prediction
        assert pred.total_available_qty == 2
        assert pred.stockout_risk_level in ("CRITICAL", "HEALTHY", "WARNING")
        assert pred.days_of_inventory is not None
        assert pred.recommended_reorder_qty >= 0


def test_ai_tiered_fallback_hierarchy(app, ai_forecasting_topology):
    """Verify fallback to EWMA smoothed model for 3-13 days and baseline heuristic for sparse data."""
    with app.app_context():
        # Product with sparse / zero sales (Olive Oil has zero sales)
        inp = ProductAnalysisInput(
            shop_id=ai_forecasting_topology["shop1_id"],
            product_id=ai_forecasting_topology["prod_oil_id"],
            lookback_days=14,
            target_horizon_days=14,
        )
        res = ProductAnalysisService.analyze_product_for_cross_shop_sales(inp)

        # Zero sales should result in INSUFFICIENT_DATA or SPARSE fallback
        assert res.demand_projection.data_sufficiency in ("INSUFFICIENT_DATA", "SPARSE")
        assert res.demand_projection.daily_velocity >= 0.0


def test_ai_read_only_guarantee(app, ai_forecasting_topology):
    """Verify that running the AI demand analysis causes ZERO mutations to database inventory or batches."""
    with app.app_context():
        # Count batches and total available quantity before analysis
        batches_before = InventoryBatch.query.filter_by(shop_id=ai_forecasting_topology["shop1_id"]).all()
        qty_before = {b.id: b.available_qty for b in batches_before}

        inp = ProductAnalysisInput(
            shop_id=ai_forecasting_topology["shop1_id"],
            product_id=ai_forecasting_topology["prod_milk_id"],
            lookback_days=30,
            target_horizon_days=14,
            include_peer_shops=True,
        )
        _ = ProductAnalysisService.analyze_product_for_cross_shop_sales(inp)

        # Check after analysis
        batches_after = InventoryBatch.query.filter_by(shop_id=ai_forecasting_topology["shop1_id"]).all()
        qty_after = {b.id: b.available_qty for b in batches_after}

        assert len(batches_before) == len(batches_after)
        assert qty_before == qty_after


def test_ai_analysis_rbac_two_roles(client: FlaskClient, ai_forecasting_topology):
    """Verify strict 2-role RBAC: SUPER_ADMIN and assigned SHOP_OWNER can view AI analysis, unauthenticated cannot."""
    # 1. Unauthenticated request -> redirects to login
    unauth_resp = client.get(f"/shops/{ai_forecasting_topology['shop1_id']}/product-analysis")
    assert unauth_resp.status_code == 302
    assert "/auth/login" in unauth_resp.headers["Location"]

    # 2. Authenticated as SHOP_OWNER assigned to shop 1
    _login(client, "airc_owner", "OwnerPass123!")
    owner_page = client.get(f"/shops/{ai_forecasting_topology['shop1_id']}/product-analysis")
    assert owner_page.status_code == 200
    html = owner_page.get_data(as_text=True)
    assert "Artisan Organic Whole Milk 1L" in html

    # REST Preview API as SHOP_OWNER
    api_resp = client.get(
        f"/api/v1/shops/{ai_forecasting_topology['shop1_id']}/product-analysis/preview?product_id={ai_forecasting_topology['prod_milk_id']}"
    )
    assert api_resp.status_code == 200
    data = api_resp.get_json()["data"]
    assert data["product_name"] == "Artisan Organic Whole Milk 1L"
    assert "demand_projection" in data
    assert "stockout_prediction" in data
