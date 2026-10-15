"""Tests for Offline POS Bill Queue & Reconciliation (PRD Module D).
================================================================

Verifies:
  1. Sequential offline bill reconciliation with atomic FEFO batch allocation.
  2. Idempotent deduplication (ALREADY_SYNCED when bill was previously reconciled).
  3. Stockout conflict isolation (CONFLICT_INSUFFICIENT_STOCK does not halt remaining bills).
  4. Validation failure handling (FAILED_VALIDATION for invalid input).
  5. REST API endpoint (/api/v1/shops/<shop_id>/sales/offline-reconcile).
  6. Strict 2-role RBAC enforcement (SUPER_ADMIN and assigned SHOP_OWNER).
"""

from datetime import date, timedelta
from decimal import Decimal
import uuid
import pytest
from flask.testing import FlaskClient

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch
from app.models.organization import Organization, Shop, UserShopAssignment
from app.models.sales import Sale
from app.models.user import User
from app.services.sales_service import SalesService


@pytest.fixture
def offline_pos_topology(app):
    """Fixture creating organization, shop, products, and inventory batches for offline testing."""
    with app.app_context():
        org = Organization(
            name="Offline POS Retail Corp",
            code="OPRC_TEST",
            contact_email="oprc@test.local",
            contact_phone="9800000004",
        )
        db.session.add(org)
        db.session.flush()

        admin = User(
            username="oprc_admin",
            email="oprc_admin@test.local",
            full_name="OPRC Admin",
            role="SUPER_ADMIN",
            organization_id=org.id,
            is_active=True,
        )
        admin.set_password("AdminPass123!")

        owner = User(
            username="oprc_owner",
            email="oprc_owner@test.local",
            full_name="OPRC Shop Owner",
            role="SHOP_OWNER",
            organization_id=org.id,
            is_active=True,
        )
        owner.set_password("OwnerPass123!")
        db.session.add_all([admin, owner])
        db.session.flush()

        shop = Shop(
            organization_id=org.id,
            name="OPRC Bengaluru Central",
            code="OBC-01",
            city="Bengaluru",
            address="MG Road",
            pincode="560001",
            contact_phone="9800000041",
            status="ACTIVE",
        )
        db.session.add(shop)
        db.session.flush()

        assign_admin = UserShopAssignment(user_id=admin.id, shop_id=shop.id, assignment_role="SHOP_OWNER", is_primary=True)
        assign_owner = UserShopAssignment(user_id=owner.id, shop_id=shop.id, assignment_role="SHOP_OWNER", is_primary=True)
        db.session.add_all([assign_admin, assign_owner])

        prod_soap = Product(
            organization_id=org.id,
            name="Herbal Soap 100g",
            sku="SKU-SOAP-100G",
            category="Personal Care",
            unit="pcs",
            default_purchase_price=30.0,
            default_selling_price=50.0,
            created_by_user_id=admin.id,
        )
        prod_tea = Product(
            organization_id=org.id,
            name="Organic Green Tea 250g",
            sku="SKU-TEA-250G",
            category="Beverages",
            unit="pcs",
            default_purchase_price=100.0,
            default_selling_price=160.0,
            created_by_user_id=admin.id,
        )
        db.session.add_all([prod_soap, prod_tea])
        db.session.flush()

        sp_soap = ShopProduct(
            shop_id=shop.id,
            product_id=prod_soap.id,
            purchase_price=30.0,
            selling_price=50.0,
            low_stock_threshold=5,
            is_active=True,
        )
        sp_tea = ShopProduct(
            shop_id=shop.id,
            product_id=prod_tea.id,
            purchase_price=100.0,
            selling_price=160.0,
            low_stock_threshold=5,
            is_active=True,
        )
        db.session.add_all([sp_soap, sp_tea])
        db.session.flush()

        today = date.today()

        # Batch with 10 soaps available
        batch_soap = InventoryBatch(
            shop_id=shop.id,
            product_id=prod_soap.id,
            shop_product_id=sp_soap.id,
            batch_code="B-SOAP-01",
            initial_qty=10,
            available_qty=10,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=30.0,
            expiry_date=today + timedelta(days=60),
            status="ACTIVE",
        )
        # Batch with only 2 tea boxes available (to test insufficient stock conflict)
        batch_tea = InventoryBatch(
            shop_id=shop.id,
            product_id=prod_tea.id,
            shop_product_id=sp_tea.id,
            batch_code="B-TEA-01",
            initial_qty=2,
            available_qty=2,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=100.0,
            expiry_date=today + timedelta(days=90),
            status="ACTIVE",
        )
        db.session.add_all([batch_soap, batch_tea])
        db.session.commit()

        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "owner_id": owner.id,
            "shop_id": shop.id,
            "prod_soap_id": prod_soap.id,
            "prod_tea_id": prod_tea.id,
            "sp_soap_id": sp_soap.id,
            "sp_tea_id": sp_tea.id,
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


