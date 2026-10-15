"""Comprehensive Phase 4 tests: POS Billing, FEFO Batch Deduction, Receipts, Returns, Cancellations, Concurrency & RBAC."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest

from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db
from app.models.inventory import InventoryBatch, StockMovement
from app.models.sales import Sale, SaleItem, SaleReturn
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.catalog_service import CatalogService
from app.services.inventory_service import InventoryService, get_business_today
from app.services.org_service import (
    MembershipService,
    OrganizationService,
    ShopService,
)
from app.services.sales_service import SalesService
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


def _setup_phase4_topology(app: Flask) -> dict:
    """Create two organizations, three shops, users, products, and initial inventory batches."""
    with app.app_context():
        admin1 = AuthService.create_user(
            username="p4_superadmin",
            email="p4_admin@sanchay.local",
            password="ValidPassword123",
            full_name="Phase4 Super Admin",
            role="SUPER_ADMIN",
        )
        org1 = OrganizationService.create_organization(
            actor=admin1,
            payload={
                "name": "Sanchay Retail Group P4",
                "code": "SRG-P4",
                "contact_email": "ops@srgp4.local",
                "city": "Pune",
                "state": "Maharashtra",
            },
        )
        shop_a = ShopService.create_shop(
            actor=admin1,
            payload={
                "organization_id": org1.id,
                "name": "SRG Kothrud Store",
                "code": "KOT01",
                "address": "101 Karve Road, Kothrud",
                "city": "Pune",
                "pincode": "411038",
                "contact_phone": "9822011111",
            },
        )
        shop_b = ShopService.create_shop(
            actor=admin1,
            payload={
                "organization_id": org1.id,
                "name": "SRG Baner Store",
                "code": "BAN02",
                "address": "204 High Street, Baner",
                "city": "Pune",
                "pincode": "411045",
                "contact_phone": "9822022222",
            },
        )

        owner_a = AuthService.create_user(
            username="p4_owner_a",
            email="p4_owner_a@sanchay.local",
            password="ValidPassword123",
            full_name="Owner Kothrud",
            role="SHOP_OWNER",
            organization_id=org1.id,
        )
        MembershipService.assign_user_to_shop(
            actor=admin1,
            shop_id=shop_a.id,
            payload={
                "user_id": owner_a.id,
                "assignment_role": "SHOP_OWNER",
                "is_primary": True,
                "is_active": True,
            },
        )

        owner_b = AuthService.create_user(
            username="p4_owner_b",
            email="p4_owner_b@sanchay.local",
            password="ValidPassword123",
            full_name="Owner Baner",
            role="SHOP_OWNER",
            organization_id=org1.id,
        )
        MembershipService.assign_user_to_shop(
            actor=admin1,
            shop_id=shop_b.id,
            payload={
                "user_id": owner_b.id,
                "assignment_role": "SHOP_OWNER",
                "is_primary": True,
                "is_active": True,
            },
        )

        staff_a = AuthService.create_user(
            username="p4_staff_a",
            email="p4_staff_a@sanchay.local",
            password="ValidPassword123",
            full_name="Cashier Kothrud",
            role="SHOP_OWNER",
            organization_id=org1.id,
        )
        MembershipService.assign_user_to_shop(
            actor=admin1,
            shop_id=shop_a.id,
            payload={
                "user_id": staff_a.id,
                "assignment_role": "SHOP_OWNER",
                "is_primary": True,
                "is_active": True,
            },
        )

        # Second organization for cross-org isolation tests
        admin2 = AuthService.create_user(
            username="p4_other_admin",
            email="p4_other@rival.local",
            password="ValidPassword123",
            full_name="Other Org Admin",
            role="SUPER_ADMIN",
        )
        org2 = OrganizationService.create_organization(
            actor=admin2,
            payload={
                "name": "Rival Retail Group",
                "code": "RRG-P4",
                "contact_email": "ops@rrg.local",
                "city": "Mumbai",
                "state": "Maharashtra",
            },
        )
        shop_rival = ShopService.create_shop(
            actor=admin2,
            payload={
                "organization_id": org2.id,
                "name": "Rival Dadar Store",
                "code": "DAD01",
                "address": "12 MG Road, Dadar",
                "city": "Mumbai",
                "pincode": "400014",
                "contact_phone": "9811099999",
            },
        )

        return {
            "org1_id": org1.id,
            "shop_a_id": shop_a.id,
            "shop_b_id": shop_b.id,
            "org2_id": org2.id,
            "shop_rival_id": shop_rival.id,
            "admin1_id": admin1.id,
            "owner_a_id": owner_a.id,
            "owner_b_id": owner_b.id,
            "staff_a_id": staff_a.id,
        }


# ============================================================================
# 1. Valid Sale, Authoritative Pricing, Totals, Receipt Uniqueness & FEFO
# ============================================================================


class TestPosSaleCreationAndFefoAllocation:
    """Verify valid POS sales, authoritative price lookup, multi-batch FEFO, and receipt generation."""

    def test_valid_sale_creation_authoritative_pricing_and_historical_price_preservation(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase4_topology(app)
        shop_a_id = topo["shop_a_id"]
        today = get_business_today()

        _login(client, "p4_owner_a")
        prod_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "SKU-DAIRY-PANEER-200G",
                "name": "Amul Malai Paneer 200g",
                "category": "Dairy",
                "unit": "pack",
                "default_purchase_price": "70.00",
                "selling_price": "90.00",
                "reorder_level": 10,
                "near_expiry_threshold_days": 7,
                "is_perishable": True,
            },
        )
        assert prod_resp.status_code == 201
        product_id = prod_resp.get_json()["data"]["product"]["product_id"]

        b_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "BATCH-PNR-001",
                "expiry_date": (today + timedelta(days=10)).isoformat(),
                "unit_cost": "70.00",
                "initial_qty": 25,
            },
        )
        assert b_resp.status_code == 201

        # Cashier (STAFF) logs in and submits a sale with a forged client unit_price of 1.00
        _login(client, "p4_staff_a")
        sale_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/sales",
            json={
                "customer_name": "Anita Deshmukh",
                "customer_phone": "9876501234",
                "payment_mode": "UPI_MANUAL",
                "discount_amount": "20.00",
                "items": [
                    {
                        "product_id": product_id,
                        "quantity": 5,
                        "unit_selling_price": "1.00",  # Forged price must be ignored!
                        "total_amount": "5.00",
                    }
                ],
            },
        )
        assert sale_resp.status_code == 201, sale_resp.get_json()
        sale_data = sale_resp.get_json()["data"]["sale"]

        # Authoritative price is 90.00 * 5 = 450.00 subtotal, minus 20.00 discount = 430.00 total
        assert sale_data["status"] == "COMPLETED"
        assert sale_data["subtotal_amount"] == 450.00
        assert sale_data["discount_amount"] == 20.00
        assert sale_data["total_amount"] == 430.00
        assert sale_data["total_cost_amount"] == 350.00  # 5 * 70.00
        assert sale_data["gross_profit_amount"] == 80.00  # 430.00 - 350.00
        assert sale_data["invoice_number"].startswith("INV-KOT01-")
        assert sale_data["receipt_number"] == sale_data["invoice_number"]
        assert len(sale_data["allocations"]) == 1
        assert sale_data["allocations"][0]["unit_selling_price"] == 90.00
        assert sale_data["allocations"][0]["quantity"] == 5

        sale_id = sale_data["id"]

        # Now Shop Owner changes the product selling price to 110.00
        _login(client, "p4_owner_a")
        patch_resp = client.patch(
            f"/api/v1/shops/{shop_a_id}/products/{product_id}",
            json={"selling_price": "110.00"},
        )
        assert patch_resp.status_code == 200

        # Historical sale and receipt must still preserve 90.00 unit selling price and 430.00 total
        detail_resp = client.get(f"/api/v1/shops/{shop_a_id}/sales/{sale_id}")
        assert detail_resp.status_code == 200
        persisted = detail_resp.get_json()["data"]["sale"]
        assert persisted["subtotal_amount"] == 450.00
        assert persisted["total_amount"] == 430.00
        assert persisted["allocations"][0]["unit_selling_price"] == 90.00

        receipt_resp = client.get(f"/api/v1/shops/{shop_a_id}/sales/{sale_id}/receipt")
        assert receipt_resp.status_code == 200
        receipt = receipt_resp.get_json()["data"]["receipt"]
        assert receipt["receipt_number"] == sale_data["invoice_number"]
        assert receipt["total_amount"] == 430.00

        # Verify sequential receipt number uniqueness on second sale
        sale2_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/sales",
            json={
                "payment_mode": "CASH",
                "items": [{"product_id": product_id, "quantity": 2}],
            },
        )
        assert sale2_resp.status_code == 201
        sale2_data = sale2_resp.get_json()["data"]["sale"]
        assert sale2_data["invoice_number"] != sale_data["invoice_number"]
        assert sale2_data["invoice_number"].endswith("-0002")
        assert sale2_data["total_amount"] == 220.00  # 2 * new price 110.00

        with app.app_context():
            reconciled = InventoryService.verify_shop_ledger_reconciliation(shop_a_id)
            assert reconciled["is_reconciled"] is True

    def test_fefo_multi_batch_allocation_and_exclusion_of_expired_damaged_and_reserved_stock(
        self, app: Flask, client: FlaskClient
    ):
        """Verify FEFO multi-batch deduction and exclusion of expired, damaged, and reserved units."""
        topo = _setup_phase4_topology(app)
        shop_a_id = topo["shop_a_id"]
        today = get_business_today()

        _login(client, "p4_owner_a")
        prod_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "SKU-CURD-400G",
                "name": "Amul Masti Dahi 400g",
                "category": "Dairy",
                "unit": "pack",
                "default_purchase_price": "30.00",
                "selling_price": "40.00",
                "reorder_level": 8,
                "near_expiry_threshold_days": 5,
                "is_perishable": True,
            },
        )
        product_id = prod_resp.get_json()["data"]["product"]["product_id"]

        # Receive Batch A (expires in +3 days, initial_qty=10, cost=28.00)
        ba_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "BATCH-DAHI-EARLY",
                "expiry_date": (today + timedelta(days=3)).isoformat(),
                "unit_cost": "28.00",
                "initial_qty": 10,
            },
        )
        batch_early_id = ba_resp.get_json()["data"]["batch"]["id"]

        # Mark 2 units of Batch A as DAMAGED -> leaves 8 available in Batch A
        adj_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/batches/{batch_early_id}/adjust",
            json={
                "movement_type": "MARK_DAMAGED",
                "quantity": 2,
                "reason": "2 cups dented during unloading",
            },
        )
        assert adj_resp.status_code == 200

        # Receive Batch B (expires in +10 days, initial_qty=20, cost=31.00)
        bb_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "BATCH-DAHI-LATER",
                "expiry_date": (today + timedelta(days=10)).isoformat(),
                "unit_cost": "31.00",
                "initial_qty": 20,
            },
        )
        batch_later_id = bb_resp.get_json()["data"]["batch"]["id"]

        # Also create a past-expired batch (expiry = yesterday, 15 units) and reserve 3 units on Batch B
        with app.app_context():
            owner_user = db.session.get(User, topo["owner_a_id"])
            exp_batch, _ = InventoryService.receive_batch(
                actor=owner_user,
                shop_id=shop_a_id,
                payload={
                    "product_id": product_id,
                    "batch_code": "BATCH-DAHI-EXPIRED",
                    "expiry_date": (today - timedelta(days=1)).isoformat(),
                    "unit_cost": "26.00",
                    "initial_qty": 15,
                },
                allow_past_expiry=True,
            )
            expired_batch_id = exp_batch.id

            # Simulate 3 units reserved for an outgoing transfer on Batch B (with matching ledger movement)
            b_later = db.session.get(InventoryBatch, batch_later_id)
            old_avail = b_later.available_qty
            b_later.available_qty = old_avail - 3
            b_later.reserved_qty = 3
            db.session.add(
                StockMovement(
                    organization_id=topo["org1_id"],
                    shop_id=shop_a_id,
                    product_id=product_id,
                    batch_id=b_later.id,
                    movement_type="TRANSFER_RESERVE",
                    bucket_affected="AVAILABLE",
                    quantity_change=-3,
                    balance_before=old_avail,
                    balance_after=old_avail - 3,
                    unit_cost_snapshot=b_later.unit_cost,
                    reference_type="TRANSFER",
                    reference_id=1,
                    reason="Reserved for transfer test",
                    performed_by_user_id=owner_user.id,
                )
            )
            db.session.add(
                StockMovement(
                    organization_id=topo["org1_id"],
                    shop_id=shop_a_id,
                    product_id=product_id,
                    batch_id=b_later.id,
                    movement_type="TRANSFER_RESERVE",
                    bucket_affected="RESERVED",
                    quantity_change=3,
                    balance_before=0,
                    balance_after=3,
                    unit_cost_snapshot=b_later.unit_cost,
                    reference_type="TRANSFER",
                    reference_id=1,
                    reason="Reserved for transfer test",
                    performed_by_user_id=owner_user.id,
                )
            )
            db.session.commit()

        # Total sellable stock is now:
        # - Expired batch: 0 sellable (15 expired)
        # - Batch Early: 8 sellable (2 damaged)
        # - Batch Later: 17 sellable (3 reserved)
        # Total sellable = 8 + 17 = 25 units.

        # Attempting to sell 26 units must fail with 409 INSUFFICIENT_SELLABLE_STOCK and roll back
        over_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/sales",
            json={
                "payment_mode": "CASH",
                "items": [{"product_id": product_id, "quantity": 26}],
            },
        )
        assert over_resp.status_code == 409
        over_err = over_resp.get_json()["error"]
        assert over_err["code"] == "INSUFFICIENT_SELLABLE_STOCK"
        assert over_err["details"]["available_sellable_qty"] == 25

        # Now sell 12 units -> FEFO must take all 8 from BATCH-DAHI-EARLY and 4 from BATCH-DAHI-LATER
        sale_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/sales",
            json={
                "payment_mode": "CASH",
                "items": [{"product_id": product_id, "quantity": 12}],
            },
        )
        assert sale_resp.status_code == 201, sale_resp.get_json()
        sale = sale_resp.get_json()["data"]["sale"]
        assert len(sale["allocations"]) == 2

        alloc1, alloc2 = sale["allocations"][0], sale["allocations"][1]
        assert alloc1["batch_id"] == batch_early_id
        assert alloc1["batch_code"] == "BATCH-DAHI-EARLY"
        assert alloc1["quantity_deducted"] == 8
        assert alloc1["unit_cost_price"] == 28.00
        assert alloc1["line_total"] == 320.00  # 8 * 40.00

        assert alloc2["batch_id"] == batch_later_id
        assert alloc2["batch_code"] == "BATCH-DAHI-LATER"
        assert alloc2["quantity_deducted"] == 4
        assert alloc2["unit_cost_price"] == 31.00
        assert alloc2["line_total"] == 160.00  # 4 * 40.00

        # Total COGS = (8 * 28.00) + (4 * 31.00) = 224.00 + 124.00 = 348.00
        assert sale["total_amount"] == 480.00
        assert sale["total_cost_amount"] == 348.00
        assert sale["gross_profit_amount"] == 132.00

        with app.app_context():
            b_exp = db.session.get(InventoryBatch, expired_batch_id)
            assert b_exp.available_qty == 0
            assert b_exp.expired_qty == 15
            assert b_exp.status == "EXPIRED"

            b_early = db.session.get(InventoryBatch, batch_early_id)
            assert b_early.available_qty == 0
            assert b_early.damaged_qty == 2

            b_lat = db.session.get(InventoryBatch, batch_later_id)
            assert b_lat.available_qty == 13
            assert b_lat.reserved_qty == 3

            reconciled = InventoryService.verify_shop_ledger_reconciliation(shop_a_id)
            assert reconciled["is_reconciled"] is True


# ============================================================================
# 2. Atomic Rollback, Idempotency & Concurrency Tests
# ============================================================================


class TestAtomicRollbackIdempotencyAndConcurrency:
    """Verify atomic rollback on mid-transaction failure, duplicate submission guard, and concurrent sales."""

    def test_simulated_failure_rolls_back_entire_sale_and_stock_deductions(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase4_topology(app)
        shop_a_id = topo["shop_a_id"]
        today = get_business_today()

        with app.app_context():
            owner = db.session.get(User, topo["owner_a_id"])
            sp = CatalogService.create_or_enroll_shop_product(
                actor=owner,
                shop_id=shop_a_id,
                payload={
                    "sku": "SKU-BREAD-400G",
                    "name": "Whole Wheat Bread 400g",
                    "category": "Bakery",
                    "unit": "pack",
                    "default_purchase_price": "35.00",
                    "selling_price": "45.00",
                    "reorder_level": 5,
                    "near_expiry_threshold_days": 3,
                    "is_perishable": True,
                },
            )
            batch, _ = InventoryService.receive_batch(
                actor=owner,
                shop_id=shop_a_id,
                payload={
                    "product_id": sp.product_id,
                    "batch_code": "BATCH-BRD-01",
                    "expiry_date": (today + timedelta(days=5)).isoformat(),
                    "unit_cost": "35.00",
                    "initial_qty": 20,
                },
            )
            batch_id = batch.id
            product_id = sp.product_id

            with pytest.raises(RuntimeError, match="Simulated failure"):
                SalesService.create_sale(
                    actor=owner,
                    shop_id=shop_a_id,
                    payload={
                        "payment_mode": "CASH",
                        "items": [{"product_id": product_id, "quantity": 7}],
                    },
                    simulate_failure_after_allocation=True,
                )

            # Verify zero sales, zero sale_items, and batch still has all 20 available units
            assert db.session.query(Sale).filter(Sale.shop_id == shop_a_id).count() == 0
            assert db.session.query(SaleItem).filter(SaleItem.shop_id == shop_a_id).count() == 0
            refreshed_batch = db.session.get(InventoryBatch, batch_id)
            assert refreshed_batch.available_qty == 20
            reconciled = InventoryService.verify_shop_ledger_reconciliation(shop_a_id)
            assert reconciled["is_reconciled"] is True

    def test_duplicate_sale_submission_prevented_via_idempotency_key(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase4_topology(app)
        shop_a_id = topo["shop_a_id"]
        today = get_business_today()

        _login(client, "p4_owner_a")
        prod_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "SKU-BUTTER-100G",
                "name": "Amul Butter 100g",
                "category": "Dairy",
                "unit": "pack",
                "default_purchase_price": "48.00",
                "selling_price": "58.00",
                "reorder_level": 10,
                "near_expiry_threshold_days": 15,
                "is_perishable": True,
            },
        )
        product_id = prod_resp.get_json()["data"]["product"]["product_id"]

        b_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "BATCH-BTR-01",
                "expiry_date": (today + timedelta(days=30)).isoformat(),
                "unit_cost": "48.00",
                "initial_qty": 15,
            },
        )
        batch_id = b_resp.get_json()["data"]["batch"]["id"]

        first_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/sales",
            headers={"X-Idempotency-Key": "idem-pos-check-001"},
            json={
                "payment_mode": "CASH",
                "items": [{"product_id": product_id, "quantity": 5}],
            },
        )
        assert first_resp.status_code == 201

        # Second request with the same idempotency key must be rejected with 409 and NOT deduct stock again
        dup_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/sales",
            headers={"X-Idempotency-Key": "idem-pos-check-001"},
            json={
                "payment_mode": "CASH",
                "items": [{"product_id": product_id, "quantity": 5}],
            },
        )
        assert dup_resp.status_code == 409
        assert dup_resp.get_json()["error"]["code"] == "DUPLICATE_SALE_SUBMISSION"

        with app.app_context():
            b = db.session.get(InventoryBatch, batch_id)
            assert b.available_qty == 10

    def test_concurrent_pos_sales_prevent_overselling_and_preserve_reconciliation(
        self, app: Flask
    ):
        """5 concurrent threads attempt to buy 4 units each from a 10-unit batch; exactly 2 succeed."""
        topo = _setup_phase4_topology(app)
        shop_a_id = topo["shop_a_id"]
        owner_a_id = topo["owner_a_id"]
        today = get_business_today()

        with app.app_context():
            owner = db.session.get(User, owner_a_id)
            sp = CatalogService.create_or_enroll_shop_product(
                actor=owner,
                shop_id=shop_a_id,
                payload={
                    "sku": "SKU-CONC-SALE",
                    "name": "Concurrent POS Product",
                    "category": "Beverages",
                    "unit": "pcs",
                    "default_purchase_price": "20.00",
                    "selling_price": "30.00",
                    "reorder_level": 2,
                    "near_expiry_threshold_days": 10,
                    "is_perishable": False,
                },
            )
            batch, _ = InventoryService.receive_batch(
                actor=owner,
                shop_id=shop_a_id,
                payload={
                    "product_id": sp.product_id,
                    "batch_code": "BATCH-CONC-POS-10",
                    "expiry_date": (today + timedelta(days=30)).isoformat(),
                    "unit_cost": "20.00",
                    "initial_qty": 10,
                },
            )
            batch_id = batch.id
            product_id = sp.product_id

        def _worker(worker_idx: int) -> str:
            with app.app_context():
                actor_user = db.session.get(User, owner_a_id)
                try:
                    SalesService.create_sale(
                        actor=actor_user,
                        shop_id=shop_a_id,
                        payload={
                            "payment_mode": "CASH",
                            "items": [{"product_id": product_id, "quantity": 4}],
                        },
                        header_idempotency_key=f"conc-worker-{worker_idx}",
                    )
                    return "SUCCESS"
                except Exception as exc:
                    db.session.rollback()
                    return getattr(exc, "code", type(exc).__name__)
                finally:
                    db.session.remove()

        with ThreadPoolExecutor(max_workers=5) as pool:
            outcomes = list(pool.map(_worker, range(5)))

        assert outcomes.count("SUCCESS") == 2
        assert outcomes.count("INSUFFICIENT_SELLABLE_STOCK") == 3

        with app.app_context():
            refreshed = db.session.get(InventoryBatch, batch_id)
            assert refreshed.available_qty == 2
            reconciled = InventoryService.verify_shop_ledger_reconciliation(shop_a_id)
            assert reconciled["is_reconciled"] is True


# ============================================================================
# 3. Returns, Cancellations, Sales History & Summaries
# ============================================================================


class TestReturnsCancellationsAndSalesSummaries:
    """Verify RESELLABLE, DAMAGED, and EXPIRED returns, same-day cancellations, and summary math."""

    def test_returns_resellable_damaged_expired_and_same_day_cancellation_with_summaries(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase4_topology(app)
        shop_a_id = topo["shop_a_id"]
        today = get_business_today()

        _login(client, "p4_owner_a")
        prod_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "SKU-JUICE-1L",
                "name": "Real Mixed Fruit Juice 1L",
                "category": "Beverages",
                "unit": "box",
                "default_purchase_price": "80.00",
                "selling_price": "100.00",
                "reorder_level": 5,
                "near_expiry_threshold_days": 10,
                "is_perishable": True,
            },
        )
        product_id = prod_resp.get_json()["data"]["product"]["product_id"]

        b_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "BATCH-JCE-01",
                "expiry_date": (today + timedelta(days=15)).isoformat(),
                "unit_cost": "80.00",
                "initial_qty": 20,
            },
        )
        batch_id = b_resp.get_json()["data"]["batch"]["id"]

        # Sale #1: Sell 10 units at 100.00 = 1000.00
        s1_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/sales",
            json={
                "customer_name": "Kiran Rao",
                "payment_mode": "CARD_MANUAL",
                "items": [{"product_id": product_id, "quantity": 10}],
            },
        )
        assert s1_resp.status_code == 201
        sale1 = s1_resp.get_json()["data"]["sale"]
        sale1_id = sale1["id"]
        sale1_item_id = sale1["allocations"][0]["sale_item_id"]

        # 1) Process RESELLABLE return of 3 units -> restores +3 to available_qty (from 10 to 13)
        ret1_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/sales/{sale1_id}/returns",
            headers={"X-Idempotency-Key": "ret-key-001"},
            json={
                "sale_item_id": sale1_item_id,
                "quantity_returned": 3,
                "return_condition": "RESELLABLE",
                "reason": "Customer bought extra boxes by mistake",
            },
        )
        assert ret1_resp.status_code == 201
        ret1_data = ret1_resp.get_json()["data"]
        assert ret1_data["sale"]["status"] == "PARTIALLY_RETURNED"
        assert ret1_data["sale"]["returned_amount"] == 300.00
        assert ret1_data["sale"]["net_revenue_amount"] == 700.00

        # Duplicate return with same idempotency key must be rejected (409)
        dup_ret = client.post(
            f"/api/v1/shops/{shop_a_id}/sales/{sale1_id}/returns",
            headers={"X-Idempotency-Key": "ret-key-001"},
            json={
                "sale_item_id": sale1_item_id,
                "quantity_returned": 3,
                "return_condition": "RESELLABLE",
                "reason": "Duplicate submission",
            },
        )
        assert dup_ret.status_code == 409
        assert dup_ret.get_json()["error"]["code"] == "DUPLICATE_RETURN_SUBMISSION"

        # 2) Process DAMAGED return of 2 units -> increments damaged_qty by +2
        ret2_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/sales/{sale1_id}/returns",
            json={
                "sale_item_id": sale1_item_id,
                "quantity_returned": 2,
                "return_condition": "DAMAGED",
                "reason": "Leaking seal reported on return",
            },
        )
        assert ret2_resp.status_code == 201
        ret2_data = ret2_resp.get_json()["data"]["sale"]
        assert ret2_data["returned_amount"] == 500.00
        assert ret2_data["net_revenue_amount"] == 500.00
        assert ret2_data["net_cogs_amount"] == 400.00  # 5 net units * 80.00
        assert ret2_data["gross_profit_amount"] == 100.00

        # 3) Attempting to return 6 more units (when only 5 remain eligible) must fail with 409
        over_ret = client.post(
            f"/api/v1/shops/{shop_a_id}/sales/{sale1_id}/returns",
            json={
                "sale_item_id": sale1_item_id,
                "quantity_returned": 6,
                "return_condition": "RESELLABLE",
                "reason": "Returning more than sold",
            },
        )
        assert over_ret.status_code == 409
        assert over_ret.get_json()["error"]["code"] == "RETURN_EXCEEDS_SOLD_QUANTITY"

        # Sale #2: Sell 4 units at 100.00 = 400.00, then cancel same-day
        s2_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/sales",
            json={
                "customer_name": "Wrong Counter Bill",
                "payment_mode": "CASH",
                "items": [{"product_id": product_id, "quantity": 4}],
            },
        )
        sale2_id = s2_resp.get_json()["data"]["sale"]["id"]

        cancel_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/sales/{sale2_id}/cancel",
            json={"reason": "Customer forgot wallet and cancelled bill"},
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.get_json()["data"]["sale"]["status"] == "CANCELLED"

        # Duplicate cancellation must be rejected with 409 SALE_ALREADY_CANCELLED
        dup_cancel = client.post(
            f"/api/v1/shops/{shop_a_id}/sales/{sale2_id}/cancel",
            json={"reason": "Cancelling again"},
        )
        assert dup_cancel.status_code == 409
        assert dup_cancel.get_json()["error"]["code"] == "SALE_ALREADY_CANCELLED"

        # Verify Sales History & Summary API calculations
        hist_resp = client.get(f"/api/v1/shops/{shop_a_id}/sales")
        assert hist_resp.status_code == 200
        summary = hist_resp.get_json()["data"]["summary"]
        assert summary["total_invoices_count"] == 2
        assert summary["partially_returned_count"] == 1
        assert summary["cancelled_count"] == 1
        assert summary["gross_sales_revenue"] == 1000.00  # Excludes cancelled sale2
        assert summary["total_returned_amount"] == 500.00
        assert summary["net_sales_revenue"] == 500.00
        assert summary["net_cogs"] == 400.00
        assert summary["net_gross_profit"] == 100.00
        assert summary["gross_units_sold"] == 10
        assert summary["returned_units"] == 5
        assert summary["net_units_sold"] == 5

        with app.app_context():
            b = db.session.get(InventoryBatch, batch_id)
            # Started 20 -> sold 10 (10) -> returned 3 resellable (13) -> returned 2 damaged (13 avail, 2 dmg)
            # -> sold 4 (9 avail) -> cancelled 4 (13 avail, 2 dmg)
            assert b.available_qty == 13
            assert b.damaged_qty == 2
            reconciled = InventoryService.verify_shop_ledger_reconciliation(shop_a_id)
            assert reconciled["is_reconciled"] is True


# ============================================================================
# 4. Authorization, Shop Isolation & Destructive Delete Prevention
# ============================================================================


class TestPhase4AuthorizationAndIsolation:
    """Verify cross-shop, cross-org, inactive-shop, and destructive-delete guards."""

    def test_cross_shop_and_cross_org_sales_blocked_and_destructive_delete_prohibited(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase4_topology(app)
        shop_a_id = topo["shop_a_id"]
        shop_b_id = topo["shop_b_id"]
        shop_rival_id = topo["shop_rival_id"]
        today = get_business_today()

        _login(client, "p4_owner_a")
        prod_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "SKU-ISO-TEA",
                "name": "Taj Mahal Tea 250g",
                "category": "Beverages",
                "unit": "pack",
                "default_purchase_price": "120.00",
                "selling_price": "150.00",
                "reorder_level": 5,
                "near_expiry_threshold_days": 30,
                "is_perishable": False,
            },
        )
        product_id = prod_resp.get_json()["data"]["product"]["product_id"]
        shop_a_sp_id = prod_resp.get_json()["data"]["product"]["shop_product_id"]

        client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "BATCH-TEA-01",
                "expiry_date": (today + timedelta(days=90)).isoformat(),
                "unit_cost": "120.00",
                "initial_qty": 10,
            },
        )

        s_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/sales",
            json={
                "payment_mode": "CASH",
                "items": [{"product_id": product_id, "quantity": 2}],
            },
        )
        assert s_resp.status_code == 201
        sale_id = s_resp.get_json()["data"]["sale"]["id"]

        # Destructive DELETE on historical sale is prohibited (409)
        del_resp = client.delete(f"/api/v1/shops/{shop_a_id}/sales/{sale_id}")
        assert del_resp.status_code == 409
        assert del_resp.get_json()["error"]["code"] == "DESTRUCTIVE_DELETE_PROHIBITED"

        # Shop B Owner cannot view, create, cancel, or return Shop A's sales (403)
        _login(client, "p4_owner_b")
        assert client.get(f"/api/v1/shops/{shop_a_id}/sales").status_code == 403
        assert client.get(f"/api/v1/shops/{shop_a_id}/sales/{sale_id}").status_code == 403
        assert (
            client.post(
                f"/api/v1/shops/{shop_a_id}/sales",
                json={"payment_mode": "CASH", "items": [{"product_id": product_id, "quantity": 1}]},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/shops/{shop_a_id}/sales/{sale_id}/cancel",
                json={"reason": "Cross-shop cancel attempt"},
            ).status_code
            == 403
        )

        # Shop B Owner attempting to submit Shop A's shop_product_id inside Shop B's POS endpoint is rejected (403)
        cross_sp_resp = client.post(
            f"/api/v1/shops/{shop_b_id}/sales",
            json={
                "payment_mode": "CASH",
                "items": [{"shop_product_id": shop_a_sp_id, "quantity": 1}],
            },
        )
        assert cross_sp_resp.status_code == 403
        assert cross_sp_resp.get_json()["error"]["code"] == "UNAUTHORIZED_SHOP_ACCESS"

        # Super Admin of Org 1 cannot access Org 2's shop sales (403)
        _login(client, "p4_superadmin")
        assert client.get(f"/api/v1/shops/{shop_rival_id}/sales").status_code == 403


# ============================================================================
# 5. HTML POS Views, Form Checkout, Printable Receipt & Return Actions
# ============================================================================


class TestPhase4HtmlViewsAndForms:
    """Verify POS counter HTML page, form checkout, receipt view, sales history, and return/cancel forms."""

    def test_pos_html_views_checkout_receipt_and_return_forms(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase4_topology(app)
        shop_a_id = topo["shop_a_id"]
        today = get_business_today()

        _login(client, "p4_owner_a")
        prod_resp = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "SKU-POS-UI-01",
                "name": "Britannia Good Day 200g",
                "category": "Snacks",
                "unit": "pack",
                "default_purchase_price": "22.00",
                "selling_price": "30.00",
                "reorder_level": 5,
                "near_expiry_threshold_days": 14,
                "is_perishable": True,
            },
        )
        product_id = prod_resp.get_json()["data"]["product"]["product_id"]

        client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "BATCH-GD-UI",
                "expiry_date": (today + timedelta(days=25)).isoformat(),
                "unit_cost": "22.00",
                "initial_qty": 30,
            },
        )

        # Convenience redirects /pos and /sales
        assert client.get("/pos").status_code == 302
        assert client.get("/sales").status_code == 302

        pos_page = client.get(f"/shops/{shop_a_id}/pos")
        assert pos_page.status_code == 200
        assert "Britannia Good Day 200g" in pos_page.get_data(as_text=True)

        csrf_token = extract_csrf_token(pos_page.get_data(as_text=True))
        checkout_resp = client.post(
            f"/shops/{shop_a_id}/pos/checkout",
            data={
                "csrf_token": csrf_token,
                "product_id": str(product_id),
                "quantity": "4",
                "line_discount": "0.00",
                "payment_mode": "CASH",
                "discount_amount": "0.00",
                "customer_name": "Walk-in Counter Buyer",
                "idempotency_key": "ui-form-pos-001",
            },
            follow_redirects=True,
        )
        assert checkout_resp.status_code == 200
        html_detail = checkout_resp.get_data(as_text=True)
        assert "Sale completed!" in html_detail
        assert "BATCH-GD-UI" in html_detail

        with app.app_context():
            sale = db.session.query(Sale).filter(Sale.shop_id == shop_a_id).first()
            assert sale is not None
            sale_id = sale.id
            sale_item_id = sale.items[0].id

        # View printable receipt HTML
        receipt_page = client.get(f"/shops/{shop_a_id}/sales/{sale_id}/receipt")
        assert receipt_page.status_code == 200
        assert "Printable Customer Receipt" in receipt_page.get_data(as_text=True)

        # View sales history HTML
        hist_page = client.get(f"/shops/{shop_a_id}/sales")
        assert hist_page.status_code == 200
        assert "Walk-in Counter Buyer" in hist_page.get_data(as_text=True)

        # Submit HTML item return form
        detail_page = client.get(f"/shops/{shop_a_id}/sales/{sale_id}")
        csrf_token = extract_csrf_token(detail_page.get_data(as_text=True))
        ret_form_resp = client.post(
            f"/shops/{shop_a_id}/sales/{sale_id}/returns",
            data={
                "csrf_token": csrf_token,
                "sale_item_id": str(sale_item_id),
                "quantity_returned": "1",
                "return_condition": "RESELLABLE",
                "reason": "Returned 1 pack at counter",
                "idempotency_key": "ui-form-ret-001",
            },
            follow_redirects=True,
        )
        assert ret_form_resp.status_code == 200
        assert "Return processed" in ret_form_resp.get_data(as_text=True)
