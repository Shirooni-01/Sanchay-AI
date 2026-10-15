"""Comprehensive Phase 5 tests: Rule-Based Stock Analysis, Sales Velocity, FEFO Batch Expiry Risk, Surplus Detection, Low-Stock Replenishment, Recommendations, Freshness Invalidation & RBAC."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db
from app.models.inventory import InventoryBatch, StockMovement
from app.models.sales import Sale
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
from app.services.surplus_service import StockAnalysisService, SurplusService
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


def _setup_phase5_topology(app: Flask) -> dict:
    """Create two organizations, three shops, users, and products for Phase 5 stock analysis tests."""
    with app.app_context():
        StockAnalysisService.clear_snapshot_cache()
        admin1 = AuthService.create_user(
            username="p5_superadmin",
            email="p5_admin@sanchay.local",
            password="ValidPassword123",
            full_name="Phase5 Super Admin",
            role="SUPER_ADMIN",
        )
        org1 = OrganizationService.create_organization(
            actor=admin1,
            payload={
                "name": "Sanchay Retail Group P5",
                "code": "SRG-P5",
                "contact_email": "ops@srgp5.local",
                "city": "Pune",
                "state": "Maharashtra",
                "low_stock_default_threshold": 10,
                "near_expiry_days_threshold": 7,
            },
        )
        shop_a = ShopService.create_shop(
            actor=admin1,
            payload={
                "organization_id": org1.id,
                "name": "SRG Aundh Store",
                "code": "AUN01",
                "address": "10 ITI Road, Aundh",
                "city": "Pune",
                "pincode": "411007",
                "contact_phone": "9822011111",
            },
        )
        shop_b = ShopService.create_shop(
            actor=admin1,
            payload={
                "organization_id": org1.id,
                "name": "SRG Wakad Store",
                "code": "WAK02",
                "address": "22 Datta Mandir Road, Wakad",
                "city": "Pune",
                "pincode": "411057",
                "contact_phone": "9822022222",
            },
        )

        owner_a = AuthService.create_user(
            username="p5_owner_a",
            email="p5_ownera@sanchay.local",
            password="ValidPassword123",
            full_name="Aundh Store Owner",
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
        staff_a = AuthService.create_user(
            username="p5_staff_a",
            email="p5_staffa@sanchay.local",
            password="ValidPassword123",
            full_name="Aundh Cashier",
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
        owner_b = AuthService.create_user(
            username="p5_owner_b",
            email="p5_ownerb@sanchay.local",
            password="ValidPassword123",
            full_name="Wakad Store Owner",
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

        # Second organization for cross-tenant isolation
        admin2 = AuthService.create_user(
            username="p5_other_admin",
            email="p5_other@rival.local",
            password="ValidPassword123",
            full_name="Rival Org Admin",
            role="SUPER_ADMIN",
        )
        org2 = OrganizationService.create_organization(
            actor=admin2,
            payload={
                "name": "Rival Retail P5",
                "code": "RIV-P5",
                "contact_email": "ops@rivalp5.local",
                "city": "Mumbai",
                "state": "Maharashtra",
            },
        )
        shop_c = ShopService.create_shop(
            actor=admin2,
            payload={
                "organization_id": org2.id,
                "name": "Rival Bandra Store",
                "code": "RIV01",
                "address": "Linking Road, Bandra",
                "city": "Mumbai",
                "pincode": "400050",
                "contact_phone": "9811133333",
            },
        )

        # Products in Org 1
        prod_milk = CatalogService.create_organization_product(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "sku": "P5-MILK-1L",
                "name": "Organic Milk 1L",
                "category": "Dairy",
                "unit": "pack",
                "is_perishable": True,
                "default_purchase_price": "45.00",
                "default_selling_price": "60.00",
                "reorder_threshold": 10,
                "near_expiry_days": 7,
                "surplus_threshold": 25,
            },
        )
        prod_yogurt = CatalogService.create_organization_product(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "sku": "P5-YOG-400G",
                "name": "Probiotic Yogurt 400g",
                "category": "Dairy",
                "unit": "pcs",
                "is_perishable": True,
                "default_purchase_price": "35.00",
                "default_selling_price": "50.00",
                "reorder_threshold": 8,
                "near_expiry_days": 6,
                "surplus_threshold": 20,
            },
        )
        prod_tea = CatalogService.create_organization_product(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "sku": "P5-TEA-250G",
                "name": "Assam Leaf Tea 250g",
                "category": "Beverages",
                "unit": "box",
                "is_perishable": False,
                "default_purchase_price": "90.00",
                "default_selling_price": "130.00",
                "reorder_threshold": 12,
                "near_expiry_days": 30,
                "surplus_threshold": 40,
            },
        )
        prod_new = CatalogService.create_organization_product(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "sku": "P5-NEW-JAM",
                "name": "Artisan Strawberry Jam",
                "category": "Spreads",
                "unit": "pcs",
                "is_perishable": True,
                "default_purchase_price": "110.00",
                "default_selling_price": "160.00",
                "reorder_threshold": 5,
                "near_expiry_days": 10,
                "surplus_threshold": 15,
            },
        )

        # Enroll prod_milk in Shop B as well to test shop-scoped sales isolation
        CatalogService.create_or_enroll_shop_product(
            actor=owner_b,
            shop_id=shop_b.id,
            payload={
                "product_id": prod_milk.id,
                "purchase_price": "45.00",
                "selling_price": "60.00",
                "low_stock_threshold": 10,
                "near_expiry_days": 7,
                "surplus_threshold": 25,
            },
        )

        return {
            "admin1_id": admin1.id,
            "owner_a_id": owner_a.id,
            "staff_a_id": staff_a.id,
            "owner_b_id": owner_b.id,
            "admin2_id": admin2.id,
            "org1_id": org1.id,
            "org2_id": org2.id,
            "shop_a_id": shop_a.id,
            "shop_b_id": shop_b.id,
            "shop_c_id": shop_c.id,
            "prod_milk_id": prod_milk.id,
            "prod_yogurt_id": prod_yogurt.id,
            "prod_tea_id": prod_tea.id,
            "prod_new_id": prod_new.id,
        }


def _backdate_first_receipt_and_create_sales(
    shop_id: int,
    actor: User,
    product_id: int,
    batch_code: str,
    initial_qty: int,
    expiry_date,
    sale_days_ago_and_qtys: list[tuple[int, int]],
) -> InventoryBatch:
    """Receive a batch, backdate its received_at so the product has full lookback history, and record sales on distinct past days."""
    today = get_business_today()
    batch, _ = InventoryService.receive_batch(
        actor=actor,
        shop_id=shop_id,
        payload={
            "product_id": product_id,
            "batch_code": batch_code,
            "quantity": initial_qty,
            "unit_cost": "45.00",
            "manufactured_date": (today - timedelta(days=20)).isoformat(),
            "expiry_date": expiry_date.isoformat() if expiry_date else None,
        },
    )
    past_dt = datetime.now(timezone.utc) - timedelta(days=16)
    batch.received_at = past_dt
    db.session.flush()
    mov = (
        db.session.query(StockMovement)
        .filter_by(batch_id=batch.id, movement_type="INITIAL_STOCK")
        .first()
    )
    if mov:
        mov.created_at = past_dt
    db.session.commit()

    for days_ago, qty in sale_days_ago_and_qtys:
        sale = SalesService.create_sale(
            actor=actor,
            shop_id=shop_id,
            payload={
                "payment_mode": "CASH",
                "items": [{"product_id": product_id, "quantity": qty}],
            },
        )
        sale_dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
        sale.created_at = sale_dt
        db.session.commit()

    return db.session.get(InventoryBatch, batch.id)


def test_01_02_sales_velocity_with_returns_cancellations_and_shop_isolation(app: Flask):
    """Scenarios 1 & 2:
    - Sales velocity calculation from completed sales over lookback window.
    - Cancelled sales are excluded from velocity.
    - Valid item returns (RESTOCK and DAMAGED) are subtracted from gross sold units.
    - Sales in Shop B never pollute Shop A's velocity for the same product.
    """
    ctx = _setup_phase5_topology(app)
    with app.app_context():
        owner_a = db.session.get(User, ctx["owner_a_id"])
        owner_b = db.session.get(User, ctx["owner_b_id"])
        today = get_business_today()

        # Receive 200 units of Milk in Shop A (stocked 16 days ago) and record sales:
        # Day -5: 14 units
        # Day -3: 10 units
        # Day -1: 8 units
        # Total gross on completed sales = 32 units across 3 distinct days
        _backdate_first_receipt_and_create_sales(
            shop_id=ctx["shop_a_id"],
            actor=owner_a,
            product_id=ctx["prod_milk_id"],
            batch_code="MILK-VEL-A1",
            initial_qty=200,
            expiry_date=today + timedelta(days=12),
            sale_days_ago_and_qtys=[(5, 14), (3, 10), (1, 8)],
        )

        # Create a same-day sale of 20 units and CANCEL it -> must NOT count toward velocity
        cancelled_sale = SalesService.create_sale(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            payload={
                "payment_mode": "UPI",
                "items": [{"product_id": ctx["prod_milk_id"], "quantity": 20}],
            },
        )
        SalesService.cancel_sale(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            sale_id=cancelled_sale.id,
            payload={"reason": "Customer forgot wallet"},
        )

        # Process a return of 4 units on the Day -1 sale -> net sold becomes 32 - 4 = 28 units
        valid_sale = (
            db.session.query(Sale)
            .filter(
                Sale.shop_id == ctx["shop_a_id"],
                Sale.status == "COMPLETED",
            )
            .order_by(Sale.id.desc())
            .first()
        )
        sale_item = valid_sale.items[0]
        SalesService.process_return(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            sale_id=valid_sale.id,
            payload={
                "items": [
                    {
                        "sale_item_id": sale_item.id,
                        "quantity": 4,
                        "return_condition": "RESELLABLE",
                        "reason": "Customer bought extra packets",
                    }
                ]
            },
        )

        # Also create massive sales of the same product in Shop B (140 units) to verify isolation
        _backdate_first_receipt_and_create_sales(
            shop_id=ctx["shop_b_id"],
            actor=owner_b,
            product_id=ctx["prod_milk_id"],
            batch_code="MILK-VEL-B1",
            initial_qty=300,
            expiry_date=today + timedelta(days=15),
            sale_days_ago_and_qtys=[(4, 70), (2, 70)],
        )

        vel_a = StockAnalysisService.calculate_product_sales_velocity(
            shop_id=ctx["shop_a_id"],
            product_id=ctx["prod_milk_id"],
            lookback_days=14,
            today=today,
        )
        assert vel_a["gross_units_sold"] == 32
        assert vel_a["returned_units"] == 4
        assert vel_a["net_units_sold"] == 28
        assert vel_a["completed_sales_count"] == 3
        assert vel_a["distinct_sale_days"] == 3
        # 28 net units / 14 days = 2.0 units/day
        assert vel_a["daily_sales_velocity"] == pytest.approx(2.0)
        assert vel_a["active_day_velocity"] == pytest.approx(round(28 / 3, 4))
        assert vel_a["history_status"] == "SUFFICIENT_HISTORY"
        assert vel_a["is_demand_reliable"] is True
        assert vel_a["insufficient_data"] is False

        # Verify Shop B's velocity is strictly 140 / 14 = 10.0 units/day
        vel_b = StockAnalysisService.calculate_product_sales_velocity(
            shop_id=ctx["shop_b_id"],
            product_id=ctx["prod_milk_id"],
            lookback_days=14,
            today=today,
        )
        assert vel_b["net_units_sold"] == 140
        assert vel_b["daily_sales_velocity"] == pytest.approx(10.0)


def test_03_04_batch_expiry_risk_and_fefo_queue_awareness(app: Flask):
    """Scenarios 3 & 4:
    - Batch expiry-risk calculation with valid expiry dates.
    - Multiple batches for the same product respecting FEFO order:
      Earlier-expiring batch absorbs demand first before later-expiring batch gets remaining demand.
    """
    ctx = _setup_phase5_topology(app)
    with app.app_context():
        owner_a = db.session.get(User, ctx["owner_a_id"])
        today = get_business_today()

        # Establish velocity of 2.0 units/day for Milk over 14-day window (28 units sold).
        # Start with 48 units so after 28 sold, Batch 1 has 20 sellable units remaining, expiring in 5 days.
        b1 = _backdate_first_receipt_and_create_sales(
            shop_id=ctx["shop_a_id"],
            actor=owner_a,
            product_id=ctx["prod_milk_id"],
            batch_code="MILK-FEFO-1",
            initial_qty=48,
            expiry_date=today + timedelta(days=5),
            sale_days_ago_and_qtys=[(6, 10), (4, 10), (2, 8)],
        )
        assert b1.available_qty == 20

        # Receive Batch 2 of Milk: 30 units expiring in 7 days (still within near_expiry_days=7)
        b2, _ = InventoryService.receive_batch(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            payload={
                "product_id": ctx["prod_milk_id"],
                "batch_code": "MILK-FEFO-2",
                "quantity": 30,
                "unit_cost": "45.00",
                "expiry_date": (today + timedelta(days=7)).isoformat(),
            },
        )

        # Receive Batch 3 of Milk: 15 units expiring in 30 days (safe far-future batch)
        b3, _ = InventoryService.receive_batch(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            payload={
                "product_id": ctx["prod_milk_id"],
                "batch_code": "MILK-FEFO-3",
                "quantity": 15,
                "unit_cost": "45.00",
                "expiry_date": (today + timedelta(days=30)).isoformat(),
            },
        )

        prod_res = StockAnalysisService.get_product_analysis(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            product_id=ctx["prod_milk_id"],
            lookback_days=14,
            safety_stock_days=2,
        )["product_analysis"]

        batches_by_id = {b["batch_id"]: b for b in prod_res["batches"]}

        # Batch 1 (FEFO #1): 20 units, expires in 5 days, velocity=2.0/day
        # Product demand over 5 days = 10 units; earlier batches = 0 -> batch demand = 10 -> unsold = 10 (50% unsold -> CRITICAL)
        ba1 = batches_by_id[b1.id]
        assert ba1["fefo_rank"] == 1
        assert ba1["days_to_expiry"] == 5
        assert ba1["earlier_batches_sellable_qty"] == 0
        assert ba1["estimated_product_demand_before_expiry"] == 10
        assert ba1["estimated_batch_demand_before_expiry"] == 10
        assert ba1["estimated_sell_through_qty"] == 10
        assert ba1["estimated_unsold_at_expiry_qty"] == 10
        assert ba1["expiry_risk_level"] == "CRITICAL"

        # Batch 2 (FEFO #2): 30 units, expires in 7 days, velocity=2.0/day
        # Total product demand over 7 days = 14 units.
        # Because earlier FEFO Batch 1 holds 20 sellable units, remaining demand for Batch 2 = max(0, 14 - 20) = 0!
        # Therefore all 30 units of Batch 2 are projected unsold at expiry (0% sell-through within near_expiry) -> CRITICAL risk.
        ba2 = batches_by_id[b2.id]
        assert ba2["fefo_rank"] == 2
        assert ba2["days_to_expiry"] == 7
        assert ba2["earlier_batches_sellable_qty"] == 20
        assert ba2["estimated_product_demand_before_expiry"] == 14
        assert ba2["estimated_batch_demand_before_expiry"] == 0
        assert ba2["estimated_sell_through_qty"] == 0
        assert ba2["estimated_unsold_at_expiry_qty"] == 30
        assert ba2["expiry_risk_level"] == "CRITICAL"

        # Batch 3 (FEFO #3): 15 units, expires in 30 days, velocity=2.0/day
        # Total product demand over 30 days = 60 units.
        # Earlier batches hold 20 + 30 = 50 units -> remaining demand for Batch 3 = 60 - 50 = 10 units -> 5 unsold -> MEDIUM
        ba3 = batches_by_id[b3.id]
        assert ba3["fefo_rank"] == 3
        assert ba3["days_to_expiry"] == 30
        assert ba3["earlier_batches_sellable_qty"] == 50
        assert ba3["estimated_product_demand_before_expiry"] == 60
        assert ba3["estimated_batch_demand_before_expiry"] == 10
        assert ba3["estimated_unsold_at_expiry_qty"] == 5
        assert ba3["expiry_risk_level"] == "MEDIUM"


def test_05_06_excluded_stock_and_surplus_respects_safety_and_reorder_floor(app: Flask):
    """Scenarios 5 & 6:
    - Expired, damaged, and reserved stock excluded from eligible surplus and sellable risk calculations.
    - Surplus calculation respects safety stock and reorder threshold, never recommends transferring stock
      the shop needs to retain, and never double-counts units across batches.
    """
    ctx = _setup_phase5_topology(app)
    with app.app_context():
        owner_a = db.session.get(User, ctx["owner_a_id"])
        today = get_business_today()

        # Yogurt has reorder_threshold = 8.
        # Let's establish velocity = 1.0 unit/day (14 units sold over 14 days).
        # Start with 25 units in Batch 1 (expiring in 4 days) -> 11 units left after 14 sold.
        b_near = _backdate_first_receipt_and_create_sales(
            shop_id=ctx["shop_a_id"],
            actor=owner_a,
            product_id=ctx["prod_yogurt_id"],
            batch_code="YOG-NEAR-1",
            initial_qty=25,
            expiry_date=today + timedelta(days=4),
            sale_days_ago_and_qtys=[(7, 5), (4, 5), (1, 4)],
        )
        assert b_near.available_qty == 11

        # Reserve 2 units and mark 1 unit damaged on b_near:
        # available_qty drops by 1 damaged -> 10, then 2 moved to reserved_qty -> available_qty (sellable) = 8!
        InventoryService.adjust_batch(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            batch_id=b_near.id,
            payload={
                "movement_type": "MARK_DAMAGED",
                "quantity": 1,
                "reason": "Cracked lid during inspection",
            },
        )
        b_near_db = db.session.get(InventoryBatch, b_near.id)
        b_near_db.available_qty -= 2
        b_near_db.reserved_qty = 2
        db.session.commit()

        # Also insert an already-expired batch (expiry = yesterday) with 12 units still marked available
        # to verify read-only analysis excludes expired units and flags EXPIRED without mutating DB!
        b_exp = InventoryBatch(
            shop_id=ctx["shop_a_id"],
            product_id=ctx["prod_yogurt_id"],
            shop_product_id=b_near.shop_product_id,
            batch_code="YOG-EXPIRED-OLD",
            manufactured_date=today - timedelta(days=15),
            expiry_date=today - timedelta(days=1),
            initial_qty=12,
            available_qty=12,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=Decimal("35.00"),
            status="ACTIVE",
        )
        db.session.add(b_exp)
        db.session.commit()

        # Case A: Total sellable Yogurt is only 8 units (from b_near; b_exp has 0 sellable because it is expired).
        # Reorder threshold = 8, safety_stock_qty = ceil(1.0 * 2) = 2 -> min_product_retention_qty = max(8, 2) = 8.
        # Even though b_near expires in 4 days (demand=4, unsold=4), total shop sellable stock (8) == retention floor (8),
        # so potential_surplus_qty MUST be 0 (shop cannot transfer stock it needs to stay at its retention floor)!
        analysis_a = StockAnalysisService.get_product_analysis(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            product_id=ctx["prod_yogurt_id"],
            lookback_days=14,
            safety_stock_days=2,
            force_refresh=True,
        )["product_analysis"]

        assert analysis_a["stock_summary"]["sellable_qty"] == 8
        assert analysis_a["stock_summary"]["reserved_qty"] == 2
        assert analysis_a["stock_summary"]["damaged_qty"] == 1
        assert analysis_a["stock_summary"]["expired_qty"] == 12
        assert analysis_a["stock_summary"]["potential_surplus_qty"] == 0

        batches_map_a = {b["batch_id"]: b for b in analysis_a["batches"]}
        assert batches_map_a[b_exp.id]["expiry_risk_level"] == "EXPIRED"
        assert batches_map_a[b_exp.id]["eligible_sellable_qty"] == 0
        assert batches_map_a[b_exp.id]["potential_surplus_qty"] == 0

        assert batches_map_a[b_near.id]["eligible_sellable_qty"] == 8
        assert batches_map_a[b_near.id]["reserved_qty_excluded"] == 2
        assert batches_map_a[b_near.id]["damaged_qty_excluded"] == 1
        assert batches_map_a[b_near.id]["estimated_unsold_at_expiry_qty"] == 4
        assert batches_map_a[b_near.id]["potential_surplus_qty"] == 0
        assert batches_map_a[b_near.id]["retained_qty"] == 8

        # Case B: Now receive a second sellable batch of Yogurt (30 units expiring in 6 days).
        # Total sellable stock = 8 + 30 = 38 units.
        # Retention floor = max(reorder=8, safety=2) = 8.
        # Batch 1 (4 days): demand=4, safety=2 -> max(4+2=6, floor=8) = 8 retained in Batch 1 -> 0 surplus in Batch 1.
        # Batch 2 (6 days): cumulative product demand over 6 days = 6 + safety 2 = 8 (already satisfied by Batch 1's 8 retained units!).
        # Therefore Batch 2 has 0 retained units and 30 potential surplus units, and total surplus across batches = 30
        # (never exceeding 38 - 8 = 30, and never double-counting!).
        b_near2, _ = InventoryService.receive_batch(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            payload={
                "product_id": ctx["prod_yogurt_id"],
                "batch_code=" if False else "batch_code": "YOG-NEAR-2",
                "quantity": 30,
                "unit_cost": "35.00",
                "expiry_date": (today + timedelta(days=6)).isoformat(),
            },
        )

        analysis_b = StockAnalysisService.get_product_analysis(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            product_id=ctx["prod_yogurt_id"],
            lookback_days=14,
            safety_stock_days=2,
        )["product_analysis"]

        assert analysis_b["stock_summary"]["sellable_qty"] == 38
        assert analysis_b["stock_summary"]["retained_qty"] == 8
        assert analysis_b["stock_summary"]["potential_surplus_qty"] == 30
        assert (
            sum(b["potential_surplus_qty"] for b in analysis_b["batches"])
            == analysis_b["stock_summary"]["potential_surplus_qty"]
        )
        batches_map_b = {b["batch_id"]: b for b in analysis_b["batches"]}
        assert batches_map_b[b_near2.id]["potential_surplus_qty"] == 30
        assert batches_map_b[b_near2.id]["is_surplus_candidate"] is True


def test_07_08_low_stock_detection_and_missing_sparse_sales_history(app: Flask):
    """Scenarios 7 & 8:
    - Low-stock detection against reorder threshold (OUT_OF_STOCK, LOW_STOCK, ADEQUATE).
    - Missing or sparse sales history handled safely:
      Missing sales history is NEVER treated as proof of zero demand (`is_demand_reliable=False`,
      `insufficient_data=True`, `potential_surplus_qty=0`).
    """
    ctx = _setup_phase5_topology(app)
    with app.app_context():
        owner_a = db.session.get(User, ctx["owner_a_id"])
        today = get_business_today()

        # 1. Receive a brand-new batch of Artisan Strawberry Jam (prod_new_id) TODAY with 20 units expiring in 5 days,
        # and NO sales history at all.
        # Because it was just stocked today and has 0 sales, it must be flagged as NO_SALES_HISTORY
        # (`insufficient_data=True`), and MUST NOT be marked as surplus (`potential_surplus_qty == 0`).
        InventoryService.receive_batch(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            payload={
                "product_id": ctx["prod_new_id"],
                "batch_code": "JAM-NEW-01",
                "quantity": 20,
                "unit_cost": "110.00",
                "expiry_date": (today + timedelta(days=5)).isoformat(),
            },
        )

        jam_analysis = StockAnalysisService.get_product_analysis(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            product_id=ctx["prod_new_id"],
        )["product_analysis"]

        assert jam_analysis["sales_history"]["history_status"] == "NO_SALES_HISTORY"
        assert jam_analysis["sales_history"]["is_demand_reliable"] is False
        assert jam_analysis["sales_history"]["insufficient_data"] is True
        # Missing history is NOT treated as zero demand -> estimated_unsold is None, potential_surplus is 0
        jam_batch = jam_analysis["batches"][0]
        assert jam_batch["estimated_unsold_at_expiry_qty"] is None
        assert jam_batch["unverified_at_risk_qty"] == 20
        assert jam_batch["potential_surplus_qty"] == 0
        assert jam_batch["is_surplus_candidate"] is False
        assert any(
            r["recommendation_type"] == "INSUFFICIENT_SALES_HISTORY"
            for r in jam_analysis["recommendations"]
        )

        # 2. Low-stock detection on Assam Leaf Tea (`prod_tea_id`, reorder_threshold=12):
        # Initially 0 stock -> OUT_OF_STOCK with deficit=12
        tea_out = StockAnalysisService.get_product_analysis(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            product_id=ctx["prod_tea_id"],
        )["product_analysis"]
        assert tea_out["low_stock_analysis"]["is_out_of_stock"] is True
        assert tea_out["low_stock_analysis"]["is_low_stock"] is True
        assert tea_out["low_stock_analysis"]["replenishment_status"] == "OUT_OF_STOCK"
        assert tea_out["low_stock_analysis"]["deficit_qty"] == 12

        # Receive 7 boxes of Tea -> still <= reorder_threshold (12) -> LOW_STOCK with deficit = 5
        InventoryService.receive_batch(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            payload={
                "product_id": ctx["prod_tea_id"],
                "batch_code": "TEA-LOW-01",
                "quantity": 7,
                "unit_cost": "90.00",
            },
        )
        tea_low = StockAnalysisService.get_product_analysis(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            product_id=ctx["prod_tea_id"],
        )["product_analysis"]
        assert tea_low["low_stock_analysis"]["is_out_of_stock"] is False
        assert tea_low["low_stock_analysis"]["is_low_stock"] is True
        assert tea_low["low_stock_analysis"]["replenishment_status"] == "LOW_STOCK"
        assert tea_low["low_stock_analysis"]["deficit_qty"] == 5
        assert any(
            r["recommendation_type"] == "REPLENISHMENT_NEEDED"
            for r in tea_low["recommendations"]
        )


def test_10_11_12_13_read_only_guarantee_explanations_deduplication_and_staleness(app: Flask):
    """Scenarios 10, 11, 12, 13:
    - Running analysis leaves all batch stock quantities and stock movements 100% unchanged.
    - Recommendations include expected reasons, relevant DB values, calculation assumptions, rule_version, and analyzed_at.
    - Repeated analysis calls do not create duplicate active recommendations.
    - Stale analysis snapshot is automatically invalidated and recalculated after a sale, stock receipt, or stock adjustment.
    """
    ctx = _setup_phase5_topology(app)
    with app.app_context():
        owner_a = db.session.get(User, ctx["owner_a_id"])
        today = get_business_today()

        b1 = _backdate_first_receipt_and_create_sales(
            shop_id=ctx["shop_a_id"],
            actor=owner_a,
            product_id=ctx["prod_milk_id"],
            batch_code="MILK-SNAP-01",
            initial_qty=90,
            expiry_date=today + timedelta(days=5),
            sale_days_ago_and_qtys=[(5, 14), (2, 14)],
        )

        # Also add an expired batch to verify read-only analysis does not mutate it
        b_exp = InventoryBatch(
            shop_id=ctx["shop_a_id"],
            product_id=ctx["prod_milk_id"],
            shop_product_id=b1.shop_product_id,
            batch_code="MILK-EXP-READONLY",
            expiry_date=today - timedelta(days=2),
            initial_qty=5,
            available_qty=5,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=Decimal("45.00"),
            status="ACTIVE",
        )
        db.session.add(b_exp)
        db.session.commit()

        # Capture exact DB state before analysis
        before_batches = {
            b.id: (b.available_qty, b.reserved_qty, b.damaged_qty, b.expired_qty, b.status)
            for b in db.session.query(InventoryBatch)
            .filter_by(shop_id=ctx["shop_a_id"])
            .all()
        }
        before_movements_count = (
            db.session.query(StockMovement)
            .filter_by(shop_id=ctx["shop_a_id"])
            .count()
        )

        # First analysis run (fresh computation)
        res1 = SurplusService.analyze_shop(actor=owner_a, shop_id=ctx["shop_a_id"])
        assert res1["freshness"]["was_cached"] is False
        fp1 = res1["freshness"]["data_fingerprint"]

        # Verify 100% read-only guarantee (zero stock or movement mutations)
        after_batches = {
            b.id: (b.available_qty, b.reserved_qty, b.damaged_qty, b.expired_qty, b.status)
            for b in db.session.query(InventoryBatch)
            .filter_by(shop_id=ctx["shop_a_id"])
            .all()
        }
        after_movements_count = (
            db.session.query(StockMovement)
            .filter_by(shop_id=ctx["shop_a_id"])
            .count()
        )
        assert before_batches == after_batches
        assert before_movements_count == after_movements_count

        # Verify recommendation explanations & metadata (Scenario 11)
        recs1 = res1["recommendations"]
        assert len(recs1) > 0
        rec_keys1 = [r["recommendation_key"] for r in recs1]
        assert len(rec_keys1) == len(set(rec_keys1)), "Recommendations must be unique by key"
        for r in recs1:
            assert r["reason"]
            assert r["explanation"]
            assert isinstance(r["relevant_values"], dict) and len(r["relevant_values"]) > 0
            assert isinstance(r["calculation_assumptions"], dict)
            assert r["rule_version"] == res1["rule_version"]
            assert r["analyzed_at"]

        # Second analysis run without DB changes -> served from verified snapshot without duplicating recommendations (Scenario 12)
        res2 = SurplusService.analyze_shop(actor=owner_a, shop_id=ctx["shop_a_id"])
        assert res2["freshness"]["was_cached"] is True
        assert res2["freshness"]["evaluation_count"] == 2
        assert [r["recommendation_key"] for r in res2["recommendations"]] == rec_keys1

        # Now record a POS sale -> snapshot must become STALE (`DATA_CHANGED`) and automatically recalculate (Scenario 13)
        SalesService.create_sale(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            payload={
                "payment_mode": "CASH",
                "items": [{"product_id": ctx["prod_milk_id"], "quantity": 6}],
            },
        )
        freshness_after_sale = StockAnalysisService.check_snapshot_freshness(
            shop_id=ctx["shop_a_id"]
        )
        assert freshness_after_sale["is_stale"] is True
        assert freshness_after_sale["stale_reason"] == "DATA_CHANGED"

        res3 = SurplusService.analyze_shop(actor=owner_a, shop_id=ctx["shop_a_id"])
        assert res3["freshness"]["was_cached"] is False
        assert res3["freshness"]["recalculated_reason"] == "DATA_CHANGED"
        assert res3["freshness"]["data_fingerprint"] != fp1

        # Now perform a stock adjustment -> snapshot must again invalidate and recalculate
        InventoryService.adjust_batch(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            batch_id=b1.id,
            payload={
                "movement_type": "MARK_DAMAGED",
                "quantity": 2,
                "reason": "Damaged packet in chiller",
            },
        )
        res4 = SurplusService.analyze_shop(actor=owner_a, shop_id=ctx["shop_a_id"])
        assert res4["freshness"]["was_cached"] is False
        assert res4["freshness"]["recalculated_reason"] == "DATA_CHANGED"
        assert res4["freshness"]["data_fingerprint"] != res3["freshness"]["data_fingerprint"]


def test_09_14_rbac_isolation_and_html_and_api_endpoints(app: Flask, client: FlaskClient):
    """Scenarios 9 & 14:
    - Unauthorized access to another shop's or another organization's analysis is denied (403).
    - All HTML pages and REST API endpoints for Phase 5 render and respond with expected status codes and payloads.
    """
    ctx = _setup_phase5_topology(app)
    with app.app_context():
        owner_a = db.session.get(User, ctx["owner_a_id"])
        today = get_business_today()
        b1 = _backdate_first_receipt_and_create_sales(
            shop_id=ctx["shop_a_id"],
            actor=owner_a,
            product_id=ctx["prod_milk_id"],
            batch_code="MILK-HTTP-01",
            initial_qty=100,
            expiry_date=today + timedelta(days=5),
            sale_days_ago_and_qtys=[(4, 14), (2, 14)],
        )
        b1_id = b1.id

    shop_a_id = ctx["shop_a_id"]
    shop_b_id = ctx["shop_b_id"]
    shop_c_id = ctx["shop_c_id"]
    prod_milk_id = ctx["prod_milk_id"]

    # 1. Unauthenticated access -> 401 on API, 302 redirect on HTML
    unauth_api = client.get(f"/api/v1/shops/{shop_a_id}/analysis/overview")
    assert unauth_api.status_code == 401
    unauth_html = client.get(f"/shops/{shop_a_id}/analysis")
    assert unauth_html.status_code == 302

    # 2. Authenticate as Shop A Owner (`p5_owner_a`)
    _login(client, "p5_owner_a")

    # Entry redirect `/analysis` -> `/shops/<shop_a_id>/analysis`
    entry_resp = client.get("/analysis")
    assert entry_resp.status_code == 302
    assert f"/shops/{shop_a_id}/analysis" in entry_resp.headers["Location"]

    # HTML Views for Shop A (200 OK)
    for path in (
        f"/shops/{shop_a_id}/analysis",
        f"/shops/{shop_a_id}/analysis/expiry-risk?only_near_expiry=1&q=MILK",
        f"/shops/{shop_a_id}/analysis/surplus",
        f"/shops/{shop_a_id}/analysis/low-stock?show_all=1",
        f"/shops/{shop_a_id}/analysis/recommendations?severity=WARNING",
        f"/shops/{shop_a_id}/analysis/products/{prod_milk_id}",
        f"/shops/{shop_a_id}/analysis/batches/{b1_id}",
    ):
        html_resp = client.get(path)
        assert html_resp.status_code == 200, f"Expected 200 for {path}, got {html_resp.status_code}"
        assert "Sanchay AI" in html_resp.get_data(as_text=True)

    # REST API Endpoints for Shop A (200 OK)
    overview_api = client.get(
        f"/api/v1/shops/{shop_a_id}/analysis/overview?lookback_days=14&safety_stock_days=2"
    )
    assert overview_api.status_code == 200
    overview_json = overview_api.get_json()
    assert overview_json["status"] == "success"
    assert overview_json["data"]["shop_id"] == shop_a_id
    assert overview_json["data"]["kpis"]["surplus_batches_count"] >= 1

    expiry_api = client.get(
        f"/api/v1/shops/{shop_a_id}/analysis/expiry-risk?only_near_expiry=1"
    )
    assert expiry_api.status_code == 200
    assert expiry_api.get_json()["meta"]["count"] >= 1

    surplus_api = client.get(f"/api/v1/shops/{shop_a_id}/analysis/surplus")
    assert surplus_api.status_code == 200
    assert len(surplus_api.get_json()["data"]["candidates"]) >= 1

    compat_surplus_api = client.get(f"/api/v1/shops/{shop_a_id}/surplus-candidates")
    assert compat_surplus_api.status_code == 200
    assert compat_surplus_api.get_json()["data"]["shop_id"] == shop_a_id

    low_stock_api = client.get(f"/api/v1/shops/{shop_a_id}/analysis/low-stock")
    assert low_stock_api.status_code == 200
    assert low_stock_api.get_json()["data"]["kpis"]["low_stock_products_count"] >= 1

    recs_api = client.get(f"/api/v1/shops/{shop_a_id}/analysis/recommendations")
    assert recs_api.status_code == 200
    assert len(recs_api.get_json()["data"]["recommendations"]) >= 1

    prod_api = client.get(
        f"/api/v1/shops/{shop_a_id}/analysis/products/{prod_milk_id}"
    )
    assert prod_api.status_code == 200
    assert prod_api.get_json()["data"]["product_analysis"]["sku"] == "P5-MILK-1L"

    batch_api = client.get(
        f"/api/v1/shops/{shop_a_id}/analysis/batches/{b1_id}"
    )
    assert batch_api.status_code == 200
    assert batch_api.get_json()["data"]["batch_analysis"]["batch_code"] == "MILK-HTTP-01"

    # Invalid query parameter validation -> 400 VALIDATION_ERROR
    bad_param_api = client.get(
        f"/api/v1/shops/{shop_a_id}/analysis/overview?lookback_days=0"
    )
    assert bad_param_api.status_code == 400
    assert bad_param_api.get_json()["error"]["code"] == "VALIDATION_ERROR"

    # 3. Cross-shop access within same organization denied (`p5_owner_a` accessing `shop_b_id`) -> 403
    cross_shop_api = client.get(f"/api/v1/shops/{shop_b_id}/analysis/overview")
    assert cross_shop_api.status_code == 403
    cross_shop_html = client.get(f"/shops/{shop_b_id}/analysis")
    assert cross_shop_html.status_code == 403

    # 4. Cross-organization access denied (`p5_owner_a` accessing `shop_c_id` in Org 2) -> 403
    cross_org_api = client.get(f"/api/v1/shops/{shop_c_id}/analysis/overview")
    assert cross_org_api.status_code == 403

    # 5. Super Admin of Org 1 (`p5_superadmin`) can view both Shop A and Shop B in Org 1, but NOT Shop C in Org 2
    _login(client, "p5_superadmin")
    assert client.get(f"/api/v1/shops/{shop_a_id}/analysis/overview").status_code == 200
    assert client.get(f"/api/v1/shops/{shop_b_id}/analysis/overview").status_code == 200
    assert client.get(f"/api/v1/shops/{shop_c_id}/analysis/overview").status_code == 403

    # 6. Non-existent product / batch -> 404
    assert client.get(f"/api/v1/shops/{shop_a_id}/analysis/products/999999").status_code == 404
    assert client.get(f"/api/v1/shops/{shop_a_id}/analysis/batches/999999").status_code == 404


def test_15_overstock_coverage_sparse_history_and_safety_stock_override(app: Flask):
    """Verify Condition B overstock surplus (NON_PERISHABLE), SPARSE_HISTORY classification, and explicit safety_stock_qty override."""
    ctx = _setup_phase5_topology(app)
    with app.app_context():
        owner_a = db.session.get(User, ctx["owner_a_id"])
        today = get_business_today()

        # 1. Non-perishable Tea (`prod_tea_id`, reorder_threshold=12, surplus_threshold=40):
        # Receive 128 boxes stocked 16 days ago, sell 28 boxes over 2 days (velocity = 2.0/day),
        # leaving 100 sellable boxes (> surplus_threshold=40).
        # Over a 30-day surplus_coverage_days horizon, coverage demand = 30 * 2.0 = 60 boxes.
        # With safety_stock_days=2 (4 boxes), overstock_retention_floor = max(40, 12, 60 + 4) = 64 boxes.
        # Therefore potential_surplus_qty = 100 - 64 = 36 boxes (`OVERSTOCK_COVERAGE`)!
        _backdate_first_receipt_and_create_sales(
            shop_id=ctx["shop_a_id"],
            actor=owner_a,
            product_id=ctx["prod_tea_id"],
            batch_code="TEA-OVERSTOCK-01",
            initial_qty=128,
            expiry_date=None,
            sale_days_ago_and_qtys=[(6, 14), (2, 14)],
        )

        tea_res = StockAnalysisService.get_product_analysis(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            product_id=ctx["prod_tea_id"],
            lookback_days=14,
            safety_stock_days=2,
            surplus_coverage_days=30,
        )["product_analysis"]

        assert tea_res["stock_summary"]["sellable_qty"] == 100
        assert tea_res["thresholds"]["overstock_retention_floor"] == 64
        assert tea_res["stock_summary"]["potential_surplus_qty"] == 36
        assert tea_res["batches"][0]["surplus_reason"] == "OVERSTOCK_COVERAGE"
        assert tea_res["batches"][0]["expiry_risk_level"] == "NON_PERISHABLE"

        # Now apply an explicit safety_stock_qty override of 25 boxes:
        # overstock_retention_floor becomes max(40, max(12, 25), 60 + 25) = 85 boxes -> surplus drops to 100 - 85 = 15!
        tea_override = StockAnalysisService.get_product_analysis(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            product_id=ctx["prod_tea_id"],
            lookback_days=14,
            safety_stock_days=2,
            safety_stock_qty=25,
            surplus_coverage_days=30,
        )["product_analysis"]
        assert tea_override["thresholds"]["safety_stock_qty"] == 25
        assert tea_override["thresholds"]["overstock_retention_floor"] == 85
        assert tea_override["stock_summary"]["potential_surplus_qty"] == 15

        # 2. Sparse sales history (`SPARSE_HISTORY`):
        # Yogurt stocked 16 days ago, but sold on ONLY 1 distinct day in the 14-day window (< min_active_sale_days=2).
        _backdate_first_receipt_and_create_sales(
            shop_id=ctx["shop_a_id"],
            actor=owner_a,
            product_id=ctx["prod_yogurt_id"],
            batch_code="YOG-SPARSE-01",
            initial_qty=30,
            expiry_date=today + timedelta(days=5),
            sale_days_ago_and_qtys=[(3, 7)],
        )
        yog_res = StockAnalysisService.get_product_analysis(
            actor=owner_a,
            shop_id=ctx["shop_a_id"],
            product_id=ctx["prod_yogurt_id"],
            lookback_days=14,
        )["product_analysis"]
        assert yog_res["sales_history"]["history_status"] == "SPARSE_HISTORY"
        assert yog_res["sales_history"]["insufficient_data"] is True
        assert yog_res["sales_history"]["demand_confidence"] == "LOW_SPARSE_HISTORY"

