"""Server-side input validation helpers for Phases 1, 2, 3, and 4."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import urljoin, urlparse

from flask import request

from app.utils.errors import ValidationError

USERNAME_REGEX = re.compile(r"^[a-z0-9_.-]{3,64}$")
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_REGEX = re.compile(r"^[A-Z0-9_-]{2,32}$")
PINCODE_REGEX = re.compile(r"^[A-Za-z0-9 -]{4,12}$")
PHONE_REGEX = re.compile(r"^[0-9+\-\s()]{7,20}$")
SKU_REGEX = re.compile(r"^[A-Z0-9_.-]{2,64}$")
BATCH_CODE_REGEX = re.compile(r"^[A-Za-z0-9_.-]{2,64}$")

VALID_ROLES = {"SUPER_ADMIN", "SHOP_OWNER", "STAFF"}
VALID_ASSIGNMENT_ROLES = {"SHOP_OWNER", "STAFF"}
VALID_ORG_STATUSES = {"ACTIVE", "INACTIVE"}
VALID_SHOP_STATUSES = {"ACTIVE", "INACTIVE", "SUSPENDED"}
VALID_PRODUCT_UNITS = {"pcs", "pack", "kg", "g", "litre", "ml", "box"}
VALID_BATCH_STATUSES = {"ACTIVE", "DEPLETED", "EXPIRED", "ARCHIVED"}
VALID_ADJUSTMENT_TYPES = {
    "ADJUSTMENT_ADD",
    "ADJUSTMENT_SUBTRACT",
    "MARK_DAMAGED",
    "MARK_EXPIRED",
}
ADJUSTMENT_TYPE_ALIASES = {
    "INCREASE": "ADJUSTMENT_ADD",
    "ADD": "ADJUSTMENT_ADD",
    "CORRECTION_ADD": "ADJUSTMENT_ADD",
    "DECREASE": "ADJUSTMENT_SUBTRACT",
    "SUBTRACT": "ADJUSTMENT_SUBTRACT",
    "SHRINKAGE": "ADJUSTMENT_SUBTRACT",
    "CORRECTION_SUBTRACT": "ADJUSTMENT_SUBTRACT",
    "DAMAGE": "MARK_DAMAGED",
    "DAMAGED": "MARK_DAMAGED",
    "EXPIRY_REMOVAL": "MARK_EXPIRED",
    "EXPIRED": "MARK_EXPIRED",
}


def validate_login_input(identifier: str | None, password: str | None) -> tuple[str, str]:
    """Validate login form or JSON payload inputs server-side."""
    errors: dict[str, str] = {}

    cleaned_identifier = (identifier or "").strip()
    raw_password = password if isinstance(password, str) else ""

    if not cleaned_identifier:
        errors["identifier"] = "Username or email is required."
    elif len(cleaned_identifier) > 150:
        errors["identifier"] = "Username or email must not exceed 150 characters."

    if not raw_password:
        errors["password"] = "Password is required."
    elif len(raw_password) > 256:
        errors["password"] = "Password must not exceed 256 characters."

    if errors:
        raise ValidationError(
            message="Please provide a valid username/email and password.",
            details=errors,
        )

    return cleaned_identifier.lower(), raw_password


def validate_password_strength(password: str | None, field_name: str = "password") -> str:
    """Enforce password complexity policy (>= 8 chars, upper, lower, digit)."""
    if not password or not isinstance(password, str):
        raise ValidationError(
            message="Password is required.",
            details={field_name: "Password cannot be empty."},
        )

    if len(password) < 8:
        raise ValidationError(
            message="Password must be at least 8 characters long.",
            details={field_name: "Minimum length is 8 characters."},
        )

    if len(password) > 128:
        raise ValidationError(
            message="Password must not exceed 128 characters.",
            details={field_name: "Maximum length is 128 characters."},
        )

    has_upper = any(ch.isupper() for ch in password)
    has_lower = any(ch.islower() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)

    if not (has_upper and has_lower and has_digit):
        raise ValidationError(
            message="Password must contain at least one uppercase letter, one lowercase letter, and one digit.",
            details={
                field_name: "Include at least one uppercase letter, one lowercase letter, and one digit."
            },
        )

    return password


def validate_user_creation_input(
    username: str | None,
    email: str | None,
    password: str | None,
    full_name: str | None,
    role: str = "STAFF",
    phone: str | None = None,
) -> dict[str, str | None]:
    """Validate fields required to create a user account."""
    errors: dict[str, str] = {}

    clean_username = (username or "").strip().lower()
    if not clean_username or not USERNAME_REGEX.match(clean_username):
        errors["username"] = (
            "Username must be 3-64 characters and contain only lowercase letters, digits, '_', '.', or '-'."
        )

    clean_email = (email or "").strip().lower()
    if not clean_email or len(clean_email) > 150 or not EMAIL_REGEX.match(clean_email):
        errors["email"] = "A valid email address (max 150 characters) is required."

    clean_full_name = (full_name or "").strip()
    if not clean_full_name or len(clean_full_name) < 2 or len(clean_full_name) > 120:
        errors["full_name"] = "Full name is required (2-120 characters)."

    clean_role = (role or "STAFF").strip().upper()
    if clean_role not in VALID_ROLES:
        errors["role"] = f"Role must be one of: {', '.join(sorted(VALID_ROLES))}."

    clean_phone = (phone or "").strip() or None
    if clean_phone and len(clean_phone) > 20:
        errors["phone"] = "Phone number must not exceed 20 characters."

    if errors:
        raise ValidationError(
            message="User validation failed.",
            details=errors,
        )

    validated_password = validate_password_strength(password, field_name="password")

    return {
        "username": clean_username,
        "email": clean_email,
        "password": validated_password,
        "full_name": clean_full_name,
        "role": clean_role,
        "phone": clean_phone,
    }


def _parse_int_threshold(
    value: Any,
    field_name: str,
    min_value: int,
    max_value: int = 100000,
    default: int | None = None,
) -> int:
    if value is None or value == "":
        if default is not None:
            return default
        raise ValidationError(
            message=f"{field_name} is required.",
            details={field_name: f"Must be an integer >= {min_value}."},
        )
    if isinstance(value, bool):
        raise ValidationError(
            message=f"Invalid {field_name}.",
            details={field_name: "Must be an integer."},
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            message=f"Invalid {field_name}.",
            details={field_name: "Must be an integer."},
        ) from exc

    if parsed < min_value or parsed > max_value:
        raise ValidationError(
            message=f"{field_name} must be between {min_value} and {max_value}.",
            details={field_name: f"Must be between {min_value} and {max_value}."},
        )
    return parsed


def _parse_coordinate(
    value: Any,
    field_name: str,
    min_bound: float,
    max_bound: float,
) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValidationError(
            message=f"Invalid {field_name}.",
            details={field_name: f"{field_name} must be a valid coordinate number."},
        )
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError(
            message=f"Invalid {field_name}.",
            details={field_name: f"{field_name} must be a valid decimal coordinate."},
        ) from exc

    if not dec.is_finite() or dec < Decimal(str(min_bound)) or dec > Decimal(str(max_bound)):
        raise ValidationError(
            message=f"{field_name} must be between {min_bound} and {max_bound}.",
            details={field_name: f"Coordinate must be between {min_bound} and {max_bound}."},
        )
    return dec.quantize(Decimal("0.000001"))


def _parse_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValidationError(
        message=f"Invalid boolean value for {field_name}.",
        details={field_name: "Must be true or false."},
    )


def validate_organization_create_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate fields for creating an Organization."""
    errors: dict[str, str] = {}

    name = str(payload.get("name") or "").strip()
    if not name or len(name) < 2 or len(name) > 150:
        errors["name"] = "Organization name is required (2-150 characters)."

    code = str(payload.get("code") or "").strip().upper()
    if not code or not CODE_REGEX.match(code):
        errors["code"] = (
            "Organization code must be 2-32 characters using uppercase letters, digits, '_', or '-'."
        )

    contact_email = str(payload.get("contact_email") or "").strip().lower() or None
    if contact_email and (len(contact_email) > 150 or not EMAIL_REGEX.match(contact_email)):
        errors["contact_email"] = "Provide a valid contact email address (max 150 characters)."

    contact_phone = str(payload.get("contact_phone") or "").strip() or None
    if contact_phone and len(contact_phone) > 20:
        errors["contact_phone"] = "Contact phone must not exceed 20 characters."

    address = str(payload.get("address") or "").strip() or None
    if address and len(address) > 1000:
        errors["address"] = "Address must not exceed 1000 characters."

    status = str(payload.get("status") or "ACTIVE").strip().upper()
    if status not in VALID_ORG_STATUSES:
        errors["status"] = f"Status must be one of: {', '.join(sorted(VALID_ORG_STATUSES))}."

    if errors:
        raise ValidationError(message="Organization validation failed.", details=errors)

    low_stock_threshold = _parse_int_threshold(
        payload.get("low_stock_default_threshold"),
        field_name="low_stock_default_threshold",
        min_value=0,
        default=10,
    )
    near_expiry_threshold = _parse_int_threshold(
        payload.get("near_expiry_days_threshold"),
        field_name="near_expiry_days_threshold",
        min_value=1,
        default=15,
    )

    return {
        "name": name,
        "code": code,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
        "address": address,
        "low_stock_default_threshold": low_stock_threshold,
        "near_expiry_days_threshold": near_expiry_threshold,
        "status": status,
    }


