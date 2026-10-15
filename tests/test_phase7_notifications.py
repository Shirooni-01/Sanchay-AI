"""Phase 7 Test Suite: In-App Notifications & Event Alerts.

Verifies:
1. Notification creation for supported events (low stock, near expiry, transfers).
2. Correct recipients for each shop (owner/staff isolation).
3. Unauthorized notification access prevention (cross-user & cross-org isolation).
4. Unread count accuracy.
5. Mark-one-as-read behavior and timestamp tracking.
6. Mark-all-as-read behavior.
7. Pagination and status/type filtering.
8. Event deduplication handling (dedup_key).
9. Failed transactions not producing success notifications (atomic rollback).
10. Expiry and low-stock alert deduplication.
11. Inter-store transfer lifecycle notification integration.
12. HTML views, navbar bell badge, and on-demand alert evaluation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch
from app.models.notification import Notification
from app.models.organization import Organization, Shop, UserShopAssignment
from app.models.transfer import TransferRequest
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.catalog_service import CatalogService
from app.services.inventory_service import InventoryService
from app.services.notification_service import NotificationService
from app.services.org_service import MembershipService, OrganizationService, ShopService
from app.services.transfer_service import TransferService
from app.utils.errors import AuthorizationError, InsufficientStockError, ValidationError
from tests.test_phase1_auth import extract_csrf_token


def _login(client: FlaskClient, identity: str, password: str = "ValidPassword123") -> str:
    """Authenticate a test client via JSON API, rotate CSRF, and set default header."""
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


def _setup_phase7_topology(app: Flask) -> dict[str, Any]:
    """Create topology with Org 1 (Shop A, Shop B) and Org 2 (Shop X) with products and batches."""
    with app.app_context():
        # Super admin
        admin = AuthService.create_user(
            username="p7_superadmin",
            email="p7_admin@sanchay.local",
            password="ValidPassword123",
            full_name="Phase7 Super Admin",
            role="SUPER_ADMIN",
        )

        # Organization 1
        org1 = OrganizationService.create_organization(
            actor=admin,
            payload={
                "name": "Sanchay Retail Org P7",
                "code": "SRO-P7",
                "contact_email": "ops@srop7.local",
                "city": "Pune",
                "state": "Maharashtra",
                "low_stock_default_threshold": 10,
                "near_expiry_days_threshold": 7,
            },
        )

        # Shop A
        shop_a = ShopService.create_shop(
            actor=admin,
            payload={
                "organization_id": org1.id,
                "name": "Shop A - Baner",
                "code": "BNR-P7",
                "address": "Baner High Street",
                "city": "Pune",
                "pincode": "411045",
                "contact_phone": "9811111111",
            },
        )

        # Shop B
        shop_b = ShopService.create_shop(
            actor=admin,
            payload={
                "organization_id": org1.id,
                "name": "Shop B - Kothrud",
                "code": "KTH-P7",
                "address": "Paud Road, Kothrud",
                "city": "Pune",
                "pincode": "411038",
                "contact_phone": "9822222222",
            },
        )

        # Shop A Owner & Staff
        owner_a = AuthService.create_user(
            username="p7_owner_a",
            email="owner_a@srop7.local",
            password="ValidPassword123",
            full_name="Owner Shop A",
            role="SHOP_OWNER",
            organization_id=org1.id,
        )
        MembershipService.assign_user_to_shop(
            actor=admin,
            shop_id=shop_a.id,
            payload={
                "user_id": owner_a.id,
                "assignment_role": "SHOP_OWNER",
                "is_primary": True,
            },
        )
        shop_a.owner_user_id = owner_a.id

        staff_a = AuthService.create_user(
            username="p7_staff_a",
            email="staff_a@srop7.local",
            password="ValidPassword123",
            full_name="Staff Shop A",
            role="SHOP_OWNER",
            organization_id=org1.id,
        )
        MembershipService.assign_user_to_shop(
            actor=admin,
            shop_id=shop_a.id,
            payload={
                "user_id": staff_a.id,
                "assignment_role": "SHOP_OWNER",
                "is_primary": False,
            },
        )

        # Shop B Owner
        owner_b = AuthService.create_user(
            username="p7_owner_b",
            email="owner_b@srop7.local",
            password="ValidPassword123",
            full_name="Owner Shop B",
            role="SHOP_OWNER",
            organization_id=org1.id,
        )
        MembershipService.assign_user_to_shop(
            actor=admin,
            shop_id=shop_b.id,
            payload={
                "user_id": owner_b.id,
                "assignment_role": "SHOP_OWNER",
                "is_primary": True,
            },
        )
        shop_b.owner_user_id = owner_b.id

        # Organization 2 (Tenant isolation)
        admin2 = AuthService.create_user(
            username="p7_other_admin",
            email="p7_other@otherp7.local",
            password="ValidPassword123",
            full_name="Other Org Admin",
            role="SUPER_ADMIN",
        )
        org2 = OrganizationService.create_organization(
            actor=admin2,
            payload={
                "name": "Other Retail Org P7",
                "code": "OTHER-P7",
                "contact_email": "ops@otherp7.local",
                "city": "Mumbai",
            },
        )
        shop_x = ShopService.create_shop(
            actor=admin2,
            payload={
                "organization_id": org2.id,
                "name": "Shop X - Bandra",
                "code": "BND01",
                "address": "Linking Road, Bandra",
                "city": "Mumbai",
                "pincode": "400050",
                "contact_phone": "9899999999",
            },
        )
        owner_x = AuthService.create_user(
            username="p7_owner_x",
            email="owner_x@otherp7.local",
            password="ValidPassword123",
            full_name="Owner Shop X",
            role="SHOP_OWNER",
            organization_id=org2.id,
        )
        MembershipService.assign_user_to_shop(
            actor=admin2,
            shop_id=shop_x.id,
            payload={
                "user_id": owner_x.id,
                "assignment_role": "SHOP_OWNER",
                "is_primary": True,
            },
        )
        shop_x.owner_user_id = owner_x.id

        # Product & Batch in Shop A
        prod = CatalogService.create_organization_product(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "sku": "P7-DAIRY-PANEER-200G",
                "name": "Fresh Paneer 200g",
                "category": "Dairy",
                "unit": "pack",
                "default_purchase_price": "70.00",
                "default_selling_price": "90.00",
                "reorder_threshold": 15,
                "is_perishable": True,
            },
        )
        CatalogService.create_or_enroll_shop_product(
            actor=owner_b,
            shop_id=shop_b.id,
            payload={
                "product_id": prod.id,
                "purchase_price": "70.00",
                "selling_price": "95.00",
                "low_stock_threshold": 10,
            },
        )

        today = date.today()
        batch_a, _ = InventoryService.receive_batch(
            actor=owner_a,
            shop_id=shop_a.id,
            payload={
                "product_id": prod.id,
                "batch_code": "BATCH-P7-PNR-001",
                "unit_cost": "70.00",
                "initial_qty": 30,
                "expiry_date": (today + timedelta(days=4)).isoformat(),  # near expiry (4 <= 7)
            },
        )

        db.session.commit()

        return {
            "org1_id": org1.id,
            "org2_id": org2.id,
            "shop_a_id": shop_a.id,
            "shop_b_id": shop_b.id,
            "shop_x_id": shop_x.id,
            "admin_id": admin.id,
            "admin_username": admin.username,
            "owner_a_id": owner_a.id,
            "owner_a_username": owner_a.username,
            "staff_a_id": staff_a.id,
            "staff_a_username": staff_a.username,
            "owner_b_id": owner_b.id,
            "owner_b_username": owner_b.username,
            "owner_x_id": owner_x.id,
            "owner_x_username": owner_x.username,
            "product_id": prod.id,
            "batch_a_id": batch_a.id,
        }


# ============================================================================
# Phase 7 Notification Tests
# ============================================================================


def test_notification_creation_for_supported_events(app: Flask):
    """Test 1: Verify notification creation across supported domain event types."""
    ctx = _setup_phase7_topology(app)
    with app.app_context():
        # Inventory low stock
        notif_low = NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            shop_id=ctx["shop_a_id"],
            notification_type="LOW_STOCK",
            severity="WARNING",
            title="Low Stock: Fresh Paneer 200g",
            message="Product has 8 units remaining, below threshold of 15.",
            related_entity_type="product",
            related_entity_id=ctx["product_id"],
        )
        assert notif_low.id is not None
        assert notif_low.notification_type == "LOW_STOCK"
        assert notif_low.severity == "WARNING"
        assert notif_low.is_read is False
        assert notif_low.read_at is None
        assert notif_low.related_entity_type == "product"
        assert notif_low.related_entity_id == ctx["product_id"]

        # Transfer offer received
        notif_trf = NotificationService.create_notification(
            recipient_user_id=ctx["owner_b_id"],
            organization_id=ctx["org1_id"],
            shop_id=ctx["shop_b_id"],
            notification_type="TRANSFER_OFFER_RECEIVED",
            severity="INFO",
            title="New Transfer Offer: TRF-20261013-0001",
            message="Shop A offered 10 units of Fresh Paneer 200g.",
            related_entity_type="transfer",
            related_entity_id=999,
        )
        assert notif_trf.id is not None
        assert notif_trf.notification_type == "TRANSFER_OFFER_RECEIVED"
        assert notif_trf.severity == "INFO"

        # Serialization to dict
        d = notif_low.to_dict()
        assert d["title"] == "Low Stock: Fresh Paneer 200g"
        assert d["is_read"] is False
        assert d["created_at"] is not None


def test_correct_recipients_for_each_shop(app: Flask):
    """Test 2: Broadcast to Shop A reaches only Shop A users, not Shop B or Org 2."""
    ctx = _setup_phase7_topology(app)
    with app.app_context():
        created = NotificationService.notify_shop_users(
            shop_id=ctx["shop_a_id"],
            notification_type="NEAR_EXPIRY",
            title="Near Expiry: Batch 001",
            message="Batch expiring soon.",
            severity="WARNING",
            dedup_key_prefix="test_recipients_near_exp",
        )
        recipient_ids = {n.recipient_user_id for n in created}
        # Both Owner A and Staff A must be notified
        assert ctx["owner_a_id"] in recipient_ids
        assert ctx["staff_a_id"] in recipient_ids
        # Owner B in Shop B and Owner X in Org 2 must NOT receive it
        assert ctx["owner_b_id"] not in recipient_ids
        assert ctx["owner_x_id"] not in recipient_ids

        # Verify DB records
        b_notifs = db.session.query(Notification).filter_by(recipient_user_id=ctx["owner_b_id"]).all()
        assert len(b_notifs) == 0


def test_unauthorized_notification_access_prevention(app: Flask, client: FlaskClient):
    """Test 3: User A cannot read or mark User B's notifications."""
    ctx = _setup_phase7_topology(app)
    with app.app_context():
        notif_b = NotificationService.create_notification(
            recipient_user_id=ctx["owner_b_id"],
            organization_id=ctx["org1_id"],
            shop_id=ctx["shop_b_id"],
            notification_type="TRANSFER_OFFER_RECEIVED",
            title="Private Offer for Owner B",
            message="Details for Owner B only.",
        )
        notif_b_id = notif_b.id

    # Owner A logs in
    _login(client, ctx["owner_a_username"])

    # 1. Listing notifications returns 0 for Owner A
    resp = client.get("/api/v1/notifications")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"]["unread_count"] == 0
    assert len(body["data"]["notifications"]) == 0

    # 2. Owner A attempts to mark Owner B's notification as read -> 403 Forbidden
    mark_resp = client.post(f"/api/v1/notifications/{notif_b_id}/read")
    assert mark_resp.status_code in (403, 404)
    assert mark_resp.get_json()["success"] is False

    # Verify notif_b remains unread in database
    with app.app_context():
        reloaded = db.session.get(Notification, notif_b_id)
        assert reloaded.is_read is False


