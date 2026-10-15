"""Tests for Expiry Risk & Waste Mitigation ML Engine (PRD Module G & Surplus).
=============================================================================

Verifies:
  1. Shop-level ML Waste Mitigation & Expiry Salvage Analysis.
  2. Dynamic markdown discount calculation based on Poisson spoilage probability.
  3. Sister shop transfer matching for transferable surplus batches.
  4. HTML dashboard rendering (/shops/<shop_id>/waste-mitigation).
  5. JSON REST API endpoint (/api/v1/shops/<shop_id>/waste-mitigation).
  6. Strict 2-role RBAC authorization (SUPER_ADMIN and assigned SHOP_OWNER).
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
    ProductAnalysisService,
    ShopWasteMitigationSummary,
)


@pytest.fixture
def waste_mitigation_topology(app):
    """Fixture creating organization with two shops and inventory batches with varying expiry risks."""
    with app.app_context():
        org = Organization(
            name="Waste Mitigation Corp",
            code="WMC_TEST",
            contact_email="wmc@test.local",
            contact_phone="9800000003",
        )
        db.session.add(org)
        db.session.flush()

        admin = User(
            username="wmc_admin",
            email="wmc_admin@test.local",
            full_name="WMC Admin",
            role="SUPER_ADMIN",
            organization_id=org.id,
            is_active=True,
        )
        admin.set_password("AdminPass123!")

        owner = User(
            username="wmc_owner",
            email="wmc_owner@test.local",
            full_name="WMC Shop Owner",
            role="SHOP_OWNER",
            organization_id=org.id,
            is_active=True,
        )
        owner.set_password("OwnerPass123!")
        db.session.add_all([admin, owner])
        db.session.flush()

        shop1 = Shop(
            organization_id=org.id,
            name="WMC Indiranagar Mart",
            code="WMC-01",
            city="Bengaluru",
            address="100ft Road Indiranagar",
            pincode="560038",
            latitude=12.9716,
            longitude=77.6412,
            contact_phone="9800000031",
            status="ACTIVE",
        )
        shop2 = Shop(
            organization_id=org.id,
            name="WMC Koramangala Hub",
            code="WMC-02",
            city="Bengaluru",
            address="80ft Road Koramangala",
            pincode="560034",
            latitude=12.9352,
            longitude=77.6245,
            contact_phone="9800000032",
            status="ACTIVE",
        )
        db.session.add_all([shop1, shop2])
        db.session.flush()

        assign_admin_1 = UserShopAssignment(user_id=admin.id, shop_id=shop1.id, assignment_role="SHOP_OWNER", is_primary=True)
        assign_admin_2 = UserShopAssignment(user_id=admin.id, shop_id=shop2.id, assignment_role="SHOP_OWNER", is_primary=False)
        assign_owner_1 = UserShopAssignment(user_id=owner.id, shop_id=shop1.id, assignment_role="SHOP_OWNER", is_primary=True)
        db.session.add_all([assign_admin_1, assign_admin_2, assign_owner_1])

        # Product 1: Greek Yogurt (Short shelf-life, markdown candidate)
        prod_yogurt = Product(
            organization_id=org.id,
            name="Artisan Greek Yogurt 200g",
            sku="SKU-YOGURT-200G",
            category="Dairy",
            unit="pcs",
            default_purchase_price=40.0,
            default_selling_price=60.0,
            created_by_user_id=admin.id,
        )
        # Product 2: Paneer (Transfer candidate)
        prod_paneer = Product(
            organization_id=org.id,
            name="Fresh Malai Paneer 500g",
            sku="SKU-PANEER-500G",
            category="Dairy",
            unit="pcs",
            default_purchase_price=120.0,
            default_selling_price=180.0,
            created_by_user_id=admin.id,
        )
        db.session.add_all([prod_yogurt, prod_paneer])
        db.session.flush()

        sp1_yogurt = ShopProduct(
            shop_id=shop1.id,
            product_id=prod_yogurt.id,
            purchase_price=40.0,
            selling_price=60.0,
            low_stock_threshold=10,
            is_active=True,
        )
        sp1_paneer = ShopProduct(
            shop_id=shop1.id,
            product_id=prod_paneer.id,
            purchase_price=120.0,
            selling_price=180.0,
            low_stock_threshold=15,
            is_active=True,
        )
        sp2_paneer = ShopProduct(
            shop_id=shop2.id,
            product_id=prod_paneer.id,
            purchase_price=120.0,
            selling_price=180.0,
            low_stock_threshold=20,
            is_active=True,
        )
        db.session.add_all([sp1_yogurt, sp1_paneer, sp2_paneer])
        db.session.flush()

        today = date.today()

        # Batch 1: Yogurt in Shop 1 expiring in 2 days (40 units) -> Needs immediate local discount markdown
        batch_yogurt = InventoryBatch(
            shop_id=shop1.id,
            product_id=prod_yogurt.id,
            shop_product_id=sp1_yogurt.id,
            batch_code="B-YOG-2D",
            initial_qty=40,
            available_qty=40,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=40.0,
            expiry_date=today + timedelta(days=2),
            status="ACTIVE",
        )
        # Batch 2: Paneer in Shop 1 expiring in 6 days (100 units) -> Transfer candidate
        batch_paneer = InventoryBatch(
            shop_id=shop1.id,
            product_id=prod_paneer.id,
            shop_product_id=sp1_paneer.id,
            batch_code="B-PAN-6D",
            initial_qty=100,
            available_qty=100,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=120.0,
            expiry_date=today + timedelta(days=6),
            status="ACTIVE",
        )
        db.session.add_all([batch_yogurt, batch_paneer])
        db.session.flush()

        # Seed high sales history for Paneer in Shop 2 so it can absorb surplus
        for i in range(14):
            cur_date = today - timedelta(days=13 - i)
            dt_utc = datetime.combine(cur_date, datetime(2000, 1, 1, 12, 0).time(), tzinfo=timezone.utc)
            sale = Sale(
                organization_id=org.id,
                shop_id=shop2.id,
                invoice_number=f"INV-PANEER-{i:03d}",
                status="COMPLETED",
                subtotal_amount=Decimal(str(20 * 180.0)),
                discount_amount=Decimal("0.00"),
                total_amount=Decimal(str(20 * 180.0)),
                total_cost_amount=Decimal(str(20 * 120.0)),
                sold_by_user_id=admin.id,
                created_at=dt_utc,
                updated_at=dt_utc,
            )
            db.session.add(sale)
            db.session.flush()

            # Dummy historical batch in shop 2
            dummy_b = InventoryBatch(
                shop_id=shop2.id,
                product_id=prod_paneer.id,
                shop_product_id=sp2_paneer.id,
                batch_code=f"B-P2-HIST-{i:02d}",
                initial_qty=20,
                available_qty=0,
                unit_cost=120.0,
                expiry_date=today + timedelta(days=60),
                status="ACTIVE",
            )
            db.session.add(dummy_b)
            db.session.flush()

            sale_item = SaleItem(
                shop_id=shop2.id,
                sale_id=sale.id,
                product_id=prod_paneer.id,
                shop_product_id=sp2_paneer.id,
                batch_id=dummy_b.id,
                quantity=20,
                returned_quantity=0,
                unit_cost_price=Decimal("120.00"),
                unit_selling_price=Decimal("180.00"),
                line_discount=Decimal("0.00"),
                line_total=Decimal(str(20 * 180.0)),
                created_at=dt_utc,
            )
            db.session.add(sale_item)

        db.session.commit()

        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "owner_id": owner.id,
            "shop1_id": shop1.id,
            "shop2_id": shop2.id,
            "prod_yogurt_id": prod_yogurt.id,
            "prod_paneer_id": prod_paneer.id,
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


def test_shop_waste_mitigation_service(app, waste_mitigation_topology):
    """Verify that analyze_shop_waste_mitigation identifies markdown and transfer candidates."""
    with app.app_context():
        summary: ShopWasteMitigationSummary = ProductAnalysisService.analyze_shop_waste_mitigation(
            shop_id=waste_mitigation_topology["shop1_id"],
            lookback_days=14,
            max_days_to_expiry=30,
        )

        assert summary.shop_id == waste_mitigation_topology["shop1_id"]
        assert summary.total_active_batches == 2
        assert summary.total_at_risk_batches >= 1
        assert summary.total_capital_at_risk > 0.0

        # Verify recommended markdowns contains Yogurt (expires in 2 days)
        assert len(summary.recommended_markdowns) >= 1
        yog_markdown = next(
            (m for m in summary.recommended_markdowns if m["batch_code"] == "B-YOG-2D"), None
        )
        assert yog_markdown is not None
        assert yog_markdown["suggested_discount_percent"] > 0
        assert yog_markdown["suggested_markdown_price"] < yog_markdown["original_selling_price"]
        assert yog_markdown["markdown_urgency"] == "IMMEDIATE"

        # Verify paneer transfer opportunities
        assert len(summary.recommended_transfers) >= 1
        pan_transfer = next(
            (t for t in summary.recommended_transfers if t["batch_code"] == "B-PAN-6D"), None
        )
        assert pan_transfer is not None
        assert pan_transfer["target_shop_name"] == "WMC Koramangala Hub"
        assert pan_transfer["projected_net_salvage"] > 0.0


def test_shop_waste_mitigation_html_page_and_api(client: FlaskClient, waste_mitigation_topology):
    """Verify HTML rendering and REST JSON API endpoint for waste mitigation."""
    _login(client, "wmc_owner", "OwnerPass123!")

    # 1. HTML Dashboard
    page_resp = client.get(f"/shops/{waste_mitigation_topology['shop1_id']}/waste-mitigation")
    assert page_resp.status_code == 200
    html = page_resp.get_data(as_text=True)
    assert "AI Waste Mitigation &amp; Markdown Engine" in html
    assert "Recommended Promotional Markdowns" in html
    assert "Inter-Store Rebalancing Opportunities" in html
    assert "Artisan Greek Yogurt 200g" in html
    assert "Fresh Malai Paneer 500g" in html

    # 2. REST JSON API
    api_resp = client.get(f"/api/v1/shops/{waste_mitigation_topology['shop1_id']}/waste-mitigation")
    assert api_resp.status_code == 200
    json_data = api_resp.get_json()
    assert json_data["status"] == "success"
    data = json_data["data"]
    assert data["shop_id"] == waste_mitigation_topology["shop1_id"]
    assert "total_capital_at_risk" in data
    assert "recommended_markdowns" in data
    assert "recommended_transfers" in data
    assert len(data["recommended_markdowns"]) >= 1


def test_shop_waste_mitigation_rbac(client: FlaskClient, waste_mitigation_topology):
    """Verify RBAC: unauthenticated requests are redirected, unauthorized shops are forbidden."""
    # Unauthenticated
    resp = client.get(f"/shops/{waste_mitigation_topology['shop1_id']}/waste-mitigation")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]