def validate_organization_update_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate partial update fields for an Organization."""
    if not payload or not isinstance(payload, dict):
        raise ValidationError(message="At least one field must be provided to update organization.")

    errors: dict[str, str] = {}
    cleaned: dict[str, Any] = {}

    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name or len(name) < 2 or len(name) > 150:
            errors["name"] = "Organization name must be 2-150 characters."
        else:
            cleaned["name"] = name

    if "code" in payload:
        code = str(payload.get("code") or "").strip().upper()
        if not code or not CODE_REGEX.match(code):
            errors["code"] = (
                "Organization code must be 2-32 characters using uppercase letters, digits, '_', or '-'."
            )
        else:
            cleaned["code"] = code

    if "contact_email" in payload:
        email = str(payload.get("contact_email") or "").strip().lower() or None
        if email and (len(email) > 150 or not EMAIL_REGEX.match(email)):
            errors["contact_email"] = "Provide a valid contact email address (max 150 characters)."
        else:
            cleaned["contact_email"] = email

    if "contact_phone" in payload:
        phone = str(payload.get("contact_phone") or "").strip() or None
        if phone and len(phone) > 20:
            errors["contact_phone"] = "Contact phone must not exceed 20 characters."
        else:
            cleaned["contact_phone"] = phone

    if "address" in payload:
        address = str(payload.get("address") or "").strip() or None
        if address and len(address) > 1000:
            errors["address"] = "Address must not exceed 1000 characters."
        else:
            cleaned["address"] = address

    if "status" in payload:
        status = str(payload.get("status") or "").strip().upper()
        if status not in VALID_ORG_STATUSES:
            errors["status"] = f"Status must be one of: {', '.join(sorted(VALID_ORG_STATUSES))}."
        else:
            cleaned["status"] = status

    if errors:
        raise ValidationError(message="Organization update validation failed.", details=errors)

    if "low_stock_default_threshold" in payload:
        cleaned["low_stock_default_threshold"] = _parse_int_threshold(
            payload.get("low_stock_default_threshold"),
            field_name="low_stock_default_threshold",
            min_value=0,
        )

    if "near_expiry_days_threshold" in payload:
        cleaned["near_expiry_days_threshold"] = _parse_int_threshold(
            payload.get("near_expiry_days_threshold"),
            field_name="near_expiry_days_threshold",
            min_value=1,
        )

    if not cleaned:
        raise ValidationError(message="No valid organization fields provided for update.")

    return cleaned


def validate_shop_create_input(
    payload: dict[str, Any],
    default_low_stock: int = 10,
    default_near_expiry: int = 15,
) -> dict[str, Any]:
    """Validate fields for creating a Shop within an Organization."""
    errors: dict[str, str] = {}

    name = str(payload.get("name") or "").strip()
    if not name or len(name) < 2 or len(name) > 150:
        errors["name"] = "Shop name is required (2-150 characters)."

    code = str(payload.get("code") or "").strip().upper()
    if not code or not CODE_REGEX.match(code):
        errors["code"] = (
            "Shop code is required (2-32 characters using uppercase letters, digits, '_', or '-')."
        )

    address = str(payload.get("address") or "").strip()
    if not address or len(address) < 3 or len(address) > 1000:
        errors["address"] = "Shop address is required (3-1000 characters)."

    city = str(payload.get("city") or "").strip()
    if not city or len(city) < 2 or len(city) > 80:
        errors["city"] = "City is required (2-80 characters)."

    pincode = str(payload.get("pincode") or "").strip()
    if not pincode or not PINCODE_REGEX.match(pincode):
        errors["pincode"] = "Valid pincode/postal code is required (4-12 characters)."

    contact_phone = str(payload.get("contact_phone") or "").strip()
    if not contact_phone or len(contact_phone) < 5 or len(contact_phone) > 20:
        errors["contact_phone"] = "Contact phone is required (5-20 characters)."

    contact_email = str(payload.get("contact_email") or "").strip().lower() or None
    if contact_email and (len(contact_email) > 150 or not EMAIL_REGEX.match(contact_email)):
        errors["contact_email"] = "Provide a valid contact email address (max 150 characters)."

    status = str(payload.get("status") or "ACTIVE").strip().upper()
    if status not in VALID_SHOP_STATUSES:
        errors["status"] = f"Shop status must be one of: {', '.join(sorted(VALID_SHOP_STATUSES))}."

    if errors:
        raise ValidationError(message="Shop validation failed.", details=errors)

    latitude = _parse_coordinate(payload.get("latitude"), "latitude", -90.0, 90.0)
    longitude = _parse_coordinate(payload.get("longitude"), "longitude", -180.0, 180.0)
    if (latitude is None) != (longitude is None):
        raise ValidationError(
            message="Both latitude and longitude must be provided together, or both omitted.",
            details={"coordinates": "Provide both latitude and longitude."},
        )

    low_stock_threshold = _parse_int_threshold(
        payload.get("low_stock_default_threshold"),
        field_name="low_stock_default_threshold",
        min_value=0,
        default=default_low_stock,
    )
    near_expiry_threshold = _parse_int_threshold(
        payload.get("near_expiry_days_threshold"),
        field_name="near_expiry_days_threshold",
        min_value=1,
        default=default_near_expiry,
    )

    return {
        "name": name,
        "code": code,
        "address": address,
        "city": city,
        "pincode": pincode,
        "latitude": latitude,
        "longitude": longitude,
        "contact_phone": contact_phone,
        "contact_email": contact_email,
        "low_stock_default_threshold": low_stock_threshold,
        "near_expiry_days_threshold": near_expiry_threshold,
        "status": status,
    }


def validate_shop_update_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate partial update fields for a Shop."""
    if not payload or not isinstance(payload, dict):
        raise ValidationError(message="At least one field must be provided to update the shop.")

    errors: dict[str, str] = {}
    cleaned: dict[str, Any] = {}

    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name or len(name) < 2 or len(name) > 150:
            errors["name"] = "Shop name must be 2-150 characters."
        else:
            cleaned["name"] = name

    if "code" in payload:
        code = str(payload.get("code") or "").strip().upper()
        if not code or not CODE_REGEX.match(code):
            errors["code"] = (
                "Shop code must be 2-32 characters using uppercase letters, digits, '_', or '-'."
            )
        else:
            cleaned["code"] = code

    if "address" in payload:
        address = str(payload.get("address") or "").strip()
        if not address or len(address) < 3 or len(address) > 1000:
            errors["address"] = "Shop address must be 3-1000 characters."
        else:
            cleaned["address"] = address

    if "city" in payload:
        city = str(payload.get("city") or "").strip()
        if not city or len(city) < 2 or len(city) > 80:
            errors["city"] = "City must be 2-80 characters."
        else:
            cleaned["city"] = city

    if "pincode" in payload:
        pincode = str(payload.get("pincode") or "").strip()
        if not pincode or not PINCODE_REGEX.match(pincode):
            errors["pincode"] = "Valid pincode/postal code is required (4-12 characters)."
        else:
            cleaned["pincode"] = pincode

    if "contact_phone" in payload:
        contact_phone = str(payload.get("contact_phone") or "").strip()
        if not contact_phone or len(contact_phone) < 5 or len(contact_phone) > 20:
            errors["contact_phone"] = "Contact phone must be 5-20 characters."
        else:
            cleaned["contact_phone"] = contact_phone

    if "contact_email" in payload:
        contact_email = str(payload.get("contact_email") or "").strip().lower() or None
        if contact_email and (len(contact_email) > 150 or not EMAIL_REGEX.match(contact_email)):
            errors["contact_email"] = "Provide a valid contact email address (max 150 characters)."
        else:
            cleaned["contact_email"] = contact_email

    if "status" in payload:
        status = str(payload.get("status") or "").strip().upper()
        if status not in VALID_SHOP_STATUSES:
            errors["status"] = f"Shop status must be one of: {', '.join(sorted(VALID_SHOP_STATUSES))}."
        else:
            cleaned["status"] = status

    if errors:
        raise ValidationError(message="Shop update validation failed.", details=errors)

    if "latitude" in payload:
        cleaned["latitude"] = _parse_coordinate(payload.get("latitude"), "latitude", -90.0, 90.0)
    if "longitude" in payload:
        cleaned["longitude"] = _parse_coordinate(payload.get("longitude"), "longitude", -180.0, 180.0)

    if "low_stock_default_threshold" in payload:
        cleaned["low_stock_default_threshold"] = _parse_int_threshold(
            payload.get("low_stock_default_threshold"),
            field_name="low_stock_default_threshold",
            min_value=0,
        )

    if "near_expiry_days_threshold" in payload:
        cleaned["near_expiry_days_threshold"] = _parse_int_threshold(
            payload.get("near_expiry_days_threshold"),
            field_name="near_expiry_days_threshold",
            min_value=1,
        )

    if not cleaned:
        raise ValidationError(message="No valid shop fields provided for update.")

    return cleaned