def test_unread_count_accuracy(app: Flask, client: FlaskClient):
    """Test 4: Verify unread count calculation and API endpoint accuracy."""
    ctx = _setup_phase7_topology(app)
    with app.app_context():
        # Create 3 notifications for Owner A (2 unread, 1 read)
        NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            notification_type="LOW_STOCK",
            title="Alert 1",
            message="Msg 1",
        )
        NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            notification_type="LOW_STOCK",
            title="Alert 2",
            message="Msg 2",
        )
        n3 = NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            notification_type="LOW_STOCK",
            title="Alert 3",
            message="Msg 3",
        )
        n3.is_read = True
        db.session.commit()

        owner_a = db.session.get(User, ctx["owner_a_id"])
        count = NotificationService.get_unread_count(owner_a)
        assert count == 2

    _login(client, ctx["owner_a_username"])
    resp = client.get("/api/v1/notifications/unread-count")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["unread_count"] == 2


def test_mark_one_as_read_behavior(app: Flask, client: FlaskClient):
    """Test 5: Mark single notification as read updates read_at and unread count."""
    ctx = _setup_phase7_topology(app)
    with app.app_context():
        notif = NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            notification_type="LOW_STOCK",
            title="Mark Read Test",
            message="Test Msg",
        )
        notif_id = notif.id

    _login(client, ctx["owner_a_username"])

    # Before mark
    assert client.get("/api/v1/notifications/unread-count").get_json()["data"]["unread_count"] == 1

    # Mark as read via POST
    resp = client.post(f"/api/v1/notifications/{notif_id}/read")
    assert resp.status_code == 200
    data = resp.get_json()["data"]["notification"]
    assert data["is_read"] is True
    assert data["read_at"] is not None

    # After mark
    assert client.get("/api/v1/notifications/unread-count").get_json()["data"]["unread_count"] == 0

    # Repeat call is idempotent
    resp_repeat = client.post(f"/api/v1/notifications/{notif_id}/read")
    assert resp_repeat.status_code == 200
    assert resp_repeat.get_json()["data"]["notification"]["is_read"] is True


