# Sanchay AI — Security Architecture & Threat Model

**Document Status:** Proposed Security Baseline (Phase 0)  
**Related Rules:** `RULE-14`, `RULE-15`, `RULE-16`, `RULE-21`, `BR-012`, `BR-013`, `BR-014`

---

## 1. Threat Model (STRIDE Analysis)

| Threat Category | Specific Threat Scenario in Sanchay AI | Mitigation Control |
| :--- | :--- | :--- |
| **Spoofing** | Attacker guesses or brute-forces a Shop Owner's password or hijacks a session cookie. | Password hashing via `scrypt` / `pbkdf2:sha256` (`Werkzeug`), generic login failure messages, `HttpOnly` + `SameSite=Lax` signed session cookies, login rate-limiting/lockout logging. |
| **Tampering** | Malicious user modifies `unit_selling_price` in POS payload, or sends negative `quantity` to inflate stock. | Server-side price lookup from `shop_products`, strict schema validation (`quantity > 0`), and PostgreSQL `CHECK` constraints (`>= 0`). |
| **Repudiation** | A Shop Owner or Staff member denies adjusting inventory, cancelling a sale, or approving a transfer. | Immutable `stock_movements`, `transfer_status_logs`, and `audit_logs` recording `actor_user_id`, `actor_role`, `ip_address`, `before_state`, and `after_state`. |
| **Information Disclosure (IDOR)** | Shop 1 Owner changes URL/API path from `/api/v1/shops/1/inventory` to `/api/v1/shops/2/inventory` to spy on Shop 2's sales or batch costs. | Mandatory `@require_shop_access` backend decorator verifying active assignment in `user_shop_assignments` (`BR-013`). Cross-shop surplus views use a restricted projection (`OD-003`). |
| **Denial of Service / Race Condition** | Concurrent POS sales and transfer approvals attempt to double-spend the same batch stock. | Row-level locking (`SELECT ... FOR UPDATE`) in deterministic key order (`batch.id ASC`) within short ACID transactions. |
| **Elevation of Privilege** | A `STAFF` user crafts a direct `POST /api/v1/transfers/{id}/approve` or `POST /api/v1/users` request bypassing hidden UI buttons. | Server-side `@require_role('SHOP_OWNER')` and `@require_role('SUPER_ADMIN')` decorators on every endpoint (`RULE-14`, `BR-014`). |

---

## 2. Authentication & Session Security

1. **Password Storage:**
   - Passwords are never stored or logged in plaintext.
   - Hashed using `werkzeug.security.generate_password_hash(password, method="scrypt")` (with minimum password complexity: `>= 8` characters, at least 1 uppercase, 1 lowercase, 1 digit).
2. **Session Lifecycle (`Flask-Login`):**
   - Session cookie name: `sanchay_session`.
   - `SESSION_COOKIE_HTTPONLY = True` (prevents JavaScript `document.cookie` access in case of XSS).
   - `SESSION_COOKIE_SAMESITE = 'Lax'` (protects against cross-site request forgery on top-level navigations).
   - `SESSION_COOKIE_SECURE = True` in HTTPS staging/production environments.
   - `PERMANENT_SESSION_LIFETIME = 8 hours` (480 minutes).
   - On every request (`@login_manager.user_loader`), the backend checks `user.is_active == True`. If an admin deactivates a user, their active session is immediately rejected on the very next request.

---

## 3. Role-Based & Shop-Scoped Authorization Matrix

Every protected endpoint enforces two orthogonal checks:
1. **Role Check (`@require_role`):** Is the user's role permitted to invoke this action?
2. **Scope/Tenant Check (`@require_shop_access`):** Does the user's organization and shop assignment grant access to the target `shop_id` or `transfer_id`?