def validate_user_update_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate partial update fields for a User account."""
    if not payload or not isinstance(payload, dict):
        raise ValidationError(message="At least one field must be provided to update the user.")

    errors: dict[str, str] = {}
    cleaned: dict[str, Any] = {}

    if "full_name" in payload:
        full_name = str(payload.get("full_name") or "").strip()
        if not full_name or len(full_name) < 2 or len(full_name) > 120:
            errors["full_name"] = "Full name must be 2-120 characters."
        else:
            cleaned["full_name"] = full_name

    if "email" in payload:
        email = str(payload.get("email") or "").strip().lower()
        if not email or len(email) > 150 or not EMAIL_REGEX.match(email):
            errors["email"] = "Provide a valid email address (max 150 characters)."
        else:
            cleaned["email"] = email

    if "phone" in payload:
        phone = str(payload.get("phone") or "").strip() or None
        if phone and len(phone) > 20:
            errors["phone"] = "Phone number must not exceed 20 characters."
        else:
            cleaned["phone"] = phone

    if "role" in payload:
        role = str(payload.get("role") or "").strip().upper()
        if role not in VALID_ROLES:
            errors["role"] = f"Role must be one of: {', '.join(sorted(VALID_ROLES))}."
        else:
            cleaned["role"] = role

    if errors:
        raise ValidationError(message="User update validation failed.", details=errors)

    if "is_active" in payload:
        cleaned["is_active"] = _parse_bool(payload.get("is_active"), "is_active")

    if not cleaned:
        raise ValidationError(message="No valid user fields provided for update.")

    return cleaned


def validate_shop_assignment_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate input for assigning or updating a UserShopAssignment."""
    if not payload or not isinstance(payload, dict):
        raise ValidationError(message="Assignment payload is required.")

    raw_user_id = payload.get("user_id")
    if raw_user_id is None or isinstance(raw_user_id, bool):
        raise ValidationError(
            message="Valid user_id is required.",
            details={"user_id": "user_id must be a positive integer."},
        )
    try:
        user_id = int(raw_user_id)
        if user_id <= 0:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ValidationError(
            message="Valid user_id is required.",
            details={"user_id": "user_id must be a positive integer."},
        ) from exc

    assignment_role = str(payload.get("assignment_role") or "").strip().upper()
    if assignment_role not in VALID_ASSIGNMENT_ROLES:
        raise ValidationError(
            message=f"assignment_role must be one of: {', '.join(sorted(VALID_ASSIGNMENT_ROLES))}.",
            details={"assignment_role": "Must be SHOP_OWNER or STAFF."},
        )

    is_primary = (
        _parse_bool(payload.get("is_primary"), "is_primary")
        if "is_primary" in payload and payload.get("is_primary") is not None
        else True
    )
    is_active = (
        _parse_bool(payload.get("is_active"), "is_active")
        if "is_active" in payload and payload.get("is_active") is not None
        else True
    )

    return {
        "user_id": user_id,
        "assignment_role": assignment_role,
        "is_primary": is_primary,
        "is_active": is_active,
    }


def is_safe_redirect_url(target: str | None) -> bool:
    """Prevent open-redirect vulnerabilities on post-login redirects."""
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


# ============================================================================
# Phase 3: Product Catalog, Batch Receipt & Inventory Adjustment Validators
# ============================================================================


def _parse_money(
    value: Any,
    field_name: str,
    allow_zero: bool = True,
    default: Decimal | None = None,
) -> Decimal:
    """Parse and validate a non-negative monetary amount rounded to 2 decimal places."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is not None:
            return default
        raise ValidationError(
            message=f"{field_name} is required.",
            details={field_name: "Provide a valid non-negative monetary amount."},
        )
    if isinstance(value, bool):
        raise ValidationError(
            message=f"Invalid {field_name}.",
            details={field_name: "Must be a valid numeric amount."},
        )
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError(
            message=f"Invalid {field_name}.",
            details={field_name: "Must be a valid decimal number."},
        ) from exc

    if not dec.is_finite():
        raise ValidationError(
            message=f"Invalid {field_name}.",
            details={field_name: "Must be a finite decimal number."},
        )

    quantized = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if allow_zero and quantized < Decimal("0.00"):
        raise ValidationError(
            message=f"{field_name} cannot be negative.",
            details={field_name: "Amount must be >= 0.00."},
        )
    if not allow_zero and quantized <= Decimal("0.00"):
        raise ValidationError(
            message=f"{field_name} must be greater than zero.",
            details={field_name: "Amount must be > 0.00."},
        )
    if quantized > Decimal("9999999999.99"):
        raise ValidationError(
            message=f"{field_name} exceeds maximum allowed value.",
            details={field_name: "Amount is too large."},
        )
    return quantized


def _parse_date(value: Any, field_name: str, required: bool = False) -> date | None:
    """Parse an ISO-8601 date string (YYYY-MM-DD) or date object."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationError(
                message=f"{field_name} is required.",
                details={field_name: "Provide a valid date in YYYY-MM-DD format."},
            )
        return None
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    if isinstance(value, str):
        raw = value.strip()
        try:
            return date.fromisoformat(raw[:10])
        except ValueError as exc:
            raise ValidationError(
                message=f"Invalid {field_name} format.",
                details={field_name: "Date must be in YYYY-MM-DD format."},
            ) from exc
    raise ValidationError(
        message=f"Invalid {field_name}.",
        details={field_name: "Date must be in YYYY-MM-DD format."},
    )


