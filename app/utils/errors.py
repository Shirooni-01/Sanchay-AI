"""Standardized domain exceptions and Flask error handlers (docs/ERROR_HANDLING.md)."""

from __future__ import annotations

import uuid
from typing import Any

from flask import Flask, jsonify, render_template, request
from flask_wtf.csrf import CSRFError
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from werkzeug.exceptions import HTTPException

from app.extensions import db


class DomainError(Exception):
    """Base class for all structured application domain errors."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}


class ValidationError(DomainError):
    """Raised when user input fails server-side validation."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            http_status=400,
            details=details,
        )


class AuthenticationError(DomainError):
    """Raised on invalid credentials or unauthenticated access."""

    def __init__(
        self,
        message: str = "Invalid username/email or password.",
        code: str = "INVALID_CREDENTIALS",
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            http_status=401,
        )


class AuthorizationError(DomainError):
    """Raised when an authenticated user lacks role or shop permission."""

    def __init__(
        self,
        message: str = "You do not have permission to perform this action.",
        code: str = "INSUFFICIENT_ROLE_PERMISSION",
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            http_status=403,
        )


class ResourceNotFoundError(DomainError):
    """Raised when a requested entity does not exist."""

    def __init__(self, message: str = "Requested resource was not found.") -> None:
        super().__init__(
            code="RESOURCE_NOT_FOUND",
            message=message,
            http_status=404,
        )


class DuplicateResourceError(DomainError):
    """Raised when creating a record violates a uniqueness constraint."""

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
        code: str = "DUPLICATE_RESOURCE",
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            http_status=409,
            details=details,
        )


class InsufficientStockError(DomainError):
    """Raised when an operation attempts to deduct more stock than is available."""

    def __init__(
        self,
        message: str = "Requested quantity exceeds available stock.",
        code: str = "INSUFFICIENT_AVAILABLE_STOCK",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            http_status=409,
            details=details,
        )


class BatchExpiredError(DomainError):
    """Raised when attempting a sellable stock operation on an expired batch."""

    def __init__(
        self,
        message: str = "Operation cannot be performed because the batch is expired.",
        code: str = "BATCH_EXPIRED",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            http_status=409,
            details=details,
        )


class ResourceConflictError(DomainError):
    """Raised when a domain invariant or state constraint prevents the requested mutation."""

    def __init__(
        self,
        message: str,
        code: str = "RESOURCE_CONFLICT",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            http_status=409,
            details=details,
        )


class DatabaseConflictError(DomainError):
    """Raised when a PostgreSQL connection, lock, or integrity failure occurs."""

    def __init__(
        self,
        message: str = "A database error occurred while processing your request.",
        code: str = "DATABASE_CONFLICT_OR_UNAVAILABLE",
        http_status: int = 503,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            http_status=http_status,
        )


def wants_json_response() -> bool:
    """Return True if the request targets the REST API or explicitly prefers JSON."""
    if request.path.startswith("/api/"):
        return True
    if request.is_json:
        return True
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return (
        best == "application/json"
        and request.accept_mimetypes[best] > request.accept_mimetypes["text/html"]
    )


def build_error_payload(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Construct the canonical JSON error envelope."""
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id or f"req_{uuid.uuid4().hex[:8]}",
        },
    }


def register_error_handlers(app: Flask) -> None:
    """Register centralized exception handlers on the Flask application."""

    @app.errorhandler(DomainError)
    def handle_domain_error(exc: DomainError):
        db.session.rollback()
        if wants_json_response():
            return (
                jsonify(build_error_payload(exc.code, exc.message, exc.details)),
                exc.http_status,
            )
        return (
            render_template(
                "errors/error.html",
                status_code=exc.http_status,
                error_code=exc.code,
                error_message=exc.message,
            ),
            exc.http_status,
        )

    @app.errorhandler(CSRFError)
    def handle_csrf_error(exc: CSRFError):
        db.session.rollback()
        message = "Security token (CSRF) is missing or expired. Please refresh and try again."
        if wants_json_response():
            return (
                jsonify(
                    build_error_payload(
                        code="CSRF_TOKEN_MISSING_OR_INVALID",
                        message=message,
                        details={"reason": exc.description},
                    )
                ),
                400,
            )
        return (
            render_template(
                "errors/error.html",
                status_code=400,
                error_code="CSRF_TOKEN_MISSING_OR_INVALID",
                error_message=message,
            ),
            400,
        )

    @app.errorhandler(OperationalError)
    def handle_db_operational_error(exc: OperationalError):
        db.session.rollback()
        app.logger.error("PostgreSQL OperationalError encountered: %s", exc.__class__.__name__)
        message = "Database connection or operation failed. Verify that PostgreSQL is running and accessible."
        if wants_json_response():
            return (
                jsonify(
                    build_error_payload(
                        code="DATABASE_UNAVAILABLE",
                        message=message,
                    )
                ),
                503,
            )
        return (
            render_template(
                "errors/error.html",
                status_code=503,
                error_code="DATABASE_UNAVAILABLE",
                error_message=message,
            ),
            503,
        )

    @app.errorhandler(SQLAlchemyError)
    def handle_sqlalchemy_error(exc: SQLAlchemyError):
        db.session.rollback()
        app.logger.error("SQLAlchemyError encountered: %s", exc.__class__.__name__)
        message = "A database error prevented the operation from completing."
        if wants_json_response():
            return (
                jsonify(
                    build_error_payload(
                        code="DATABASE_ERROR",
                        message=message,
                    )
                ),
                500,
            )
        return (
            render_template(
                "errors/error.html",
                status_code=500,
                error_code="DATABASE_ERROR",
                error_message=message,
            ),
            500,
        )

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        status_code = exc.code or 500
        code_map = {
            400: "BAD_REQUEST",
            401: "AUTHENTICATION_REQUIRED",
            403: "FORBIDDEN",
            404: "RESOURCE_NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
        }
        error_code = code_map.get(status_code, f"HTTP_{status_code}")
        message = exc.description or "Request could not be completed."
        if wants_json_response():
            return jsonify(build_error_payload(error_code, message)), status_code
        return (
            render_template(
                "errors/error.html",
                status_code=status_code,
                error_code=error_code,
                error_message=message,
            ),
            status_code,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_exception(exc: Exception):
        db.session.rollback()
        request_id = f"req_{uuid.uuid4().hex[:8]}"
        app.logger.exception("Unhandled application exception [%s]", request_id)
        message = "An unexpected internal server error occurred."
        if wants_json_response():
            return (
                jsonify(
                    build_error_payload(
                        code="INTERNAL_SERVER_ERROR",
                        message=message,
                        request_id=request_id,
                    )
                ),
                500,
            )
        return (
            render_template(
                "errors/error.html",
                status_code=500,
                error_code="INTERNAL_SERVER_ERROR",
                error_message=message,
            ),
            500,
        )