| Operation | `SUPER_ADMIN` | `SHOP_OWNER` (Own Shop) | `SHOP_OWNER` (Other Shop) | `STAFF` (Own Shop) |
| :--- | :---: | :---: | :---: | :---: |
| Create / Edit Shops & Org Settings | **ALLOW** | DENY (`403`) | DENY (`403`) | DENY (`403`) |
| Create / Deactivate `SHOP_OWNER` | **ALLOW** | DENY (`403`) | DENY (`403`) | DENY (`403`) |
| Create / Deactivate `STAFF` | **ALLOW** | **ALLOW** | DENY (`403`) | DENY (`403`) |
| Create / Edit Shop Product Catalog | **ALLOW** | **ALLOW** | DENY (`403`) | DENY (`403`) |
| View Shop Inventory & Batches | **ALLOW** | **ALLOW** | DENY (`403`) | **ALLOW** |
| Receive New Inventory Batch (`RECEIPT`) | **ALLOW** | **ALLOW** | DENY (`403`) | **ALLOW** |
| Manual Stock Adjustment (`ADJUSTMENT_*`, `MARK_DAMAGED`) | **ALLOW** | **ALLOW** | DENY (`403`) | DENY (`403`) |
| Create POS Sale & View Invoices | View Only | **ALLOW** | DENY (`403`) | **ALLOW** |
| Propose Stock Transfer | DENY (Admin oversees) | **ALLOW** | DENY (`403`) | DENY (`403`) |
| Approve / Reject / Cancel Stock Transfer | DENY (`403`) | **ALLOW** (Own side only) | DENY (`403`) | DENY (`403`) |
| Record Physical Transfer Dispatch | DENY (`403`) | **ALLOW** (If Source Shop) | DENY (`403`) | **ALLOW** (If Source Shop) |
| Confirm Physical Transfer Receipt (`COMPLETED`) | DENY (`403`) | **ALLOW** (If Recipient Shop) | DENY (`403`) | DENY (`403`) |
| View Organization-Wide Executive Reports | **ALLOW** | DENY (`403`) | DENY (`403`) | DENY (`403`) |

---

## 4. Application Security Controls

### 4.1 Cross-Site Request Forgery (CSRF) Protection
- Enabled globally via `Flask-WTF` (`CSRFProtect`).
- All HTML `<form method="POST">` elements include `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
- All vanilla JavaScript `fetch()` calls read `<meta name="csrf-token" content="{{ csrf_token() }}">` and pass `headers: { "X-CSRFToken": token }`.

### 4.2 Cross-Site Scripting (XSS) & Content Security
- Jinja2 auto-escaping is enabled for all `.html` templates.
- Vanilla JS DOM updates use `element.textContent = value` or `document.createElement()` rather than raw `innerHTML` string concatenation.
- Security response headers set via `@app.after_request`:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: SAMEORIGIN`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Content-Security-Policy: default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:;`

### 4.3 SQL Injection Prevention
- All database queries use SQLAlchemy ORM expressions or parameterized bound parameters.
- String formatting (`f"SELECT ... {user_input}"`) in SQL queries is **strictly forbidden**.

### 4.4 Secret Management (`RULE-21`)
- `SECRET_KEY`, `DATABASE_URL`, and `TEST_DATABASE_URL` are loaded exclusively via `os.environ` (populated locally from `.env` via `python-dotenv`).
- `.env` is listed in `.gitignore`. Startup validation fails fast if `SECRET_KEY` is missing or left at the placeholder value in production mode.

---

## 5. Audit Logging, Backup & Recovery

1. **Audit Trail (`audit_logs`):**
   - Records authentication events (login success, login failure, logout, password change), RBAC violations (`403` cross-shop attempts), catalog mutations, manual stock adjustments, sale cancellations/returns, and every transfer state transition.
2. **Database Backup & Recovery:**
   - Daily logical backups via `pg_dump -Fc sanchay_ai_prod > backup_YYYYMMDD.dump`.
   - Pre-migration snapshots before running `flask db upgrade` in staging/production (detailed in [`docs/DEPLOYMENT.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/DEPLOYMENT.md)).