def validate_pagination_params(
    page_raw: Any = None,
    per_page_raw: Any = None,
    default_per_page: int = 20,
    max_per_page: int = 100,
) -> tuple[int, int]:
    """Validate and normalize pagination query parameters (page, per_page)."""
    page = _parse_int_threshold(
        page_raw,
        field_name="page",
        min_value=1,
        max_value=100000,
        default=1,
    )
    per_page = _parse_int_threshold(
        per_page_raw,
        field_name="per_page",
        min_value=1,
        max_value=max_per_page,
        default=default_per_page,
    )
    return page, per_page


def validate_product_create_input(
    payload: dict[str, Any],
    default_reorder_threshold: int = 10,
    default_near_expiry_days: int = 15,
    default_surplus_threshold: int = 40,
) -> dict[str, Any]:
    """Validate payload for creating a manual Product and/or ShopProduct configuration."""
    if not payload or not isinstance(payload, dict):
        raise ValidationError(message="Product payload is required.")

    # Check if caller is linking an existing product_id to a shop
    raw_product_id = payload.get("product_id")
    if raw_product_id is not None and str(raw_product_id).strip() != "":
        product_id = _parse_int_threshold(
            raw_product_id,
            field_name="product_id",
            min_value=1,
            max_value=2147483647,
        )
        raw_purchase = payload.get("purchase_price", payload.get("default_purchase_price"))
        raw_selling = payload.get("selling_price", payload.get("default_selling_price"))
        purchase_price = (
            _parse_money(raw_purchase, "purchase_price")
            if raw_purchase is not None and str(raw_purchase).strip() != ""
            else None
        )
        selling_price = (
            _parse_money(raw_selling, "selling_price")
            if raw_selling is not None and str(raw_selling).strip() != ""
            else None
        )
        raw_low_stock = payload.get(
            "low_stock_threshold",
            payload.get("reorder_threshold", payload.get("reorder_level")),
        )
        low_stock_threshold = _parse_int_threshold(
            raw_low_stock,
            field_name="low_stock_threshold",
            min_value=0,
            default=default_reorder_threshold,
        )
        raw_near_expiry = payload.get(
            "near_expiry_days",
            payload.get("near_expiry_threshold_days"),
        )
        near_expiry_days = _parse_int_threshold(
            raw_near_expiry,
            field_name="near_expiry_days",
            min_value=1,
            default=default_near_expiry_days,
        )
        surplus_threshold = _parse_int_threshold(
            payload.get("surplus_threshold"),
            field_name="surplus_threshold",
            min_value=1,
            default=default_surplus_threshold,
        )
        if (
            purchase_price is not None
            and selling_price is not None
            and selling_price < purchase_price
        ):
            raise ValidationError(
                message="Selling price cannot be less than purchase cost.",
                details={"selling_price": "Must be greater than or equal to purchase cost."},
            )
        is_active = (
            _parse_bool(payload.get("is_active"), "is_active")
            if "is_active" in payload and payload.get("is_active") is not None
            else True
        )
        return {
            "product_id": product_id,
            "purchase_price": purchase_price,
            "selling_price": selling_price,
            "low_stock_threshold": low_stock_threshold,
            "reorder_threshold": low_stock_threshold,
            "near_expiry_days": near_expiry_days,
            "surplus_threshold": surplus_threshold,
            "is_active": is_active,
        }

    errors: dict[str, str] = {}

    sku = str(payload.get("sku") or payload.get("code") or "").strip().upper()
    if not sku or not SKU_REGEX.match(sku):
        errors["sku"] = (
            "SKU is required (2-64 characters using uppercase letters, digits, '_', '.', or '-')."
        )

    name = str(payload.get("name") or "").strip()
    if not name or len(name) < 2 or len(name) > 150:
        errors["name"] = "Product name is required (2-150 characters)."

    category = str(payload.get("category") or "").strip()
    if not category or len(category) < 2 or len(category) > 80:
        errors["category"] = "Category is required (2-80 characters)."

    unit = str(payload.get("unit") or "").strip().lower()
    if not unit or unit not in VALID_PRODUCT_UNITS:
        errors["unit"] = (
            f"Unit of measurement must be one of: {', '.join(sorted(VALID_PRODUCT_UNITS))}."
        )

    description = str(payload.get("description") or "").strip() or None
    if description and len(description) > 2000:
        errors["description"] = "Description must not exceed 2000 characters."

    if errors:
        raise ValidationError(message="Product validation failed.", details=errors)

    raw_purchase = payload.get("default_purchase_price", payload.get("purchase_price"))
    raw_selling = payload.get("default_selling_price", payload.get("selling_price"))

    default_purchase_price = _parse_money(
        raw_purchase,
        field_name="default_purchase_price",
        allow_zero=True,
    )
    default_selling_price = _parse_money(
        raw_selling,
        field_name="default_selling_price",
        allow_zero=True,
    )

    purchase_price = _parse_money(
        payload.get("purchase_price", default_purchase_price),
        field_name="purchase_price",
        allow_zero=True,
    )
    selling_price = _parse_money(
        payload.get("selling_price", default_selling_price),
        field_name="selling_price",
        allow_zero=True,
    )

    if selling_price < purchase_price or default_selling_price < default_purchase_price:
        raise ValidationError(
            message="Selling price cannot be less than purchase cost.",
            details={"selling_price": "Selling price must be >= purchase cost."},
        )

    raw_reorder = payload.get(
        "low_stock_threshold",
        payload.get("reorder_threshold", payload.get("reorder_level")),
    )
    reorder_field_name = "reorder_level" if "reorder_level" in payload else "reorder_threshold"
    reorder_threshold = _parse_int_threshold(
        raw_reorder,
        field_name=reorder_field_name,
        min_value=0,
        default=default_reorder_threshold,
    )
    raw_near_expiry = payload.get(
        "near_expiry_days",
        payload.get("near_expiry_threshold_days"),
    )
    near_expiry_days = _parse_int_threshold(
        raw_near_expiry,
        field_name="near_expiry_days",
        min_value=1,
        default=default_near_expiry_days,
    )
    surplus_threshold = _parse_int_threshold(
        payload.get("surplus_threshold"),
        field_name="surplus_threshold",
        min_value=1,
        default=default_surplus_threshold,
    )
    is_perishable = (
        _parse_bool(payload.get("is_perishable"), "is_perishable")
        if "is_perishable" in payload and payload.get("is_perishable") is not None
        else True
    )
    is_active = (
        _parse_bool(payload.get("is_active"), "is_active")
        if "is_active" in payload and payload.get("is_active") is not None
        else True
    )
    link_existing = (
        _parse_bool(payload.get("link_existing"), "link_existing")
        if "link_existing" in payload and payload.get("link_existing") is not None
        else False
    )

    return {
        "sku": sku,
        "name": name,
        "category": category,
        "unit": unit,
        "description": description,
        "is_perishable": is_perishable,
        "default_purchase_price": default_purchase_price,
        "default_selling_price": default_selling_price,
        "purchase_price": purchase_price,
        "selling_price": selling_price,
        "reorder_threshold": reorder_threshold,
        "low_stock_threshold": reorder_threshold,
        "near_expiry_days": near_expiry_days,
        "surplus_threshold": surplus_threshold,
        "is_active": is_active,
        "link_existing": link_existing,
    }


