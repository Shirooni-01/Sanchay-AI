"""Tests for Automated Stock Redistribution Recommendation Engine (PRD Module F).
=================================================================================

Verifies:
  1. Multi-shop stock redistribution matching between surplus and deficit shops.
  2. Haversine distance calculation, transit cost balancing, and net salvage profit.
  3. Priority scoring (CRITICAL for stockouts, HIGH for near-expiry surplus).
  4. HTML recommendations view (/shops/<shop_id>/transfers/recommendations).
  5. REST API endpoint (/api/v1/shops/<shop_id>/transfers/redistribution-recommendations).
  6. Strict 2-role RBAC enforcement (SUPER_ADMIN and assigned SHOP_OWNER).
"""

from datetime import date, timedelta
from decimal import Decimal
import pytest
from flask.testing import FlaskClient

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch
from app.models.organization import Organization, Shop, UserShopAssignment
from app.models.user import User
from app.services.transfer_service import TransferService


@pytest.fixture
def redistribution_topology(app):
    """Fixture creating organization with three shops in different stock states."""
    with app.app_context():
        org = Organization(
            name="Redistribution Retail Corp",
            code="RRC_TEST",
            contact_email="rrc@test.local",
            contact_phone="9800000006",
        )
        db.session.add(org)
        db.session.flush()

        admin = User(
            username="rrc_admin",
            email="rrc_admin@test.local",
            full_name="RRC Admin",
            role="SUPER_ADMIN",
            organization_id=org.id,
            is_active=True,
        )
        admin.set_password("AdminPass123!")

        owner_a = User(
            username="rrc_owner_a",
            email="rrc_owner_a@test.local",
            full_name="RRC Owner A",
            role="SHOP_OWNER",
            organization_id=org.id,
            is_active=True,
        )
        owner_a.set_password("OwnerPass123!")

        owner_b = User(
            username="rrc_owner_b",
            email="rrc_owner_b@test.local",
            full_name="RRC Owner B",
            role="SHOP_OWNER",
            organization_id=org.id,
            is_active=True,
        )
        owner_b.set_password("OwnerPass123!")
        db.session.add_all([admin, owner_a, owner_b])
        db.session.flush()

        # Shop 1 (Source Shop with Surplus): Bengaluru Indiranagar
        shop_src = Shop(
            organization_id=org.id,
            name="RRC Indiranagar Surplus Hub",
            code="RRC-SRC-01",
            city="Bengaluru",
            address="Indiranagar",
            pincode="560038",
            latitude=12.9716,
            longitude=77.6412,
            contact_phone="9800000061",
            status="ACTIVE",
        )
        # Shop 2 (Recipient with 0 Stock / Out-of-Stock): Bengaluru Koramangala (~5.2 km away)
        shop_rec_oos = Shop(
            organization_id=org.id,
            name="RRC Koramangala Stockout Depot",
            code="RRC-OOS-02",
            city="Bengaluru",
            address="Koramangala",
            pincode="560034",
            latitude=12.9352,
            longitude=77.6245,
            contact_phone="9800000062",
            status="ACTIVE",
        )
        # Shop 3 (Recipient with Low Stock): Bengaluru Whitefield (~12.5 km away)
        shop_rec_low = Shop(
            organization_id=org.id,
            name="RRC Whitefield Low Stock Depot",
            code="RRC-LOW-03",
            city="Bengaluru",
            address="Whitefield",
            pincode="560066",
            latitude=12.9698,
            longitude=77.7500,
            contact_phone="9800000063",
            status="ACTIVE",
        )
        db.session.add_all([shop_src, shop_rec_oos, shop_rec_low])
        db.session.flush()

        assign_admin = UserShopAssignment(user_id=admin.id, shop_id=shop_src.id, assignment_role="SHOP_OWNER", is_primary=True)
        assign_owner_a = UserShopAssignment(user_id=owner_a.id, shop_id=shop_src.id, assignment_role="SHOP_OWNER", is_primary=True)
        assign_owner_b = UserShopAssignment(user_id=owner_b.id, shop_id=shop_rec_oos.id, assignment_role="SHOP_OWNER", is_primary=True)
        db.session.add_all([assign_admin, assign_owner_a, assign_owner_b])

        # High-value product: Cold Pressed Extra Virgin Coconut Oil
        prod = Product(
            organization_id=org.id,
            name="Cold Pressed Coconut Oil 1L",
            sku="SKU-COCO-1L",
            category="Cooking Oils",
            unit="pcs",
            default_purchase_price=200.0,
            default_selling_price=350.0,
            created_by_user_id=admin.id,
        )
        db.session.add(prod)
        db.session.flush()

        # Configure ShopProducts across all 3 shops
        sp_src = ShopProduct(
            shop_id=shop_src.id,
            product_id=prod.id,
            purchase_price=200.0,
            selling_price=350.0,
            low_stock_threshold=10,
            is_active=True,
        )
        sp_oos = ShopProduct(
            shop_id=shop_rec_oos.id,
            product_id=prod.id,
            purchase_price=200.0,
            selling_price=350.0,
            low_stock_threshold=15,
            is_active=True,
        )
        sp_low = ShopProduct(
            shop_id=shop_rec_low.id,
            product_id=prod.id,
            purchase_price=200.0,
            selling_price=350.0,
            low_stock_threshold=20,
            is_active=True,
        )
        db.session.add_all([sp_src, sp_oos, sp_low])
        db.session.flush()

        today = date.today()

        # Shop 1 has surplus batch: 100 units expiring in 6 days
        batch_surplus = InventoryBatch(
            shop_id=shop_src.id,
            product_id=prod.id,
            shop_product_id=sp_src.id,
            batch_code="B-COCO-SURPLUS",
            initial_qty=100,
            available_qty=100,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=200.0,
            expiry_date=today + timedelta(days=6),
            status="ACTIVE",
        )
        # Shop 2 (shop_rec_oos) has 0 units (complete stockout)

        # Shop 3 (shop_rec_low) has 5 units (below threshold of 20)
        batch_low = InventoryBatch(
            shop_id=shop_rec_low.id,
            product_id=prod.id,
            shop_product_id=sp_low.id,
            batch_code="B-COCO-LOW",
            initial_qty=10,
            available_qty=5,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=200.0,
            expiry_date=today + timedelta(days=90),
            status="ACTIVE",
        )
        db.session.add_all([batch_surplus, batch_low])
        db.session.commit()

        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "owner_a_id": owner_a.id,
            "owner_b_id": owner_b.id,
            "shop_src_id": shop_src.id,
            "shop_rec_oos_id": shop_rec_oos.id,
            "shop_rec_low_id": shop_rec_low.id,
            "prod_id": prod.id,
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