def test_mark_all_as_read_behavior(app: Flask, client: FlaskClient):
    """Test 6: Mark all notifications as read updates all user records atomically."""
    ctx = _setup_phase7_topology(app)
    with app.app_context():
        for i in range(4):
            NotificationService.create_notification(
                recipient_user_id=ctx["owner_a_id"],
                organization_id=ctx["org1_id"],
                notification_type="LOW_STOCK",
                title=f"Alert {i}",
                message=f"Msg {i}",
            )
        # Create 1 for Owner B
        NotificationService.create_notification(
            recipient_user_id=ctx["owner_b_id"],
            organization_id=ctx["org1_id"],
            notification_type="LOW_STOCK",
            title="Owner B Alert",
            message="Should stay unread",
        )

    _login(client, ctx["owner_a_username"])
    assert client.get("/api/v1/notifications/unread-count").get_json()["data"]["unread_count"] == 4

    resp = client.post("/api/v1/notifications/read-all")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["marked_count"] == 4

    assert client.get("/api/v1/notifications/unread-count").get_json()["data"]["unread_count"] == 0

    # Verify Owner B's notification is unaffected
    with app.app_context():
        owner_b = db.session.get(User, ctx["owner_b_id"])
        assert NotificationService.get_unread_count(owner_b) == 1