def validate_product_update_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate partial update payload for Product and/or ShopProduct."""
    if not payload or not isinstance(payload, dict):
        raise ValidationError(message="At least one field must be provided to update the product.")

    errors: dict[str, str] = {}
    cleaned: dict[str, Any] = {}

    if "sku" in payload:
        sku = str(payload.get("sku") or "").strip().upper()
        if not sku or not SKU_REGEX.match(sku):
            errors["sku"] = (
                "SKU must be 2-64 characters using uppercase letters, digits, '_', '.', or '-'."
            )
        else:
            cleaned["sku"] = sku

    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name or len(name) < 2 or len(name) > 150:
            errors["name"] = "Product name must be 2-150 characters."
        else:
            cleaned["name"] = name

    if "category" in payload:
        category = str(payload.get("category") or "").strip()
        if not category or len(category) < 2 or len(category) > 80:
            errors["category"] = "Category must be 2-80 characters."
        else:
            cleaned["category"] = category

    if "unit" in payload:
        unit = str(payload.get("unit") or "").strip().lower()
        if not unit or unit not in VALID_PRODUCT_UNITS:
            errors["unit"] = (
                f"Unit of measurement must be one of: {', '.join(sorted(VALID_PRODUCT_UNITS))}."
            )
        else:
            cleaned["unit"] = unit

    if "description" in payload:
        description = str(payload.get("description") or "").strip() or None
        if description and len(description) > 2000:
            errors["description"] = "Description must not exceed 2000 characters."
        else:
            cleaned["description"] = description

    if errors:
        raise ValidationError(message="Product update validation failed.", details=errors)

    if "is_perishable" in payload and payload.get("is_perishable") is not None:
        cleaned["is_perishable"] = _parse_bool(payload.get("is_perishable"), "is_perishable")

    if "is_active" in payload and payload.get("is_active") is not None:
        cleaned["is_active"] = _parse_bool(payload.get("is_active"), "is_active")

    if "default_purchase_price" in payload:
        val = _parse_money(
            payload.get("default_purchase_price"),
            "default_purchase_price",
        )
        cleaned["default_purchase_price"] = val
        cleaned.setdefault("purchase_price", val)
    if "default_selling_price" in payload:
        val = _parse_money(
            payload.get("default_selling_price"),
            "default_selling_price",
        )
        cleaned["default_selling_price"] = val
        cleaned.setdefault("selling_price", val)
    if "purchase_price" in payload:
        cleaned["purchase_price"] = _parse_money(
            payload.get("purchase_price"),
            "purchase_price",
        )
    if "selling_price" in payload:
        cleaned["selling_price"] = _parse_money(
            payload.get("selling_price"),
            "selling_price",
        )

    if "reorder_threshold" in payload or "reorder_level" in payload:
        raw_val = payload.get("reorder_threshold", payload.get("reorder_level"))
        val = _parse_int_threshold(
            raw_val,
            field_name="reorder_level" if "reorder_level" in payload else "reorder_threshold",
            min_value=0,
        )
        cleaned["reorder_threshold"] = val
        cleaned.setdefault("low_stock_threshold", val)

    if "low_stock_threshold" in payload:
        val = _parse_int_threshold(
            payload.get("low_stock_threshold"),
            field_name="low_stock_threshold",
            min_value=0,
        )
        cleaned["low_stock_threshold"] = val
        cleaned.setdefault("reorder_threshold", val)

    if "near_expiry_days" in payload or "near_expiry_threshold_days" in payload:
        raw_ne = payload.get("near_expiry_days", payload.get("near_expiry_threshold_days"))
        cleaned["near_expiry_days"] = _parse_int_threshold(
            raw_ne,
            field_name="near_expiry_days",
            min_value=1,
        )

    if "surplus_threshold" in payload:
        cleaned["surplus_threshold"] = _parse_int_threshold(
            payload.get("surplus_threshold"),
            field_name="surplus_threshold",
            min_value=1,
        )

    if not cleaned:
        raise ValidationError(message="No valid product fields provided for update.")

    return cleaned


def validate_batch_receive_input(
    payload: dict[str, Any],
    is_perishable: bool,
    today: date,
    default_unit_cost: Decimal | None = None,
    allow_past_expiry: bool = False,
) -> dict[str, Any]:
    """Validate payload for receiving a new InventoryBatch (RECEIPT)."""
    if not payload or not isinstance(payload, dict):
        raise ValidationError(message="Batch receipt payload is required.")

    raw_batch_code = str(payload.get("batch_code") or "").strip().upper()
    if not raw_batch_code or not BATCH_CODE_REGEX.match(raw_batch_code):
        raise ValidationError(
            message="Batch code is required (2-64 characters using letters, digits, '_', '.', or '-').",
            details={"batch_code": "Provide a valid batch code (2-64 characters)."},
        )

    raw_qty = payload.get("initial_qty", payload.get("quantity", payload.get("received_qty")))
    initial_qty = _parse_int_threshold(
        raw_qty,
        field_name="initial_qty",
        min_value=1,
        max_value=1000000,
    )

    raw_cost = payload.get("unit_cost", payload.get("purchase_cost"))
    unit_cost = _parse_money(
        raw_cost,
        field_name="unit_cost",
        allow_zero=True,
        default=default_unit_cost,
    )

    manufactured_date = _parse_date(
        payload.get("manufactured_date"),
        field_name="manufactured_date",
        required=False,
    )
    expiry_date = _parse_date(
        payload.get("expiry_date"),
        field_name="expiry_date",
        required=is_perishable,
    )

    if manufactured_date is not None and expiry_date is not None:
        if expiry_date < manufactured_date:
            raise ValidationError(
                message="Expiry date cannot be earlier than the manufactured date.",
                details={"expiry_date": "Must be >= manufactured_date."},
            )

    if not allow_past_expiry and expiry_date is not None and expiry_date < today:
        raise ValidationError(
            message=f"Cannot receive already-expired batch (expiry_date {expiry_date.isoformat()} is before today {today.isoformat()}).",
            details={"expiry_date": "Expiry date must not be in the past for new stock receipts."},
        )

    reason = str(payload.get("reason") or payload.get("notes") or "").strip() or None
    if reason and len(reason) > 500:
        raise ValidationError(
            message="Receipt notes/reason must not exceed 500 characters.",
            details={"reason": "Maximum length is 500 characters."},
        )

    return {
        "batch_code": raw_batch_code,
        "manufactured_date": manufactured_date,
        "expiry_date": expiry_date,
        "unit_cost": unit_cost,
        "initial_qty": initial_qty,
        "reason": reason,
    }


def validate_batch_adjustment_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate payload for manual batch stock adjustments (API-018)."""
    if not payload or not isinstance(payload, dict):
        raise ValidationError(message="Stock adjustment payload is required.")

    raw_type = str(
        payload.get("movement_type") or payload.get("adjustment_type") or ""
    ).strip().upper()
    movement_type = ADJUSTMENT_TYPE_ALIASES.get(raw_type, raw_type)

    if movement_type not in VALID_ADJUSTMENT_TYPES:
        raise ValidationError(
            message=f"movement_type must be one of: {', '.join(sorted(VALID_ADJUSTMENT_TYPES))}.",
            details={
                "movement_type": f"Allowed values: {', '.join(sorted(VALID_ADJUSTMENT_TYPES))}."
            },
        )

    raw_qty = payload.get("quantity", payload.get("quantity_change"))
    quantity = _parse_int_threshold(
        raw_qty,
        field_name="quantity",
        min_value=1,
        max_value=1000000,
    )

    reason = str(payload.get("reason") or "").strip()
    if not reason or len(reason) < 3 or len(reason) > 500:
        raise ValidationError(
            message="A reason (3-500 characters) is mandatory for all stock adjustments.",
            details={"reason": "Provide a clear reason (3-500 characters) for audit compliance."},
        )

    return {
        "movement_type": movement_type,
        "quantity": quantity,
        "reason": reason,
    }


