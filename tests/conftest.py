"""Pytest fixtures for Phase 1 testing against the isolated PostgreSQL test database."""

from __future__ import annotations

import re
import pytest
from flask import Flask
from flask.testing import FlaskClient
from flask_migrate import upgrade as alembic_upgrade
from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.services.auth_service import AuthService


def extract_csrf_token(html_text: str) -> str:
    """Extract the CSRF token from rendered HTML meta tag or hidden form input."""
    meta_match = re.search(
        r'<meta\s+name="csrf-token"\s+content="([^"]+)"',
        html_text,
    )
    if meta_match:
        return meta_match.group(1)
    input_match = re.search(
        r'name="csrf_token"\s+value="([^"]+)"',
        html_text,
    )
    if input_match:
        return input_match.group(1)
    raise AssertionError("CSRF token not found in HTML response.")


@pytest.fixture(scope="session")
def app() -> Flask:
    """Create the Flask application configured for the isolated PostgreSQL test database."""
    test_app = create_app("testing")

    with test_app.app_context():
        # Apply Alembic migrations to ensure sanchay_ai_test matches migration head
        alembic_upgrade()

    yield test_app

    with test_app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture(autouse=True)
def clean_db(app: Flask):
    """Truncate Phase 1, Phase 2, Phase 3 & Phase 4 tables before each test to guarantee deterministic isolation."""
    with app.app_context():
        db.session.execute(
            text(
                "TRUNCATE TABLE notifications, transfer_status_logs, transfer_requests, "
                "sale_returns, sale_items, sales, "
                "stock_movements, inventory_batches, shop_products, products, "
                "audit_logs, user_shop_assignments, shops, users, organizations "
                "RESTART IDENTITY CASCADE;"
            )
        )
        db.session.commit()
    yield
    with app.app_context():
        db.session.rollback()


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    """Return a Flask test client with CSRF enabled."""
    return app.test_client()


@pytest.fixture()
def active_user(app: Flask):
    """Create and return an active SUPER_ADMIN user in the test database."""
    with app.app_context():
        user = AuthService.create_user(
            username="admin_rajesh",
            email="rajesh@sanchay.local",
            password="ValidPassword123",
            full_name="Rajesh Sharma",
            role="SUPER_ADMIN",
            phone="9876543210",
            is_active=True,
        )
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "password": "ValidPassword123",
            "full_name": user.full_name,
            "role": user.role,
        }


@pytest.fixture()
def inactive_user(app: Flask):
    """Create and return a deactivated SHOP_OWNER user in the test database."""
    with app.app_context():
        user = AuthService.create_user(
            username="inactive_owner",
            email="inactive@sanchay.local",
            password="ValidPassword123",
            full_name="Former Employee",
            role="SHOP_OWNER",
            is_active=False,
        )
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "password": "ValidPassword123",
        }
