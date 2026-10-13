"""Phase 2 Test Suite: Organizations, Shops, Roles, Membership & Tenant Isolation.

Verifies all 12 required Phase 2 areas against the isolated sanchay_ai_test PostgreSQL database:
1. Organization creation and editing
2. Shop creation within an organization
3. Duplicate shop code/identifier rejection within an organization (and allowed across distinct organizations)
4. Shop editing and status changes
5. Deactivated shop & deactivated organization operational blocking
6. Role assignment (SUPER_ADMIN, SHOP_OWNER, STAFF) & primary shop uniqueness
7. Super Admin access across authorized organization shops
8. Shop Owner access limited to assigned shops
9. Staff access limited to assigned shops and permitted actions
10. Unauthorized access rejection & security audit logging
11. Cross-shop access prevention (intra-org IDOR)
12. Cross-organization access prevention (inter-org IDOR)
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db
from app.models.audit import AuditLog
from app.services.auth_service import AuthService
from app.services.org_service import (
    MembershipService,
    OrganizationService,
    ShopService,
)
from app.utils.geo import haversine_distance_km
from tests.conftest import extract_csrf_token


def api_login_with_csrf(
    client: FlaskClient,
    identifier: str,
    password: str = "ValidPassword123",
) -> str:
    """Authenticate a test client via JSON API and return the active CSRF token."""
    login_page = client.get("/auth/login")
    if login_page.status_code == 302:
        landing = client.get("/")
        active_csrf = extract_csrf_token(landing.get_data(as_text=True))
        client.post("/api/v1/auth/logout", headers={"X-CSRFToken": active_csrf})
        login_page = client.get("/auth/login")

    csrf_token = extract_csrf_token(login_page.get_data(as_text=True))

    resp = client.post(
        "/api/v1/auth/login",
        json={"identifier": identifier, "password": password},
        headers={"X-CSRFToken": csrf_token},
    )
    assert resp.status_code == 200, f"Login failed for {identifier}: {resp.get_json()}"

    # Fetch fresh CSRF token after session rotation
    me_page = client.get("/")
    return extract_csrf_token(me_page.get_data(as_text=True))


@pytest.fixture()
def multi_tenant_topology(app: Flask) -> dict:
    """Seed two isolated organizations (Org A and Org B), shops, owners, and staff."""
    with app.app_context():
        # --- Organization A (Sanchay Retail Co-op) ---
        admin_a = AuthService.create_user(
            username="admin_org_a",
            email="admin_a@sanchay.local",
            password="ValidPassword123",
            full_name="Rajesh Admin A",
            role="SUPER_ADMIN",
            phone="9800000001",
        )
        org_a = OrganizationService.create_organization(
            actor=admin_a,
            payload={
                "name": "Sanchay Retail Co-op",
                "code": "SANCHAY_A",
                "contact_email": "hq_a@sanchay.local",
                "contact_phone": "080-11112222",
                "address": "MG Road, Bengaluru",
                "low_stock_default_threshold": 12,
                "near_expiry_days_threshold": 20,
            },
            attach_actor=True,
        )

        shop_a1 = ShopService.create_shop(
            actor=admin_a,
            payload={
                "name": "Indiranagar Branch",
                "code": "SHP-BLR-01",
                "address": "100 Feet Road, Indiranagar",
                "city": "Bengaluru",
                "pincode": "560038",
                "latitude": "12.971599",
                "longitude": "77.641151",
                "contact_phone": "9800000101",
                "contact_email": "indiranagar@sanchay.local",
            },
        )
        shop_a2 = ShopService.create_shop(
            actor=admin_a,
            payload={
                "name": "Koramangala Branch",
                "code": "SHP-BLR-02",
                "address": "80 Feet Road, Koramangala",
                "city": "Bengaluru",
                "pincode": "560034",
                "latitude": "12.935192",
                "longitude": "77.624481",
                "contact_phone": "9800000102",
                "contact_email": "koramangala@sanchay.local",
            },
        )

        owner_a1 = MembershipService.create_member_user(
            actor=admin_a,
            payload={
                "username": "owner_a1",
                "email": "owner_a1@sanchay.local",
                "password": "ValidPassword123",
                "full_name": "Priya Owner A1",
                "role": "SHOP_OWNER",
                "shop_id": shop_a1.id,
            },
        )
        owner_a2 = MembershipService.create_member_user(
            actor=admin_a,
            payload={
                "username": "owner_a2",
                "email": "owner_a2@sanchay.local",
                "password": "ValidPassword123",
                "full_name": "Vikram Owner A2",
                "role": "SHOP_OWNER",
                "shop_id": shop_a2.id,
            },
        )
        staff_a1 = MembershipService.create_member_user(
            actor=owner_a1,
            payload={
                "username": "staff_a1",
                "email": "staff_a1@sanchay.local",
                "password": "ValidPassword123",
                "full_name": "Anil Staff A1",
                "role": "STAFF",
                "shop_id": shop_a1.id,
            },
        )

        # --- Organization B (Other Tenant) ---
        admin_b = AuthService.create_user(
            username="admin_org_b",
            email="admin_b@other.local",
            password="ValidPassword123",
            full_name="Meera Admin B",
            role="SUPER_ADMIN",
            phone="9800000002",
        )
        org_b = OrganizationService.create_organization(
            actor=admin_b,
            payload={
                "name": "Metro Grocers Network",
                "code": "METRO_B",
                "contact_email": "hq_b@other.local",
                "contact_phone": "022-33334444",
                "address": "Andheri West, Mumbai",
            },
            attach_actor=True,
        )
        shop_b1 = ShopService.create_shop(
            actor=admin_b,
            payload={
                "name": "Andheri Store",
                "code": "SHP-MUM-01",
                "address": "Link Road, Andheri",
                "city": "Mumbai",
                "pincode": "400053",
                "contact_phone": "9800000201",
            },
        )

        return {
            "org_a_id": org_a.id,
            "org_b_id": org_b.id,
            "shop_a1_id": shop_a1.id,
            "shop_a2_id": shop_a2.id,
            "shop_b1_id": shop_b1.id,
            "admin_a_id": admin_a.id,
            "owner_a1_id": owner_a1.id,
            "owner_a2_id": owner_a2.id,
            "staff_a1_id": staff_a1.id,
            "admin_b_id": admin_b.id,
        }


# ============================================================================
# 1. Organization Creation, Editing & Status Management
# ============================================================================


class TestOrganizationManagement:
    """Verify organization creation, editing, unique codes, and role enforcement."""

    def test_super_admin_creates_and_edits_organization(
        self, app: Flask, client: FlaskClient
    ):
        with app.app_context():
            AuthService.create_user(
                username="founder_admin",
                email="founder@sanchay.local",
                password="ValidPassword123",
                full_name="Founder Admin",
                role="SUPER_ADMIN",
            )

        csrf_token = api_login_with_csrf(client, "founder_admin")

        # Create organization
        create_resp = client.post(
            "/api/v1/organizations",
            json={
                "name": "GreenHarvest Stores",
                "code": "green_01",
                "contact_email": "info@greenharvest.local",
                "contact_phone": "9123456780",
                "address": "Pune, Maharashtra",
                "low_stock_default_threshold": 15,
                "near_expiry_days_threshold": 18,
            },
            headers={"X-CSRFToken": csrf_token},
        )
        assert create_resp.status_code == 201
        org_data = create_resp.get_json()["data"]["organization"]
        assert org_data["name"] == "GreenHarvest Stores"
        assert org_data["code"] == "GREEN_01"
        assert org_data["status"] == "ACTIVE"
        assert org_data["low_stock_default_threshold"] == 15
        assert org_data["near_expiry_days_threshold"] == 18

        # Creating a second organization with the same Super Admin is rejected
        second_resp = client.post(
            "/api/v1/organizations",
            json={"name": "Second Org", "code": "SECOND_02"},
            headers={"X-CSRFToken": csrf_token},
        )
        assert second_resp.status_code == 409

        # Edit organization details
        patch_resp = client.patch(
            "/api/v1/organization",
            json={
                "name": "GreenHarvest Retail Ltd",
                "contact_phone": "9998887776",
                "low_stock_default_threshold": 20,
            },
            headers={"X-CSRFToken": csrf_token},
        )
        assert patch_resp.status_code == 200
        updated = patch_resp.get_json()["data"]["organization"]
        assert updated["name"] == "GreenHarvest Retail Ltd"
        assert updated["contact_phone"] == "9998887776"
        assert updated["low_stock_default_threshold"] == 20

    def test_duplicate_organization_code_rejected(
        self, client: FlaskClient, multi_tenant_topology: dict, app: Flask
    ):
        with app.app_context():
            AuthService.create_user(
                username="new_super_admin",
                email="new_sa@sanchay.local",
                password="ValidPassword123",
                full_name="New Super Admin",
                role="SUPER_ADMIN",
            )

        csrf_token = api_login_with_csrf(client, "new_super_admin")
        dup_resp = client.post(
            "/api/v1/organizations",
            json={"name": "Copycat Org", "code": "SANCHAY_A"},
            headers={"X-CSRFToken": csrf_token},
        )
        assert dup_resp.status_code == 409
        assert dup_resp.get_json()["error"]["code"] == "DUPLICATE_RESOURCE"

    def test_non_super_admin_cannot_create_or_edit_organization(
        self, app: Flask, multi_tenant_topology: dict
    ):
        # Shop Owner cannot update organization
        owner_client = app.test_client()
        csrf_owner = api_login_with_csrf(owner_client, "owner_a1")
        owner_patch = owner_client.patch(
            "/api/v1/organization",
            json={"name": "Unauthorized Rename"},
            headers={"X-CSRFToken": csrf_owner},
        )
        assert owner_patch.status_code == 403
        assert owner_patch.get_json()["error"]["code"] == "INSUFFICIENT_ROLE_PERMISSION"

        # Staff cannot update organization or create organization
        staff_client = app.test_client()
        csrf_staff = api_login_with_csrf(staff_client, "staff_a1")
        staff_patch = staff_client.patch(
            "/api/v1/organization",
            json={"name": "Staff Rename"},
            headers={"X-CSRFToken": csrf_staff},
        )
        assert staff_patch.status_code == 403

        staff_create = staff_client.post(
            "/api/v1/organizations",
            json={"name": "Staff Org", "code": "STAFF_ORG"},
            headers={"X-CSRFToken": csrf_staff},
        )
        assert staff_create.status_code == 403


# ============================================================================
# 2, 3, 4. Shop Creation, Duplicate Code Scope, Editing & Status Changes
# ============================================================================


class TestShopManagement:
    """Verify shop creation, per-org unique codes, filtering, editing, and Haversine distances."""

    def test_super_admin_creates_shop_inheriting_org_thresholds_and_owner_assignment(
        self, client: FlaskClient, multi_tenant_topology: dict
    ):
        csrf_token = api_login_with_csrf(client, "admin_org_a")

        resp = client.post(
            "/api/v1/shops",
            json={
                "name": "Jayanagar Branch",
                "code": "shp-blr-03",
                "address": "4th Block, Jayanagar",
                "city": "Bengaluru",
                "pincode": "560011",
                "latitude": "12.927923",
                "longitude": "77.583101",
                "contact_phone": "9800000103",
                "contact_email": "jayanagar@sanchay.local",
                "owner_user_id": multi_tenant_topology["owner_a1_id"],
            },
            headers={"X-CSRFToken": csrf_token},
        )
        assert resp.status_code == 201
        shop = resp.get_json()["data"]["shop"]
        assert shop["code"] == "SHP-BLR-03"
        assert shop["low_stock_default_threshold"] == 12
        assert shop["near_expiry_days_threshold"] == 20
        assert shop["status"] == "ACTIVE"
        assert shop["is_operational"] is True
        assert len(shop["assignments"]) == 1
        assert shop["assignments"][0]["user_id"] == multi_tenant_topology["owner_a1_id"]

    def test_duplicate_shop_code_rejected_within_same_org_but_allowed_across_orgs(
        self, app: Flask, multi_tenant_topology: dict
    ):
        client_a = app.test_client()
        csrf_a = api_login_with_csrf(client_a, "admin_org_a")

        # Duplicate code inside Org A must fail with 409
        dup_resp = client_a.post(
            "/api/v1/shops",
            json={
                "name": "Duplicate Indiranagar",
                "code": "SHP-BLR-01",
                "address": "Some Street",
                "city": "Bengaluru",
                "pincode": "560038",
                "contact_phone": "9800000999",
            },
            headers={"X-CSRFToken": csrf_a},
        )
        assert dup_resp.status_code == 409
        assert dup_resp.get_json()["error"]["code"] == "DUPLICATE_RESOURCE"

        # Same code 'SHP-BLR-01' inside Org B must succeed (scoped to organization_id)
        client_b = app.test_client()
        csrf_b = api_login_with_csrf(client_b, "admin_org_b")
        cross_org_resp = client_b.post(
            "/api/v1/shops",
            json={
                "name": "Metro Bengaluru Store",
                "code": "SHP-BLR-01",
                "address": "Whitefield Main Rd",
                "city": "Bengaluru",
                "pincode": "560066",
                "contact_phone": "9800000888",
            },
            headers={"X-CSRFToken": csrf_b},
        )
        assert cross_org_resp.status_code == 201
        assert cross_org_resp.get_json()["data"]["shop"]["code"] == "SHP-BLR-01"

    def test_shop_search_and_filtering_and_peer_haversine_distance(
        self, client: FlaskClient, multi_tenant_topology: dict
    ):
        api_login_with_csrf(client, "admin_org_a")

        # Search by name substring
        search_resp = client.get("/api/v1/shops?search=Koramangala&city=Bengaluru&status=ACTIVE")
        assert search_resp.status_code == 200
        shops = search_resp.get_json()["data"]["shops"]
        assert len(shops) == 1
        assert shops[0]["code"] == "SHP-BLR-02"

        # Retrieve shop detail and verify peer shop Haversine distance is computed
        detail_resp = client.get(
            f"/api/v1/shops/{multi_tenant_topology['shop_a1_id']}"
        )
        assert detail_resp.status_code == 200
        shop_detail = detail_resp.get_json()["data"]["shop"]
        assert len(shop_detail["peer_shops"]) == 1
        peer = shop_detail["peer_shops"][0]
        assert peer["code"] == "SHP-BLR-02"
        assert peer["distance_km"] is not None
        assert 3.0 <= peer["distance_km"] <= 6.0

        # Direct unit check of Haversine utility
        assert haversine_distance_km(None, 77.0, 12.0, 77.0) is None

    def test_shop_editing_by_assigned_owner_and_status_change_by_super_admin(
        self, client: FlaskClient, multi_tenant_topology: dict
    ):
        shop_a1_id = multi_tenant_topology["shop_a1_id"]

        # Assigned Shop Owner can edit address, phone, and alert thresholds
        csrf_owner = api_login_with_csrf(client, "owner_a1")
        owner_edit = client.patch(
            f"/api/v1/shops/{shop_a1_id}",
            json={
                "address": "Updated 100ft Road, Indiranagar Stage 2",
                "low_stock_default_threshold": 25,
            },
            headers={"X-CSRFToken": csrf_owner},
        )
        assert owner_edit.status_code == 200
        assert (
            owner_edit.get_json()["data"]["shop"]["low_stock_default_threshold"] == 25
        )

        # Assigned Shop Owner CANNOT deactivate the shop or change its internal code
        owner_deactivate = client.patch(
            f"/api/v1/shops/{shop_a1_id}",
            json={"status": "INACTIVE"},
            headers={"X-CSRFToken": csrf_owner},
        )
        assert owner_deactivate.status_code == 403
        assert (
            owner_deactivate.get_json()["error"]["code"]
            == "INSUFFICIENT_ROLE_PERMISSION"
        )

        owner_code_change = client.patch(
            f"/api/v1/shops/{shop_a1_id}",
            json={"code": "HACKED-01"},
            headers={"X-CSRFToken": csrf_owner},
        )
        assert owner_code_change.status_code == 403


# ============================================================================
# 5. Deactivated Shop & Deactivated Organization Operational Blocking
# ============================================================================


class TestDeactivatedShopAndOrgRestrictions:
    """Verify deactivated shops and organizations cannot perform operational actions while preserving records."""

    def test_deactivated_shop_blocks_operational_activities_and_preserves_history(
        self, client: FlaskClient, multi_tenant_topology: dict
    ):
        shop_a1_id = multi_tenant_topology["shop_a1_id"]
        csrf_admin = api_login_with_csrf(client, "admin_org_a")

        # Operational check succeeds while ACTIVE
        op_ok = client.post(
            f"/api/v1/shops/{shop_a1_id}/operational-check",
            headers={"X-CSRFToken": csrf_admin},
        )
        assert op_ok.status_code == 200
        assert op_ok.get_json()["data"]["is_operational"] is True

        # Super Admin deactivates Shop A1
        deact_resp = client.patch(
            f"/api/v1/shops/{shop_a1_id}",
            json={"status": "INACTIVE"},
            headers={"X-CSRFToken": csrf_admin},
        )
        assert deact_resp.status_code == 200
        assert deact_resp.get_json()["data"]["shop"]["status"] == "INACTIVE"
        assert deact_resp.get_json()["data"]["shop"]["is_operational"] is False

        # Historical record and assignments are still preserved and readable via GET
        get_resp = client.get(f"/api/v1/shops/{shop_a1_id}")
        assert get_resp.status_code == 200
        assert get_resp.get_json()["data"]["shop"]["name"] == "Indiranagar Branch"
        assert len(get_resp.get_json()["data"]["shop"]["assignments"]) >= 2

        # Operational check is now rejected with 403 INACTIVE_SHOP
        op_blocked = client.post(
            f"/api/v1/shops/{shop_a1_id}/operational-check",
            headers={"X-CSRFToken": csrf_admin},
        )
        assert op_blocked.status_code == 403
        assert op_blocked.get_json()["error"]["code"] == "INACTIVE_SHOP"

        # Assigning a new user to a deactivated shop is rejected with 403 INACTIVE_SHOP
        assign_blocked = client.post(
            f"/api/v1/shops/{shop_a1_id}/assignments",
            json={
                "user_id": multi_tenant_topology["owner_a2_id"],
                "assignment_role": "STAFF",
            },
            headers={"X-CSRFToken": csrf_admin},
        )
        assert assign_blocked.status_code == 403
        assert assign_blocked.get_json()["error"]["code"] == "INACTIVE_SHOP"

        # Editing shop attributes while INACTIVE without reactivating is blocked
        edit_blocked = client.patch(
            f"/api/v1/shops/{shop_a1_id}",
            json={"city": "Mysuru"},
            headers={"X-CSRFToken": csrf_admin},
        )
        assert edit_blocked.status_code == 403
        assert edit_blocked.get_json()["error"]["code"] == "INACTIVE_SHOP"

        # Super Admin can reactivate the shop
        reactivate_resp = client.patch(
            f"/api/v1/shops/{shop_a1_id}",
            json={"status": "ACTIVE"},
            headers={"X-CSRFToken": csrf_admin},
        )
        assert reactivate_resp.status_code == 200
        assert reactivate_resp.get_json()["data"]["shop"]["is_operational"] is True

    def test_deactivated_organization_blocks_shop_creation_and_operations(
        self, client: FlaskClient, multi_tenant_topology: dict
    ):
        shop_a1_id = multi_tenant_topology["shop_a1_id"]
        csrf_admin = api_login_with_csrf(client, "admin_org_a")

        # Deactivate Organization A
        org_deact = client.patch(
            "/api/v1/organization",
            json={"status": "INACTIVE"},
            headers={"X-CSRFToken": csrf_admin},
        )
        assert org_deact.status_code == 200
        assert org_deact.get_json()["data"]["organization"]["status"] == "INACTIVE"

        # Creating a shop in an INACTIVE organization is blocked
        shop_create = client.post(
            "/api/v1/shops",
            json={
                "name": "Blocked Shop",
                "code": "SHP-BLK-01",
                "address": "Addr",
                "city": "Bengaluru",
                "pincode": "560001",
                "contact_phone": "9800000900",
            },
            headers={"X-CSRFToken": csrf_admin},
        )
        assert shop_create.status_code == 403
        assert shop_create.get_json()["error"]["code"] == "INACTIVE_ORGANIZATION"

        # Operational check on a shop under an INACTIVE organization is blocked
        op_check = client.post(
            f"/api/v1/shops/{shop_a1_id}/operational-check",
            headers={"X-CSRFToken": csrf_admin},
        )
        assert op_check.status_code == 403
        assert op_check.get_json()["error"]["code"] == "INACTIVE_ORGANIZATION"

        # Super Admin can reactivate Organization A
        org_react = client.patch(
            "/api/v1/organization",
            json={"status": "ACTIVE"},
            headers={"X-CSRFToken": csrf_admin},
        )
        assert org_react.status_code == 200
        assert org_react.get_json()["data"]["organization"]["status"] == "ACTIVE"


# ============================================================================
# 6, 7, 8, 9. Roles, Membership & Multi-Shop Assignment Rules
# ============================================================================


class TestRolesAndMembership:
    """Verify Super Admin, Shop Owner, and Staff role boundaries and primary shop uniqueness."""

    def test_super_admin_accesses_all_org_shops_and_manages_owners_and_staff(
        self, client: FlaskClient, multi_tenant_topology: dict
    ):
        csrf_admin = api_login_with_csrf(client, "admin_org_a")

        # Super Admin sees both Shop A1 and Shop A2
        shops_resp = client.get("/api/v1/shops")
        assert shops_resp.status_code == 200
        shop_codes = {s["code"] for s in shops_resp.get_json()["data"]["shops"]}
        assert shop_codes == {"SHP-BLR-01", "SHP-BLR-02"}

        # Super Admin assigns Owner A1 as secondary SHOP_OWNER on Shop A2 as well
        assign_resp = client.post(
            f"/api/v1/shops/{multi_tenant_topology['shop_a2_id']}/assignments",
            json={
                "user_id": multi_tenant_topology["owner_a1_id"],
                "assignment_role": "SHOP_OWNER",
                "is_primary": True,
            },
            headers={"X-CSRFToken": csrf_admin},
        )
        assert assign_resp.status_code == 201
        assignment_id = assign_resp.get_json()["data"]["assignment"]["id"]

        # Verify partial unique index behavior: Owner A1 has exactly one active primary shop
        user_resp = client.get(f"/api/v1/users/{multi_tenant_topology['owner_a1_id']}")
        assert user_resp.status_code == 200
        assigned_shops = user_resp.get_json()["data"]["user"]["assigned_shops"]
        assert len(assigned_shops) == 2
        primary_shops = [a for a in assigned_shops if a["is_primary"]]
        assert len(primary_shops) == 1
        assert primary_shops[0]["shop_id"] == multi_tenant_topology["shop_a2_id"]

        # Super Admin can update/revoke assignment via PATCH /api/v1/shops/<id>/assignments/<id>
        patch_assign = client.patch(
            f"/api/v1/shops/{multi_tenant_topology['shop_a2_id']}/assignments/{assignment_id}",
            json={"is_active": False},
            headers={"X-CSRFToken": csrf_admin},
        )
        assert patch_assign.status_code == 200
        assert patch_assign.get_json()["data"]["assignment"]["is_active"] is False

        # Super Admin can update a user's details and status, and cannot deactivate themselves
        self_deact = client.patch(
            f"/api/v1/users/{multi_tenant_topology['admin_a_id']}",
            json={"is_active": False},
            headers={"X-CSRFToken": csrf_admin},
        )
        assert self_deact.status_code == 400

        update_staff = client.patch(
            f"/api/v1/users/{multi_tenant_topology['staff_a1_id']}",
            json={"full_name": "Anil Kumar Updated", "phone": "9811122233"},
            headers={"X-CSRFToken": csrf_admin},
        )
        assert update_staff.status_code == 200
        assert update_staff.get_json()["data"]["user"]["full_name"] == "Anil Kumar Updated"

    def test_shop_owner_scoped_to_assigned_shops_and_can_manage_only_staff(
        self, client: FlaskClient, multi_tenant_topology: dict
    ):
        csrf_owner = api_login_with_csrf(client, "owner_a1")
        shop_a1_id = multi_tenant_topology["shop_a1_id"]

        # Owner A1 only sees Shop A1 in GET /api/v1/shops (not Shop A2)
        shops_resp = client.get("/api/v1/shops")
        assert shops_resp.status_code == 200
        shops = shops_resp.get_json()["data"]["shops"]
        assert len(shops) == 1
        assert shops[0]["id"] == shop_a1_id

        # Owner A1 can list users for their shop
        users_resp = client.get(f"/api/v1/users?shop_id={shop_a1_id}")
        assert users_resp.status_code == 200
        assert len(users_resp.get_json()["data"]["users"]) >= 2

        # Owner A1 can create a new STAFF member for Shop A1
        create_staff_resp = client.post(
            "/api/v1/users",
            json={
                "username": "cashier_a1",
                "email": "cashier_a1@sanchay.local",
                "password": "ValidPassword123",
                "full_name": "Kiran Cashier",
                "role": "STAFF",
                "shop_id": shop_a1_id,
            },
            headers={"X-CSRFToken": csrf_owner},
        )
        assert create_staff_resp.status_code == 201
        created_staff = create_staff_resp.get_json()["data"]["user"]
        assert created_staff["role"] == "STAFF"
        assert created_staff["assigned_shops"][0]["shop_id"] == shop_a1_id

        # Owner A1 can deactivate/reactivate their shop's STAFF member
        deact_staff = client.patch(
            f"/api/v1/users/{created_staff['id']}",
            json={"is_active": False},
            headers={"X-CSRFToken": csrf_owner},
        )
        assert deact_staff.status_code == 200
        assert deact_staff.get_json()["data"]["user"]["is_active"] is False

        # Owner A1 CANNOT promote staff to SHOP_OWNER
        promote_attempt = client.patch(
            f"/api/v1/users/{created_staff['id']}",
            json={"role": "SHOP_OWNER"},
            headers={"X-CSRFToken": csrf_owner},
        )
        assert promote_attempt.status_code == 403

        # Owner A1 CANNOT create a SHOP_OWNER or SUPER_ADMIN
        escalate_resp = client.post(
            "/api/v1/users",
            json={
                "username": "illegal_owner",
                "email": "illegal@sanchay.local",
                "password": "ValidPassword123",
                "full_name": "Illegal Owner",
                "role": "SHOP_OWNER",
                "shop_id": shop_a1_id,
            },
            headers={"X-CSRFToken": csrf_owner},
        )
        assert escalate_resp.status_code == 403
        assert escalate_resp.get_json()["error"]["code"] == "INSUFFICIENT_ROLE_PERMISSION"

        # Owner A1 CANNOT create a new shop
        create_shop_resp = client.post(
            "/api/v1/shops",
            json={
                "name": "Owner Unauthorized Shop",
                "code": "SHP-UNAUTH",
                "address": "Addr",
                "city": "Bengaluru",
                "pincode": "560001",
                "contact_phone": "9800000777",
            },
            headers={"X-CSRFToken": csrf_owner},
        )
        assert create_shop_resp.status_code == 403

    def test_staff_restricted_to_assigned_shop_and_cannot_elevate_role_or_manage_users(
        self, client: FlaskClient, multi_tenant_topology: dict
    ):
        csrf_staff = api_login_with_csrf(client, "staff_a1")
        shop_a1_id = multi_tenant_topology["shop_a1_id"]
        staff_id = multi_tenant_topology["staff_a1_id"]

        # Staff sees only assigned Shop A1
        shops_resp = client.get("/api/v1/shops")
        assert shops_resp.status_code == 200
        shops = shops_resp.get_json()["data"]["shops"]
        assert len(shops) == 1
        assert shops[0]["id"] == shop_a1_id

        # Staff can view their assigned shop details
        shop_detail = client.get(f"/api/v1/shops/{shop_a1_id}")
        assert shop_detail.status_code == 200
        assert shop_detail.get_json()["data"]["shop"]["my_assignment_role"] == "STAFF"
        # Staff does not receive personnel assignment management list
        assert "assignments" not in shop_detail.get_json()["data"]["shop"]

        # Staff CANNOT edit shop details
        staff_edit_shop = client.patch(
            f"/api/v1/shops/{shop_a1_id}",
            json={"name": "Staff Renamed Shop"},
            headers={"X-CSRFToken": csrf_staff},
        )
        assert staff_edit_shop.status_code == 403
        assert (
            staff_edit_shop.get_json()["error"]["code"]
            == "INSUFFICIENT_ROLE_PERMISSION"
        )

        # Staff CANNOT list users or create users
        assert client.get("/api/v1/users").status_code == 403
        assert (
            client.post(
                "/api/v1/users",
                json={
                    "username": "staff_created",
                    "email": "sc@sanchay.local",
                    "password": "ValidPassword123",
                    "full_name": "Nope",
                    "role": "STAFF",
                },
                headers={"X-CSRFToken": csrf_staff},
            ).status_code
            == 403
        )

        # Staff CANNOT elevate their own role to SUPER_ADMIN or SHOP_OWNER
        self_elevate = client.patch(
            f"/api/v1/users/{staff_id}",
            json={"role": "SUPER_ADMIN"},
            headers={"X-CSRFToken": csrf_staff},
        )
        assert self_elevate.status_code == 403
        assert self_elevate.get_json()["error"]["code"] == "INSUFFICIENT_ROLE_PERMISSION"


# ============================================================================
# 10, 11, 12. Unauthorized Access, Cross-Shop IDOR & Cross-Organization IDOR
# ============================================================================


class TestTenantAndShopIsolationSecurity:
    """Verify strict backend rejection of cross-shop and cross-organization IDOR attempts."""

    def test_unauthenticated_requests_rejected_across_all_phase2_endpoints(
        self, client: FlaskClient, multi_tenant_topology: dict
    ):
        shop_id = multi_tenant_topology["shop_a1_id"]
        user_id = multi_tenant_topology["owner_a1_id"]

        assert client.get("/api/v1/organization").status_code == 401
        assert client.get("/api/v1/shops").status_code == 401
        assert client.get(f"/api/v1/shops/{shop_id}").status_code == 401
        assert client.get("/api/v1/users").status_code == 401
        assert client.get(f"/api/v1/users/{user_id}").status_code == 401

        # HTML pages redirect unauthenticated users to /auth/login
        for path in ("/dashboard", "/organization", "/shops", f"/shops/{shop_id}", "/users"):
            resp = client.get(path)
            assert resp.status_code == 302
            assert "/auth/login" in resp.headers["Location"]

    def test_cross_shop_idor_blocked_within_same_organization(
        self, app: Flask, client: FlaskClient, multi_tenant_topology: dict
    ):
        shop_a2_id = multi_tenant_topology["shop_a2_id"]
        owner_a2_id = multi_tenant_topology["owner_a2_id"]

        # Log in as Owner A1 (assigned ONLY to Shop A1, NOT Shop A2)
        csrf_owner_a1 = api_login_with_csrf(client, "owner_a1")

        # 1. Viewing Shop A2 details is rejected with 403 UNAUTHORIZED_SHOP_ACCESS
        get_other_shop = client.get(f"/api/v1/shops/{shop_a2_id}")
        assert get_other_shop.status_code == 403
        assert (
            get_other_shop.get_json()["error"]["code"] == "UNAUTHORIZED_SHOP_ACCESS"
        )

        # 2. Editing Shop A2 is rejected with 403 UNAUTHORIZED_SHOP_ACCESS
        patch_other_shop = client.patch(
            f"/api/v1/shops/{shop_a2_id}",
            json={"name": "Cross-Shop Tamper"},
            headers={"X-CSRFToken": csrf_owner_a1},
        )
        assert patch_other_shop.status_code == 403
        assert (
            patch_other_shop.get_json()["error"]["code"] == "UNAUTHORIZED_SHOP_ACCESS"
        )

        # 3. Assigning staff to Shop A2 is rejected with 403 UNAUTHORIZED_SHOP_ACCESS
        assign_other_shop = client.post(
            f"/api/v1/shops/{shop_a2_id}/assignments",
            json={
                "user_id": multi_tenant_topology["staff_a1_id"],
                "assignment_role": "STAFF",
            },
            headers={"X-CSRFToken": csrf_owner_a1},
        )
        assert assign_other_shop.status_code == 403

        # 4. Viewing or editing Owner A2 (who is on Shop A2, not Shop A1) is rejected with 403
        get_other_user = client.get(f"/api/v1/users/{owner_a2_id}")
        assert get_other_user.status_code == 403

        # Verify security audit logs recorded UNAUTHORIZED_SHOP_ACCESS
        with app.app_context():
            audit_count = (
                db.session.query(AuditLog)
                .filter(
                    AuditLog.actor_user_id == multi_tenant_topology["owner_a1_id"],
                    AuditLog.action == "UNAUTHORIZED_SHOP_ACCESS",
                )
                .count()
            )
            assert audit_count >= 3

    def test_cross_organization_idor_strictly_blocked_even_for_super_admin(
        self, app: Flask, client: FlaskClient, multi_tenant_topology: dict
    ):
        org_b_id = multi_tenant_topology["org_b_id"]
        shop_b1_id = multi_tenant_topology["shop_b1_id"]
        admin_b_id = multi_tenant_topology["admin_b_id"]

        # Log in as Super Admin of Organization A
        csrf_admin_a = api_login_with_csrf(client, "admin_org_a")

        # 1. Passing Org B's organization_id to GET /api/v1/organization -> 403 UNAUTHORIZED_ORG_ACCESS
        get_org_b = client.get(f"/api/v1/organization?organization_id={org_b_id}")
        assert get_org_b.status_code == 403
        assert get_org_b.get_json()["error"]["code"] == "UNAUTHORIZED_ORG_ACCESS"

        # 2. Passing Org B's organization_id to PATCH /api/v1/organization -> 403
        patch_org_b = client.patch(
            f"/api/v1/organization?organization_id={org_b_id}",
            json={"name": "Cross-Tenant Takeover"},
            headers={"X-CSRFToken": csrf_admin_a},
        )
        assert patch_org_b.status_code == 403
        assert patch_org_b.get_json()["error"]["code"] == "UNAUTHORIZED_ORG_ACCESS"

        # 3. Accessing Org B's shop (Shop B1) -> 403 UNAUTHORIZED_ORG_ACCESS
        get_shop_b1 = client.get(f"/api/v1/shops/{shop_b1_id}")
        assert get_shop_b1.status_code == 403
        assert get_shop_b1.get_json()["error"]["code"] == "UNAUTHORIZED_ORG_ACCESS"

        patch_shop_b1 = client.patch(
            f"/api/v1/shops/{shop_b1_id}",
            json={"name": "Hijacked Shop"},
            headers={"X-CSRFToken": csrf_admin_a},
        )
        assert patch_shop_b1.status_code == 403
        assert patch_shop_b1.get_json()["error"]["code"] == "UNAUTHORIZED_ORG_ACCESS"

        # 4. Creating a shop with forged organization_id = org_b_id -> 403 UNAUTHORIZED_ORG_ACCESS
        forged_shop = client.post(
            "/api/v1/shops",
            json={
                "organization_id": org_b_id,
                "name": "Forged Org Shop",
                "code": "SHP-FORGE",
                "address": "Addr",
                "city": "Mumbai",
                "pincode": "400001",
                "contact_phone": "9800000555",
            },
            headers={"X-CSRFToken": csrf_admin_a},
        )
        assert forged_shop.status_code == 403
        assert forged_shop.get_json()["error"]["code"] == "UNAUTHORIZED_ORG_ACCESS"

        # 5. Viewing, modifying, or assigning a user from Org B into Org A's shop -> 403
        assert client.get(f"/api/v1/users/{admin_b_id}").status_code == 403
        assert (
            client.patch(
                f"/api/v1/users/{admin_b_id}",
                json={"full_name": "Tampered"},
                headers={"X-CSRFToken": csrf_admin_a},
            ).status_code
            == 403
        )
        cross_assign = client.post(
            f"/api/v1/shops/{multi_tenant_topology['shop_a1_id']}/assignments",
            json={"user_id": admin_b_id, "assignment_role": "SHOP_OWNER"},
            headers={"X-CSRFToken": csrf_admin_a},
        )
        assert cross_assign.status_code == 403
        assert cross_assign.get_json()["error"]["code"] == "UNAUTHORIZED_ORG_ACCESS"

        # Verify UNAUTHORIZED_ORG_ACCESS audit logs were persisted
        with app.app_context():
            cross_org_audits = (
                db.session.query(AuditLog)
                .filter(
                    AuditLog.actor_user_id == multi_tenant_topology["admin_a_id"],
                    AuditLog.action == "UNAUTHORIZED_ORG_ACCESS",
                )
                .count()
            )
            assert cross_org_audits >= 5


# ============================================================================
# HTML Views, Form Workflows, Validator Edge Cases & CLI Tests
# ============================================================================


class TestPhase2HtmlViewsAndCli:
    """Verify server-rendered Phase 2 HTML pages, form submissions, and CLI commands."""

    def test_html_dashboard_organization_shops_and_users_views(
        self, client: FlaskClient, multi_tenant_topology: dict
    ):
        api_login_with_csrf(client, "admin_org_a")
        shop_a1_id = multi_tenant_topology["shop_a1_id"]

        # Dashboard view
        dash = client.get("/dashboard")
        assert dash.status_code == 200
        dash_html = dash.get_data(as_text=True)
        assert "Sanchay Retail Co-op" in dash_html
        assert "Indiranagar Branch" in dash_html

        # Organization view & form update
        org_page = client.get("/organization")
        assert org_page.status_code == 200
        org_csrf = extract_csrf_token(org_page.get_data(as_text=True))
        org_upd = client.post(
            "/organization/update",
            data={
                "csrf_token": org_csrf,
                "name": "Sanchay Retail Co-op Updated",
                "code": "SANCHAY_A",
                "low_stock_default_threshold": "14",
                "near_expiry_days_threshold": "21",
                "status": "ACTIVE",
            },
            follow_redirects=True,
        )
        assert org_upd.status_code == 200
        assert "Sanchay Retail Co-op Updated" in org_upd.get_data(as_text=True)

        # Shops view & form creation
        shops_page = client.get("/shops")
        assert shops_page.status_code == 200
        shops_csrf = extract_csrf_token(shops_page.get_data(as_text=True))
        shop_create = client.post(
            "/shops/create",
            data={
                "csrf_token": shops_csrf,
                "name": "Whitefield Branch",
                "code": "SHP-BLR-04",
                "address": "ITPL Main Rd, Whitefield",
                "city": "Bengaluru",
                "pincode": "560066",
                "latitude": "12.969819",
                "longitude": "77.749972",
                "contact_phone": "9800000104",
                "contact_email": "whitefield@sanchay.local",
                "low_stock_default_threshold": "10",
                "near_expiry_days_threshold": "15",
            },
            follow_redirects=True,
        )
        assert shop_create.status_code == 200
        assert "Whitefield Branch" in shop_create.get_data(as_text=True)

        # Shop detail view & form update
        detail_page = client.get(f"/shops/{shop_a1_id}")
        assert detail_page.status_code == 200
        detail_csrf = extract_csrf_token(detail_page.get_data(as_text=True))
        shop_upd = client.post(
            f"/shops/{shop_a1_id}/update",
            data={
                "csrf_token": detail_csrf,
                "name": "Indiranagar Flagship Branch",
                "code": "SHP-BLR-01",
                "address": "100 Feet Road, Indiranagar",
                "city": "Bengaluru",
                "pincode": "560038",
                "latitude": "12.971599",
                "longitude": "77.641151",
                "contact_phone": "9800000101",
                "low_stock_default_threshold": "12",
                "near_expiry_days_threshold": "20",
                "status": "ACTIVE",
            },
            follow_redirects=True,
        )
        assert shop_upd.status_code == 200
        assert "Indiranagar Flagship Branch" in shop_upd.get_data(as_text=True)

        # Users directory view & form user creation & status update
        users_page = client.get("/users")
        assert users_page.status_code == 200
        users_csrf = extract_csrf_token(users_page.get_data(as_text=True))
        user_create = client.post(
            "/users/create",
            data={
                "csrf_token": users_csrf,
                "username": "form_staff_01",
                "email": "form_staff_01@sanchay.local",
                "full_name": "Form Staff Member",
                "role": "STAFF",
                "shop_id": str(shop_a1_id),
                "password": "ValidPassword123",
            },
            follow_redirects=True,
        )
        assert user_create.status_code == 200
        assert "form_staff_01" in user_create.get_data(as_text=True)

        # Toggle user status via HTML form
        users_csrf2 = extract_csrf_token(user_create.get_data(as_text=True))
        user_toggle = client.post(
            f"/users/{multi_tenant_topology['staff_a1_id']}/update",
            data={"csrf_token": users_csrf2, "is_active": "false"},
            follow_redirects=True,
        )
        assert user_toggle.status_code == 200

    def test_shop_validation_edge_cases(
        self, client: FlaskClient, multi_tenant_topology: dict
    ):
        csrf_token = api_login_with_csrf(client, "admin_org_a")

        # Out-of-bounds latitude (> 90) rejected with 400
        bad_lat = client.post(
            "/api/v1/shops",
            json={
                "name": "Bad Coord Shop",
                "code": "SHP-BAD-01",
                "address": "123 Street",
                "city": "Bengaluru",
                "pincode": "560001",
                "latitude": "105.000",
                "longitude": "77.000",
                "contact_phone": "9800000199",
            },
            headers={"X-CSRFToken": csrf_token},
        )
        assert bad_lat.status_code == 400
        assert bad_lat.get_json()["error"]["code"] == "VALIDATION_ERROR"

        # Single coordinate without matching pair rejected with 400
        half_coord = client.post(
            "/api/v1/shops",
            json={
                "name": "Half Coord Shop",
                "code": "SHP-BAD-02",
                "address": "123 Street",
                "city": "Bengaluru",
                "pincode": "560001",
                "latitude": "12.971599",
                "contact_phone": "9800000199",
            },
            headers={"X-CSRFToken": csrf_token},
        )
        assert half_coord.status_code == 400

    def test_cli_create_user_with_organization_bootstrap(self, app: Flask):
        runner = app.test_cli_runner()
        result = runner.invoke(
            args=[
                "create-user",
                "--username",
                "cli_org_admin",
                "--email",
                "cli_org@sanchay.local",
                "--full-name",
                "CLI Org Admin",
                "--role",
                "SUPER_ADMIN",
                "--org-code",
                "CLI_ORG_01",
                "--org-name",
                "CLI Provisioned Retail",
                "--password",
                "ValidPassword123",
            ]
        )
        assert result.exit_code == 0
        assert "Created and linked organization 'CLI Provisioned Retail'" in result.output
        assert "Successfully created user 'cli_org_admin'" in result.output

    def test_html_organization_create_and_shop_assignment_forms_and_model_helpers(
        self, app: Flask, multi_tenant_topology: dict
    ):
        with app.app_context():
            unlinked_admin = AuthService.create_user(
                username="unlinked_sa",
                email="unlinked_sa@sanchay.local",
                password="ValidPassword123",
                full_name="Unlinked SA",
                role="SUPER_ADMIN",
            )
            unlinked_id = unlinked_admin.id

        sa_client = app.test_client()
        sa_csrf = api_login_with_csrf(sa_client, "unlinked_sa")

        # User with no organization gets 403 ORGANIZATION_MEMBERSHIP_REQUIRED on GET /api/v1/organization
        no_org_resp = sa_client.get("/api/v1/organization")
        assert no_org_resp.status_code == 403
        assert (
            no_org_resp.get_json()["error"]["code"]
            == "ORGANIZATION_MEMBERSHIP_REQUIRED"
        )

        # Create organization via HTML form POST /organization/create
        org_form_resp = sa_client.post(
            "/organization/create",
            data={
                "csrf_token": sa_csrf,
                "name": "Form Created Org",
                "code": "FORM_ORG_01",
                "contact_email": "form_org@sanchay.local",
                "contact_phone": "9812345670",
                "address": "Chennai, TN",
                "low_stock_default_threshold": "10",
                "near_expiry_days_threshold": "15",
            },
            follow_redirects=True,
        )
        assert org_form_resp.status_code == 200
        assert "Form Created Org" in org_form_resp.get_data(as_text=True)

        # HTML shop assignment form and assignment update form on Org A
        admin_a_client = app.test_client()
        api_login_with_csrf(admin_a_client, "admin_org_a")
        shop_a2_id = multi_tenant_topology["shop_a2_id"]
        staff_a1_id = multi_tenant_topology["staff_a1_id"]

        detail_page = admin_a_client.get(f"/shops/{shop_a2_id}")
        detail_csrf = extract_csrf_token(detail_page.get_data(as_text=True))

        assign_form = admin_a_client.post(
            f"/shops/{shop_a2_id}/assignments",
            data={
                "csrf_token": detail_csrf,
                "user_id": str(staff_a1_id),
                "assignment_role": "STAFF",
                "is_primary": "false",
            },
            follow_redirects=True,
        )
        assert assign_form.status_code == 200

        # Verify User model helper methods inside app_context
        with app.app_context():
            from app.models.user import User
            from app.models.organization import UserShopAssignment

            admin_user = db.session.get(User, multi_tenant_topology["admin_a_id"])
            staff_user = db.session.get(User, staff_a1_id)
            assert len(admin_user.get_accessible_shops(include_inactive=False)) == 2
            assert len(staff_user.get_accessible_shops(include_inactive=False)) == 2
            assert staff_user.get_primary_assignment() is not None
            a2_assignment = staff_user.get_shop_assignment(shop_a2_id)
            assert a2_assignment is not None
            a2_assignment_id = a2_assignment.id

        # Toggle assignment via HTML form POST /shops/<id>/assignments/<id>/update
        detail_csrf2 = extract_csrf_token(assign_form.get_data(as_text=True))
        upd_assign_form = admin_a_client.post(
            f"/shops/{shop_a2_id}/assignments/{a2_assignment_id}/update",
            data={"csrf_token": detail_csrf2, "is_active": "false"},
            follow_redirects=True,
        )
        assert upd_assign_form.status_code == 200