def test_pagination_and_filtering(app: Flask, client: FlaskClient):
    """Test 7: Pagination and filtering by is_read and notification_type."""
    ctx = _setup_phase7_topology(app)
    with app.app_context():
        # Create 3 LOW_STOCK (2 unread, 1 read)
        NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            notification_type="LOW_STOCK",
            title="Low Stock 1",
            message="Msg",
        )
        NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            notification_type="LOW_STOCK",
            title="Low Stock 2",
            message="Msg",
        )
        n_read = NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            notification_type="LOW_STOCK",
            title="Low Stock 3",
            message="Msg",
        )
        n_read.is_read = True

        # Create 2 TRANSFER_APPROVED (unread)
        NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            notification_type="TRANSFER_APPROVED",
            title="Transfer 1",
            message="Msg",
        )
        NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            notification_type="TRANSFER_APPROVED",
            title="Transfer 2",
            message="Msg",
        )
        db.session.commit()

    _login(client, ctx["owner_a_username"])

    # 1. Total count
    resp_all = client.get("/api/v1/notifications")
    assert resp_all.status_code == 200
    assert resp_all.get_json()["meta"]["total_items"] == 5

    # 2. Filter is_read=false
    resp_unread = client.get("/api/v1/notifications?is_read=false")
    assert resp_unread.status_code == 200
    assert resp_unread.get_json()["meta"]["total_items"] == 4

    # 3. Filter is_read=true
    resp_read = client.get("/api/v1/notifications?is_read=true")
    assert resp_read.status_code == 200
    assert resp_read.get_json()["meta"]["total_items"] == 1

    # 4. Filter notification_type=TRANSFER_APPROVED
    resp_type = client.get("/api/v1/notifications?notification_type=TRANSFER_APPROVED")
    assert resp_type.status_code == 200
    assert resp_type.get_json()["meta"]["total_items"] == 2

    # 5. Pagination per_page=2
    resp_p1 = client.get("/api/v1/notifications?page=1&per_page=2")
    assert resp_p1.status_code == 200
    assert len(resp_p1.get_json()["data"]["notifications"]) == 2
    assert resp_p1.get_json()["meta"]["total_pages"] == 3


