# Sanchay AI — Error Handling & Resilience Specification

This document defines the standardized error handling architecture, domain exception hierarchy, HTTP status mappings, database transaction rollback guarantees, and frontend error presentation rules for **Sanchay AI** (`RULE-19`).

---

## 1. Non-Negotiable Error Handling Principles (`RULE-19`)

1. **No Silent Failures:** Never write bare `except:` or `except Exception: pass`. Every caught exception must either be handled deterministically, translated into a typed domain exception, or logged with stack trace and re-raised.
2. **No Fake Success Responses:** Never return `200 OK` or `{"success": true}` when a validation, authorization, or database operation fails.
3. **Mandatory Transaction Rollback:** Any exception raised inside a service method that performs database writes must immediately execute `db.session.rollback()` before returning an error response.
4. **No Internal Leak in Production:** `500 Internal Server Error` responses must log the full traceback to `logs/sanchay_ai.log` with a correlation `request_id`, while returning a safe, generic error message to the client without exposing SQL queries, stack traces, or environment variables.

---

## 2. Standard JSON Error Envelope

All `/api/v1/*` error responses must conform to this schema:

```json
{
  "success": false,
  "error": {
    "code": "DOMAIN_ERROR_CODE",
    "message": "Clear, human-readable explanation of what went wrong.",
    "details": {
      "field_or_context": "Specific contextual values or field-level validation messages"
    },
    "request_id": "req_8f92a1c4"
  }
}
```

For browser navigation requests (`Accept: text/html`), Flask error handlers render styled HTML error pages (`403.html`, `404.html`, `500.html`) or flash a user-friendly alert banner while preserving form inputs.

---

## 3. Domain Error Code Catalog

| Error Code | HTTP Status | Triggering Condition | Rollback Required? |
| :--- | :--- | :--- | :--- |
| `VALIDATION_ERROR` | `400` / `422` | Missing required fields, negative quantities, invalid date formats, or malformed JSON. | Yes (No writes committed) |
| `AUTHENTICATION_REQUIRED` | `401` | Unauthenticated user or expired session attempting to access a protected route. | N/A |
| `INVALID_CREDENTIALS` | `401` | Incorrect username/email or password, or deactivated user account (`is_active = False`). | N/A |
| `CSRF_TOKEN_MISSING_OR_INVALID` | `400` | Missing or expired `X-CSRFToken` header / form token on state-changing request. | N/A |
| `INSUFFICIENT_ROLE_PERMISSION` | `403` | Authenticated user lacks required role (e.g., `STAFF` attempting to approve a stock transfer or adjust batch shrinkage). | Yes |
| `FORBIDDEN_SHOP_ACCESS` | `403` | `SHOP_OWNER` or `STAFF` attempting to read or mutate another shop's inventory, batches, or sales (`BR-013`). | Yes |
| `SHOP_INACTIVE_OR_SUSPENDED` | `403` | Attempting operational mutations on a shop whose `status != 'ACTIVE'`. | Yes |
| `RESOURCE_NOT_FOUND` | `404` | Requested `shop_id`, `product_id`, `batch_id`, `sale_id`, or `transfer_id` does not exist. | N/A |
| `DUPLICATE_RESOURCE` | `409` | Duplicate `sku`, `batch_code`, `shop.code`, `username`, or `email`. | Yes |
| `INSUFFICIENT_SELLABLE_STOCK` | `409` | POS sale quantity exceeds sum of active, non-expired `available_qty` across batches (`BR-004`). | Yes |
| `OFFER_EXCEEDS_ELIGIBLE_STOCK` | `409` | Proposed transfer quantity exceeds `batch.available_qty - open_offered_qty` (`BR-005`). | Yes |
| `INSUFFICIENT_STOCK_AT_RESERVATION` | `409` | Source batch `available_qty` dropped below transfer `quantity` before second owner approved (`BR-016`). | Yes |
| `BATCH_EXPIRED` | `409` | Attempting to sell, offer, approve, or receive a batch whose `expiry_date < CURRENT_DATE` (`BR-016`). | Yes (except expiry sweep commit) |
| `INVALID_TRANSFER_STATE_TRANSITION` | `409` | Attempting an illegal state transition (e.g., `confirm-receipt` before `DISPATCHED_IN_TRANSIT`, or cancelling after dispatch). | Yes |
| `TRANSFER_ALREADY_COMPLETED` | `409` | Idempotency guard: attempting to complete or mutate a transfer already in `COMPLETED` or terminal state (`BR-011`). | Yes |
| `SELF_TRANSFER_PROHIBITED` | `422` | `source_shop_id == recipient_shop_id` (`BR-012`). | Yes |
| `CROSS_ORG_TRANSFER_PROHIBITED` | `403` | Source and recipient shops do not belong to the same organization (`BR-012`). | Yes |
| `DATABASE_CONFLICT_OR_LOCK_TIMEOUT` | `503` / `409` | PostgreSQL serialization/lock timeout or integrity constraint violation. | Yes (`db.session.rollback()`) |
| `INTERNAL_SERVER_ERROR` | `500` | Unhandled unexpected runtime exception. | Yes (`db.session.rollback()`) |

---

## 4. Python Service Transaction Pattern

Every state-changing method in `app/services/` must follow this transactional pattern:

```python
from sqlalchemy.exc import IntegrityError, OperationalError
from app.extensions import db
from app.utils.errors import DomainError, DatabaseConflictError

def execute_transactional_mutation(operation_callable, *args, **kwargs):
    try:
        result = operation_callable(*args, **kwargs)
        db.session.commit()
        return result
    except DomainError:
        db.session.rollback()
        raise
    except IntegrityError as exc:
        db.session.rollback()
        raise DatabaseConflictError(
            code="DATABASE_INTEGRITY_CONFLICT",
            message="The operation conflicted with an existing record or constraint."
        ) from exc
    except OperationalError as exc:
        db.session.rollback()
        raise DatabaseConflictError(
            code="DATABASE_LOCK_OR_CONNECTION_ERROR",
            message="Database is temporarily busy or unavailable. Please retry."
        ) from exc
    except Exception:
        db.session.rollback()
        raise
```
