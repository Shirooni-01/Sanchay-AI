"""Flask CLI commands for user/organization provisioning and PostgreSQL connectivity checks.

Provides a safe, documented mechanism to create an initial development or test user
and optional organization without hardcoding credentials into source code (RULE-11, RULE-21).
"""

from __future__ import annotations

import click
from flask import Flask
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models.organization import Organization
from app.services.auth_service import AuthService
from app.services.org_service import OrganizationService
from app.utils.errors import DomainError


def register_cli_commands(app: Flask) -> None:
    """Attach custom CLI commands to the Flask application."""

    @app.cli.command("create-user")
    @click.option("--username", required=True, help="Unique login username (lowercase).")
    @click.option("--email", required=True, help="Unique user email address.")
    @click.option("--full-name", required=True, help="Full display name.")
    @click.option(
        "--role",
        default="SUPER_ADMIN",
        show_default=True,
        type=click.Choice(["SUPER_ADMIN", "SHOP_OWNER", "STAFF"], case_sensitive=False),
        help="User role.",
    )
    @click.option("--phone", default=None, help="Optional contact phone number.")
    @click.option(
        "--org-code",
        default=None,
        help="Optional organization code to link or bootstrap.",
    )
    @click.option(
        "--org-name",
        default=None,
        help="Optional organization name when bootstrapping a new organization.",
    )
    @click.option(
        "--password",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
        help="User password (min 8 chars, upper, lower, digit).",
    )
    def create_user_command(
        username: str,
        email: str,
        full_name: str,
        role: str,
        phone: str | None,
        org_code: str | None,
        org_name: str | None,
        password: str,
    ) -> None:
        """Create a new user (and optionally link/create an organization) via the command line."""
        try:
            existing_org_id: int | None = None
            if org_code:
                existing_org = (
                    db.session.query(Organization)
                    .filter(func.upper(Organization.code) == org_code.strip().upper())
                    .first()
                )
                if existing_org is not None:
                    existing_org_id = existing_org.id

            user = AuthService.create_user(
                username=username,
                email=email,
                password=password,
                full_name=full_name,
                role=role,
                phone=phone,
                is_active=True,
                organization_id=existing_org_id,
            )

            if org_code and existing_org_id is None and user.role == "SUPER_ADMIN":
                org = OrganizationService.create_organization(
                    actor=user,
                    payload={
                        "name": org_name or f"{full_name} Organization",
                        "code": org_code.strip().upper(),
                        "contact_email": email,
                        "contact_phone": phone,
                    },
                    attach_actor=True,
                )
                click.secho(
                    f"Created and linked organization '{org.name}' (code={org.code}, id={org.id}).",
                    fg="cyan",
                )

            click.secho(
                f"Successfully created user '{user.username}' (id={user.id}, role={user.role}, org_id={user.organization_id}).",
                fg="green",
            )
        except DomainError as exc:
            click.secho(f"Error [{exc.code}]: {exc.message}", fg="red", err=True)
            if exc.details:
                for field, msg in exc.details.items():
                    click.secho(f"  - {field}: {msg}", fg="yellow", err=True)
            raise SystemExit(1) from exc

    @app.cli.command("db-check")
    def db_check_command() -> None:
        """Verify PostgreSQL connectivity and print server version."""
        try:
            version_row = db.session.execute(text("SELECT version();")).scalar()
            click.secho("PostgreSQL connection verified successfully.", fg="green")
            click.echo(f"Server: {version_row}")
        except SQLAlchemyError as exc:
            db.session.rollback()
            click.secho(
                f"PostgreSQL connection failed ({exc.__class__.__name__}). "
                "Verify DATABASE_URL in .env and ensure PostgreSQL is running.",
                fg="red",
                err=True,
            )
            raise SystemExit(1) from exc
