"""Phase 1 Test Suite: Foundation, Configuration, PostgreSQL, Migrations & Authentication.

Covers all 10 required Phase 1 verification areas against the isolated
sanchay_ai_test PostgreSQL database.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient
from flask_migrate import current as alembic_current
from flask_migrate import downgrade as alembic_downgrade
from flask_migrate import upgrade as alembic_upgrade
from sqlalchemy import inspect, text

from app import create_app
from app.config import validate_postgresql_uri
from app.extensions import db
from app.models.audit import AuditLog
from app.models.user import User
from app.services.audit_service import sanitize_audit_payload
from app.services.auth_service import GENERIC_AUTH_ERROR_MESSAGE, AuthService
from app.utils.errors import DuplicateResourceError, ValidationError
from tests.conftest import extract_csrf_token


# ============================================================================
# 1. Application Startup & Configuration Tests
# ============================================================================


class TestAppStartupAndConfig:
    """Verify application factory, environment configs, and safety guards."""

    def test_testing_app_configuration(self, app: Flask):
        assert app.config["TESTING"] is True
        assert app.config["WTF_CSRF_ENABLED"] is True
        assert app.config["AI_ENABLED"] is False
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert "sanchay_ai_test" in app.config["SQLALCHEMY_DATABASE_URI"]

    def test_unknown_config_environment_rejected(self):
        with pytest.raises(RuntimeError, match="Unknown configuration environment"):
            create_app("nonexistent_env")

    def test_sqlite_fallback_strictly_prohibited(self):
        with pytest.raises(RuntimeError, match="SQLite is prohibited"):
            validate_postgresql_uri("sqlite:///local.db")

        with pytest.raises(RuntimeError, match="SQLite is prohibited"):
            create_app(
                "development",
                config_overrides={"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"},
            )

    def test_missing_or_invalid_database_uri_rejected(self):
        with pytest.raises(RuntimeError, match="Missing required PostgreSQL configuration"):
            validate_postgresql_uri("")

        with pytest.raises(RuntimeError, match="Invalid database URI scheme"):
            validate_postgresql_uri("mysql://user:pass@localhost/db")

    def test_postgres_uri_scheme_normalized(self):
        normalized = validate_postgresql_uri("postgres://user:pass@localhost:5432/mydb")
        assert normalized == "postgresql+psycopg2://user:pass@localhost:5432/mydb"

    def test_production_config_rejects_missing_or_weak_secret_key(self, app: Flask):
        test_db_uri = app.config["SQLALCHEMY_DATABASE_URI"]
        with pytest.raises(RuntimeError, match="Production configuration error"):
            create_app(
                "production",
                config_overrides={
                    "SECRET_KEY": "changeme",
                    "SQLALCHEMY_DATABASE_URI": test_db_uri,
                },
            )

    def test_testing_config_rejects_non_test_database_name(self):
        with pytest.raises(RuntimeError, match="Safety guard triggered"):
            create_app(
                "testing",
                config_overrides={
                    "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://postgres:postgres@localhost:5432/sanchay_ai_dev"
                },
            )

    def test_security_headers_attached_to_responses(self, client: FlaskClient):
        resp = client.get("/auth/login")
        assert resp.status_code == 200
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
        assert "Content-Security-Policy" in resp.headers
        assert "no-store" in resp.headers.get("Cache-Control", "")


# ============================================================================
# 2. PostgreSQL Connectivity & Phase 1 Schema Scope Tests
# ============================================================================


class TestPostgreSQLConnectivityAndScope:
    """Verify PostgreSQL connection, health endpoint, CLI check, and strict Phase 1 table scope."""

    def test_health_endpoint_verifies_postgresql_connection(self, client: FlaskClient):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["success"] is True
        assert payload["data"]["status"] == "healthy"
        assert payload["data"]["database"] == "connected"
        assert payload["data"]["phase"] == 1
        assert payload["data"]["ai_enabled"] is False

    def test_cli_db_check_command(self, app: Flask):
        runner = app.test_cli_runner()
        result = runner.invoke(args=["db-check"])
        assert result.exit_code == 0
        assert "PostgreSQL connection verified successfully." in result.output

    def test_only_phase1_tables_exist(self, app: Flask):
        with app.app_context():
            inspector = inspect(db.engine)
            table_names = set(inspector.get_table_names())
            assert "users" in table_names
            assert "audit_logs" in table_names
            assert "organizations" in table_names
            assert "shops" in table_names
            assert "user_shop_assignments" in table_names
            assert "products" in table_names
            assert "shop_products" in table_names
            assert "inventory_batches" in table_names
            assert "stock_movements" in table_names
            assert "sales" in table_names
            assert "sale_items" in table_names
            assert "sale_returns" in table_names
            assert "transfer_requests" in table_names
            assert "transfer_status_logs" in table_names
            assert "notifications" in table_names
            # Ensure future phase (Phase 8) tables were NOT created prematurely
            prohibited_future_tables = {
                "transfer_batch_allocations",
                "ai_model_runs",
                "ai_forecasts",
                "ai_transfer_recommendations",
            }
            assert table_names.isdisjoint(prohibited_future_tables)


# ============================================================================
# 3. Database Migration Behavior Tests
# ============================================================================


class TestDatabaseMigrations:
    """Verify Alembic downgrade and upgrade lifecycle on PostgreSQL."""

    def test_migration_downgrade_and_upgrade_cycle(self, app: Flask):
        with app.app_context():
            alembic_downgrade(revision="base")
            inspector_after_down = inspect(db.engine)
            tables_after_down = set(inspector_after_down.get_table_names())
            assert "users" not in tables_after_down
            assert "audit_logs" not in tables_after_down

            alembic_upgrade(revision="head")
            inspector_after_up = inspect(db.engine)
            tables_after_up = set(inspector_after_up.get_table_names())
            assert "users" in tables_after_up
            assert "audit_logs" in tables_after_up

            user_cols = {col["name"] for col in inspector_after_up.get_columns("users")}
            assert {
                "id",
                "username",
                "email",
                "password_hash",
                "full_name",
                "role",
                "is_active",
                "session_token",
                "last_login_at",
                "created_at",
                "updated_at",
            }.issubset(user_cols)


# ============================================================================
# 4. Successful Login & CLI User Creation Tests
# ============================================================================


class TestSuccessfulAuthentication:
    """Verify valid login via HTML form and JSON API using username or email."""

    def test_html_form_login_and_authenticated_landing_page(
        self, client: FlaskClient, active_user: dict
    ):
        login_get = client.get("/auth/login")
        assert login_get.status_code == 200
        csrf_token = extract_csrf_token(login_get.get_data(as_text=True))

        login_post = client.post(
            "/auth/login",
            data={
                "csrf_token": csrf_token,
                "identifier": active_user["username"],
                "password": active_user["password"],
            },
            follow_redirects=True,
        )
        assert login_post.status_code == 200
        html = login_post.get_data(as_text=True)
        assert "Authenticated Session Active" in html
        assert active_user["full_name"] in html
        assert active_user["username"] in html

    def test_api_login_with_email_case_insensitive(
        self, app: Flask, client: FlaskClient, active_user: dict
    ):
        csrf_token = extract_csrf_token(client.get("/auth/login").get_data(as_text=True))

        resp = client.post(
            "/api/v1/auth/login",
            json={
                "identifier": active_user["email"].upper(),
                "password": active_user["password"],
            },
            headers={"X-CSRFToken": csrf_token},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["user"]["username"] == active_user["username"]
        assert body["data"]["user"]["last_login_at"] is not None
        assert "password_hash" not in body["data"]["user"]
        assert "session_token" not in body["data"]["user"]

        # Verify audit log entry was persisted in PostgreSQL
        with app.app_context():
            log = (
                db.session.query(AuditLog)
                .filter_by(action="USER_LOGIN_SUCCESS", actor_user_id=active_user["id"])
                .first()
            )
            assert log is not None

    def test_cli_create_user_command(self, app: Flask):
        runner = app.test_cli_runner()
        result = runner.invoke(
            args=[
                "create-user",
                "--username",
                "cli_owner",
                "--email",
                "cli_owner@sanchay.local",
                "--full-name",
                "CLI Created Owner",
                "--role",
                "SHOP_OWNER",
                "--password",
                "StrongPass99",
            ]
        )
        assert result.exit_code == 0
        assert "Successfully created user 'cli_owner'" in result.output


# ============================================================================
# 5. Rejected Login & Anti-Enumeration Tests
# ============================================================================


class TestRejectedAuthentication:
    """Verify invalid credentials and inactive accounts are rejected with identical generic errors."""

    def test_wrong_password_unknown_user_and_inactive_user_return_identical_generic_error(
        self, app: Flask, client: FlaskClient, active_user: dict, inactive_user: dict
    ):
        csrf_token = extract_csrf_token(client.get("/auth/login").get_data(as_text=True))
        headers = {"X-CSRFToken": csrf_token}

        # Case A: Wrong password for existing user
        resp_wrong_pwd = client.post(
            "/api/v1/auth/login",
            json={"identifier": active_user["username"], "password": "WrongPassword999"},
            headers=headers,
        )
        # Case B: Non-existent username
        resp_unknown = client.post(
            "/api/v1/auth/login",
            json={"identifier": "nobody@sanchay.local", "password": "WrongPassword999"},
            headers=headers,
        )
        # Case C: Deactivated user with correct password
        resp_inactive = client.post(
            "/api/v1/auth/login",
            json={"identifier": inactive_user["username"], "password": inactive_user["password"]},
            headers=headers,
        )

        for resp in (resp_wrong_pwd, resp_unknown, resp_inactive):
            assert resp.status_code == 401
            payload = resp.get_json()
            assert payload["success"] is False
            assert payload["error"]["code"] == "INVALID_CREDENTIALS"
            assert payload["error"]["message"] == GENERIC_AUTH_ERROR_MESSAGE

        # Verify failed and blocked login attempts are recorded in audit_logs
        with app.app_context():
            failed_count = (
                db.session.query(AuditLog)
                .filter(
                    AuditLog.action.in_(
                        ["USER_LOGIN_FAILED", "USER_LOGIN_BLOCKED_INACTIVE"]
                    )
                )
                .count()
            )
            assert failed_count == 3


# ============================================================================
# 6. Password Hashing, Verification & Password Change Tests
# ============================================================================


class TestPasswordSecurity:
    """Verify scrypt password hashing, secret redaction, and password change workflow."""

    def test_password_hashed_with_scrypt_and_never_stored_plaintext(
        self, app: Flask, active_user: dict
    ):
        with app.app_context():
            user = db.session.get(User, active_user["id"])
            assert user is not None
            assert user.password_hash != active_user["password"]
            assert user.password_hash.startswith("scrypt:")
            assert user.check_password(active_user["password"]) is True
            assert user.check_password("WrongPassword123") is False
            assert user.check_password("") is False

    def test_audit_sanitizer_redacts_sensitive_fields(self):
        raw = {
            "username": "test",
            "password": "PlaintextSecret123",
            "nested": {"new_password": "Secret2", "safe_key": "visible"},
        }
        sanitized = sanitize_audit_payload(raw)
        assert sanitized["password"] == "[REDACTED]"
        assert sanitized["nested"]["new_password"] == "[REDACTED]"
        assert sanitized["nested"]["safe_key"] == "visible"

    def test_change_password_updates_hash_and_allows_relogin(
        self, client: FlaskClient, active_user: dict
    ):
        csrf_token = extract_csrf_token(client.get("/auth/login").get_data(as_text=True))
        headers = {"X-CSRFToken": csrf_token}

        client.post(
            "/api/v1/auth/login",
            json={"identifier": active_user["username"], "password": active_user["password"]},
            headers=headers,
        )

        change_resp = client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": active_user["password"],
                "new_password": "UpdatedPass456",
                "confirm_password": "UpdatedPass456",
            },
            headers=headers,
        )
        assert change_resp.status_code == 200
        assert change_resp.get_json()["success"] is True

        # Session remains active for current client with rotated token
        me_resp = client.get("/api/v1/auth/me")
        assert me_resp.status_code == 200


# ============================================================================
# 7. Protected Routes Rejecting Unauthenticated Requests
# ============================================================================


class TestProtectedRoutes:
    """Verify unauthenticated requests are blocked on both HTML and JSON routes."""

    def test_html_landing_page_redirects_unauthenticated_user(self, client: FlaskClient):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_api_protected_routes_return_401_json_when_unauthenticated(
        self, client: FlaskClient
    ):
        csrf_token = extract_csrf_token(client.get("/auth/login").get_data(as_text=True))
        headers = {"X-CSRFToken": csrf_token}

        assert client.get("/api/v1/auth/me").status_code == 401
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 401
        assert (
            client.post("/api/v1/auth/change-password", json={}, headers=headers).status_code
            == 401
        )


# ============================================================================
# 8. Logout & Server-Side Session Invalidation Tests
# ============================================================================


class TestLogoutAndSessionInvalidation:
    """Verify logout clears session and invalidates replayed pre-logout session cookies."""

    def test_logout_invalidates_session_and_prevents_cookie_replay(
        self, app: Flask, active_user: dict
    ):
        client = app.test_client()
        csrf_token = extract_csrf_token(client.get("/auth/login").get_data(as_text=True))
        headers = {"X-CSRFToken": csrf_token}

        login_resp = client.post(
            "/api/v1/auth/login",
            json={"identifier": active_user["username"], "password": active_user["password"]},
            headers=headers,
        )
        assert login_resp.status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 200

        # Capture the raw session cookie value before logging out
        session_cookie = client.get_cookie("sanchay_session")
        assert session_cookie is not None
        captured_cookie_value = session_cookie.value

        # Perform logout
        logout_resp = client.post("/api/v1/auth/logout", headers=headers)
        assert logout_resp.status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401

        # Attempt to replay the captured pre-logout session cookie in a fresh client
        replay_client = app.test_client()
        replay_client.set_cookie("sanchay_session", captured_cookie_value)
        replay_resp = replay_client.get("/api/v1/auth/me")
        assert replay_resp.status_code == 401
        assert replay_resp.get_json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


# ============================================================================
# 9. CSRF Validation Tests
# ============================================================================


class TestCSRFProtection:
    """Verify state-changing endpoints reject requests missing a valid CSRF token."""

    def test_login_and_logout_reject_missing_csrf_token(
        self, client: FlaskClient, active_user: dict
    ):
        # JSON login without X-CSRFToken header
        json_resp = client.post(
            "/api/v1/auth/login",
            json={"identifier": active_user["username"], "password": active_user["password"]},
        )
        assert json_resp.status_code == 400
        assert json_resp.get_json()["error"]["code"] == "CSRF_TOKEN_MISSING_OR_INVALID"

        # HTML form login without csrf_token field
        form_resp = client.post(
            "/auth/login",
            data={"identifier": active_user["username"], "password": active_user["password"]},
        )
        assert form_resp.status_code == 400


# ============================================================================
# 10. Input Validation & Expected Failure Cases
# ============================================================================


class TestInputValidationAndFailureCases:
    """Verify server-side validation for login, user creation, duplicates, and open redirects."""

    def test_empty_login_inputs_return_400_validation_error(self, client: FlaskClient):
        csrf_token = extract_csrf_token(client.get("/auth/login").get_data(as_text=True))
        resp = client.post(
            "/api/v1/auth/login",
            json={"identifier": "   ", "password": ""},
            headers={"X-CSRFToken": csrf_token},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "identifier" in body["error"]["details"]
        assert "password" in body["error"]["details"]

    def test_weak_password_and_invalid_user_fields_rejected(self, app: Flask):
        with app.app_context():
            with pytest.raises(ValidationError) as exc_info:
                AuthService.create_user(
                    username="bad user!",
                    email="invalid-email",
                    password="short",
                    full_name="A",
                    role="INVALID_ROLE",
                )
            details = exc_info.value.details
            assert "username" in details
            assert "email" in details
            assert "full_name" in details
            assert "role" in details

            with pytest.raises(ValidationError, match="uppercase"):
                AuthService.create_user(
                    username="valid_user",
                    email="valid@sanchay.local",
                    password="alllowercase123",
                    full_name="Valid Name",
                    role="STAFF",
                )

    def test_duplicate_username_or_email_rejected_with_409(
        self, app: Flask, active_user: dict
    ):
        with app.app_context():
            with pytest.raises(DuplicateResourceError) as dup_user_exc:
                AuthService.create_user(
                    username=active_user["username"].upper(),
                    email="unique_other@sanchay.local",
                    password="ValidPassword123",
                    full_name="Duplicate Username",
                    role="STAFF",
                )
            assert dup_user_exc.value.http_status == 409

            with pytest.raises(DuplicateResourceError) as dup_email_exc:
                AuthService.create_user(
                    username="unique_username",
                    email=active_user["email"].upper(),
                    password="ValidPassword123",
                    full_name="Duplicate Email",
                    role="STAFF",
                )
            assert dup_email_exc.value.http_status == 409

    def test_open_redirect_prevented_on_form_login(
        self, client: FlaskClient, active_user: dict
    ):
        login_page = client.get("/auth/login?next=https://malicious.example.com/phish")
        csrf_token = extract_csrf_token(login_page.get_data(as_text=True))

        resp = client.post(
            "/auth/login",
            data={
                "csrf_token": csrf_token,
                "identifier": active_user["username"],
                "password": active_user["password"],
                "next": "https://malicious.example.com/phish",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")
        assert "malicious.example.com" not in resp.headers["Location"]

    def test_html_form_logout_and_html_change_password_flows(
        self, client: FlaskClient, active_user: dict
    ):
        csrf_token = extract_csrf_token(client.get("/auth/login").get_data(as_text=True))

        # Invalid HTML login renders error banner and 401 status
        bad_login = client.post(
            "/auth/login",
            data={
                "csrf_token": csrf_token,
                "identifier": active_user["username"],
                "password": "WrongPassword999",
            },
        )
        assert bad_login.status_code == 401
        assert GENERIC_AUTH_ERROR_MESSAGE in bad_login.get_data(as_text=True)

        # Valid HTML login
        client.post(
            "/auth/login",
            data={
                "csrf_token": csrf_token,
                "identifier": active_user["username"],
                "password": active_user["password"],
                "next": "/",
            },
        )
        # Visiting /auth/login while already authenticated redirects to /
        already_logged_in = client.get("/auth/login", follow_redirects=False)
        assert already_logged_in.status_code == 302

        landing_html = client.get("/").get_data(as_text=True)
        landing_csrf = extract_csrf_token(landing_html)

        # HTML change-password with mismatched confirmation returns 400
        bad_pw_change = client.post(
            "/auth/change-password",
            data={
                "csrf_token": landing_csrf,
                "current_password": active_user["password"],
                "new_password": "NewValidPass123",
                "confirm_password": "DifferentPass123",
            },
        )
        assert bad_pw_change.status_code == 400

        # HTML change-password success
        good_pw_change = client.post(
            "/auth/change-password",
            data={
                "csrf_token": landing_csrf,
                "current_password": active_user["password"],
                "new_password": "NewValidPass123",
                "confirm_password": "NewValidPass123",
            },
            follow_redirects=True,
        )
        assert good_pw_change.status_code == 200
        assert "Password updated successfully." in good_pw_change.get_data(as_text=True)

        # HTML form logout
        logout_csrf = extract_csrf_token(good_pw_change.get_data(as_text=True))
        logout_resp = client.post(
            "/auth/logout",
            data={"csrf_token": logout_csrf},
            follow_redirects=True,
        )
        assert logout_resp.status_code == 200
        assert "You have been signed out." in logout_resp.get_data(as_text=True)

    def test_api_change_password_validation_failures(
        self, client: FlaskClient, active_user: dict
    ):
        csrf_token = extract_csrf_token(client.get("/auth/login").get_data(as_text=True))
        headers = {"X-CSRFToken": csrf_token}

        client.post(
            "/api/v1/auth/login",
            json={"identifier": active_user["username"], "password": active_user["password"]},
            headers=headers,
        )

        # Missing current_password
        r1 = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "", "new_password": "NewValidPass123"},
            headers=headers,
        )
        assert r1.status_code == 400

        # Wrong current_password
        r2 = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "WrongOldPassword1", "new_password": "NewValidPass123"},
            headers=headers,
        )
        assert r2.status_code == 401
        assert r2.get_json()["error"]["code"] == "INVALID_CURRENT_PASSWORD"

        # Same as current password
        r3 = client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": active_user["password"],
                "new_password": active_user["password"],
                "confirm_password": active_user["password"],
            },
            headers=headers,
        )
        assert r3.status_code == 400

    def test_cli_create_user_validation_failure_exits_nonzero(self, app: Flask):
        runner = app.test_cli_runner()
        result = runner.invoke(
            args=[
                "create-user",
                "--username",
                "x",
                "--email",
                "not-an-email",
                "--full-name",
                "X",
                "--role",
                "STAFF",
                "--password",
                "weak",
            ]
        )
        assert result.exit_code != 0
        assert "Error [VALIDATION_ERROR]" in result.output

