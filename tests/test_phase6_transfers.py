"""Comprehensive automated tests for Phase 6: Inter-Store Stock Exchange & Transfers.

Covers:
1. Eligible recipient shop listing & demand indicators.
2. Transfer offer proposal creation (source-initiated and recipient-initiated).
3. Available stock validation & open proposal reservation boundaries.
4. Counterparty approval workflow & atomic stock reservation (SELECT ... FOR UPDATE).
5. Rejection workflow & reservation release.
6. Cancellation workflow & reservation release.
7. Double-sell prevention & POS isolation while stock is reserved.
8. Physical dispatch workflow (DISPATCHED_IN_TRANSIT).
9. Atomic receipt confirmation & completion (TRANSFER_OUT and TRANSFER_IN).
10. Exact expiry date & batch details preservation in recipient inventory.
11. Transit damage handling & stock bucket reconciliation.
12. Status audit timeline, network surplus offers endpoint, and HTML views.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch, StockMovement
from app.models.organization import Organization, Shop
from app.models.transfer import TransferRequest, TransferStatusLog
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.catalog_service import CatalogService
from app.services.inventory_service import InventoryService, get_business_today
from app.services.org_service import MembershipService, OrganizationService, ShopService
from app.services.sales_service import SalesService
from app.services.transfer_service import TransferService
from tests.conftest import extract_csrf_token


def _login(client: FlaskClient, identity: str, password: str = "ValidPassword123") -> str:
    """Authenticate a test client via JSON API, rotate CSRF, and set default X-CSRFToken header."""
    client.environ_base.pop("HTTP_X_CSRFTOKEN", None)
    login_page = client.get("/auth/login")
    if login_page.status_code == 302:
        landing = client.get("/dashboard")
        if landing.status_code == 302:
            landing = client.get("/")
        active_csrf = extract_csrf_token(landing.get_data(as_text=True))
        client.post("/api/v1/auth/logout", headers={"X-CSRFToken": active_csrf})
        login_page = client.get("/auth/login")

    csrf_token = extract_csrf_token(login_page.get_data(as_text=True))
    resp = client.post(
        "/api/v1/auth/login",
        json={"identifier": identity, "password": password},
        headers={"X-CSRFToken": csrf_token},
    )
    assert resp.status_code == 200, f"Login failed for {identity}: {resp.get_json()}"

    dash_page = client.get("/dashboard")
    if dash_page.status_code == 302:
        dash_page = client.get("/shops")
    fresh_csrf = extract_csrf_token(dash_page.get_data(as_text=True))
    client.environ_base["HTTP_X_CSRFTOKEN"] = fresh_csrf
    return fresh_csrf


def _setup_phase6_topology(app: Flask) -> dict:
    """Create organization with 3 shops (Shop A, Shop B, Shop C) and another org for tenant isolation."""
    with app.app_context():
        # Super admin
        admin = AuthService.create_user(
            username="p6_superadmin",
            email="p6_admin@sanchay.local",
            password="ValidPassword123",
            full_name="Phase6 Super Admin",
            role="SUPER_ADMIN",
        )

        # Organization 1
        org1 = OrganizationService.create_organization(
            actor=admin,
            payload={
                "name": "Sanchay Retail Org P6",
                "code": "SRO-P6",
                "contact_email": "ops@srop6.local",
                "city": "Pune",
                "state": "Maharashtra",
                "low_stock_default_threshold": 10,
                "near_expiry_days_threshold": 7,
            },
        )

        # Shop A (Source shop)
        shop_a = ShopService.create_shop(
            actor=admin,
            payload={
                "organization_id": org1.id,
                "name": "Shop A - Baner",
                "code": "BNR01",
                "address": "Baner High Street",
                "city": "Pune",
                "pincode": "411045",
                "contact_phone": "9811111111",
            },
        )

        # Shop B (Recipient shop)
        shop_b = ShopService.create_shop(
            actor=admin,
            payload={
                "organization_id": org1.id,
                "name": "Shop B - Kothrud",
                "code": "KTH02",
                "address": "Paud Road, Kothrud",
                "city": "Pune",
                "pincode": "411038",
                "contact_phone": "9822222222",
            },
        )

        # Shop C (Peer shop in same org)
        shop_c = ShopService.create_shop(
            actor=admin,
            payload={
                "organization_id": org1.id,
                "name": "Shop C - Hinjewadi",
                "code": "HIN03",
                "address": "Phase 1, Hinjewadi",
                "city": "Pune",
                "pincode": "411057",
                "contact_phone": "9833333333",
            },
        )

        # Shop Owners & Staff
        owner_a = AuthService.create_user(
            username="p6_owner_a",
            email="owner_a@sanchay.local",
            password="ValidPassword123",
            full_name="Owner Shop A",
            role="SHOP_OWNER",
            organization_id=org1.id,
        )
        MembershipService.assign_user_to_shop(
            actor=admin,
            shop_id=shop_a.id,
            payload={"user_id": owner_a.id, "assignment_role": "SHOP_OWNER", "is_primary": True},
        )

        staff_a = AuthService.create_user(
            username="p6_staff_a",
            email="staff_a@sanchay.local",
            password="ValidPassword123",
            full_name="Staff Shop A",
            role="STAFF",
            organization_id=org1.id,
        )
        MembershipService.assign_user_to_shop(
            actor=admin,
            shop_id=shop_a.id,
            payload={"user_id": staff_a.id, "assignment_role": "STAFF", "is_primary": True},
        )

        owner_b = AuthService.create_user(
            username="p6_owner_b",
            email="owner_b@sanchay.local",
            password="ValidPassword123",
            full_name="Owner Shop B",
            role="SHOP_OWNER",
            organization_id=org1.id,
        )
        MembershipService.assign_user_to_shop(
            actor=admin,
            shop_id=shop_b.id,
            payload={"user_id": owner_b.id, "assignment_role": "SHOP_OWNER", "is_primary": True},
        )

        # Organization 2 (Different tenant to test isolation)
        admin2 = AuthService.create_user(
            username="p6_other_admin",
            email="p6_other@rival.local",
            password="ValidPassword123",
            full_name="Rival Org Admin",
            role="SUPER_ADMIN",
        )
        org2 = OrganizationService.create_organization(
            actor=admin2,
            payload={
                "name": "Other Org P6",
                "code": "OTH-P6",
                "contact_email": "ops@other.local",
            },
        )
        shop_other = ShopService.create_shop(
            actor=admin2,
            payload={
                "organization_id": org2.id,
                "name": "Other Org Store",
                "code": "OTH01",
                "address": "Camp",
                "city": "Pune",
                "pincode": "411001",
                "contact_phone": "9899999999",
            },
        )

        # Create Products
        prod_paneer = CatalogService.create_organization_product(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "sku": "SKU-P6-PANEER-200G",
                "name": "Fresh Paneer 200g",
                "category": "Dairy",
                "unit": "pack",
                "default_purchase_price": "70.00",
                "default_selling_price": "90.00",
                "reorder_threshold": 10,
                "is_perishable": True,
            },
        )

        prod_ghee = CatalogService.create_organization_product(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "sku": "SKU-P6-GHEE-1L",
                "name": "Pure Cow Ghee 1L",
                "category": "Dairy",
                "unit": "pcs",
                "default_purchase_price": "550.00",
                "default_selling_price": "650.00",
                "reorder_threshold": 5,
                "is_perishable": True,
            },
        )

        # Enroll paneer in Shop B as well
        CatalogService.create_or_enroll_shop_product(
            actor=owner_b,
            shop_id=shop_b.id,
            payload={
                "product_id": prod_paneer.id,
                "purchase_price": "70.00",
                "selling_price": "90.00",
                "low_stock_threshold": 15,
            },
        )

        # Receive Batch in Shop A (50 units, expiring in 10 days)
        today = get_business_today()
        exp_date = today + timedelta(days=10)
        batch_a, _ = InventoryService.receive_batch(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "product_id": prod_paneer.id,
                "batch_code": "BATCH-PNR-A01",
                "initial_qty": 50,
                "unit_cost": "70.00",
                "expiry_date": exp_date.isoformat(),
            },
        )

        # Receive a batch in Shop A and set its expiry date in the past for validation tests
        batch_exp, _ = InventoryService.receive_batch(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "product_id": prod_paneer.id,
                "batch_code": "BATCH-PNR-EXPIRED",
                "initial_qty": 20,
                "unit_cost": "70.00",
                "expiry_date": (today + timedelta(days=1)).isoformat(),
            },
        )
        batch_exp.expiry_date = today - timedelta(days=2)

        db.session.commit()

        return {
            "admin_id": admin.id,
            "admin_username": "p6_superadmin",
            "org1_id": org1.id,
            "org2_id": org2.id,
            "shop_a_id": shop_a.id,
            "shop_b_id": shop_b.id,
            "shop_c_id": shop_c.id,
            "shop_other_id": shop_other.id,
            "owner_a_id": owner_a.id,
            "owner_a_username": "p6_owner_a",
            "staff_a_id": staff_a.id,
            "staff_a_username": "p6_staff_a",
            "owner_b_id": owner_b.id,
            "owner_b_username": "p6_owner_b",
            "prod_paneer_id": prod_paneer.id,
            "prod_ghee_id": prod_ghee.id,
            "batch_a_id": batch_a.id,
            "batch_exp_id": batch_exp.id,
            "exp_date": exp_date,
            "exp_date_str": exp_date.isoformat(),
        }


# ============================================================================
# Test Cases
# ============================================================================


def test_eligible_recipient_shops_listing(app: Flask, client: FlaskClient):
    """Test 1: Listing eligible sister shops excludes source shop and outside orgs, showing demand."""
    ctx = _setup_phase6_topology(app)
    _login(client, ctx["owner_a_username"])

    resp = client.get(
        f"/api/v1/shops/{ctx['shop_a_id']}/transfer-recipients?product_id={ctx['prod_paneer_id']}"
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    recipients = data["recipients"]

    # Must contain Shop B and Shop C, but NOT Shop A and NOT Shop Other
    recipient_ids = [r["shop_id"] for r in recipients]
    assert ctx["shop_b_id"] in recipient_ids
    assert ctx["shop_c_id"] in recipient_ids
    assert ctx["shop_a_id"] not in recipient_ids
    assert ctx["shop_other_id"] not in recipient_ids

    # Shop B has 0 stock of paneer and threshold of 15 -> needs_replenishment=True
    shop_b_info = next(r for r in recipients if r["shop_id"] == ctx["shop_b_id"])
    assert shop_b_info["current_stock"] == 0
    assert shop_b_info["is_out_of_stock"] is True
    assert shop_b_info["needs_replenishment"] is True


def test_transfer_proposal_creation_by_source_owner(app: Flask, client: FlaskClient):
    """Test 2: Source shop owner proposes transfer, generates unique code, snapshots expiry."""
    ctx = _setup_phase6_topology(app)
    _login(client, ctx["owner_a_username"])

    resp = client.post(
        "/api/v1/transfers",
        json={
            "source_shop_id": ctx["shop_a_id"],
            "recipient_shop_id": ctx["shop_b_id"],
            "product_id": ctx["prod_paneer_id"],
            "source_batch_id": ctx["batch_a_id"],
            "quantity": 20,
            "unit_transfer_price": "72.00",
            "offer_duration_days": 7,
            "notes": "Surplus paneer redistribution to Kothrud store",
        },
    )
    assert resp.status_code == 201
    res_data = resp.get_json()["data"]
    tr = res_data["transfer"]

    assert tr["transfer_code"].startswith("TRF-")
    assert tr["status"] == "PENDING_RECIPIENT_APPROVAL"
    assert tr["source_approved_by_user_id"] == ctx["owner_a_id"]
    assert tr["source_approved_at"] is not None
    assert tr["recipient_approved_at"] is None
    assert tr["quantity"] == 20
    assert tr["unit_transfer_price"] == 72.00
    assert tr["batch_expiry_date"] == ctx["exp_date_str"]

    # Verify no stock was reserved yet at proposal stage
    with app.app_context():
        batch = db.session.query(InventoryBatch).get(ctx["batch_a_id"])
        assert batch.available_qty == 50
        assert batch.reserved_qty == 0

        # Verify initial status log
        transfer_db = db.session.query(TransferRequest).get(tr["id"])
        assert len(transfer_db.status_logs) == 1
        assert transfer_db.status_logs[0].to_status == "PENDING_RECIPIENT_APPROVAL"


def test_proposal_validation_insufficient_stock_and_expired(app: Flask, client: FlaskClient):
    """Test 3: Validations for excessive quantity, open proposals, expired stock, and cross-org."""
    ctx = _setup_phase6_topology(app)
    _login(client, ctx["owner_a_username"])

    # A: Quantity > available stock (50 available, request 60)
    resp = client.post(
        "/api/v1/transfers",
        json={
            "source_shop_id": ctx["shop_a_id"],
            "recipient_shop_id": ctx["shop_b_id"],
            "product_id": ctx["prod_paneer_id"],
            "source_batch_id": ctx["batch_a_id"],
            "quantity": 60,
            "unit_transfer_price": "70.00",
        },
    )
    assert resp.status_code in (400, 409)

    # B: Propose from expired batch
    resp_exp = client.post(
        "/api/v1/transfers",
        json={
            "source_shop_id": ctx["shop_a_id"],
            "recipient_shop_id": ctx["shop_b_id"],
            "product_id": ctx["prod_paneer_id"],
            "source_batch_id": ctx["batch_exp_id"],
            "quantity": 5,
            "unit_transfer_price": "70.00",
        },
    )
    assert resp_exp.status_code in (400, 409)

    # C: Cross-organization proposal rejected
    resp_org = client.post(
        "/api/v1/transfers",
        json={
            "source_shop_id": ctx["shop_a_id"],
            "recipient_shop_id": ctx["shop_other_id"],
            "product_id": ctx["prod_paneer_id"],
            "source_batch_id": ctx["batch_a_id"],
            "quantity": 10,
            "unit_transfer_price": "70.00",
        },
    )
    assert resp_org.status_code in (400, 403, 404)

    # D: Same shop rejected
    resp_same = client.post(
        "/api/v1/transfers",
        json={
            "source_shop_id": ctx["shop_a_id"],
            "recipient_shop_id": ctx["shop_a_id"],
            "product_id": ctx["prod_paneer_id"],
            "source_batch_id": ctx["batch_a_id"],
            "quantity": 10,
            "unit_transfer_price": "70.00",
        },
    )
    assert resp_same.status_code in (400, 422)


def test_recipient_approval_reserves_stock_atomically(app: Flask, client: FlaskClient):
    """Test 4: Recipient owner approves -> APPROVED_RESERVED, stock atomically reserved."""
    ctx = _setup_phase6_topology(app)

    # Step 1: Owner A creates proposal for 15 units
    with app.app_context():
        owner_a = db.session.query(User).get(ctx["owner_a_id"])
        transfer = TransferService.create_transfer_proposal(
            actor=owner_a,
            payload={
                "source_shop_id": ctx["shop_a_id"],
                "recipient_shop_id": ctx["shop_b_id"],
                "product_id": ctx["prod_paneer_id"],
                "source_batch_id": ctx["batch_a_id"],
                "quantity": 15,
                "unit_transfer_price": "72.00",
            },
        )
        transfer_id = transfer.id

    # Step 2: Owner B logs in and approves proposal
    _login(client, ctx["owner_b_username"])
    resp = client.post(
        f"/api/v1/transfers/{transfer_id}/approve",
        json={"notes": "Offer accepted by Kothrud owner"},
    )
    assert resp.status_code == 200
    res_data = resp.get_json()["data"]
    assert res_data["transfer"]["status"] == "APPROVED_RESERVED"
    assert res_data["source_batch_balance"]["available_qty"] == 35
    assert res_data["source_batch_balance"]["reserved_qty"] == 15

    # Verify database state & stock movements ledger
    with app.app_context():
        batch = db.session.query(InventoryBatch).get(ctx["batch_a_id"])
        assert batch.available_qty == 35
        assert batch.reserved_qty == 15
        assert batch.on_hand_qty == 50

        # Check ledger entries for TRANSFER_RESERVE
        movements = (
            db.session.query(StockMovement)
            .filter(
                StockMovement.reference_type == "TRANSFER",
                StockMovement.reference_id == transfer_id,
            )
            .order_by(StockMovement.id.asc())
            .all()
        )
        assert len(movements) == 2
        m_avail = next(m for m in movements if m.bucket_affected == "AVAILABLE")
        m_res = next(m for m in movements if m.bucket_affected == "RESERVED")

        assert m_avail.quantity_change == -15
        assert m_avail.balance_before == 50
        assert m_avail.balance_after == 35
        assert m_avail.movement_type == "TRANSFER_RESERVE"

        assert m_res.quantity_change == 15
        assert m_res.balance_before == 0
        assert m_res.balance_after == 15
        assert m_res.movement_type == "TRANSFER_RESERVE"


def test_recipient_initiated_proposal_and_source_approval(app: Flask, client: FlaskClient):
    """Test 5: Recipient owner initiates transfer request, then source owner approves."""
    ctx = _setup_phase6_topology(app)

    # Recipient owner proposes (requests stock)
    with app.app_context():
        owner_b = db.session.query(User).get(ctx["owner_b_id"])
        transfer = TransferService.create_transfer_proposal(
            actor=owner_b,
            payload={
                "source_shop_id": ctx["shop_a_id"],
                "recipient_shop_id": ctx["shop_b_id"],
                "product_id": ctx["prod_paneer_id"],
                "source_batch_id": ctx["batch_a_id"],
                "quantity": 12,
                "unit_transfer_price": "70.00",
            },
        )
        assert transfer.status == "PENDING_SOURCE_APPROVAL"
        assert transfer.recipient_approved_at is not None
        assert transfer.source_approved_at is None
        t_id = transfer.id

    # Source owner logs in and approves
    _login(client, ctx["owner_a_username"])
    resp = client.post(
        f"/api/v1/transfers/{t_id}/approve",
        json={"notes": "Approved by source shop"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["transfer"]["status"] == "APPROVED_RESERVED"

    with app.app_context():
        batch = db.session.query(InventoryBatch).get(ctx["batch_a_id"])
        assert batch.available_qty == 38
        assert batch.reserved_qty == 12


def test_rejection_workflow_and_reservation_release(app: Flask, client: FlaskClient):
    """Test 6: Rejecting an APPROVED_RESERVED transfer releases reservation back to available_qty."""
    ctx = _setup_phase6_topology(app)

    with app.app_context():
        owner_a = db.session.query(User).get(ctx["owner_a_id"])
        owner_b = db.session.query(User).get(ctx["owner_b_id"])
        transfer = TransferService.create_transfer_proposal(
            actor=owner_a,
            payload={
                "source_shop_id": ctx["shop_a_id"],
                "recipient_shop_id": ctx["shop_b_id"],
                "product_id": ctx["prod_paneer_id"],
                "source_batch_id": ctx["batch_a_id"],
                "quantity": 10,
                "unit_transfer_price": "72.00",
            },
        )
        TransferService.approve_transfer(actor=owner_b, transfer_id=transfer.id)
        t_id = transfer.id

    # Verify stock was reserved
    with app.app_context():
        b = db.session.query(InventoryBatch).get(ctx["batch_a_id"])
        assert b.available_qty == 40
        assert b.reserved_qty == 10

    # Counterparty rejects transfer
    _login(client, ctx["owner_b_username"])
    resp = client.post(
        f"/api/v1/transfers/{t_id}/reject",
        json={"reason": "Delivery schedule cannot be accommodated"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["transfer"]["status"] == "REJECTED"

    # Verify reservation was released!
    with app.app_context():
        b_after = db.session.query(InventoryBatch).get(ctx["batch_a_id"])
        assert b_after.available_qty == 50
        assert b_after.reserved_qty == 0

        # Verify ledger has TRANSFER_RELEASE
        release_m = (
            db.session.query(StockMovement)
            .filter(
                StockMovement.reference_type == "TRANSFER",
                StockMovement.reference_id == t_id,
                StockMovement.movement_type == "TRANSFER_RELEASE",
            )
            .all()
        )
        assert len(release_m) == 2


def test_cancellation_workflow_and_reservation_release(app: Flask, client: FlaskClient):
    """Test 7: Initiator or shop owner cancels transfer prior to dispatch, releasing reservation."""
    ctx = _setup_phase6_topology(app)

    with app.app_context():
        owner_a = db.session.query(User).get(ctx["owner_a_id"])
        owner_b = db.session.query(User).get(ctx["owner_b_id"])
        transfer = TransferService.create_transfer_proposal(
            actor=owner_a,
            payload={
                "source_shop_id": ctx["shop_a_id"],
                "recipient_shop_id": ctx["shop_b_id"],
                "product_id": ctx["prod_paneer_id"],
                "source_batch_id": ctx["batch_a_id"],
                "quantity": 10,
                "unit_transfer_price": "72.00",
            },
        )
        TransferService.approve_transfer(actor=owner_b, transfer_id=transfer.id)
        t_id = transfer.id

    _login(client, ctx["owner_a_username"])
    resp = client.post(
        f"/api/v1/transfers/{t_id}/cancel",
        json={"reason": "Local surge in demand at source shop"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["transfer"]["status"] == "CANCELLED"

    with app.app_context():
        b_after = db.session.query(InventoryBatch).get(ctx["batch_a_id"])
        assert b_after.available_qty == 50
        assert b_after.reserved_qty == 0


def test_stock_reservation_prevents_pos_overselling(app: Flask, client: FlaskClient):
    """Test 8: Reserved stock cannot be sold in POS (FEFO allocator respects reserved_qty)."""
    ctx = _setup_phase6_topology(app)

    # Total batch stock is 50. Reserve 45 units for transfer.
    with app.app_context():
        owner_a = db.session.query(User).get(ctx["owner_a_id"])
        owner_b = db.session.query(User).get(ctx["owner_b_id"])
        transfer = TransferService.create_transfer_proposal(
            actor=owner_a,
            payload={
                "source_shop_id": ctx["shop_a_id"],
                "recipient_shop_id": ctx["shop_b_id"],
                "product_id": ctx["prod_paneer_id"],
                "source_batch_id": ctx["batch_a_id"],
                "quantity": 45,
                "unit_transfer_price": "72.00",
            },
        )
        TransferService.approve_transfer(actor=owner_b, transfer_id=transfer.id)

    # In Shop A, only 5 available units remain
    _login(client, ctx["owner_a_username"])

    # Attempting to sell 10 units via POS must fail with InsufficientStock
    resp_oversell = client.post(
        f"/api/v1/shops/{ctx['shop_a_id']}/sales",
        json={
            "items": [{"product_id": ctx["prod_paneer_id"], "quantity": 10}],
            "payment_mode": "CASH",
        },
    )
    assert resp_oversell.status_code in (400, 409)

    # Selling 5 units (exact available remainder) succeeds
    resp_exact = client.post(
        f"/api/v1/shops/{ctx['shop_a_id']}/sales",
        json={
            "items": [{"product_id": ctx["prod_paneer_id"], "quantity": 5}],
            "payment_mode": "CASH",
        },
    )
    assert resp_exact.status_code == 201

    with app.app_context():
        b = db.session.query(InventoryBatch).get(ctx["batch_a_id"])
        assert b.available_qty == 0
        assert b.reserved_qty == 45


def test_physical_dispatch_workflow(app: Flask, client: FlaskClient):
    """Test 9: Handover from APPROVED_RESERVED -> DISPATCHED_IN_TRANSIT."""
    ctx = _setup_phase6_topology(app)

    with app.app_context():
        owner_a = db.session.query(User).get(ctx["owner_a_id"])
        owner_b = db.session.query(User).get(ctx["owner_b_id"])
        transfer = TransferService.create_transfer_proposal(
            actor=owner_a,
            payload={
                "source_shop_id": ctx["shop_a_id"],
                "recipient_shop_id": ctx["shop_b_id"],
                "product_id": ctx["prod_paneer_id"],
                "source_batch_id": ctx["batch_a_id"],
                "quantity": 10,
                "unit_transfer_price": "72.00",
            },
        )
        TransferService.approve_transfer(actor=owner_b, transfer_id=transfer.id)
        t_id = transfer.id

    # Dispatched by staff A
    _login(client, ctx["staff_a_username"])
    resp = client.post(
        f"/api/v1/transfers/{t_id}/dispatch",
        json={
            "carrier_reference": "DL-01-EXP-9999",
            "dispatch_notes": "Driver Ramesh handed 10 packs in insulated cooling box",
        },
    )
    assert resp.status_code == 200
    res_data = resp.get_json()["data"]
    assert res_data["transfer"]["status"] == "DISPATCHED_IN_TRANSIT"
    assert res_data["transfer"]["dispatched_by_user_id"] == ctx["staff_a_id"]
    assert res_data["transfer"]["dispatched_at"] is not None


def test_atomic_receipt_completion_and_expiry_preservation(app: Flask, client: FlaskClient):
    """Test 10: Physical receipt atomically executes TRANSFER_OUT and TRANSFER_IN, preserving expiry date."""
    ctx = _setup_phase6_topology(app)

    with app.app_context():
        owner_a = db.session.query(User).get(ctx["owner_a_id"])
        owner_b = db.session.query(User).get(ctx["owner_b_id"])
        transfer = TransferService.create_transfer_proposal(
            actor=owner_a,
            payload={
                "source_shop_id": ctx["shop_a_id"],
                "recipient_shop_id": ctx["shop_b_id"],
                "product_id": ctx["prod_paneer_id"],
                "source_batch_id": ctx["batch_a_id"],
                "quantity": 25,
                "unit_transfer_price": "72.00",
            },
        )
        TransferService.approve_transfer(actor=owner_b, transfer_id=transfer.id)
        TransferService.dispatch_transfer(actor=owner_a, transfer_id=transfer.id)
        t_id = transfer.id

    # Recipient owner confirms receipt
    _login(client, ctx["owner_b_username"])
    resp = client.post(
        f"/api/v1/transfers/{t_id}/confirm-receipt",
        json={
            "received_good_qty": 25,
            "transit_damaged_qty": 0,
            "receipt_notes": "All 25 units received in chilled condition",
        },
    )
    assert resp.status_code == 200
    res_data = resp.get_json()["data"]
    assert res_data["transfer"]["status"] == "COMPLETED"
    rec_batch_id = res_data["recipient_batch_id"]

    # Verify database state in both shops!
    with app.app_context():
        # Source batch: reserved units transferred out
        src_batch = db.session.query(InventoryBatch).get(ctx["batch_a_id"])
        assert src_batch.available_qty == 25
        assert src_batch.reserved_qty == 0
        assert src_batch.on_hand_qty == 25

        # Recipient batch: exists in Shop B, has 25 available units
        dst_batch = db.session.query(InventoryBatch).get(rec_batch_id)
        assert dst_batch.shop_id == ctx["shop_b_id"]
        assert dst_batch.product_id == ctx["prod_paneer_id"]
        assert dst_batch.available_qty == 25
        assert dst_batch.status == "ACTIVE"
        assert dst_batch.source_transfer_id == t_id

        # STRICT EXPIRY DATE PRESERVATION (BR-011)
        assert dst_batch.expiry_date == ctx["exp_date"]
        assert dst_batch.batch_code == src_batch.batch_code

        # Check stock movements on both sides
        m_out = (
            db.session.query(StockMovement)
            .filter(
                StockMovement.reference_type == "TRANSFER",
                StockMovement.reference_id == t_id,
                StockMovement.movement_type == "TRANSFER_OUT",
            )
            .one()
        )
        assert m_out.shop_id == ctx["shop_a_id"]
        assert m_out.quantity_change == -25

        m_in = (
            db.session.query(StockMovement)
            .filter(
                StockMovement.reference_type == "TRANSFER",
                StockMovement.reference_id == t_id,
                StockMovement.movement_type == "TRANSFER_IN",
            )
            .one()
        )
        assert m_in.shop_id == ctx["shop_b_id"]
        assert m_in.batch_id == dst_batch.id
        assert m_in.quantity_change == 25


def test_transit_damage_recording(app: Flask, client: FlaskClient):
    """Test 11: Recording transit damage accurately routes good units to AVAILABLE and damaged to DAMAGED."""
    ctx = _setup_phase6_topology(app)

    with app.app_context():
        owner_a = db.session.query(User).get(ctx["owner_a_id"])
        owner_b = db.session.query(User).get(ctx["owner_b_id"])
        transfer = TransferService.create_transfer_proposal(
            actor=owner_a,
            payload={
                "source_shop_id": ctx["shop_a_id"],
                "recipient_shop_id": ctx["shop_b_id"],
                "product_id": ctx["prod_paneer_id"],
                "source_batch_id": ctx["batch_a_id"],
                "quantity": 20,
                "unit_transfer_price": "72.00",
            },
        )
        TransferService.approve_transfer(actor=owner_b, transfer_id=transfer.id)
        TransferService.dispatch_transfer(actor=owner_a, transfer_id=transfer.id)
        t_id = transfer.id

    _login(client, ctx["owner_b_username"])
    resp = client.post(
        f"/api/v1/transfers/{t_id}/confirm-receipt",
        json={
            "received_good_qty": 18,
            "transit_damaged_qty": 2,
            "receipt_notes": "2 packs had seal leakage upon unboxing",
        },
    )
    assert resp.status_code == 200

    with app.app_context():
        t = db.session.query(TransferRequest).get(t_id)
        dst_batch = db.session.query(InventoryBatch).get(t.recipient_batch_id)
        assert dst_batch.available_qty == 18
        assert dst_batch.damaged_qty == 2
        assert dst_batch.on_hand_qty == 20


def test_network_surplus_and_html_views(app: Flask, client: FlaskClient):
    """Test 12: Network surplus offers API and HTML transfer web views."""
    ctx = _setup_phase6_topology(app)
    _login(client, ctx["owner_b_username"])

    # API-026: Network surplus offers available to Shop B from sister shops
    resp_surplus = client.get(f"/api/v1/shops/{ctx['shop_b_id']}/network-surplus-offers")
    assert resp_surplus.status_code == 200
    offers = resp_surplus.get_json()["data"]["surplus_offers"]
    assert len(offers) >= 1
    paneer_offer = next(o for o in offers if o["product_id"] == ctx["prod_paneer_id"])
    assert paneer_offer["source_shop_id"] == ctx["shop_a_id"]
    assert paneer_offer["available_qty"] == 50

    # HTML Views:
    # 1. Shop Transfers Hub
    h_list = client.get(f"/shops/{ctx['shop_b_id']}/transfers")
    assert h_list.status_code == 200
    assert b"Inter-Store Transfers" in h_list.data

    # 2. New Transfer Proposal Form
    h_new = client.get(f"/shops/{ctx['shop_b_id']}/transfers/new")
    assert h_new.status_code == 200
    assert b"Propose Stock Transfer" in h_new.data

    # 3. Transfer Detail Page
    with app.app_context():
        owner_a = db.session.query(User).get(ctx["owner_a_id"])
        transfer = TransferService.create_transfer_proposal(
            actor=owner_a,
            payload={
                "source_shop_id": ctx["shop_a_id"],
                "recipient_shop_id": ctx["shop_b_id"],
                "product_id": ctx["prod_paneer_id"],
                "source_batch_id": ctx["batch_a_id"],
                "quantity": 10,
                "unit_transfer_price": "70.00",
            },
        )
        t_id = transfer.id

    h_detail = client.get(f"/shops/{ctx['shop_b_id']}/transfers/{t_id}")
    assert h_detail.status_code == 200
    assert b"Transfer Specifications" in h_detail.data
    assert b"Status Audit Timeline" in h_detail.data