VALID_PAYMENT_MODES = {"CASH", "UPI_MANUAL", "CARD_MANUAL", "OTHER"}
PAYMENT_MODE_ALIASES = {
    "UPI": "UPI_MANUAL",
    "CARD": "CARD_MANUAL",
}
VALID_SALE_STATUSES = {"COMPLETED", "PARTIALLY_RETURNED", "RETURNED", "CANCELLED"}
VALID_RETURN_CONDITIONS = {"RESELLABLE", "DAMAGED", "EXPIRED"}


def validate_sale_create_input(
    payload: dict[str, Any],
    header_idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Validate payload for creating a POS sale (API-020).

    Note: Client-submitted prices, totals, or shop_ids are strictly ignored;
    authoritative prices are loaded from ShopProduct in SalesService.
    """
    if not payload or not isinstance(payload, dict):
        raise ValidationError(message="Sale payload is required.")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or len(raw_items) == 0:
        raise ValidationError(
            message="At least one cart item is required to complete a sale.",
            details={"items": "Provide a non-empty list of cart items."},
        )
    if len(raw_items) > 100:
        raise ValidationError(
            message="A single sale cannot exceed 100 line items.",
            details={"items": "Maximum 100 items per invoice."},
        )

    cleaned_items: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise ValidationError(
                message=f"Cart item at index {idx} must be an object.",
                details={f"items[{idx}]": "Must be a JSON object."},
            )
        raw_pid = item.get("product_id")
        raw_spid = item.get("shop_product_id")
        product_id: int | None = None
        shop_product_id: int | None = None

        if raw_pid is not None and str(raw_pid).strip() != "":
            product_id = _parse_int_threshold(
                raw_pid,
                field_name=f"items[{idx}].product_id",
                min_value=1,
                max_value=2147483647,
            )
        if raw_spid is not None and str(raw_spid).strip() != "":
            shop_product_id = _parse_int_threshold(
                raw_spid,
                field_name=f"items[{idx}].shop_product_id",
                min_value=1,
                max_value=2147483647,
            )
        if product_id is None and shop_product_id is None:
            raise ValidationError(
                message=f"Cart item at index {idx} requires product_id or shop_product_id.",
                details={f"items[{idx}].product_id": "product_id is required."},
            )

        quantity = _parse_int_threshold(
            item.get("quantity"),
            field_name=f"items[{idx}].quantity",
            min_value=1,
            max_value=100000,
        )

        line_discount = _parse_money(
            item.get("line_discount", item.get("discount")),
            field_name=f"items[{idx}].line_discount",
            allow_zero=True,
            default=Decimal("0.00"),
        )

        cleaned_items.append(
            {
                "product_id": product_id,
                "shop_product_id": shop_product_id,
                "quantity": quantity,
                "line_discount": line_discount,
            }
        )

    raw_mode = str(
        payload.get("payment_mode") or payload.get("payment_method") or "CASH"
    ).strip().upper()
    payment_mode = PAYMENT_MODE_ALIASES.get(raw_mode, raw_mode)
    if payment_mode not in VALID_PAYMENT_MODES:
        raise ValidationError(
            message=f"payment_mode must be one of: {', '.join(sorted(VALID_PAYMENT_MODES))}.",
            details={
                "payment_mode": f"Allowed values: {', '.join(sorted(VALID_PAYMENT_MODES))}."
            },
        )

    discount_amount = _parse_money(
        payload.get("discount_amount", payload.get("discount")),
        field_name="discount_amount",
        allow_zero=True,
        default=Decimal("0.00"),
    )

    customer_name = str(payload.get("customer_name") or "").strip() or None
    if customer_name and len(customer_name) > 120:
        raise ValidationError(
            message="customer_name must not exceed 120 characters.",
            details={"customer_name": "Maximum length is 120 characters."},
        )

    customer_phone = str(payload.get("customer_phone") or "").strip() or None
    if customer_phone:
        if not PHONE_REGEX.match(customer_phone):
            raise ValidationError(
                message="Invalid customer_phone format.",
                details={"customer_phone": "Enter a valid phone number (7-20 digits)."},
            )

    notes = str(payload.get("notes") or "").strip() or None
    if notes and len(notes) > 500:
        raise ValidationError(
            message="Sale notes must not exceed 500 characters.",
            details={"notes": "Maximum length is 500 characters."},
        )

    idem_key = str(
        header_idempotency_key or payload.get("idempotency_key") or ""
    ).strip() or None
    if idem_key and len(idem_key) > 80:
        raise ValidationError(
            message="idempotency_key must not exceed 80 characters.",
            details={"idempotency_key": "Maximum length is 80 characters."},
        )

    return {
        "items": cleaned_items,
        "payment_mode": payment_mode,
        "discount_amount": discount_amount,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "notes": notes,
        "idempotency_key": idem_key,
    }


def validate_sale_cancel_input(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Validate payload for cancelling a same-day completed POS sale (API-023)."""
    data = payload if isinstance(payload, dict) else {}
    reason = str(
        data.get("reason") or data.get("cancellation_reason") or ""
    ).strip()
    if not reason or len(reason) < 3 or len(reason) > 500:
        raise ValidationError(
            message="A cancellation reason (3-500 characters) is required.",
            details={"reason": "Provide a reason between 3 and 500 characters."},
        )
    return {"reason": reason}


def validate_sale_return_input(
    payload: dict[str, Any],
    header_idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Validate payload for processing POS item returns (API-024)."""
    if not payload or not isinstance(payload, dict):
        raise ValidationError(message="Return payload is required.")

    default_reason = str(payload.get("reason") or "").strip()

    raw_items = payload.get("items")
    if raw_items is None:
        # Support flat single-item payload
        raw_items = [payload]

    if not isinstance(raw_items, list) or len(raw_items) == 0:
        raise ValidationError(
            message="At least one return item is required.",
            details={"items": "Specify at least one sale item to return."},
        )

    cleaned_items: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise ValidationError(
                message=f"Return item at index {idx} must be an object.",
                details={f"items[{idx}]": "Must be a JSON object."},
            )
        raw_sale_item_id = item.get("sale_item_id", item.get("id"))
        raw_product_id = item.get("product_id")
        raw_batch_id = item.get("batch_id")

        sale_item_id: int | None = None
        product_id: int | None = None
        batch_id: int | None = None

        if raw_sale_item_id is not None and str(raw_sale_item_id).strip() != "":
            sale_item_id = _parse_int_threshold(
                raw_sale_item_id,
                field_name="sale_item_id",
                min_value=1,
                max_value=2147483647,
            )
        if raw_product_id is not None and str(raw_product_id).strip() != "":
            product_id = _parse_int_threshold(
                raw_product_id,
                field_name="product_id",
                min_value=1,
                max_value=2147483647,
            )
        if raw_batch_id is not None and str(raw_batch_id).strip() != "":
            batch_id = _parse_int_threshold(
                raw_batch_id,
                field_name="batch_id",
                min_value=1,
                max_value=2147483647,
            )

        if sale_item_id is None and product_id is None:
            raise ValidationError(
                message="Each return item must specify sale_item_id (or product_id).",
                details={"sale_item_id": "sale_item_id is required."},
            )

        raw_qty = item.get("quantity_returned", item.get("quantity"))
        qty_returned = _parse_int_threshold(
            raw_qty,
            field_name="quantity_returned",
            min_value=1,
            max_value=100000,
        )

        raw_cond = str(
            item.get("return_condition")
            or item.get("condition")
            or payload.get("return_condition")
            or payload.get("condition")
            or "RESELLABLE"
        ).strip().upper()
        if raw_cond not in VALID_RETURN_CONDITIONS:
            raise ValidationError(
                message=f"return_condition must be one of: {', '.join(sorted(VALID_RETURN_CONDITIONS))}.",
                details={
                    "return_condition": f"Allowed values: {', '.join(sorted(VALID_RETURN_CONDITIONS))}."
                },
            )

        item_reason = str(item.get("reason") or default_reason or "").strip()
        if not item_reason or len(item_reason) < 3 or len(item_reason) > 500:
            raise ValidationError(
                message="A return reason (3-500 characters) is required.",
                details={"reason": "Provide a clear reason (3-500 characters)."},
            )

        cleaned_items.append(
            {
                "sale_item_id": sale_item_id,
                "product_id": product_id,
                "batch_id": batch_id,
                "quantity_returned": qty_returned,
                "return_condition": raw_cond,
                "reason": item_reason,
            }
        )

    idem_key = str(
        header_idempotency_key or payload.get("idempotency_key") or ""
    ).strip() or None
    if idem_key and len(idem_key) > 80:
        raise ValidationError(
            message="idempotency_key must not exceed 80 characters.",
            details={"idempotency_key": "Maximum length is 80 characters."},
        )

    return {
        "items": cleaned_items,
        "idempotency_key": idem_key,
    }


VALID_EXPIRY_RISK_LEVELS = {
    "EXPIRED",
    "CRITICAL",
    "HIGH",
    "MEDIUM",
    "LOW",
    "NON_PERISHABLE",
    "DEPLETED",
    "INSUFFICIENT_DATA",
}

VALID_RECOMMENDATION_TYPES = {
    "MONITOR_NEAR_EXPIRY",
    "REVIEW_SURPLUS",
    "REPLENISHMENT_NEEDED",
    "INSUFFICIENT_SALES_HISTORY",
    "QUARANTINE_EXPIRED",
}


def validate_analysis_params(
    lookback_days_raw: Any = None,
    safety_stock_days_raw: Any = None,
    safety_stock_qty_raw: Any = None,
    surplus_coverage_days_raw: Any = None,
    ref_date_raw: Any = None,
    default_lookback_days: int = 14,
    default_safety_stock_days: int = 2,
    default_surplus_coverage_days: int = 30,
) -> dict[str, Any]:
    """Validate and normalize Phase 5 rule-based stock analysis parameters."""
    lookback_days = _parse_int_threshold(
        lookback_days_raw,
        field_name="lookback_days",
        min_value=1,
        max_value=365,
        default=default_lookback_days,
    )
    safety_stock_days = _parse_int_threshold(
        safety_stock_days_raw,
        field_name="safety_stock_days",
        min_value=0,
        max_value=90,
        default=default_safety_stock_days,
    )
    safety_stock_qty: int | None = None
    if safety_stock_qty_raw is not None and str(safety_stock_qty_raw).strip() != "":
        safety_stock_qty = _parse_int_threshold(
            safety_stock_qty_raw,
            field_name="safety_stock_qty",
            min_value=0,
            max_value=100000,
        )
    surplus_coverage_days = _parse_int_threshold(
        surplus_coverage_days_raw,
        field_name="surplus_coverage_days",
        min_value=1,
        max_value=365,
        default=default_surplus_coverage_days,
    )
    ref_date = _parse_date(ref_date_raw, field_name="ref_date", required=False)

    return {
        "lookback_days": lookback_days,
        "safety_stock_days": safety_stock_days,
        "safety_stock_qty": safety_stock_qty,
        "surplus_coverage_days": surplus_coverage_days,
        "ref_date": ref_date,
    }


# ============================================================================
# Phase 6: Inter-Store Stock Exchange / Transfer Validators
# ============================================================================

VALID_TRANSFER_STATUSES = {
    "PENDING_SOURCE_APPROVAL",
    "PENDING_RECIPIENT_APPROVAL",
    "APPROVED_RESERVED",
    "DISPATCHED_IN_TRANSIT",
    "COMPLETED",
    "REJECTED",
    "CANCELLED",
    "EXPIRED_OFFER",
    "DISPUTED",
    "RETURNED_TO_SOURCE",
}

VALID_TRANSFER_DIRECTIONS = {"all", "incoming", "outgoing"}


def validate_transfer_create_input(
    payload: dict[str, Any],
    default_expiry_days: int = 7,
) -> dict[str, Any]:
    """Validate input payload for proposing a new inter-store stock transfer."""
    if not payload or not isinstance(payload, dict):
        raise ValidationError(message="Transfer proposal payload is required.")

    errors: dict[str, str] = {}

    # Source Shop ID
    raw_src = payload.get("source_shop_id")
    source_shop_id: int | None = None
    if raw_src is None or str(raw_src).strip() == "" or isinstance(raw_src, bool):
        errors["source_shop_id"] = "source_shop_id is required."
    else:
        try:
            source_shop_id = int(raw_src)
            if source_shop_id <= 0:
                errors["source_shop_id"] = "source_shop_id must be a positive integer."
        except (ValueError, TypeError):
            errors["source_shop_id"] = "source_shop_id must be a positive integer."

    # Recipient Shop ID
    raw_dst = payload.get("recipient_shop_id")
    recipient_shop_id: int | None = None
    if raw_dst is None or str(raw_dst).strip() == "" or isinstance(raw_dst, bool):
        errors["recipient_shop_id"] = "recipient_shop_id is required."
    else:
        try:
            recipient_shop_id = int(raw_dst)
            if recipient_shop_id <= 0:
                errors["recipient_shop_id"] = "recipient_shop_id must be a positive integer."
        except (ValueError, TypeError):
            errors["recipient_shop_id"] = "recipient_shop_id must be a positive integer."

    if source_shop_id is not None and recipient_shop_id is not None:
        if source_shop_id == recipient_shop_id:
            errors["recipient_shop_id"] = "Source shop and recipient shop must be different."

    # Product ID
    raw_prod = payload.get("product_id")
    product_id: int | None = None
    if raw_prod is None or str(raw_prod).strip() == "" or isinstance(raw_prod, bool):
        errors["product_id"] = "product_id is required."
    else:
        try:
            product_id = int(raw_prod)
            if product_id <= 0:
                errors["product_id"] = "product_id must be a positive integer."
        except (ValueError, TypeError):
            errors["product_id"] = "product_id must be a positive integer."

    # Source Batch ID
    raw_batch = payload.get("source_batch_id", payload.get("batch_id"))
    source_batch_id: int | None = None
    if raw_batch is None or str(raw_batch).strip() == "" or isinstance(raw_batch, bool):
        errors["source_batch_id"] = "source_batch_id is required."
    else:
        try:
            source_batch_id = int(raw_batch)
            if source_batch_id <= 0:
                errors["source_batch_id"] = "source_batch_id must be a positive integer."
        except (ValueError, TypeError):
            errors["source_batch_id"] = "source_batch_id must be a positive integer."

    # Quantity
    raw_qty = payload.get("quantity")
    quantity: int | None = None
    if raw_qty is None or str(raw_qty).strip() == "" or isinstance(raw_qty, bool):
        errors["quantity"] = "quantity is required."
    else:
        try:
            quantity = int(raw_qty)
            if quantity <= 0:
                errors["quantity"] = "Transfer quantity must be greater than zero."
            elif quantity > 1_000_000:
                errors["quantity"] = "Transfer quantity cannot exceed 1,000,000 units."
        except (ValueError, TypeError):
            errors["quantity"] = "quantity must be an integer greater than zero."

    # Unit Transfer Price
    raw_price = payload.get("unit_transfer_price", payload.get("transfer_price"))
    unit_transfer_price: Decimal | None = None
    if raw_price is None or str(raw_price).strip() == "":
        errors["unit_transfer_price"] = "unit_transfer_price is required."
    else:
        try:
            unit_transfer_price = _parse_money(
                raw_price,
                field_name="unit_transfer_price",
                allow_zero=True,
            )
        except ValidationError as exc:
            errors["unit_transfer_price"] = exc.message

    if errors:
        raise ValidationError(message="Transfer proposal validation failed.", details=errors)

    # Optional fields
    estimated_distance_km: Decimal | None = None
    raw_dist = payload.get("estimated_distance_km")
    if raw_dist is not None and str(raw_dist).strip() != "":
        try:
            d_val = Decimal(str(raw_dist).strip())
            if d_val < 0:
                raise ValueError
            estimated_distance_km = d_val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (ValueError, InvalidOperation):
            raise ValidationError(
                message="Invalid estimated_distance_km.",
                details={"estimated_distance_km": "Must be a non-negative number."},
            )

    transfer_logistics_cost = Decimal("0.00")
    raw_cost = payload.get("transfer_logistics_cost")
    if raw_cost is not None and str(raw_cost).strip() != "":
        transfer_logistics_cost = _parse_money(
            raw_cost,
            field_name="transfer_logistics_cost",
            allow_zero=True,
            default=Decimal("0.00"),
        )

    notes = str(payload.get("notes") or payload.get("dispatch_notes") or "").strip() or None
    if notes and len(notes) > 1000:
        raise ValidationError(
            message="Notes must not exceed 1000 characters.",
            details={"notes": "Maximum length is 1000 characters."},
        )

    # Offer expiration
    offer_expires_at: datetime | None = None
    raw_expiry_at = payload.get("offer_expires_at")
    if raw_expiry_at:
        try:
            if isinstance(raw_expiry_at, datetime):
                offer_expires_at = raw_expiry_at
            else:
                raw_iso = str(raw_expiry_at).strip().replace("Z", "+00:00")
                offer_expires_at = datetime.fromisoformat(raw_iso)
            if offer_expires_at.tzinfo is None:
                offer_expires_at = offer_expires_at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                message="Invalid offer_expires_at format.",
                details={"offer_expires_at": "Must be ISO-8601 timestamp."},
            ) from exc
    else:
        # Default duration
        duration_days = _parse_int_threshold(
            payload.get("offer_duration_days"),
            field_name="offer_duration_days",
            min_value=1,
            max_value=90,
            default=default_expiry_days,
        )
        offer_expires_at = datetime.now(timezone.utc) + timedelta(days=duration_days)

    return {
        "source_shop_id": source_shop_id,
        "recipient_shop_id": recipient_shop_id,
        "product_id": product_id,
        "source_batch_id": source_batch_id,
        "quantity": quantity,
        "unit_transfer_price": unit_transfer_price,
        "estimated_distance_km": estimated_distance_km,
        "transfer_logistics_cost": transfer_logistics_cost,
        "offer_expires_at": offer_expires_at,
        "notes": notes,
    }


def validate_transfer_action_input(
    payload: dict[str, Any] | None,
    action: str,
) -> dict[str, Any]:
    """Validate input payload for transfer lifecycle transitions (approve, reject, cancel, dispatch, receive)."""
    p = payload or {}
    action_clean = action.strip().lower()

    if action_clean in ("reject", "cancel"):
        reason = str(p.get("reason") or p.get("rejection_or_cancel_reason") or "").strip()
        if not reason or len(reason) < 3 or len(reason) > 1000:
            raise ValidationError(
                message=f"A valid reason (3-1000 characters) is required to {action_clean} a transfer.",
                details={"reason": "Provide a descriptive reason (3-1000 characters)."},
            )
        return {"reason": reason}

    if action_clean == "approve":
        notes = str(p.get("notes") or "").strip() or None
        if notes and len(notes) > 1000:
            raise ValidationError(
                message="Approval notes must not exceed 1000 characters.",
                details={"notes": "Maximum length is 1000 characters."},
            )
        return {"notes": notes}

    if action_clean == "dispatch":
        dispatch_notes = str(p.get("dispatch_notes") or p.get("notes") or "").strip() or None
        if dispatch_notes and len(dispatch_notes) > 1000:
            raise ValidationError(
                message="Dispatch notes must not exceed 1000 characters.",
                details={"dispatch_notes": "Maximum length is 1000 characters."},
            )
        carrier_reference = str(p.get("carrier_reference") or "").strip() or None
        if carrier_reference and len(carrier_reference) > 120:
            raise ValidationError(
                message="Carrier reference must not exceed 120 characters.",
                details={"carrier_reference": "Maximum length is 120 characters."},
            )
        return {
            "dispatch_notes": dispatch_notes,
            "carrier_reference": carrier_reference,
        }

    if action_clean in ("receive", "confirm-receipt"):
        receipt_notes = str(p.get("receipt_notes") or p.get("notes") or "").strip() or None
        if receipt_notes and len(receipt_notes) > 1000:
            raise ValidationError(
                message="Receipt notes must not exceed 1000 characters.",
                details={"receipt_notes": "Maximum length is 1000 characters."},
            )
        received_good_qty: int | None = None
        raw_good = p.get("received_good_qty")
        if raw_good is not None and str(raw_good).strip() != "":
            try:
                received_good_qty = int(raw_good)
                if received_good_qty < 0:
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise ValidationError(
                    message="Invalid received_good_qty.",
                    details={"received_good_qty": "Must be a non-negative integer."},
                ) from exc

        transit_damaged_qty = 0
        raw_damaged = p.get("transit_damaged_qty")
        if raw_damaged is not None and str(raw_damaged).strip() != "":
            try:
                transit_damaged_qty = int(raw_damaged)
                if transit_damaged_qty < 0:
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise ValidationError(
                    message="Invalid transit_damaged_qty.",
                    details={"transit_damaged_qty": "Must be a non-negative integer."},
                ) from exc

        return {
            "receipt_notes": receipt_notes,
            "received_good_qty": received_good_qty,
            "transit_damaged_qty": transit_damaged_qty,
        }

    return {}


def validate_notification_query_params(
    raw_is_read: Any = None,
    raw_type: Any = None,
    raw_page: Any = 1,
    raw_per_page: Any = 20,
) -> tuple[bool | None, str | None, int, int]:
    """Validate query parameters for notification listing."""
    is_read: bool | None = None
    if raw_is_read is not None and str(raw_is_read).strip() != "":
        val_str = str(raw_is_read).strip().lower()
        if val_str in ("true", "1", "yes", "t"):
            is_read = True
        elif val_str in ("false", "0", "no", "f"):
            is_read = False
        else:
            raise ValidationError(
                message="Invalid is_read filter parameter.",
                details={"is_read": "Must be a boolean ('true' or 'false')."},
            )

    notification_type: str | None = None
    if raw_type and str(raw_type).strip():
        notification_type = str(raw_type).strip().upper()

    page, per_page = validate_pagination_params(raw_page, raw_per_page)
    return is_read, notification_type, page, per_page