def test_redistribution_recommendations_engine(app, redistribution_topology):
    """Verify recommendation generation, priority ranking, and salvage profit math."""
    with app.app_context():
        owner_a = db.session.get(User, redistribution_topology["owner_a_id"])
        shop_src_id = redistribution_topology["shop_src_id"]

        recs = TransferService.get_redistribution_recommendations(
            actor=owner_a,
            shop_id=shop_src_id,
            max_distance_km=50.0,
        )

        assert len(recs) >= 1

        # The first recommendation should be for Shop 2 (Out of stock -> CRITICAL)
        top_rec = recs[0]
        assert top_rec["source_shop_id"] == shop_src_id
        assert top_rec["recipient_shop_name"] == "RRC Koramangala Stockout Depot"
        assert top_rec["priority"] == "CRITICAL"
        assert top_rec["recipient_current_stock"] == 0
        assert top_rec["distance_km"] > 0
        assert top_rec["estimated_transport_cost"] >= 50.0
        assert top_rec["net_salvage_profit"] > 0.0
        assert top_rec["recommended_transfer_qty"] > 0
        assert "stockout" in top_rec["rationale"].lower()


def test_redistribution_recommendations_html_and_api(client: FlaskClient, redistribution_topology):
    """Verify HTML recommendations view and REST API endpoint."""
    _login(client, "rrc_owner_a", "OwnerPass123!")
    shop_src_id = redistribution_topology["shop_src_id"]

    # 1. HTML View
    page_resp = client.get(f"/shops/{shop_src_id}/transfers/recommendations")
    assert page_resp.status_code == 200
    html = page_resp.get_data(as_text=True)
    assert "Automated Stock Redistribution Engine" in html
    assert "RRC Koramangala Stockout Depot" in html
    assert "Cold Pressed Coconut Oil 1L" in html
    assert "Convert to Proposal" in html

    # 2. REST API
    api_resp = client.get(f"/api/v1/shops/{shop_src_id}/transfers/redistribution-recommendations")
    assert api_resp.status_code == 200
    json_data = api_resp.get_json()
    assert json_data["success"] is True
    data = json_data["data"]
    assert data["shop_id"] == shop_src_id
    assert data["count"] >= 1
    recs = data["recommendations"]
    assert recs[0]["priority"] in ("CRITICAL", "HIGH", "MEDIUM")


def test_redistribution_recommendations_rbac(client: FlaskClient, redistribution_topology):
    """Verify RBAC: unassigned owner receives 403 Forbidden when trying to access another shop's recommendations."""
    _login(client, "rrc_owner_b", "OwnerPass123!")
    shop_src_id = redistribution_topology["shop_src_id"]

    resp = client.get(f"/shops/{shop_src_id}/transfers/recommendations")
    assert resp.status_code == 403