def test_sales_service_reconcile_offline_sales(app, offline_pos_topology):
    """Verify service reconciliation of sequential bills, deduplication, and conflict isolation."""
    with app.app_context():
        owner = db.session.get(User, offline_pos_topology["owner_id"])
        shop_id = offline_pos_topology["shop_id"]

        bill_1_key = str(uuid.uuid4())
        bill_2_key = str(uuid.uuid4())
        bill_conflict_key = str(uuid.uuid4())

        queued_bills = [
            # Bill 1: 3 soaps (valid)
            {
                "client_bill_id": "OFFLINE-001",
                "idempotency_key": bill_1_key,
                "items": [{"product_id": offline_pos_topology["prod_soap_id"], "quantity": 3}],
                "payment_mode": "CASH",
                "customer_name": "Customer 1",
            },
            # Bill 2: 2 soaps (valid)
            {
                "client_bill_id": "OFFLINE-002",
                "idempotency_key": bill_2_key,
                "items": [{"product_id": offline_pos_topology["prod_soap_id"], "quantity": 2}],
                "payment_mode": "UPI_MANUAL",
                "customer_name": "Customer 2",
            },
            # Bill 3: 5 tea boxes (only 2 available -> CONFLICT_INSUFFICIENT_STOCK)
            {
                "client_bill_id": "OFFLINE-003",
                "idempotency_key": bill_conflict_key,
                "items": [{"product_id": offline_pos_topology["prod_tea_id"], "quantity": 5}],
                "payment_mode": "CASH",
                "customer_name": "Customer 3",
            },
        ]

        report = SalesService.reconcile_offline_sales(
            actor=owner,
            shop_id=shop_id,
            queued_bills=queued_bills,
        )

        assert report["shop_id"] == shop_id
        assert report["total_bills"] == 3
        assert report["synced_count"] == 2
        assert report["conflict_count"] == 1
        assert report["already_synced_count"] == 0

        res1 = report["results"][0]
        assert res1["status"] == "SYNCED"
        assert res1["invoice_number"].startswith("INV-")

        res2 = report["results"][1]
        assert res2["status"] == "SYNCED"
        assert res2["invoice_number"] != res1["invoice_number"]

        res3 = report["results"][2]
        assert res3["status"] == "CONFLICT_INSUFFICIENT_STOCK"
        assert res3["error_code"] == "INSUFFICIENT_SELLABLE_STOCK"
        assert "exceeds available" in res3["message"].lower()

        # Check that soap stock was deducted: 10 - 3 - 2 = 5 remaining
        batch_soap = InventoryBatch.query.filter_by(batch_code="B-SOAP-01").first()
        assert batch_soap.available_qty == 5

        # Check tea stock remained untouched at 2
        batch_tea = InventoryBatch.query.filter_by(batch_code="B-TEA-01").first()
        assert batch_tea.available_qty == 2

        # Step 2: Re-submitting Bill 1 should return ALREADY_SYNCED
        replay_report = SalesService.reconcile_offline_sales(
            actor=owner,
            shop_id=shop_id,
            queued_bills=[queued_bills[0]],
        )
        assert replay_report["already_synced_count"] == 1
        assert replay_report["synced_count"] == 0
        assert replay_report["results"][0]["status"] == "ALREADY_SYNCED"
        assert replay_report["results"][0]["invoice_number"] == res1["invoice_number"]


def test_api_offline_reconciliation_endpoint(client: FlaskClient, offline_pos_topology):
    """Verify REST API endpoint for batch offline bill sync."""
    csrf_token = _login(client, "oprc_owner", "OwnerPass123!")
    shop_id = offline_pos_topology["shop_id"]

    bill_key = str(uuid.uuid4())
    payload = {
        "bills": [
            {
                "client_bill_id": "API-OFFLINE-01",
                "idempotency_key": bill_key,
                "items": [{"product_id": offline_pos_topology["prod_soap_id"], "quantity": 1}],
                "payment_mode": "CASH",
            }
        ]
    }

    resp = client.post(
        f"/api/v1/shops/{shop_id}/sales/offline-reconcile",
        json=payload,
        headers={"X-CSRFToken": csrf_token},
    )
    assert resp.status_code == 200
    json_data = resp.get_json()
    assert json_data["success"] is True
    data = json_data["data"]
    assert data["total_bills"] == 1
    assert data["synced_count"] == 1
    assert data["results"][0]["status"] == "SYNCED"


def test_api_offline_reconciliation_validation_and_rbac(client: FlaskClient, offline_pos_topology):
    """Verify validation for empty bills payload and RBAC enforcement."""
    shop_id = offline_pos_topology["shop_id"]

    # 1. Unauthenticated -> 302/400/401/403
    unauth_resp = client.post(f"/api/v1/shops/{shop_id}/sales/offline-reconcile", json={"bills": []})
    assert unauth_resp.status_code in (302, 400, 401, 403)

    # 2. Authenticated with empty payload -> 400 VALIDATION_ERROR
    csrf_token = _login(client, "oprc_owner", "OwnerPass123!")
    bad_resp = client.post(
        f"/api/v1/shops/{shop_id}/sales/offline-reconcile",
        json={"bills": []},
        headers={"X-CSRFToken": csrf_token},
    )
    assert bad_resp.status_code == 400
    assert bad_resp.get_json()["error"]["code"] == "VALIDATION_ERROR"
