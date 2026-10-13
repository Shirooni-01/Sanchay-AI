# Sanchay AI — Non-Negotiable Project & Engineering Rules

This document codifies the mandatory governance, architecture, security, database, API, frontend, testing, and AI-phasing rules for **Sanchay AI**. Every contributor and automated coding agent must adhere to these constraints across all implementation phases.

---

## 1. Core Project Governance Rules (`RULE-01` to `RULE-24`)

| Rule ID | Rule Statement | Enforcement Mechanism |
| :--- | :--- | :--- |
| **RULE-01** | **Documentation-First Gate:** Documentation must be completed, reviewed, and approved before application implementation begins. | Phase 0 sign-off gate in [`IMPLEMENTATION_ROADMAP.md`](file:///d:/Ashutosh_01/Sanchay%20AI/IMPLEMENTATION_ROADMAP.md). |
| **RULE-02** | **Sequential Phase Execution:** Implementation must proceed strictly through Phases 1 to 8 in order. Never jump ahead or blend unapproved phases. | Phase completion checklists and pytest gates per phase. |
| **RULE-03** | **AI Reserved for Final Phase (Phase 8):** AI development, model training, model integration, and AI-powered recommendations are strictly reserved for **Phase 8**. | Zero ML imports or AI endpoints permitted in Phases 1–7. |
| **RULE-04** | **No Premature AI Features:** Earlier phases (Phases 1–7) must not contain AI logic or fake "AI" labels. Earlier phases may only collect and store structured historical data (`sales`, `stock_movements`, `inventory_batches`) needed for Phase 8. | Code review & automated grep check in CI/tests for Phases 1–7. |
| **RULE-05** | **Out-of-Scope Scanning & Vision:** Product scanning, barcode/QR scanning, camera input, OCR-based entry, image recognition, and product-recognition models are permanently **OUT OF SCOPE**. | No camera/barcode JS libraries or vision packages allowed. |
| **RULE-06** | **Manual Catalog Management:** Products, batches, expiry dates, and stock receipts are entered and managed manually via validated forms and APIs. | Validated REST APIs (`API-CAT-*`, `API-INV-*`). |
| **RULE-07** | **PostgreSQL Single Source of Truth:** PostgreSQL is the sole central source of truth for runtime application data. No SQLite, in-memory dicts, JSON files, or secondary databases at runtime. | `DATABASE_URL` required; verified in `app/config.py`. |
| **RULE-08** | **Approved Technology Stack:** Backend: Python 3.11+ with Flask. Database: PostgreSQL with SQLAlchemy & Flask-Migrate. Frontend: HTML5, CSS3, and vanilla JavaScript. | Dependency whitelist in `requirements.txt`. |
| **RULE-09** | **Modular, Maintainable Architecture:** Keep the codebase modular (Blueprints + Service Layer + Models), clean, and well-structured for a hackathon and future expansion. | Enforced directory structure in [`ARCHITECTURE.md`](file:///d:/Ashutosh_01/Sanchay%20AI/ARCHITECTURE.md). |
| **RULE-10** | **No Overengineering:** Do not introduce microservices, Kubernetes, Kafka, Redis/Celery brokers, Docker-only lock-in, or SPA frameworks (React/Vue/Angular). | Architecture review & `DECISIONS.md` (`ADR-001`). |
| **RULE-11** | **Zero Hardcoded Business Data:** Never hardcode shop records, product lists, stock quantities, sales history, expiry dates, transfer records, or user accounts into Python routes, services, or HTML/JS files. | Dynamic DB queries only; audited in Phase 7. |
| **RULE-12** | **Explicit Seed Scripts Only:** Demo data must be loaded exclusively via explicit CLI seed scripts (`scripts/seed_demo_data.py`) or test fixtures into PostgreSQL. | Separate CLI command (`flask seed-demo`). |
| **RULE-13** | **Validated Backend APIs:** All business operations must execute through validated backend endpoints backed by persistent PostgreSQL records. | Request schema/validator functions in `app/utils/validators.py`. |
| **RULE-14** | **Backend Authorization Enforcement:** Never trust frontend UI hiding, disabled buttons, or client-side role checks. Enforce authentication and RBAC on every backend endpoint. | `@login_required`, `@require_role`, `@require_shop_access` decorators. |
| **RULE-15** | **Strict Shop Data Isolation:** Never allow one shop's owner or staff to read or modify another shop's private inventory, batches, cost prices, or sales history. | Mandatory `shop_id` scoping in service queries + isolation tests. |
| **RULE-16** | **Transactional & Auditable Stock Changes:** Every stock-changing operation must run inside a single ACID transaction with row-level locking (`SELECT ... FOR UPDATE`) and write an immutable `stock_movements` and `audit_logs` record. | `InventoryService` & `SalesService` transaction boundaries. |
| **RULE-17** | **No Unilateral Transfer Completion:** A stock transfer must **never** complete or move inventory merely because one shop owner proposes or accepts it. | State machine enforcement in `TransferService` (`BR-008`). |
| **RULE-18** | **Dual Approval + Physical Handover Confirmation:** A transfer cannot transition to `COMPLETED` until: (1) Source Shop Owner approves, (2) Recipient Shop Owner approves (`APPROVED_RESERVED`), (3) Source confirms dispatch (`DISPATCHED_IN_TRANSIT`), and (4) Recipient confirms physical receipt (`COMPLETED`). | Four-step state transition guard in `TransferService` (`BR-009`). |
| **RULE-19** | **Honest Error Handling:** Never silently swallow exceptions (`except Exception: pass`), never return fake `{ "success": true }` responses on failure, and never ignore database integrity errors. | Centralized error handlers (`docs/ERROR_HANDLING.md`). |
| **RULE-20** | **Truthful Verification Reporting:** Never claim a feature, migration, API, test, or AI model works unless it has been executed and verified against PostgreSQL. | Mandatory `pytest` execution output before closing any phase. |
| **RULE-21** | **Secret Management:** Store all credentials, database URLs, and secret keys in environment variables (`.env`). Never commit `.env` or secrets to Git. | `.gitignore` rules + `.env.example` template. |
| **RULE-22** | **Living Documentation Synchronization:** Keep `CHANGELOG.md`, `DATABASE_SCHEMA.md`, `API_SPECIFICATION.md`, and `IMPLEMENTATION_ROADMAP.md` synchronized as each phase is completed. | Phase Definition of Done checklist item. |
| **RULE-23** | **Strict Scope Control:** Do not silently add unapproved integrations (e.g., Stripe/Razorpay payment gateways, Twilio SMS, external OpenAI/Gemini APIs). | Scope boundaries in `PRD.md`. |
| **RULE-24** | **Non-Destructive Inspection:** Always inspect existing files before modifying; preserve unrelated documentation and comments. | Engineering workflow policy. |

---

## 2. Coding & Architectural Conventions

### 2.1 Backend (Python & Flask)
- **Python Version:** Python 3.11+.
- **Style Guide:** PEP 8 compliant, type-annotated functions (`typing`), docstrings on all public service methods and models.
- **Layer Separation:**
  - `app/models/` — SQLAlchemy declarative models and table constraints only.
  - `app/services/` — Business logic, permission verification, `SELECT ... FOR UPDATE` row locking, stock movement creation, and `db.session.commit()` / `db.session.rollback()` management.
  - `app/blueprints/` — HTTP request parsing, input validation, calling service layer methods, and returning HTML templates or standardized JSON responses. **Blueprints must never mutate `inventory_batches` directly without calling `InventoryService`, `SalesService`, or `TransferService`.**
- **Monetary Values:** Always use `NUMERIC(12, 2)` in PostgreSQL and `decimal.Decimal` in Python (rounded to 2 decimal places using `ROUND_HALF_UP`). **Never use `float` for currency or prices.**
- **Timestamps:** Always store timestamps in UTC (`TIMESTAMPTZ` / `DateTime(timezone=True)`) using `datetime.now(timezone.utc)`.

### 2.2 Database Rules
- All tables must define explicit primary keys, foreign keys with appropriate `ON DELETE RESTRICT` (to prevent orphaned financial/audit records), `NOT NULL` constraints, and `CHECK` constraints for non-negative quantities (`available_qty >= 0`, `reserved_qty >= 0`, `damaged_qty >= 0`, `expired_qty >= 0`).
- Physical deletion (`DELETE FROM`) of `users`, `shops`, `products`, `inventory_batches`, `sales`, `stock_movements`, `transfer_requests`, and `audit_logs` is **prohibited** in application code. Use soft deactivation (`is_active = False` or terminal `status` enums).
- Every schema change must be generated and applied via `flask db migrate` and `flask db upgrade`.

### 2.3 API & Frontend Rules
- **JSON Envelope:** All `/api/v1/*` endpoints must return a consistent JSON structure:
  - Success: `{"success": true, "data": {...}, "meta": {...}}`
  - Error: `{"success": false, "error": {"code": "ERROR_CODE", "message": "...", "details": {...}}}`
- **Frontend Stack:** Semantic HTML5, modular CSS3 (custom properties/variables, responsive grid/flexbox), and clean ES6+ vanilla JavaScript (`static/js/`).
- **XSS Prevention:** Never interpolate unsanitized user strings into `innerHTML`. Use `textContent` in JavaScript and Jinja2 auto-escaping in HTML templates.
- **CSRF Protection:** Include `<meta name="csrf-token" content="{{ csrf_token() }}">` in `base.html` and attach `X-CSRFToken` in all vanilla JS `fetch()` state-changing calls.

### 2.4 Prohibited Shortcuts & Anti-Patterns
1. **PROHIBITED:** Computing shop inventory in JavaScript from hardcoded arrays.
2. **PROHIBITED:** Updating `inventory_batches.available_qty` without inserting a corresponding `stock_movements` row in the same transaction.
3. **PROHIBITED:** Allowing `SHOP_OWNER` of Shop A to pass `?shop_id=2` in a query string or JSON body to read/write Shop B's inventory or sales.
4. **PROHIBITED:** Labeling Phases 5–7 rule-based surplus detection as "AI-Powered Prediction" in the UI before Phase 8 is implemented. Phases 5–7 must honestly label rule-based metrics as *"Rule-Based Surplus Analysis"*.
5. **PROHIBITED:** Returning mocked/random numbers in Phase 8 AI endpoints instead of executing the trained statistical/ML pipeline on real PostgreSQL records.
