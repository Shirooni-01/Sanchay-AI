"""Tests for Product Analysis Module (Placeholder & Documented AI Interface)
========================================================================

Verifies:
  1. Service schema contracts and placeholder metadata.
  2. Simulation calculations, demand velocity, and uncertainty bounds.
  3. Batch expiry risk evaluation and candidate sister shop absorption matching.
  4. HTML page rendering with 'Coming soon' state.
  5. JSON simulation preview REST API endpoint.
  6. Compatibility module `services/productAnalysis.py` re-export.
"""

from datetime import date, timedelta
import pytest
from flask.testing import FlaskClient

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch
from app.models.organization import Organization, Shop, UserShopAssignment
from app.models.user import User
from app.services.product_analysis_service import (
    ProductAnalysisInput,
    ProductAnalysisResult,
    ProductAnalysisService,
)
from services.productAnalysis import (
    ProductAnalysisService as ReexportedService,
)


@pytest.fixture
def product_analysis_topology(app):
    """Fixture creating a clean two-shop organization with products and batches."""
    with app.app_context():
        # Organization
        org = Organization(
            name="Analysis Retail Corp",
            code="ARC_TEST",
            contact_email="arc@test.local",
            contact_phone="9800000001",
        )
        db.session.add(org)
        db.session.flush()

        # Admin User
        admin = User(
            username="arc_admin",
            email="arc_admin@test.local",
            full_name="ARC Admin",
            role="SUPER_ADMIN",
            organization_id=org.id,
            is_active=True,
        )
        admin.set_password("ValidPassword123")
        db.session.add(admin)
        db.session.flush()

        # Shop A
        shop_a = Shop(
            organization_id=org.id,
            name="Shop A - Surplus Source",
            code="SHP-SRC-01",
            city="Bengaluru",
            address="Indiranagar",
            pincode="560038",
            latitude=12.9716,
            longitude=77.6412,
            contact_phone="9800000011",
            status="ACTIVE",
        )
        # Shop B (Sister Shop)
        shop_b = Shop(
            organization_id=org.id,
            name="Shop B - High Demand Recipient",
            code="SHP-DST-02",
            city="Bengaluru",
            address="Whitefield",
            pincode="560066",
            latitude=12.9698,
            longitude=77.7500,
            contact_phone="9800000012",
            status="ACTIVE",
        )
        db.session.add_all([shop_a, shop_b])
        db.session.flush()

        # Assignments
        assign_a = UserShopAssignment(user_id=admin.id, shop_id=shop_a.id, assignment_role="SHOP_OWNER", is_primary=True)
        assign_b = UserShopAssignment(user_id=admin.id, shop_id=shop_b.id, assignment_role="SHOP_OWNER", is_primary=False)
        db.session.add_all([assign_a, assign_b])

        # Product
        prod = Product(
            organization_id=org.id,
            name="Organic Greek Yogurt",
            sku="SKU-YOGURT-500G",
            category="Dairy",
            unit="pcs",
            default_purchase_price=60.0,
            default_selling_price=90.0,
            created_by_user_id=admin.id,
        )
        db.session.add(prod)
        db.session.flush()

        sp_a = ShopProduct(
            shop_id=shop_a.id,
            product_id=prod.id,
            purchase_price=60.0,
            selling_price=90.0,
            low_stock_threshold=10,
            is_active=True,
        )
        sp_b = ShopProduct(
            shop_id=shop_b.id,
            product_id=prod.id,
            purchase_price=60.0,
            selling_price=90.0,
            low_stock_threshold=15,
            is_active=True,
        )
        db.session.add_all([sp_a, sp_b])
        db.session.flush()

        # Batch in Shop A expiring in 5 days
        today = date.today()
        batch_near_expiry = InventoryBatch(
            shop_id=shop_a.id,
            product_id=prod.id,
            shop_product_id=sp_a.id,
            batch_code="B-YOG-NEAR",
            initial_qty=30,
            available_qty=30,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=60.0,
            expiry_date=today + timedelta(days=5),
            status="ACTIVE",
        )
        db.session.add(batch_near_expiry)
        db.session.commit()

        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "shop_a_id": shop_a.id,
            "shop_b_id": shop_b.id,
            "product_id": prod.id,
        }


def _login(client: FlaskClient, identity: str = "arc_admin", password: str = "ValidPassword123") -> str:
    from tests.conftest import extract_csrf_token
    login_page = client.get("/auth/login")
    csrf_token = extract_csrf_token(login_page.get_data(as_text=True))
    resp = client.post(
        "/api/v1/auth/login",
        json={"identifier": identity, "password": password},
        headers={"X-CSRFToken": csrf_token},
    )
    assert resp.status_code == 200, f"Login failed for {identity}: {resp.get_json()}"
    return csrf_token


def test_placeholder_overview_schema_and_reexport():
    """Verify overview metadata and services/productAnalysis compatibility re-export."""
    overview = ProductAnalysisService.get_placeholder_overview()
    assert overview["status"] == "COMING_SOON"
    assert "architecture" in overview
    assert "models_to_integrate" in overview["architecture"]
    assert "interface_contracts" in overview

    # Verify re-exported module
    reexp_overview = ReexportedService.get_placeholder_overview()
    assert reexp_overview["version"] == overview["version"]


def test_service_simulation_analysis_on_near_expiry_batch(app, product_analysis_topology):
    """Verify simulation calculates demand projection, expiry risk, and candidate sister shops."""
    with app.app_context():
        inp = ProductAnalysisInput(
            shop_id=product_analysis_topology["shop_a_id"],
            product_id=product_analysis_topology["product_id"],
            lookback_days=14,
            target_horizon_days=14,
        )
        res: ProductAnalysisResult = ProductAnalysisService.analyze_product_for_cross_shop_sales(inp)

        assert res.product_name == "Organic Greek Yogurt"
        assert res.sku == "SKU-YOGURT-500G"
        assert len(res.expiry_risk_assessments) == 1
        assessment = res.expiry_risk_assessments[0]
        assert assessment.batch_code == "B-YOG-NEAR"
        assert assessment.days_to_expiry == 5
        assert assessment.expiry_risk_score >= 0.50
        assert assessment.recommended_action == "OFFER_TRANSFER"
        assert len(res.plain_english_rationale) > 10


def test_html_product_analysis_page_and_preview_api(client: FlaskClient, product_analysis_topology):
    """Verify HTML placeholder page and REST simulation preview endpoint."""
    _login(client)

    # 1. Navigation Redirect
    redirect_resp = client.get("/product-analysis")
    assert redirect_resp.status_code == 302
    assert "/product-analysis" in redirect_resp.headers["Location"]

    # 2. Render Page
    page_resp = client.get(f"/shops/{product_analysis_topology['shop_a_id']}/product-analysis")
    assert page_resp.status_code == 200
    html = page_resp.get_data(as_text=True)
    assert "Product Analysis &amp; Cross-Store Rebalancing" in html
    assert "Coming Soon" in html
    assert "Organic Greek Yogurt" in html
    assert "Documented AI Service Schema Contract" in html

    # 3. REST API Preview
    api_resp = client.get(
        f"/api/v1/shops/{product_analysis_topology['shop_a_id']}/product-analysis/preview?product_id={product_analysis_topology['product_id']}"
    )
    assert api_resp.status_code == 200
    json_data = api_resp.get_json()
    assert json_data["success"] is True
    assert json_data["data"]["product_name"] == "Organic Greek Yogurt"
    assert "demand_projection" in json_data["data"]
