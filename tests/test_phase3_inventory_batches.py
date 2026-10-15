"""Comprehensive Phase 3 tests: Product Catalog, Batch Inventory, Stock Ledger, Deterministic Alerts & RBAC."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db
from app.models.audit import AuditLog
from app.models.inventory import InventoryBatch, StockMovement
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.catalog_service import CatalogService
from app.services.inventory_service import InventoryService, get_business_today
from app.services.org_service import (
    MembershipService,
    OrganizationService,
    ShopService,
)
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


def _setup_phase3_topology(app: Flask) -> dict:
    """Create two organizations, three shops, and users across SUPER_ADMIN, SHOP_OWNER, and STAFF roles."""
    with app.app_context():
        admin1 = AuthService.create_user(
            username="p3_superadmin",
            email="p3_admin@sanchay.local",
            password="ValidPassword123",
            full_name="Phase3 Super Admin",
            role="SUPER_ADMIN",
        )
        org1 = OrganizationService.create_organization(
            actor=admin1,
            payload={
                "name": "Sanchay Retail Group",
                "code": "SRG-P3",
                "contact_email": "ops@srg.local",
                "city": "Pune",
                "state": "Maharashtra",
            },
        )
        shop_a = ShopService.create_shop(
            actor=admin1,
            payload={
                "organization_id": org1.id,
                "name": "SRG Kothrud Store",
                "code": "KOT-01",
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
                "code": "BAN-02",
                "address": "204 High Street, Baner",
                "city": "Pune",
                "pincode": "411045",
                "contact_phone": "9822022222",
            },
        )

        owner_a = AuthService.create_user(
            username="p3_owner_a",
            email="owner_a@sanchay.local",
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
            username="p3_owner_b",
            email="owner_b@sanchay.local",
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
            username="p3_staff_a",
            email="staff_a@sanchay.local",
            password="ValidPassword123",
            full_name="Staff Kothrud",
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

        # Second organization for cross-tenant isolation tests
        admin2 = AuthService.create_user(
            username="p3_other_admin",
            email="other_admin@sanchay.local",
            password="ValidPassword123",
            full_name="Other Org Admin",
            role="SUPER_ADMIN",
        )
        org2 = OrganizationService.create_organization(
            actor=admin2,
            payload={
                "name": "Other Retail Chain",
                "code": "ORC-P3",
                "contact_email": "info@orc.local",
            },
        )
        shop_other = ShopService.create_shop(
            actor=admin2,
            payload={
                "organization_id": org2.id,
                "name": "ORC Mumbai Store",
                "code": "MUM-01",
                "address": "12 Linking Road, Bandra",
                "city": "Mumbai",
                "pincode": "400050",
                "contact_phone": "9820099999",
            },
        )

        return {
            "org1_id": org1.id,
            "org2_id": org2.id,
            "shop_a_id": shop_a.id,
            "shop_b_id": shop_b.id,
            "shop_other_id": shop_other.id,
            "admin1_id": admin1.id,
            "owner_a_id": owner_a.id,
            "owner_b_id": owner_b.id,
            "staff_a_id": staff_a.id,
            "admin2_id": admin2.id,
        }


# ============================================================================
# 1. Product Catalog CRUD, SKU Uniqueness, Search, Filter & Deactivation Tests
# ============================================================================


class TestProductCatalogManagement:
    """Verify product creation, SKU uniqueness, price/unit validation, filtering, pagination, and soft deactivation."""

    def test_create_list_edit_search_filter_paginate_and_deactivate_product(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase3_topology(app)
        shop_a_id = topo["shop_a_id"]
        _login(client, "p3_owner_a")

        # 1. Create perishable product in Shop A
        res1 = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "dairy-milk-500ml",
                "name": "Amul Taaza Toned Milk 500ml",
                "category": "Dairy",
                "unit": "pack",
                "description": "Pasteurized toned milk pouch",
                "is_perishable": True,
                "default_purchase_price": "22.50",
                "selling_price": "27.00",
                "reorder_level": 15,
                "near_expiry_threshold_days": 5,
            },
        )
        assert res1.status_code == 201
        p1 = res1.get_json()["data"]["product"]
        assert p1["sku"] == "DAIRY-MILK-500ML"
        assert p1["name"] == "Amul Taaza Toned Milk 500ml"
        assert p1["category"] == "Dairy"
        assert p1["unit"] == "pack"
        assert p1["default_purchase_price"] == 22.50
        assert p1["selling_price"] == 27.00
        assert p1["reorder_level"] == 15
        assert p1["near_expiry_threshold_days"] == 5
        assert p1["available_qty"] == 0
        assert p1["is_out_of_stock"] is True

        # 2. Create non-perishable product in Shop A
        res2 = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "GROC-RICE-5KG",
                "name": "Indrayani Rice 5kg Bag",
                "category": "Grains",
                "unit": "kg",
                "is_perishable": False,
                "default_purchase_price": "280.00",
                "selling_price": "340.00",
                "reorder_level": 8,
                "near_expiry_threshold_days": 30,
            },
        )
        assert res2.status_code == 201
        p2 = res2.get_json()["data"]["product"]

        # 3. Search & filter products
        list_res = client.get(f"/api/v1/shops/{shop_a_id}/products?q=Taaza&category=Dairy")
        assert list_res.status_code == 200
        items = list_res.get_json()["data"]["products"]
        assert len(items) == 1
        assert items[0]["sku"] == "DAIRY-MILK-500ML"

        # 4. Pagination check
        page_res = client.get(f"/api/v1/shops/{shop_a_id}/products?page=1&per_page=1")
        assert page_res.status_code == 200
        meta = page_res.get_json()["meta"]
        assert meta["total"] == 2
        assert meta["total_pages"] == 2
        assert len(page_res.get_json()["data"]["products"]) == 1

        # 5. Update product pricing, threshold, and description
        patch_res = client.patch(
            f"/api/v1/shops/{shop_a_id}/products/{p1['shop_product_id']}",
            json={
                "selling_price": "29.00",
                "default_purchase_price": "23.00",
                "reorder_level": 20,
                "description": "Updated milk description",
            },
        )
        assert patch_res.status_code == 200
        updated = patch_res.get_json()["data"]["product"]
        assert updated["selling_price"] == 29.00
        assert updated["default_purchase_price"] == 23.00
        assert updated["reorder_level"] == 20
        assert updated["description"] == "Updated milk description"

        # 5b. Test organization-level product endpoints (/api/v1/products)
        org_list = client.get("/api/v1/products?q=Milk")
        assert org_list.status_code == 200
        assert len(org_list.get_json()["data"]["products"]) == 1

        org_get = client.get(f"/api/v1/products/{p1['product_id']}")
        assert org_get.status_code == 200
        assert org_get.get_json()["data"]["product"]["sku"] == "DAIRY-MILK-500ML"

        _login(client, "p3_superadmin")
        org_create = client.post(
            "/api/v1/products",
            json={
                "sku": "SPICE-TURMERIC-100G",
                "name": "Organic Turmeric Powder 100g",
                "category": "Spices",
                "unit": "g",
                "default_purchase_price": "30.00",
                "default_selling_price": "45.00",
                "reorder_threshold": 12,
                "shop_id": shop_a_id,
            },
        )
        assert org_create.status_code == 201
        turmeric_id = org_create.get_json()["data"]["product"]["id"]

        org_patch = client.patch(
            f"/api/v1/products/{turmeric_id}",
            json={"name": "Salem Turmeric Powder 100g"},
        )
        assert org_patch.status_code == 200
        assert org_patch.get_json()["data"]["product"]["name"] == "Salem Turmeric Powder 100g"

        _login(client, "p3_owner_a")
        # 6. Destructive DELETE is strictly forbidden (409)
        del_res = client.delete(f"/api/v1/shops/{shop_a_id}/products/{p1['shop_product_id']}")
        assert del_res.status_code == 409
        assert del_res.get_json()["error"]["code"] == "DESTRUCTIVE_DELETE_PROHIBITED"

        org_del_res = client.delete(f"/api/v1/products/{p1['product_id']}")
        assert org_del_res.status_code == 409
        assert org_del_res.get_json()["error"]["code"] == "DESTRUCTIVE_DELETE_PROHIBITED"

        # 7. Deactivate product via PATCH and verify historical record preservation
        deact_res = client.patch(
            f"/api/v1/shops/{shop_a_id}/products/{p2['shop_product_id']}",
            json={"is_active": False},
        )
        assert deact_res.status_code == 200
        assert deact_res.get_json()["data"]["product"]["is_active"] is False

        active_filter_res = client.get(f"/api/v1/shops/{shop_a_id}/products?is_active=true")
        assert len(active_filter_res.get_json()["data"]["products"]) == 2
        inactive_filter_res = client.get(f"/api/v1/shops/{shop_a_id}/products?is_active=false")
        assert len(inactive_filter_res.get_json()["data"]["products"]) == 1

    def test_sku_uniqueness_within_organization_and_cross_shop_enrollment(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase3_topology(app)
        shop_a_id = topo["shop_a_id"]
        shop_b_id = topo["shop_b_id"]
        shop_other_id = topo["shop_other_id"]

        _login(client, "p3_owner_a")
        create_res = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "BAKERY-BREAD-400G",
                "name": "Whole Wheat Bread 400g",
                "category": "Bakery",
                "unit": "pack",
                "default_purchase_price": "32.00",
                "selling_price": "40.00",
                "reorder_level": 10,
            },
        )
        assert create_res.status_code == 201
        product_id = create_res.get_json()["data"]["product"]["product_id"]

        # Duplicate SKU in the same shop -> 409 DUPLICATE_RESOURCE
        dup_same_shop = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "BAKERY-BREAD-400G",
                "name": "Duplicate Bread",
                "category": "Bakery",
                "unit": "pack",
                "default_purchase_price": "32.00",
                "selling_price": "40.00",
                "reorder_level": 10,
            },
        )
        assert dup_same_shop.status_code == 409
        assert dup_same_shop.get_json()["error"]["code"] == "DUPLICATE_RESOURCE"

        # Enroll the existing organization product in Shop B using product_id
        _login(client, "p3_owner_b")
        enroll_shop_b = client.post(
            f"/api/v1/shops/{shop_b_id}/products",
            json={
                "product_id": product_id,
                "default_purchase_price": "33.00",
                "selling_price": "42.00",
                "reorder_level": 12,
                "near_expiry_threshold_days": 4,
            },
        )
        assert enroll_shop_b.status_code == 201
        sp_b = enroll_shop_b.get_json()["data"]["product"]
        assert sp_b["product_id"] == product_id
        assert sp_b["selling_price"] == 42.00

        # Same SKU in a different organization (Org 2) is allowed
        _login(client, "p3_other_admin")
        other_org_res = client.post(
            f"/api/v1/shops/{shop_other_id}/products",
            json={
                "sku": "BAKERY-BREAD-400G",
                "name": "Other Org Bread 400g",
                "category": "Bakery",
                "unit": "pack",
                "default_purchase_price": "30.00",
                "selling_price": "38.00",
                "reorder_level": 5,
            },
        )
        assert other_org_res.status_code == 201

    def test_product_input_validation_rejects_invalid_prices_units_and_thresholds(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase3_topology(app)
        shop_a_id = topo["shop_a_id"]
        _login(client, "p3_owner_a")

        # Negative purchase price -> 400
        neg_price = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "BAD-PRICE-01",
                "name": "Bad Price Item",
                "category": "Misc",
                "unit": "pcs",
                "default_purchase_price": "-10.00",
                "selling_price": "20.00",
                "reorder_level": 5,
            },
        )
        assert neg_price.status_code == 400
        assert "default_purchase_price" in neg_price.get_json()["error"]["details"]

        # Selling price below purchase cost -> 400
        below_cost = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "BAD-PRICE-02",
                "name": "Below Cost Item",
                "category": "Misc",
                "unit": "pcs",
                "default_purchase_price": "50.00",
                "selling_price": "40.00",
                "reorder_level": 5,
            },
        )
        assert below_cost.status_code == 400
        assert "selling_price" in below_cost.get_json()["error"]["details"]

        # Invalid unit -> 400
        bad_unit = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "BAD-UNIT-01",
                "name": "Bad Unit Item",
                "category": "Misc",
                "unit": "barrels",
                "default_purchase_price": "10.00",
                "selling_price": "15.00",
                "reorder_level": 5,
            },
        )
        assert bad_unit.status_code == 400
        assert "unit" in bad_unit.get_json()["error"]["details"]

        # Negative reorder level -> 400
        neg_reorder = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "BAD-REORDER-01",
                "name": "Bad Reorder Item",
                "category": "Misc",
                "unit": "pcs",
                "default_purchase_price": "10.00",
                "selling_price": "15.00",
                "reorder_level": -3,
            },
        )
        assert neg_reorder.status_code == 400
        assert "reorder_level" in neg_reorder.get_json()["error"]["details"]


# ============================================================================
# 2. Batch Receiving, FEFO Ordering, Stock Adjustments & Reconciliation Tests
# ============================================================================


class TestBatchInventoryAndLedgerReconciliation:
    """Verify batch receipts, FEFO ordering, stock adjustments, negative stock prevention, and ledger math."""

    def test_receive_multiple_batches_fefo_ordering_and_reconciliation(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase3_topology(app)
        shop_a_id = topo["shop_a_id"]
        today = get_business_today()

        _login(client, "p3_owner_a")
        prod_res = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "DAIRY-CURD-400G",
                "name": "Fresh Dahi 400g Cup",
                "category": "Dairy",
                "unit": "pcs",
                "is_perishable": True,
                "default_purchase_price": "28.00",
                "selling_price": "35.00",
                "reorder_level": 25,
                "near_expiry_threshold_days": 7,
            },
        )
        assert prod_res.status_code == 201
        product_id = prod_res.get_json()["data"]["product"]["product_id"]

        # Staff member can receive stock batches (API-017)
        _login(client, "p3_staff_a")

        # Receive Batch 1 (expires in 12 days)
        b1_res = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "CURD-LATER",
                "received_qty": 20,
                "unit_cost": "28.00",
                "received_date": today.isoformat(),
                "expiry_date": (today + timedelta(days=12)).isoformat(),
                "supplier_reference": "INV-1001",
            },
        )
        assert b1_res.status_code == 201
        b1_data = b1_res.get_json()["data"]["batch"]
        assert b1_data["available_qty"] == 20
        assert b1_data["initial_qty"] == 20
        assert b1_data["is_near_expiry"] is False

        # Receive Batch 2 (expires in 4 days — near expiry, earlier FEFO position)
        b2_res = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "CURD-SOONER",
                "received_qty": 15,
                "unit_cost": "27.50",
                "received_date": today.isoformat(),
                "expiry_date": (today + timedelta(days=4)).isoformat(),
                "supplier_reference": "INV-1002",
            },
        )
        assert b2_res.status_code == 201
        b2_data = b2_res.get_json()["data"]["batch"]
        assert b2_data["is_near_expiry"] is True
        assert b2_data["days_until_expiry"] == 4

        # Verify FEFO order: CURD-SOONER (exp +4d) comes before CURD-LATER (exp +12d)
        batches_res = client.get(f"/api/v1/shops/{shop_a_id}/batches?product_id={product_id}")
        assert batches_res.status_code == 200
        batches_list = batches_res.get_json()["data"]["batches"]
        assert [b["batch_code"] for b in batches_list] == ["CURD-SOONER", "CURD-LATER"]

        # Verify product-level stock summary equals sum of batch available_qty (20 + 15 = 35)
        prod_get = client.get(f"/api/v1/shops/{shop_a_id}/products")
        shop_prod = prod_get.get_json()["data"]["products"][0]
        assert shop_prod["available_qty"] == 35
        assert shop_prod["is_low_stock"] is False
        assert shop_prod["has_near_expiry"] is True
        assert shop_prod["earliest_expiry_date"] == (today + timedelta(days=4)).isoformat()

    def test_stock_adjustments_ledger_reconciliation_and_negative_stock_rejection(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase3_topology(app)
        shop_a_id = topo["shop_a_id"]
        today = get_business_today()

        _login(client, "p3_owner_a")
        prod_res = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "BEV-JUICE-1L",
                "name": "Mixed Fruit Juice 1L",
                "category": "Beverages",
                "unit": "pack",
                "is_perishable": True,
                "default_purchase_price": "75.00",
                "selling_price": "110.00",
                "reorder_level": 12,
                "near_expiry_threshold_days": 10,
            },
        )
        product_id = prod_res.get_json()["data"]["product"]["product_id"]

        batch_res = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "JUICE-B01",
                "received_qty": 20,
                "unit_cost": "75.00",
                "received_date": today.isoformat(),
                "expiry_date": (today + timedelta(days=20)).isoformat(),
            },
        )
        batch_id = batch_res.get_json()["data"]["batch"]["id"]

        # 1. Non-assigned Owner (Shop B) cannot perform manual stock adjustments on Shop A -> 403 UNAUTHORIZED_SHOP_ACCESS
        _login(client, "p3_owner_b")
        unauth_adj = client.post(
            f"/api/v1/shops/{shop_a_id}/batches/{batch_id}/adjust",
            json={
                "movement_type": "ADJUSTMENT_SUBTRACT",
                "quantity": 2,
                "reason": "Cross-shop attempt",
            },
        )
        assert unauth_adj.status_code == 403
        assert unauth_adj.get_json()["error"]["code"] == "UNAUTHORIZED_SHOP_ACCESS"

        # 2. SHOP_OWNER performs ADJUSTMENT_ADD (+5 -> 25 available)
        _login(client, "p3_owner_a")
        add_res = client.post(
            f"/api/v1/shops/{shop_a_id}/batches/{batch_id}/adjust",
            json={
                "movement_type": "ADJUSTMENT_ADD",
                "quantity": 5,
                "reason": "Found 5 uncounted packs in backroom",
            },
        )
        assert add_res.status_code == 200
        assert add_res.get_json()["data"]["batch"]["available_qty"] == 25

        # 3. SHOP_OWNER performs ADJUSTMENT_SUBTRACT (-4 -> 21 available)
        sub_res = client.post(
            f"/api/v1/shops/{shop_a_id}/batches/{batch_id}/adjust",
            json={
                "movement_type": "ADJUSTMENT_SUBTRACT",
                "quantity": 4,
                "reason": "Cycle count variance correction",
            },
        )
        assert sub_res.status_code == 200
        assert sub_res.get_json()["data"]["batch"]["available_qty"] == 21

        # 4. SHOP_OWNER performs MARK_DAMAGED (3 units moved AVAILABLE -> DAMAGED)
        dmg_res = client.post(
            f"/api/v1/shops/{shop_a_id}/batches/{batch_id}/adjust",
            json={
                "movement_type": "MARK_DAMAGED",
                "quantity": 3,
                "reason": "Crushed cartons during shelf loading",
            },
        )
        assert dmg_res.status_code == 200
        b_after_dmg = dmg_res.get_json()["data"]["batch"]
        assert b_after_dmg["available_qty"] == 18
        assert b_after_dmg["damaged_qty"] == 3
        assert len(dmg_res.get_json()["data"]["movements"]) == 2

        # 5. SHOP_OWNER performs MARK_EXPIRED (2 units moved AVAILABLE -> EXPIRED)
        exp_res = client.post(
            f"/api/v1/shops/{shop_a_id}/batches/{batch_id}/adjust",
            json={
                "movement_type": "MARK_EXPIRED",
                "quantity": 2,
                "reason": "Seal compromised, quarantined early",
            },
        )
        assert exp_res.status_code == 200
        b_after_exp = exp_res.get_json()["data"]["batch"]
        assert b_after_exp["available_qty"] == 16
        assert b_after_exp["expired_qty"] == 2
        assert b_after_exp["damaged_qty"] == 3

        # 6. Reject adjustment without mandatory reason -> 400 VALIDATION_ERROR
        no_reason = client.post(
            f"/api/v1/shops/{shop_a_id}/batches/{batch_id}/adjust",
            json={
                "movement_type": "ADJUSTMENT_SUBTRACT",
                "quantity": 1,
                "reason": "ab",
            },
        )
        assert no_reason.status_code == 400
        assert "reason" in no_reason.get_json()["error"]["details"]

        # 7. Reject adjustment that would result in negative stock -> 409 INSUFFICIENT_AVAILABLE_STOCK
        neg_stock = client.post(
            f"/api/v1/shops/{shop_a_id}/batches/{batch_id}/adjust",
            json={
                "movement_type": "ADJUSTMENT_SUBTRACT",
                "quantity": 50,
                "reason": "Attempt to subtract more than available",
            },
        )
        assert neg_stock.status_code == 409
        assert neg_stock.get_json()["error"]["code"] == "INSUFFICIENT_AVAILABLE_STOCK"

        # 8. Verify full ledger reconciliation via overview API and service
        overview_res = client.get(f"/api/v1/shops/{shop_a_id}/inventory/overview")
        assert overview_res.status_code == 200
        recon = overview_res.get_json()["data"]["reconciliation"]
        assert recon["is_reconciled"] is True
        assert recon["discrepancies"] == []

        # 9. Verify Stock Movement Ledger API (API-019)
        mov_res = client.get(f"/api/v1/shops/{shop_a_id}/stock-movements?batch_id={batch_id}")
        assert mov_res.status_code == 200
        movements = mov_res.get_json()["data"]["movements"]
        # 1 RECEIPT + 1 ADD + 1 SUB + 2 MARK_DAMAGED + 2 MARK_EXPIRED = 7 rows
        assert len(movements) == 7
        for m in movements:
            assert m["balance_before"] + m["quantity_change"] == m["balance_after"]


# ============================================================================
# 3. Deterministic Expiry, Near-Expiry, Low-Stock & Out-of-Stock Rules Tests
# ============================================================================


class TestDeterministicAlertsAndExpiryQuarantine:
    """Verify deterministic expiry sweeps, near-expiry alerts, low-stock alerts, and deduplication."""

    def test_expired_batch_quarantine_near_expiry_and_low_stock_rules(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase3_topology(app)
        shop_a_id = topo["shop_a_id"]
        today = get_business_today()

        _login(client, "p3_owner_a")
        prod_res = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "DAIRY-PANEER-200G",
                "name": "Fresh Malai Paneer 200g",
                "category": "Dairy",
                "unit": "pack",
                "is_perishable": True,
                "default_purchase_price": "68.00",
                "selling_price": "85.00",
                "reorder_level": 15,
                "near_expiry_threshold_days": 5,
            },
        )
        product_id = prod_res.get_json()["data"]["product"]["product_id"]

        # 1. Receiving an already-expired batch via API is rejected with 400
        past_exp_res = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "PANEER-PAST",
                "received_qty": 10,
                "unit_cost": "68.00",
                "received_date": (today - timedelta(days=5)).isoformat(),
                "expiry_date": (today - timedelta(days=1)).isoformat(),
            },
        )
        assert past_exp_res.status_code == 400
        assert "expiry_date" in past_exp_res.get_json()["error"]["details"]

        # 2. Receive a batch that expires in 3 days (Near-Expiry, 10 units -> still <= reorder_level 15 -> Low Stock)
        near_res = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "PANEER-NEAR",
                "received_qty": 10,
                "unit_cost": "68.00",
                "received_date": today.isoformat(),
                "expiry_date": (today + timedelta(days=3)).isoformat(),
            },
        )
        assert near_res.status_code == 201

        # Receive a second batch that we will age into expiry to test automatic quarantine sweep
        aging_res = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "PANEER-TO-EXPIRE",
                "received_qty": 12,
                "unit_cost": "68.00",
                "received_date": (today - timedelta(days=7)).isoformat(),
                "expiry_date": (today + timedelta(days=1)).isoformat(),
            },
        )
        assert aging_res.status_code == 201
        aging_batch_id = aging_res.get_json()["data"]["batch"]["id"]

        # Simulate time passing by setting PANEER-TO-EXPIRE expiry_date to yesterday (days_until_expiry == -1 -> expired)
        with app.app_context():
            b_obj = db.session.get(InventoryBatch, aging_batch_id)
            b_obj.expiry_date = today - timedelta(days=1)
            db.session.commit()

        # 3. Call expiry alerts endpoint — sweep_expired_batches automatically quarantines PANEER-TO-EXPIRE!
        exp_alerts_res = client.get(f"/api/v1/shops/{shop_a_id}/alerts/expiry")
        assert exp_alerts_res.status_code == 200
        exp_data = exp_alerts_res.get_json()["data"]
        assert exp_data["near_expiry_batch_count"] == 1
        assert exp_data["expired_batch_count"] == 1
        assert exp_data["near_expiry_batches"][0]["batch_code"] == "PANEER-NEAR"
        assert exp_data["expired_batches"][0]["batch_code"] == "PANEER-TO-EXPIRE"
        assert exp_data["expired_batches"][0]["expired_qty"] == 12

        # 4. Calling the alerts endpoint a second time produces NO duplicate alerts or ledger rows
        exp_alerts_res_2 = client.get(f"/api/v1/shops/{shop_a_id}/alerts/expiry")
        exp_data_2 = exp_alerts_res_2.get_json()["data"]
        assert len(exp_data_2["near_expiry_batches"]) == 1
        assert len(exp_data_2["expired_batches"]) == 1

        # 5. Verify low-stock alert: only PANEER-NEAR (10 units) is sellable; 12 expired units are excluded!
        # Since 10 <= reorder_level (15), DAIRY-PANEER-200G must be flagged as LOW_STOCK with deficit 5.
        low_res = client.get(f"/api/v1/shops/{shop_a_id}/alerts/low-stock")
        assert low_res.status_code == 200
        low_data = low_res.get_json()["data"]
        assert low_data["low_stock_count"] == 1
        assert low_data["items"][0]["product_sku"] == "DAIRY-PANEER-200G"
        assert low_data["items"][0]["available_qty"] == 10
        assert low_data["items"][0]["deficit_qty"] == 5
        assert low_data["items"][0]["severity"] == "LOW_STOCK"


# ============================================================================
# 4. Security, RBAC, Tenant Isolation & Deactivated Entity Enforcement Tests
# ============================================================================


class TestPhase3SecurityAndIsolation:
    """Verify tenant boundaries, role enforcement, and deactivated product/shop restrictions."""

    def test_cross_shop_and_cross_org_isolation_plus_deactivated_restrictions(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase3_topology(app)
        shop_a_id = topo["shop_a_id"]
        shop_b_id = topo["shop_b_id"]
        shop_other_id = topo["shop_other_id"]
        today = get_business_today()

        # 1. Unauthenticated requests are rejected with 401
        unauth = client.get(f"/api/v1/shops/{shop_a_id}/products")
        assert unauth.status_code == 401

        # 2. Owner A creates a product & batch in Shop A
        _login(client, "p3_owner_a")
        prod_res = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "SNACK-CHIPS-50G",
                "name": "Salted Potato Chips 50g",
                "category": "Snacks",
                "unit": "pack",
                "is_perishable": True,
                "default_purchase_price": "14.00",
                "selling_price": "20.00",
                "reorder_level": 10,
            },
        )
        sp_a_id = prod_res.get_json()["data"]["product"]["shop_product_id"]
        product_id = prod_res.get_json()["data"]["product"]["product_id"]

        # 3. Owner A cannot access Shop B (same org, unauthorized shop) -> 403
        cross_shop = client.get(f"/api/v1/shops/{shop_b_id}/products")
        assert cross_shop.status_code == 403
        assert cross_shop.get_json()["error"]["code"] == "UNAUTHORIZED_SHOP_ACCESS"

        # 4. Owner A cannot access Shop in Org 2 -> 403
        cross_org = client.get(f"/api/v1/shops/{shop_other_id}/products")
        assert cross_org.status_code == 403
        assert cross_org.get_json()["error"]["code"] == "UNAUTHORIZED_ORG_ACCESS"

        # 5. Owner of Shop B cannot create or update products in Shop A -> 403 UNAUTHORIZED_SHOP_ACCESS
        _login(client, "p3_owner_b")
        cross_create = client.post(
            f"/api/v1/shops/{shop_a_id}/products",
            json={
                "sku": "CROSS-PROD-01",
                "name": "Unauthorized Product",
                "category": "Snacks",
                "unit": "pack",
                "default_purchase_price": "10.00",
                "selling_price": "15.00",
                "reorder_level": 5,
            },
        )
        assert cross_create.status_code == 403
        assert cross_create.get_json()["error"]["code"] == "UNAUTHORIZED_SHOP_ACCESS"

        # 6. Deactivate product -> receiving new batches for deactivated product is rejected (409)
        _login(client, "p3_owner_a")
        client.patch(
            f"/api/v1/shops/{shop_a_id}/products/{sp_a_id}",
            json={"is_active": False},
        )
        inactive_prod_batch = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "INACTIVE-B01",
                "received_qty": 10,
                "unit_cost": "14.00",
                "received_date": today.isoformat(),
                "expiry_date": (today + timedelta(days=30)).isoformat(),
            },
        )
        assert inactive_prod_batch.status_code == 409
        assert inactive_prod_batch.get_json()["error"]["code"] == "PRODUCT_INACTIVE"

        # Reactivate product, then deactivate Shop A -> write operations rejected with 403 INACTIVE_SHOP
        client.patch(
            f"/api/v1/shops/{shop_a_id}/products/{sp_a_id}",
            json={"is_active": True},
        )
        _login(client, "p3_superadmin")
        client.patch(f"/api/v1/shops/{shop_a_id}", json={"status": "INACTIVE"})

        inactive_shop_batch = client.post(
            f"/api/v1/shops/{shop_a_id}/batches",
            json={
                "product_id": product_id,
                "batch_code": "SHOP-INACTIVE-B01",
                "received_qty": 10,
                "unit_cost": "14.00",
                "received_date": today.isoformat(),
                "expiry_date": (today + timedelta(days=30)).isoformat(),
            },
        )
        assert inactive_shop_batch.status_code == 403
        assert inactive_shop_batch.get_json()["error"]["code"] == "INACTIVE_SHOP"


# ============================================================================
# 5. Concurrent Stock Adjustment Row-Locking Consistency Test
# ============================================================================


class TestConcurrentInventoryLocking:
    """Verify PostgreSQL SELECT ... FOR UPDATE row locking prevents race conditions."""

    def test_concurrent_adjustments_preserve_exact_ledger_and_prevent_negative_stock(
        self, app: Flask
    ):
        topo = _setup_phase3_topology(app)
        shop_a_id = topo["shop_a_id"]
        owner_a_id = topo["owner_a_id"]
        today = get_business_today()

        with app.app_context():
            actor = db.session.get(User, owner_a_id)
            sp = CatalogService.create_or_enroll_shop_product(
                actor=actor,
                shop_id=shop_a_id,
                payload={
                    "sku": "CONC-OIL-1L",
                    "name": "Sunflower Oil 1L Pouch",
                    "category": "Cooking Oil",
                    "unit": "pack",
                    "is_perishable": True,
                    "default_purchase_price": "115.00",
                    "selling_price": "140.00",
                    "reorder_level": 10,
                },
            )
            batch, _ = InventoryService.receive_batch(
                actor=actor,
                shop_id=shop_a_id,
                payload={
                    "product_id": sp.product_id,
                    "batch_code": "OIL-CONC-01",
                    "received_qty": 12,
                    "unit_cost": "115.00",
                    "received_date": today.isoformat(),
                    "expiry_date": (today + timedelta(days=60)).isoformat(),
                },
            )
            batch_id = batch.id

        # Fire 5 concurrent worker threads each attempting to subtract 4 units from 12 available.
        # Exactly 3 threads must succeed (3 * 4 = 12 -> 0 available) and 2 must fail with InsufficientStockError.
        def _worker(worker_idx: int) -> str:
            with app.app_context():
                from app.utils.errors import InsufficientStockError

                try:
                    worker_actor = db.session.get(User, owner_a_id)
                    InventoryService.adjust_batch(
                        actor=worker_actor,
                        shop_id=shop_a_id,
                        batch_id=batch_id,
                        payload={
                            "movement_type": "ADJUSTMENT_SUBTRACT",
                            "quantity": 4,
                            "reason": f"Concurrent worker #{worker_idx} deduction",
                        },
                    )
                    return "SUCCESS"
                except InsufficientStockError:
                    db.session.rollback()
                    return "INSUFFICIENT"
                finally:
                    db.session.remove()

        with ThreadPoolExecutor(max_workers=5) as pool:
            outcomes = list(pool.map(_worker, range(5)))

        assert outcomes.count("SUCCESS") == 3
        assert outcomes.count("INSUFFICIENT") == 2

        with app.app_context():
            final_batch = db.session.get(InventoryBatch, batch_id)
            assert final_batch.available_qty == 0
            assert final_batch.status == "DEPLETED"
            recon = InventoryService.verify_shop_ledger_reconciliation(shop_id=shop_a_id)
            assert recon["is_reconciled"] is True


# ============================================================================
# 6. HTML UI Views & CSRF Form Submission Tests
# ============================================================================


class TestPhase3HtmlViewsAndForms:
    """Verify all Phase 3 HTML pages render and handle CSRF-protected form submissions."""

    def test_catalog_and_inventory_html_views_and_forms(
        self, app: Flask, client: FlaskClient
    ):
        topo = _setup_phase3_topology(app)
        shop_a_id = topo["shop_a_id"]
        today = get_business_today()

        # Login via HTML form
        login_get = client.get("/auth/login")
        csrf = extract_csrf_token(login_get.get_data(as_text=True))
        client.post(
            "/auth/login",
            data={
                "identifier": "p3_owner_a",
                "password": "ValidPassword123",
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )

        # 1. Entry redirects (/catalog and /inventory)
        cat_redir = client.get("/catalog")
        assert cat_redir.status_code == 302
        assert f"/shops/{shop_a_id}/products" in cat_redir.headers["Location"]

        inv_redir = client.get("/inventory")
        assert inv_redir.status_code == 302
        assert f"/shops/{shop_a_id}/inventory" in inv_redir.headers["Location"]

        # 2. Create product via HTML form
        cat_page = client.get(f"/shops/{shop_a_id}/products")
        assert cat_page.status_code == 200
        csrf = extract_csrf_token(cat_page.get_data(as_text=True))

        create_form_res = client.post(
            f"/shops/{shop_a_id}/products/create",
            data={
                "csrf_token": csrf,
                "sku": "TEA-GOLD-250G",
                "name": "Assam Gold Tea 250g",
                "category": "Beverages",
                "unit": "box",
                "default_purchase_price": "95.00",
                "selling_price": "125.00",
                "reorder_level": "10",
                "near_expiry_threshold_days": "14",
                "description": "Premium leaf tea",
                "is_perishable": "true",
            },
            follow_redirects=True,
        )
        assert create_form_res.status_code == 200
        assert "Assam Gold Tea 250g" in create_form_res.get_data(as_text=True)

        # Lookup created product & shop_product IDs
        api_prods = client.get(f"/api/v1/shops/{shop_a_id}/products").get_json()["data"]["products"]
        sp_id = api_prods[0]["shop_product_id"]
        prod_id = api_prods[0]["product_id"]

        # 3. View & update product detail page via HTML form
        detail_page = client.get(f"/shops/{shop_a_id}/products/{sp_id}")
        assert detail_page.status_code == 200
        csrf = extract_csrf_token(detail_page.get_data(as_text=True))

        update_form_res = client.post(
            f"/shops/{shop_a_id}/products/{sp_id}/update",
            data={
                "csrf_token": csrf,
                "name": "Assam Gold Tea 250g Pack",
                "category": "Beverages",
                "unit": "box",
                "is_active": "true",
                "default_purchase_price": "96.00",
                "selling_price": "130.00",
                "reorder_level": "12",
                "near_expiry_threshold_days": "14",
                "description": "Updated leaf tea",
            },
            follow_redirects=True,
        )
        assert update_form_res.status_code == 200
        assert "Assam Gold Tea 250g Pack" in update_form_res.get_data(as_text=True)

        # 4. Receive batch via HTML form
        rec_page = client.get(f"/shops/{shop_a_id}/inventory/receive?product_id={prod_id}")
        assert rec_page.status_code == 200
        csrf = extract_csrf_token(rec_page.get_data(as_text=True))

        rec_post = client.post(
            f"/shops/{shop_a_id}/inventory/receive",
            data={
                "csrf_token": csrf,
                "product_id": str(prod_id),
                "batch_code": "TEA-B101",
                "received_qty": "30",
                "unit_cost": "96.00",
                "received_date": today.isoformat(),
                "expiry_date": (today + timedelta(days=10)).isoformat(),
                "supplier_reference": "SUP-TEA-01",
            },
            follow_redirects=True,
        )
        assert rec_post.status_code == 200
        assert "TEA-B101" in rec_post.get_data(as_text=True)

        batches_data = client.get(f"/api/v1/shops/{shop_a_id}/batches").get_json()["data"]["batches"]
        batch_id = batches_data[0]["id"]

        # 5. Adjust batch via HTML form
        adj_page = client.get(f"/shops/{shop_a_id}/inventory/adjust?batch_id={batch_id}")
        assert adj_page.status_code == 200
        csrf = extract_csrf_token(adj_page.get_data(as_text=True))

        adj_post = client.post(
            f"/shops/{shop_a_id}/inventory/adjust",
            data={
                "csrf_token": csrf,
                "batch_id": str(batch_id),
                "movement_type": "ADJUSTMENT_SUBTRACT",
                "quantity": "3",
                "reason": "Sampled 3 boxes for quality check",
            },
            follow_redirects=True,
        )
        assert adj_post.status_code == 200
        assert "Available: 27" in adj_post.get_data(as_text=True)

        # 6. Verify all remaining inventory HTML pages render 200 OK
        for path in (
            f"/shops/{shop_a_id}/inventory",
            f"/shops/{shop_a_id}/batches",
            f"/shops/{shop_a_id}/batches/{batch_id}",
            f"/shops/{shop_a_id}/inventory/expiry",
            f"/shops/{shop_a_id}/inventory/low-stock",
            f"/shops/{shop_a_id}/stock-movements",
        ):
            page_res = client.get(path)
            assert page_res.status_code == 200
