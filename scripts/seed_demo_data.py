"""Sanchay AI — PostgreSQL Demo Data Seed Script (scripts/seed_demo_data.py)
=============================================================================
Populates a realistic demo scenario:
- 1 Super Admin (`admin_rajesh`)
- 1 Organization (`Sanchay Retail Cooperative`)
- 2 Active Shops (`Sanchay Mart — Andheri West`, `Sanchay Mart — Bandra West`)
- 2 Shop Owners (`owner_andheri`, `owner_bandra`)
- 6 Catalog Products with ShopProduct pricing
- Active FEFO inventory batches with varying expiry horizons
- Realistic historical sales and stock movements
- Verified Emission Factors & Sample Sustainability Transfer Event
=============================================================================
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch, StockMovement
from app.models.organization import Organization, Shop, UserShopAssignment
from app.models.sales import Sale, SaleItem
from app.models.transfer import TransferRequest
from app.models.user import User

logger = logging.getLogger(__name__)

DEFAULT_PASSWORD = "ValidPassword123"


def seed_all() -> dict[str, int]:
    """Idempotently seed the demonstration dataset into PostgreSQL."""
    print("Beginning Sanchay AI demo dataset seed...")

    # 1. Organization Super Admin
    admin = User.query.filter_by(username="admin_rajesh").first()
    if not admin:
        admin = User(
            username="admin_rajesh",
            email="rajesh@sanchay.local",
            full_name="Rajesh Sharma (Super Admin)",
            role="SUPER_ADMIN",
            phone="9876543210",
            is_active=True,
        )
        admin.set_password(DEFAULT_PASSWORD)
        db.session.add(admin)
        db.session.flush()
        print(f"Created Super Admin: {admin.username}")
    else:
        admin.set_password(DEFAULT_PASSWORD)
        db.session.flush()
        print(f"Verified Super Admin: {admin.username}")

    # 2. Organization
    org = Organization.query.first()
    if not org:
        org = Organization(
            name="Sanchay Retail Cooperative",
            code="ORG-SANCHAY-01",
            contact_email="rajesh@sanchay.local",
            contact_phone="9876543210",
            address="Andheri West, Mumbai, Maharashtra",
            status="ACTIVE",
            low_stock_default_threshold=10,
            near_expiry_days_threshold=15,
        )
        db.session.add(org)
        db.session.flush()
        print(f"Created Organization: {org.name} ({org.code})")

    if admin.organization_id != org.id:
        admin.organization_id = org.id
        db.session.flush()

    # 3. Shop 1 — Andheri West
    shop_1 = Shop.query.filter_by(code="SHP-MUM-01").first()
    if not shop_1:
        shop_1 = Shop(
            organization_id=org.id,
            name="Sanchay Mart — Andheri West",
            code="SHP-MUM-01",
            address="Shop 4, Greenfield Heights, SV Road, Andheri West",
            city="Mumbai",
            pincode="400058",
            latitude=Decimal("19.1197"),
            longitude=Decimal("72.8464"),
            contact_phone="9811122233",
            contact_email="andheri@sanchay.local",
            status="ACTIVE",
        )
        db.session.add(shop_1)
        db.session.flush()
        print(f"Created Shop 1: {shop_1.name} ({shop_1.code})")

    # 4. Shop 2 — Bandra West
    shop_2 = Shop.query.filter_by(code="SHP-MUM-02").first()
    if not shop_2:
        shop_2 = Shop(
            organization_id=org.id,
            name="Sanchay Mart — Bandra West",
            code="SHP-MUM-02",
            address="Plot 12, Hill Road, Bandra West",
            city="Mumbai",
            pincode="400050",
            latitude=Decimal("19.0596"),
            longitude=Decimal("72.8295"),
            contact_phone="9822233344",
            contact_email="bandra@sanchay.local",
            status="ACTIVE",
        )
        db.session.add(shop_2)
        db.session.flush()
        print(f"Created Shop 2: {shop_2.name} ({shop_2.code})")

    # 5. Shop Users
    def ensure_user(username: str, email: str, full_name: str, role: str, shop: Shop, custom_password: str | None = None) -> User:
        user = User.query.filter_by(username=username).first()
        pwd = custom_password or DEFAULT_PASSWORD
        if not user:
            user = User(
                organization_id=org.id,
                username=username,
                email=email,
                full_name=full_name,
                role=role,
                phone="9800000001",
                is_active=True,
            )
            user.set_password(pwd)
            db.session.add(user)
            db.session.flush()
            print(f"Created User: {user.username} ({user.role})")
        else:
            user.set_password(pwd)
            db.session.flush()

        # Link assignment
        assignment = UserShopAssignment.query.filter_by(
            user_id=user.id, shop_id=shop.id
        ).first()
        if not assignment:
            assignment = UserShopAssignment(
                user_id=user.id,
                shop_id=shop.id,
                assignment_role=role,
                is_primary=True,
                is_active=True,
            )
            db.session.add(assignment)
            db.session.flush()

        return user

    admin_quick = User.query.filter_by(username="admin").first()
    if not admin_quick:
        admin_quick = User(
            username="admin",
            email="admin@sanchay.local",
            full_name="Organization Administrator",
            role="SUPER_ADMIN",
            phone="9811100000",
            is_active=True,
            organization_id=org.id,
        )
        db.session.add(admin_quick)
    admin_quick.set_password("orga")
    admin_quick.organization_id = org.id
    admin_quick.is_active = True
    db.session.flush()

    shop_quick = ensure_user("shop", "shop@sanchay.local", "Store Manager (Andheri West)", "SHOP_OWNER", shop_1, custom_password="12345")
    owner_1 = ensure_user("owner_andheri", "owner.andheri@sanchay.local", "Priya Nair (Store Owner)", "SHOP_OWNER", shop_1)
    owner_2 = ensure_user("owner_bandra", "owner.bandra@sanchay.local", "Rohan Deshmukh (Store Owner)", "SHOP_OWNER", shop_2)

    # 6. Catalog Products
    products_spec = [
        ("SKU-DAIRY-PANEER-200G", "Fresh Malai Paneer (200g)", "Dairy products", "pack", Decimal("72.00"), Decimal("95.00"), True),
        ("SKU-DAIRY-GREEK-YOGURT", "Probiotic Greek Yogurt (400g)", "Dairy products", "pcs", Decimal("65.00"), Decimal("90.00"), True),
        ("SKU-BAKERY-MULTIGRAIN", "Multigrain Sandwich Loaf (400g)", "Bakery products", "pack", Decimal("42.00"), Decimal("60.00"), True),
        ("SKU-BEV-COLD-COFFEE", "Arabica Cold Brew Bottle (250ml)", "Beverages", "pcs", Decimal("55.00"), Decimal("80.00"), True),
        ("SKU-BEV-ALMOND-MILK-1L", "Unsweetened Almond Milk (1L)", "Beverages", "litre", Decimal("190.00"), Decimal("250.00"), True),
        ("SKU-STAPLE-BASMATI-5KG", "Royal Aged Basmati Rice (5kg)", "Rice and wheat", "pack", Decimal("420.00"), Decimal("550.00"), False),
    ]

    today = date.today()
    created_products: list[Product] = []

    for sku, name, category, unit, cost, price, is_perishable in products_spec:
        prod = Product.query.filter_by(organization_id=org.id, sku=sku).first()
        if not prod:
            prod = Product(
                organization_id=org.id,
                sku=sku,
                name=name,
                category=category,
                unit=unit,
                default_purchase_price=cost,
                default_selling_price=price,
                is_perishable=is_perishable,
                created_by_user_id=admin.id,
                is_active=True,
            )
            db.session.add(prod)
            db.session.flush()
            print(f"Created Product: {prod.name} ({prod.sku})")
        created_products.append(prod)

        # Ensure ShopProduct mappings for both shops
        for s in (shop_1, shop_2):
            sp = ShopProduct.query.filter_by(shop_id=s.id, product_id=prod.id).first()
            if not sp:
                sp = ShopProduct(
                    shop_id=s.id,
                    product_id=prod.id,
                    purchase_price=cost,
                    selling_price=price,
                    low_stock_threshold=10,
                    near_expiry_days=15,
                    is_active=True,
                )
                db.session.add(sp)
                db.session.flush()

    # 7. Batches for Shop 1 (Andheri)
    def ensure_batch(shop: Shop, prod: Product, batch_code: str, avail_qty: int, days_to_expiry: int | None, cost: Decimal) -> InventoryBatch:
        batch = InventoryBatch.query.filter_by(shop_id=shop.id, batch_code=batch_code).first()
        if not batch:
            sp = ShopProduct.query.filter_by(shop_id=shop.id, product_id=prod.id).first()
            exp = (today + timedelta(days=days_to_expiry)) if days_to_expiry is not None else None
            batch = InventoryBatch(
                shop_id=shop.id,
                product_id=prod.id,
                shop_product_id=sp.id,
                batch_code=batch_code,
                initial_qty=avail_qty,
                available_qty=avail_qty,
                reserved_qty=0,
                damaged_qty=0,
                expired_qty=0,
                unit_cost=cost,
                expiry_date=exp,
                status="ACTIVE",
            )
            db.session.add(batch)
            db.session.flush()
            # Stock movement
            mv = StockMovement(
                organization_id=org.id,
                shop_id=shop.id,
                product_id=prod.id,
                batch_id=batch.id,
                movement_type="RECEIPT",
                bucket_affected="AVAILABLE",
                quantity_change=batch.initial_qty,
                balance_before=0,
                balance_after=batch.initial_qty,
                unit_cost_snapshot=cost,
                reference_type="INITIAL_RECEIPT",
                performed_by_user_id=admin.id,
                reason="Initial demo batch receipt",
            )
            db.session.add(mv)
            db.session.flush()
        return batch

    p_paneer = created_products[0]
    p_yogurt = created_products[1]
    p_bread = created_products[2]
    p_coffee = created_products[3]
    p_almond = created_products[4]
    p_rice = created_products[5]

    # Shop 1 Batches
    ensure_batch(shop_1, p_paneer, "B-PNR-101", avail_qty=45, days_to_expiry=6, cost=p_paneer.default_purchase_price)
    ensure_batch(shop_1, p_bread, "B-BRD-101", avail_qty=20, days_to_expiry=3, cost=p_bread.default_purchase_price)
    ensure_batch(shop_1, p_bread, "B-BRD-102", avail_qty=35, days_to_expiry=8, cost=p_bread.default_purchase_price)
    ensure_batch(shop_1, p_coffee, "B-COF-101", avail_qty=15, days_to_expiry=12, cost=p_coffee.default_purchase_price)
    ensure_batch(shop_1, p_rice, "B-RIC-101", avail_qty=50, days_to_expiry=None, cost=p_rice.default_purchase_price)

    # Shop 2 Batches
    ensure_batch(shop_2, p_paneer, "B-PNR-201", avail_qty=4, days_to_expiry=10, cost=p_paneer.default_purchase_price)
    ensure_batch(shop_2, p_yogurt, "B-YOG-201", avail_qty=28, days_to_expiry=5, cost=p_yogurt.default_purchase_price)
    ensure_batch(shop_2, p_almond, "B-ALM-201", avail_qty=40, days_to_expiry=9, cost=p_almond.default_purchase_price)
    ensure_batch(shop_2, p_bread, "B-BRD-201", avail_qty=16, days_to_expiry=4, cost=p_bread.default_purchase_price)

    # 8. Sample completed POS Sales for Shop 1 & Shop 2
    if Sale.query.count() == 0:
        sale_1 = Sale(
            organization_id=org.id,
            shop_id=shop_1.id,
            invoice_number="INV-SHP1-20261015-0001",
            status="COMPLETED",
            payment_mode="CASH",
            subtotal_amount=Decimal("475.00"),
            discount_amount=Decimal("0.00"),
            total_amount=Decimal("475.00"),
            total_cost_amount=Decimal("360.00"),
            sold_by_user_id=owner_1.id,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.session.add(sale_1)
        db.session.flush()

        sp_paneer = ShopProduct.query.filter_by(shop_id=shop_1.id, product_id=p_paneer.id).first()
        b_paneer = InventoryBatch.query.filter_by(shop_id=shop_1.id, batch_code="B-PNR-101").first()

        item_1 = SaleItem(
            sale_id=sale_1.id,
            shop_id=shop_1.id,
            product_id=p_paneer.id,
            shop_product_id=sp_paneer.id,
            batch_id=b_paneer.id,
            quantity=5,
            returned_quantity=0,
            unit_selling_price=p_paneer.default_selling_price,
            unit_cost_price=p_paneer.default_purchase_price,
            line_discount=Decimal("0.00"),
            line_total=Decimal("475.00"),
        )
        db.session.add(item_1)

    db.session.commit()

    # 9. Reference Emission Factors & Sample Sustainability Transfer Event
    from scripts.seed_emission_factors import seed_emission_factors
    from app.services.sustainability_service import SustainabilityService

    seed_emission_factors()

    if TransferRequest.query.count() == 0:
        b_paneer = InventoryBatch.query.filter_by(shop_id=shop_1.id, batch_code="B-PNR-101").first()
        trf = TransferRequest(
            transfer_code="TRF-20261015-0001",
            organization_id=org.id,
            source_shop_id=shop_1.id,
            recipient_shop_id=shop_2.id,
            product_id=p_paneer.id,
            source_batch_id=b_paneer.id,
            quantity=15,
            received_good_qty=15,
            transit_damaged_qty=0,
            unit_transfer_price=p_paneer.default_purchase_price,
            estimated_distance_km=Decimal("8.50"),
            batch_expiry_date_snapshot=date.today() + timedelta(days=6),
            status="COMPLETED",
            initiated_by_shop_id=shop_1.id,
            initiated_by_user_id=owner_1.id,
            source_approved_by_user_id=owner_1.id,
            recipient_approved_by_user_id=owner_2.id,
            dispatched_by_user_id=owner_1.id,
            received_by_user_id=owner_2.id,
            offer_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            received_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        db.session.add(trf)
        db.session.commit()

        try:
            SustainabilityService.process_transfer_sustainability_event(admin, trf.id)
            print("Seeded sample sustainability transfer event!")
        except Exception as err:
            print(f"Notice: sustainability demo event processing: {err}")

    print("Demo dataset seeded successfully!")

    counts = {
        "organizations": Organization.query.count(),
        "shops": Shop.query.count(),
        "users": User.query.count(),
        "products": Product.query.count(),
        "batches": InventoryBatch.query.count(),
    }
    return counts
