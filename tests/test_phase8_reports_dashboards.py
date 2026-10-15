"""Phase 8 Test Suite: Dashboards, Analytics & Tabular Reports.

Verifies:
1. Super Admin Dashboard metrics & organization summary (API-039).
2. Shop Owner Dashboard metrics & shop summary (API-038).
3. Strict tenant and shop-level RBAC isolation (403 on cross-shop access).
4. Daily and monthly sales trends with continuous zero-filling.
5. Top-selling products by quantity and revenue.
6. Stock distribution by category, status, and expiry horizons.
7. Inter-store transfer activity and throughput metrics.
8. Tabular report generation for SALES, INVENTORY, NEAR_EXPIRY, and TRANSFERS.
9. Date range validation (inverted dates, invalid formats).
10. RFC 4180 CSV export streaming (API-040).
11. Graceful empty datasets handling and zero division protection.
12. HTML views rendering for /dashboard, /reports, and /analytics.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import io
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch, StockMovement
from app.models.organization import Organization, Shop, UserShopAssignment
from app.models.sales import Sale, SaleItem
from app.models.transfer import TransferRequest
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.catalog_service import CatalogService
from app.services.inventory_service import InventoryService, get_business_today
from app.services.org_service import MembershipService, OrganizationService, ShopService
from app.services.report_service import ReportService, validate_report_date_range
from app.services.sales_service import SalesService
from app.services.transfer_service import TransferService
from app.utils.errors import AuthorizationError, ValidationError
from tests.test_phase1_auth import extract_csrf_token


def _login(client: FlaskClient, identity: str, password: str = "ValidPassword123") -> str:
    """Authenticate client via JSON API, rotate CSRF, and set default headers."""
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


@pytest.fixture
def phase8_topology(app: Flask) -> dict[str, Any]:
    """Create a complete topology with Organization, 2 Shops, Products, Batches, Sales, and Transfers."""
    with app.app_context():
        # 1. Super Admin
        admin = AuthService.create_user(
            username="p8_superadmin",
            email="p8_admin@sanchay.local",
            password="ValidPassword123",
            full_name="Phase8 Super Admin",
            role="SUPER_ADMIN",
        )

        # 2. Organization 1
        org1 = OrganizationService.create_organization(
            actor=admin,
            payload={
                "name": "Sanchay Retail Org P8",
                "code": "SRO-P8",
                "contact_email": "ops@srop8.local",
                "city": "Pune",
                "state": "Maharashtra",
                "low_stock_default_threshold": 15,
                "near_expiry_days_threshold": 10,
            },
        )

        # 3. Shop A and Shop B
        shop_a = ShopService.create_shop(
            actor=admin,
            payload={
                "organization_id": org1.id,
                "name": "Shop A - Shivaji Nagar",
                "code": "SVN-P8",
                "address": "FC Road",
                "city": "Pune",
                "pincode": "411005",
                "contact_phone": "9811111111",
            },
        )
        shop_b = ShopService.create_shop(
            actor=admin,
            payload={
                "organization_id": org1.id,
                "name": "Shop B - Camp",
                "code": "CMP-P8",
                "address": "MG Road",
                "city": "Pune",
                "pincode": "411001",
                "contact_phone": "9822222222",
            },
        )

        # 4. Shop Owners
        owner_a = AuthService.create_user(
            username="p8_owner_a",
            email="owner_a@srop8.local",
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
        shop_a.owner_user_id = owner_a.id

        owner_b = AuthService.create_user(
            username="p8_owner_b",
            email="owner_b@srop8.local",
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
        shop_b.owner_user_id = owner_b.id

        # 5. Products in Catalog
        prod1 = CatalogService.create_organization_product(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "name": "Fresh Cow Milk 1L",
                "sku": "SKU-MILK-1L-P8",
                "category": "Dairy",
                "unit": "litre",
                "default_purchase_price": "40.00",
                "default_selling_price": "60.00",
                "reorder_threshold": 20,
                "is_perishable": True,
            },
        )
        prod2 = CatalogService.create_organization_product(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "name": "Whole Wheat Bread 400g",
                "sku": "SKU-BREAD-400G-P8",
                "category": "Bakery",
                "unit": "pack",
                "default_purchase_price": "25.00",
                "default_selling_price": "45.00",
                "reorder_threshold": 10,
                "is_perishable": True,
            },
        )
        prod3 = CatalogService.create_organization_product(
            actor=owner_b,
            shop_id=shop_b.id,
            payload={
                "name": "Basmati Rice 5kg",
                "sku": "SKU-RICE-5KG-P8",
                "category": "Grains",
                "unit": "kg",
                "default_purchase_price": "300.00",
                "default_selling_price": "450.00",
                "reorder_threshold": 5,
                "is_perishable": False,
            },
        )

        # 6. Also Enroll prod1 in Shop B
        CatalogService.create_or_enroll_shop_product(
            actor=owner_b,
            shop_id=shop_b.id,
            payload={
                "product_id": prod1.id,
                "purchase_price": "42.00",
                "selling_price": "62.00",
                "low_stock_threshold": 15,
            },
        )

        today = get_business_today()

        # 7. Add Batches to Shop A
        batch_a1, _ = InventoryService.receive_batch(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "product_id": prod1.id,
                "batch_code": "BAT-MILK-P8-NEAR",
                "initial_qty": 10,
                "unit_cost": "40.00",
                "expiry_date": (today + timedelta(days=3)).isoformat(),
            },
        )
        batch_a2, _ = InventoryService.receive_batch(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "product_id": prod2.id,
                "batch_code": "BAT-BREAD-P8-GOOD",
                "initial_qty": 30,
                "unit_cost": "25.00",
                "expiry_date": (today + timedelta(days=20)).isoformat(),
            },
        )

        # 8. Add Batches to Shop B
        batch_b1, _ = InventoryService.receive_batch(
            actor=owner_b,
            shop_id=shop_b.id,
            payload={
                "product_id": prod1.id,
                "batch_code": "BAT-MILK-B-P8",
                "initial_qty": 50,
                "unit_cost": "42.00",
                "expiry_date": (today + timedelta(days=4)).isoformat(),
            },
        )
        batch_b3, _ = InventoryService.receive_batch(
            actor=owner_b,
            shop_id=shop_b.id,
            payload={
                "product_id": prod3.id,
                "batch_code": "BAT-RICE-B-P8",
                "initial_qty": 20,
                "unit_cost": "300.00",
                "expiry_date": (today + timedelta(days=300)).isoformat(),
            },
        )

        # 9. Record Sales in Shop A
        sale_a = SalesService.create_sale(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "customer_name": "Ramesh Sharma",
                "payment_mode": "UPI_MANUAL",
                "items": [
                    {"product_id": prod1.id, "quantity": 4},
                    {"product_id": prod2.id, "quantity": 5},
                ],
            },
        )

        # 10. Record Inter-Store Transfer: Shop B -> Shop A
        trf = TransferService.create_transfer_proposal(
            actor=owner_b,
            payload={
                "source_shop_id": shop_b.id,
                "recipient_shop_id": shop_a.id,
                "product_id": prod1.id,
                "source_batch_id": batch_b1.id,
                "quantity": 10,
                "unit_transfer_price": "42.00",
            },
        )
        TransferService.approve_transfer(actor=owner_a, transfer_id=trf.id)
        TransferService.dispatch_transfer(actor=owner_b, transfer_id=trf.id)
        TransferService.confirm_receipt(actor=owner_a, transfer_id=trf.id)

        db.session.commit()

        return {
            "admin_id": admin.id,
            "admin_username": admin.username,
            "org_id": org1.id,
            "shop_a_id": shop_a.id,
            "shop_b_id": shop_b.id,
            "owner_a_id": owner_a.id,
            "owner_a_username": owner_a.username,
            "owner_b_id": owner_b.id,
            "owner_b_username": owner_b.username,
            "prod1_id": prod1.id,
            "prod2_id": prod2.id,
            "prod3_id": prod3.id,
            "sale_a_id": sale_a.id,
            "trf_id": trf.id,
        }


# ==============================================================================
# 1. Super Admin Dashboard & Organization Summary Tests
# ==============================================================================


def test_super_admin_dashboard_metrics(app: Flask, phase8_topology: dict[str, Any]):
    """Verify Super Admin organization-wide KPIs and shop-by-shop breakdown."""
    with app.app_context():
        admin = User.query.get(phase8_topology["admin_id"])
        metrics = ReportService.get_organization_dashboard_metrics(actor=admin)

        assert metrics["organization"]["id"] == phase8_topology["org_id"]
        kpis = metrics["kpis"]
        assert kpis["total_shops"] == 2
        assert kpis["active_shops"] == 2
        assert kpis["total_products"] == 3
        assert kpis["total_sales_count"] == 1
        assert kpis["gross_revenue"] > 0
        assert kpis["gross_profit"] > 0
        assert kpis["completed_transfers_count"] == 1
        assert kpis["near_expiry_batch_count"] >= 1
        assert kpis["near_expiry_valuation_at_risk"] > 0

        # Verify shop breakdown
        breakdown = metrics["shops_breakdown"]
        assert len(breakdown) == 2
        shop_a_row = next((r for r in breakdown if r["shop_id"] == phase8_topology["shop_a_id"]), None)
        assert shop_a_row is not None
        assert shop_a_row["sales_count"] == 1
        assert shop_a_row["completed_transfers"] == 1


def test_api_organization_summary_endpoint(client: FlaskClient, phase8_topology: dict[str, Any]):
    """Verify GET /api/v1/reports/organization/summary (API-039)."""
    _login(client, phase8_topology["admin_username"])
    resp = client.get("/api/v1/reports/organization/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "kpis" in data["data"]
    assert data["data"]["kpis"]["total_shops"] == 2


# ==============================================================================
# 2. Shop Owner Dashboard & Isolation Tests
# ==============================================================================


def test_shop_owner_dashboard_metrics(app: Flask, phase8_topology: dict[str, Any]):
    """Verify Shop Owner dashboard metrics for an authorized store."""
    with app.app_context():
        owner_a = User.query.get(phase8_topology["owner_a_id"])
        shop_metrics = ReportService.get_shop_dashboard_metrics(
            actor=owner_a,
            shop_id=phase8_topology["shop_a_id"],
        )

        assert shop_metrics["shop"]["id"] == phase8_topology["shop_a_id"]
        inv = shop_metrics["inventory"]
        assert inv["total_products"] == 2
        assert inv["sellable_qty"] > 0
        assert inv["cost_valuation"] > 0

        sales = shop_metrics["sales"]
        assert sales["sales_count"] == 1
        assert sales["gross_revenue"] > 0
        assert sales["gross_profit"] > 0

        alerts = shop_metrics["alerts"]
        assert "low_stock_items" in alerts
        assert "near_expiry_items" in alerts

        activity = shop_metrics["activity"]
        assert len(activity["recent_sales"]) == 1


def test_shop_level_isolation_enforcement(client: FlaskClient, phase8_topology: dict[str, Any]):
    """Verify Shop Owner cannot access unauthorized shop metrics or org summary."""
    # Login as Owner A
    _login(client, phase8_topology["owner_a_username"])

    # 1. Accessing authorized Shop A summary -> 200 OK
    resp_a = client.get(f"/api/v1/reports/shop/{phase8_topology['shop_a_id']}/summary")
    assert resp_a.status_code == 200
    assert resp_a.get_json()["success"] is True

    # 2. Accessing unauthorized Shop B summary -> 403 Forbidden
    resp_b = client.get(f"/api/v1/reports/shop/{phase8_topology['shop_b_id']}/summary")
    assert resp_b.status_code == 403
    assert resp_b.get_json()["success"] is False
    assert resp_b.get_json()["error"]["code"] in ("UNAUTHORIZED_SHOP_ACCESS", "AUTH_FORBIDDEN")

    # 3. Accessing Super Admin org summary -> 403 Forbidden
    resp_org = client.get("/api/v1/reports/organization/summary")
    assert resp_org.status_code == 403
    assert resp_org.get_json()["success"] is False


# ==============================================================================
# 3. Analytics: Daily & Monthly Sales Trends
# ==============================================================================


def test_sales_trends_daily_and_monthly(app: Flask, phase8_topology: dict[str, Any]):
    """Verify sales trend series returns continuous points with zero-filling."""
    with app.app_context():
        admin = User.query.get(phase8_topology["admin_id"])

        # Daily trends (30 days)
        daily = ReportService.get_sales_trends(actor=admin, days=30, group_by="daily")
        assert len(daily) == 30
        today_str = get_business_today().strftime("%Y-%m-%d")
        today_point = next((p for p in daily if p["period"] == today_str), None)
        assert today_point is not None
        assert today_point["sales_count"] == 1
        assert today_point["revenue"] > 0

        # Monthly trends (6 months)
        monthly = ReportService.get_sales_trends(actor=admin, days=180, group_by="monthly")
        assert len(monthly) >= 2
        current_m = get_business_today().strftime("%Y-%m")
        current_m_point = next((p for p in monthly if p["period"] == current_m), None)
        assert current_m_point is not None
        assert current_m_point["sales_count"] == 1


def test_api_analytics_trends_endpoint(client: FlaskClient, phase8_topology: dict[str, Any]):
    """Verify GET /api/v1/reports/analytics/trends."""
    _login(client, phase8_topology["admin_username"])
    resp = client.get(f"/api/v1/reports/analytics/trends?shop_id={phase8_topology['shop_a_id']}&days=14")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert len(data["data"]["trends"]) == 14


# ==============================================================================
# 4. Analytics: Top Products, Stock Distribution & Transfers
# ==============================================================================


def test_top_selling_products(app: Flask, phase8_topology: dict[str, Any]):
    """Verify top-selling products by quantity and revenue."""
    with app.app_context():
        admin = User.query.get(phase8_topology["admin_id"])
        top = ReportService.get_top_selling_products(actor=admin, limit=5)
        assert len(top["by_quantity"]) >= 1
        assert len(top["by_revenue"]) >= 1
        assert top["by_quantity"][0]["units_sold"] > 0
        assert top["by_revenue"][0]["revenue"] > 0


def test_stock_distribution(app: Flask, phase8_topology: dict[str, Any]):
    """Verify stock distribution across categories, statuses, and horizons."""
    with app.app_context():
        admin = User.query.get(phase8_topology["admin_id"])
        dist = ReportService.get_stock_distribution(actor=admin)
        assert len(dist["by_category"]) >= 2
        assert len(dist["by_status"]) >= 1
        assert len(dist["by_expiry_horizon"]) >= 1


def test_transfer_activity(app: Flask, phase8_topology: dict[str, Any]):
    """Verify inter-store transfer activity throughput metrics."""
    with app.app_context():
        admin = User.query.get(phase8_topology["admin_id"])
        activity = ReportService.get_transfer_activity(actor=admin)
        assert activity["total_transfers"] == 1
        assert activity["completed"]["count"] == 1
        assert activity["total_units_transferred"] == 10


# ==============================================================================
# 5. Tabular Reports & Date Validation Tests
# ==============================================================================


def test_generate_report_types(app: Flask, phase8_topology: dict[str, Any]):
    """Verify report generation for SALES, INVENTORY, NEAR_EXPIRY, and TRANSFERS."""
    with app.app_context():
        admin = User.query.get(phase8_topology["admin_id"])
        today = get_business_today()

        # 1. SALES report
        sales_rep = ReportService.generate_report(
            actor=admin,
            report_type="SALES",
            start_date=today - timedelta(days=7),
            end_date=today,
        )
        assert sales_rep["report_type"] == "SALES"
        assert len(sales_rep["rows"]) == 1
        assert sales_rep["summary"]["completed_sales_count"] == 1

        # 2. INVENTORY report
        inv_rep = ReportService.generate_report(
            actor=admin,
            report_type="INVENTORY",
        )
        assert inv_rep["report_type"] == "INVENTORY"
        assert len(inv_rep["rows"]) >= 3
        assert inv_rep["summary"]["total_stock_units"] > 0

        # 3. NEAR_EXPIRY report
        exp_rep = ReportService.generate_report(
            actor=admin,
            report_type="NEAR_EXPIRY",
            end_date=today + timedelta(days=10),
        )
        assert exp_rep["report_type"] == "NEAR_EXPIRY"
        assert len(exp_rep["rows"]) >= 1

        # 4. TRANSFERS report
        trf_rep = ReportService.generate_report(
            actor=admin,
            report_type="TRANSFERS",
        )
        assert trf_rep["report_type"] == "TRANSFERS"
        assert len(trf_rep["rows"]) == 1
        assert trf_rep["summary"]["completed_count"] == 1


def test_date_range_validation_errors():
    """Verify validation errors on inverted or malformed date ranges."""
    today = get_business_today()

    # Inverted dates (start > end)
    with pytest.raises(ValidationError) as exc:
        validate_report_date_range(start_date_raw=today, end_date_raw=today - timedelta(days=5))
    assert "cannot be after end date" in str(exc.value)

    # Malformed date string
    with pytest.raises(ValidationError) as exc:
        validate_report_date_range(start_date_raw="not-a-date", end_date_raw=today)
    assert "Invalid date format" in str(exc.value)


# ==============================================================================
# 6. CSV Export Streaming (API-040)
# ==============================================================================


def test_csv_export_streaming(client: FlaskClient, phase8_topology: dict[str, Any]):
    """Verify GET /api/v1/reports/export produces valid RFC 4180 CSV."""
    _login(client, phase8_topology["admin_username"])

    resp = client.get("/api/v1/reports/export?type=SALES&format=csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["Content-Type"]
    assert "attachment; filename=" in resp.headers["Content-Disposition"]

    # Read and parse CSV content
    csv_text = resp.get_data(as_text=True)
    reader = list(csv.reader(io.StringIO(csv_text)))
    assert len(reader) >= 2  # Header + at least 1 record
    header = reader[0]
    assert "Invoice Number" in header
    assert "Gross Profit (INR)" in header

    # Verify inventory CSV
    resp_inv = client.get("/api/v1/reports/export?type=INVENTORY&format=csv")
    assert resp_inv.status_code == 200
    csv_inv = list(csv.reader(io.StringIO(resp_inv.get_data(as_text=True))))
    assert "Product SKU" in csv_inv[0]

    # Verify unsupported format returns 400
    resp_bad = client.get("/api/v1/reports/export?type=SALES&format=pdf")
    assert resp_bad.status_code == 400
    assert resp_bad.get_json()["error"]["code"] == "VALIDATION_ERROR"


# ==============================================================================
# 7. Empty Datasets & Zero-State Handling
# ==============================================================================


def test_empty_shop_graceful_handling(app: Flask, phase8_topology: dict[str, Any]):
    """Verify a shop with 0 inventory, sales, or transfers renders safely without ZeroDivisionError."""
    with app.app_context():
        admin = User.query.get(phase8_topology["admin_id"])
        # Create empty shop C
        shop_c = ShopService.create_shop(
            actor=admin,
            payload={
                "organization_id": phase8_topology["org_id"],
                "name": "Shop C - Empty",
                "code": "EMP-P8",
                "address": "Camp Area",
                "city": "Pune",
                "pincode": "411001",
                "contact_phone": "9833333333",
            },
        )

        metrics = ReportService.get_shop_dashboard_metrics(actor=admin, shop_id=shop_c.id)
        assert metrics["inventory"]["total_stock_qty"] == 0
        assert metrics["inventory"]["cost_valuation"] == 0.0
        assert metrics["sales"]["sales_count"] == 0
        assert metrics["sales"]["gross_margin_pct"] == 0.0
        assert metrics["sales"]["average_order_value"] == 0.0
        assert len(metrics["alerts"]["low_stock_items"]) == 0
        assert len(metrics["alerts"]["near_expiry_items"]) == 0


# ==============================================================================
# 8. HTML Views Rendering Tests
# ==============================================================================


def test_html_dashboard_and_reports_pages(client: FlaskClient, phase8_topology: dict[str, Any]):
    """Verify HTML pages render HTTP 200 with required dashboard & report components."""
    # 1. Super Admin Dashboard
    _login(client, phase8_topology["admin_username"])
    resp_dash = client.get("/dashboard")
    assert resp_dash.status_code == 200
    content = resp_dash.get_data(as_text=True)
    assert "Executive Operational Dashboard" in content
    assert "Shop-by-Shop Operational Performance" in content
    assert "Reports &amp; Analytics" in content

    # 2. Reports Hub
    resp_rep = client.get("/reports?type=SALES")
    assert resp_rep.status_code == 200
    rep_content = resp_rep.get_data(as_text=True)
    assert "Reports &amp; Analytics Hub" in rep_content
    assert "Export to CSV" in rep_content
    assert "Operational Analytics Visualizations" in rep_content

    # 3. Analytics redirect
    resp_ana = client.get("/analytics")
    assert resp_ana.status_code == 302
    assert "/reports" in resp_ana.headers["Location"]

    # 4. Shop Owner Dashboard
    _login(client, phase8_topology["owner_a_username"])
    resp_owner_dash = client.get("/dashboard")
    assert resp_owner_dash.status_code == 200
    owner_content = resp_owner_dash.get_data(as_text=True)
    assert "Shop Owner Operations Hub" in owner_content
    assert "Available Sellable Stock" in owner_content
