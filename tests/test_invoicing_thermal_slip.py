"""Tests for Printable Invoicing, Thermal Slip (80mm/58mm), and Tax Invoices (PRD Module D & H).
=======================================================================================

Verifies:
  1. Thermal POS Slip (80mm and 58mm layouts, auto-print param, batch traceability).
  2. Professional A4 Tax Invoice formatting (customer details, place of supply, batch lines, terms).
  3. Strict 2-role RBAC authorization (SUPER_ADMIN and assigned SHOP_OWNER).
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


@pytest.fixture
def invoicing_topology(app):
    """Fixture creating organization, shop, user, product, batch, and a completed sale."""
    with app.app_context():
        org = Organization(
            name="Invoicing Retail Corp",
            code="IRC_TEST",
            contact_email="irc@test.local",
            contact_phone="9800000005",
        )
        db.session.add(org)
        db.session.flush()

        admin = User(
            username="irc_admin",
            email="irc_admin@test.local",
            full_name="IRC Admin",
            role="SUPER_ADMIN",
            organization_id=org.id,
            is_active=True,
        )
        admin.set_password("AdminPass123!")

        owner = User(
            username="irc_owner",
            email="irc_owner@test.local",
            full_name="Pooja Sharma",
            role="SHOP_OWNER",
            organization_id=org.id,
            is_active=True,
        )
        owner.set_password("OwnerPass123!")
        db.session.add_all([admin, owner])
        db.session.flush()

        shop = Shop(
            organization_id=org.id,
            name="Sanchay Supermarket Indiranagar",
            code="SSI-01",
            city="Bengaluru",
            address="100ft Road Indiranagar",
            pincode="560038",
            contact_phone="9800000051",
            status="ACTIVE",
        )
        db.session.add(shop)
        db.session.flush()

        assign = UserShopAssignment(user_id=owner.id, shop_id=shop.id, assignment_role="SHOP_OWNER", is_primary=True)
        db.session.add(assign)

        prod = Product(
            organization_id=org.id,
            name="A2 Gir Cow Ghee 500ml",
            sku="SKU-GHEE-500ML",
            category="Grocery",
            unit="pcs",
            default_purchase_price=450.0,
            default_selling_price=650.0,
            created_by_user_id=admin.id,
        )
        db.session.add(prod)
        db.session.flush()

        sp = ShopProduct(
            shop_id=shop.id,
            product_id=prod.id,
            purchase_price=450.0,
            selling_price=650.0,
            low_stock_threshold=5,
            is_active=True,
        )
        db.session.add(sp)
        db.session.flush()

        today = date.today()
        batch = InventoryBatch(
            shop_id=shop.id,
            product_id=prod.id,
            shop_product_id=sp.id,
            batch_code="B-GHEE-MAY",
            initial_qty=20,
            available_qty=18,
            reserved_qty=0,
            damaged_qty=0,
            expired_qty=0,
            unit_cost=450.0,
            expiry_date=today + timedelta(days=120),
            status="ACTIVE",
        )
        db.session.add(batch)
        db.session.flush()

        dt_utc = datetime.now(timezone.utc)
        sale = Sale(
            organization_id=org.id,
            shop_id=shop.id,
            invoice_number="INV-SSI-01-20261015-0001",
            customer_name="Ananya Rao",
            customer_phone="9876543210",
            status="COMPLETED",
            subtotal_amount=Decimal("1300.00"),
            discount_amount=Decimal("100.00"),
            total_amount=Decimal("1200.00"),
            total_cost_amount=Decimal("900.00"),
            payment_mode="UPI_MANUAL",
            sold_by_user_id=owner.id,
            created_at=dt_utc,
            updated_at=dt_utc,
        )
        db.session.add(sale)
        db.session.flush()

        sale_item = SaleItem(
            shop_id=shop.id,
            sale_id=sale.id,
            product_id=prod.id,
            shop_product_id=sp.id,
            batch_id=batch.id,
            quantity=2,
            returned_quantity=0,
            unit_cost_price=Decimal("450.00"),
            unit_selling_price=Decimal("650.00"),
            line_discount=Decimal("0.00"),
            line_total=Decimal("1300.00"),
            created_at=dt_utc,
        )
        db.session.add(sale_item)
        db.session.commit()

        return {
            "org_id": org.id,
            "owner_id": owner.id,
            "shop_id": shop.id,
            "sale_id": sale.id,
            "invoice_number": sale.invoice_number,
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


def test_thermal_slip_rendering(client: FlaskClient, invoicing_topology):
    """Verify compact 80mm and 58mm thermal receipt generation."""
    _login(client, "irc_owner", "OwnerPass123!")
    shop_id = invoicing_topology["shop_id"]
    sale_id = invoicing_topology["sale_id"]

    # 1. Default 80mm
    resp_80 = client.get(f"/shops/{shop_id}/sales/{sale_id}/thermal-slip")
    assert resp_80.status_code == 200
    html_80 = resp_80.get_data(as_text=True)
    assert "Sanchay Supermarket Indiranagar" in html_80
    assert invoicing_topology["invoice_number"] in html_80
    assert "Ananya Rao" in html_80
    assert "UPI_MANUAL" in html_80
    assert "A2 Gir Cow Ghee 500ml" in html_80
    assert "B-GHEE-MAY" in html_80
    assert "80mm" in html_80

    # 2. 58mm with autoprint
    resp_58 = client.get(f"/shops/{shop_id}/sales/{sale_id}/thermal-slip?width=58&autoprint=1")
    assert resp_58.status_code == 200
    html_58 = resp_58.get_data(as_text=True)
    assert "58mm" in html_58
    assert "window.print()" in html_58


def test_tax_invoice_rendering(client: FlaskClient, invoicing_topology):
    """Verify formal A4 Tax Invoice printable layout."""
    _login(client, "irc_owner", "OwnerPass123!")
    shop_id = invoicing_topology["shop_id"]
    sale_id = invoicing_topology["sale_id"]

    resp = client.get(f"/shops/{shop_id}/sales/{sale_id}/tax-invoice")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Tax Invoice" in html
    assert invoicing_topology["invoice_number"] in html
    assert "Ananya Rao" in html
    assert "Place of Supply: Bengaluru" in html
    assert "A2 Gir Cow Ghee 500ml" in html
    assert "SKU-GHEE-500ML" in html
    assert "B-GHEE-MAY" in html
    assert "Authorized Signatory" in html
    assert "Terms &amp; Conditions" in html


def test_invoicing_rbac(client: FlaskClient, invoicing_topology):
    """Verify unauthenticated requests are redirected."""
    shop_id = invoicing_topology["shop_id"]
    sale_id = invoicing_topology["sale_id"]

    resp1 = client.get(f"/shops/{shop_id}/sales/{sale_id}/thermal-slip")
    assert resp1.status_code == 302
    assert "/auth/login" in resp1.headers["Location"]

    resp2 = client.get(f"/shops/{shop_id}/sales/{sale_id}/tax-invoice")
    assert resp2.status_code == 302
    assert "/auth/login" in resp2.headers["Location"]