def test_duplicate_event_handling(app: Flask):
    """Test 8: Event deduplication prevents duplicate notifications for the same dedup_key."""
    ctx = _setup_phase7_topology(app)
    with app.app_context():
        key = f"TEST_DEDUP:{ctx['shop_a_id']}:prod1:2026-10-13"

        n1 = NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            notification_type="LOW_STOCK",
            title="Dedup Test",
            message="First creation",
            dedup_key=key,
        )

        n2 = NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            notification_type="LOW_STOCK",
            title="Dedup Test",
            message="Second creation attempt",
            dedup_key=key,
        )

        assert n1.id == n2.id
        total = db.session.query(Notification).filter_by(recipient_user_id=ctx["owner_a_id"]).count()
        assert total == 1


def test_failed_transactions_do_not_produce_success_notifications(app: Flask):
    """Test 9: When a transfer proposal fails validation or stock checks, 0 notifications persist."""
    ctx = _setup_phase7_topology(app)
    with app.app_context():
        owner_a = db.session.get(User, ctx["owner_a_id"])

        # Attempt proposal with quantity exceeding batch available (30 available, propose 100)
        with pytest.raises((ValidationError, InsufficientStockError)):
            TransferService.create_transfer_proposal(
                actor=owner_a,
                payload={
                    "source_shop_id": ctx["shop_a_id"],
                    "recipient_shop_id": ctx["shop_b_id"],
                    "product_id": ctx["product_id"],
                    "source_batch_id": ctx["batch_a_id"],
                    "quantity": 100,  # exceeds available 30
                    "unit_transfer_price": "70.00",
                },
            )

        # Verify no notification was persisted in database
        count = db.session.query(Notification).count()
        assert count == 0


def test_expiry_and_low_stock_alert_deduplication(app: Flask):
    """Test 10: Periodic/on-demand alert check deduplicates on the same day."""
    ctx = _setup_phase7_topology(app)
    with app.app_context():
        # Batch in Shop A is already near expiry (4 days left <= 5 near_expiry_days)
        # Sellable stock is 30, reorder threshold is 15 -> not low stock, but near expiry!
        today = date.today()

        # First evaluation
        first_run = NotificationService.check_and_notify_inventory_alerts(
            shop_id=ctx["shop_a_id"],
            ref_date=today,
        )
        assert len(first_run) > 0
        first_count = len(first_run)

        # Second evaluation on same day must create 0 new notifications
        second_run = NotificationService.check_and_notify_inventory_alerts(
            shop_id=ctx["shop_a_id"],
            ref_date=today,
        )
        assert len(second_run) == 0

        # Total notifications in DB matches first_count exactly
        total_in_db = db.session.query(Notification).filter_by(shop_id=ctx["shop_a_id"]).count()
        assert total_in_db == first_count


def test_transfer_lifecycle_notification_integration(app: Flask):
    """Test 11: End-to-end transfer lifecycle triggers notifications at each stage."""
    ctx = _setup_phase7_topology(app)
    with app.app_context():
        owner_a = db.session.get(User, ctx["owner_a_id"])
        owner_b = db.session.get(User, ctx["owner_b_id"])

        # 1. Propose transfer from Shop A to Shop B (10 units)
        transfer = TransferService.create_transfer_proposal(
            actor=owner_a,
            payload={
                "source_shop_id": ctx["shop_a_id"],
                "recipient_shop_id": ctx["shop_b_id"],
                "product_id": ctx["product_id"],
                "source_batch_id": ctx["batch_a_id"],
                "quantity": 10,
                "unit_transfer_price": "72.00",
                "notes": "Surplus paneer transfer",
            },
        )
        # Recipient Owner B should receive TRANSFER_OFFER_RECEIVED
        notifs_b1 = db.session.query(Notification).filter_by(
            recipient_user_id=ctx["owner_b_id"],
            notification_type="TRANSFER_OFFER_RECEIVED",
        ).all()
        assert len(notifs_b1) == 1
        assert transfer.transfer_code in notifs_b1[0].title

        # 2. Recipient Owner B approves -> APPROVED_RESERVED
        TransferService.approve_transfer(actor=owner_b, transfer_id=transfer.id)

        # Both owners receive TRANSFER_APPROVED
        notifs_a_app = db.session.query(Notification).filter_by(
            recipient_user_id=ctx["owner_a_id"],
            notification_type="TRANSFER_APPROVED",
        ).all()
        notifs_b_app = db.session.query(Notification).filter_by(
            recipient_user_id=ctx["owner_b_id"],
            notification_type="TRANSFER_APPROVED",
        ).all()
        assert len(notifs_a_app) == 1
        assert len(notifs_b_app) == 1

        # 3. Source Owner A dispatches -> DISPATCHED_IN_TRANSIT
        TransferService.dispatch_transfer(
            actor=owner_a,
            transfer_id=transfer.id,
            payload={"dispatch_notes": "Driver Ramesh van #1234"},
        )
        # Recipient Owner B receives TRANSFER_DISPATCHED
        notifs_b_disp = db.session.query(Notification).filter_by(
            recipient_user_id=ctx["owner_b_id"],
            notification_type="TRANSFER_DISPATCHED",
        ).all()
        assert len(notifs_b_disp) == 1
        assert "dispatched" in notifs_b_disp[0].title.lower()

        # 4. Recipient Owner B confirms receipt -> COMPLETED
        TransferService.confirm_receipt(
            actor=owner_b,
            transfer_id=transfer.id,
            payload={"received_good_qty": 10, "transit_damaged_qty": 0},
        )
        # Source Owner A receives TRANSFER_RECEIVED
        notifs_a_rec = db.session.query(Notification).filter_by(
            recipient_user_id=ctx["owner_a_id"],
            notification_type="TRANSFER_RECEIVED",
        ).all()
        assert len(notifs_a_rec) == 1
        assert "completed" in notifs_a_rec[0].title.lower()


def test_html_views_and_bell_badge(app: Flask, client: FlaskClient):
    """Test 12: Notification Center HTML page, navbar bell, and on-demand check API."""
    ctx = _setup_phase7_topology(app)
    with app.app_context():
        NotificationService.create_notification(
            recipient_user_id=ctx["owner_a_id"],
            organization_id=ctx["org1_id"],
            shop_id=ctx["shop_a_id"],
            notification_type="LOW_STOCK",
            title="HTML View Test Alert",
            message="Checking UI presentation.",
            related_entity_type="product",
            related_entity_id=ctx["product_id"],
        )

    _login(client, ctx["owner_a_username"])

    # 1. Notification Center HTML page
    resp = client.get("/notifications")
    assert resp.status_code == 200, f"GET /notifications failed with {resp.status_code}: {resp.get_data(as_text=True)}"
    html = resp.get_data(as_text=True)
    assert "Notifications &amp; Alerts" in html or "Notifications" in html
    assert "HTML View Test Alert" in html
    assert "Checking UI presentation." in html
    assert "View Product" in html
    assert "Mark as Read" in html

    # 2. On-demand alert evaluation API
    eval_resp = client.post(f"/api/v1/shops/{ctx['shop_a_id']}/inventory/check-alerts")
    assert eval_resp.status_code == 200
    assert eval_resp.get_json()["success"] is True

    # 3. Shop notifications redirect
    redir_resp = client.get(f"/shops/{ctx['shop_a_id']}/notifications")
    assert redir_resp.status_code == 302
    assert "/notifications" in redir_resp.headers["Location"]
