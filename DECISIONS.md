# Architecture Decision Records (ADRs) & Open Decisions

This document records the key architectural, database, security, and product decisions for **Sanchay AI**, along with unresolved questions (`OD-*`) requiring stakeholder review before Phase 1 implementation begins.

---

## Part 1 — Architecture Decision Records (`ADR-001` to `ADR-010`)

### ADR-001: Modular Flask Monolith Architecture (No Microservices)

- **Status:** Accepted
- **Date:** 2026-10-12
- **Context:** Sanchay AI requires multi-shop inventory tracking, POS billing, inter-store stock transfers with strict ACID guarantees, and a future AI intelligence module, built by a lean team for a hackathon demonstration (3–5 shops, 7–10 products) with extensibility for future growth.
- **Decision:** Implement a **Modular Monolith** using Python 3.11+ and Flask 3.x (`Application Factory` pattern with domain Blueprints and a dedicated `services/` layer), backed by a single PostgreSQL database.
- **Alternatives Considered:**
  1. *Microservices (separate Auth, Inventory, Transfer, and AI services):* Rejected because distributed transactions across stock reservations and transfers introduce network failure modes, two-phase commit complexity, and operational overhead violating Project Rule #10.
  2. *Single-file Flask script:* Rejected due to poor maintainability, lack of module boundaries, and inability to test services cleanly.
- **Consequences:**
  - All stock mutations and transfer state transitions execute within native PostgreSQL ACID transactions.
  - Domain modules (`auth`, `org`, `catalog`, `inventory`, `sales`, `transfers`, `notifications`, `reports`, and Phase 8 `ai`) remain cleanly separated by Python package boundaries.

---

### ADR-002: PostgreSQL with SQLAlchemy & Flask-Migrate as Single Source of Truth

- **Status:** Accepted
- **Date:** 2026-10-12
- **Context:** Multi-store inventory management requires row-level locking (`SELECT ... FOR UPDATE`), relational integrity (`FOREIGN KEY`, `CHECK` constraints), and auditable schema evolution.
- **Decision:** Use **PostgreSQL 15+** as the sole persistent datastore, accessed via **SQLAlchemy 2.x (`Flask-SQLAlchemy`)** for ORM models/transactions and **Alembic (`Flask-Migrate`)** for versioned schema migrations.
- **Alternatives Considered:**
  1. *SQLite:* Rejected because it lacks concurrent write scalability and fine-grained row-level `FOR UPDATE` locking required by concurrent POS sales and transfer reservations (Project Rule #7).
  2. *Raw `psycopg2` SQL queries without ORM/migrations:* Rejected due to higher maintenance overhead and increased risk of inconsistent query construction.
- **Consequences:**
  - Database-level `CHECK` constraints (`available_qty >= 0`, `reserved_qty >= 0`) provide a hard safety net against negative inventory even if application bugs occur.
  - Every schema modification is tracked in `migrations/versions/`.

---

### ADR-003: Server-Side Session Authentication with RBAC and CSRF Protection

- **Status:** Accepted
- **Date:** 2026-10-12
- **Context:** The frontend uses HTML5, CSS3, and vanilla JavaScript served from the same Flask origin. We need secure authentication, immediate logout/session revocation, and role-based + shop-scoped access control.
- **Decision:** Use **Session-Based Authentication** (`Flask-Login`) with `HttpOnly`, `SameSite=Lax` signed cookies, `Werkzeug` `scrypt`/`pbkdf2:sha256` (or `argon2-cffi`) password hashing, and **CSRF tokens (`Flask-WTF`)** validated on all state-changing requests (`POST`, `PUT`, `PATCH`, `DELETE`), including `X-CSRFToken` headers for vanilla `fetch()` JSON calls.
- **Alternatives Considered:**
  1. *Stateless JWTs stored in `localStorage`:* Rejected because `localStorage` is vulnerable to XSS token theft and stateless JWTs cannot be immediately revoked when a staff member is deactivated or changes role.
- **Consequences:**
  - Every backend route enforces authentication (`@login_required`), role permissions (`@require_role(...)`), and shop ownership (`@require_shop_access(...)`) server-side.

---

### ADR-004: Batch-Level Inventory as the Canonical Source of Stock Quantities

- **Status:** Accepted
- **Date:** 2026-10-12
- **Context:** Products in grocery/retail/pharmacy stores arrive in separate shipments (`batches`) with distinct expiry dates and purchase costs. Having an independent `total_stock` integer on the `products` table often drifts from batch quantities.
- **Decision:** Establish `inventory_batches` as the **canonical record of physical stock**, partitioned into four mutually exclusive quantity buckets per batch:
  - `available_qty` (sellable / transferable if not expired)
  - `reserved_qty` (locked for dual-approved outgoing transfers)
  - `damaged_qty` (unsellable due to physical damage)
  - `expired_qty` (unsellable due to passing `expiry_date`)
  Every mutation to any bucket must atomically write an immutable record to `stock_movements`.
- **Alternatives Considered:**
  1. *Product-level stock counter with optional batch tags:* Rejected because FEFO deduction and batch-specific expiry risk require exact per-batch balances.
- **Consequences:**
  - Shop-level product totals are always equal to the sum of their underlying batch buckets.
  - Non-perishable products still use an `inventory_batches` row (with `expiry_date = NULL`), keeping POS deduction and transfer workflows uniform.

---

### ADR-005: Surrogate `BIGINT` Primary Keys with Human-Readable Business Codes

- **Status:** Accepted
- **Date:** 2026-10-12
- **Context:** Developers, cashiers, shop owners, and hackathon judges need clean identifiers in UI tables, bills, and transfer records while maintaining relational performance.
- **Decision:** Use auto-incrementing `BIGINT` (`db.BigInteger` / `SERIAL`) primary keys (`id`) for internal relational joins and URLs, paired with indexed, human-readable `UNIQUE` business codes (`org_code`, `shop_code`, `sku`, `batch_code`, `invoice_number`, `transfer_code` e.g. `TRF-20261012-0001`).
- **Alternatives Considered:**
  1. *UUIDv4 Primary Keys everywhere:* Rejected for primary keys because 36-character UUIDs clutter vanilla JS templates, demo walkthroughs, and manual API testing, without offering advantages in a single-database monolith.
- **Consequences:**
  - Fast B-tree joins on `BIGINT` keys and human-friendly codes (`SKU-AMUL-MILK-1L`, `TRF-2026-0001`) in receipts and dashboards.

---

### ADR-006: Two-Tier Product Model (`products` Master SKU + `shop_products` Store Configuration)

- **Status:** Accepted
- **Date:** 2026-10-12
- **Context:** Each shop independently manages its products, stock, prices, and thresholds (Section 2 & 5B), yet the inter-store stock exchange (Section 5D) must match surplus of a product in Shop A with demand/low-stock of the **same product** in Shop B.
- **Decision:** Use a two-tier product structure:
  1. `products`: Organization-scoped canonical product identity (`organization_id`, `sku`, `name`, `category`, `unit`, `is_perishable`, `default_purchase_price`, `default_selling_price`). When a Shop Owner adds a product with a new SKU, it registers the canonical SKU in the organization catalog; when adding an existing SKU, it links to the canonical `product_id`.
  2. `shop_products`: Shop-specific catalog entry (`shop_id`, `product_id`, shop-specific `purchase_price`, `selling_price`, `low_stock_threshold`, `near_expiry_days`, `surplus_threshold`, `is_active`).
- **Alternatives Considered:**
  1. *Completely isolated `products` table per shop with no shared identifier:* Rejected because cross-shop surplus matching and transfer receipt creation would rely on error-prone fuzzy string matching of product names.
- **Consequences:**
  - Shops retain complete independence over which products they carry, their local prices, and their alert thresholds, while cross-shop transfer matching is 100% deterministic via `(organization_id, product_id)` / `sku`.

---

### ADR-007: Dual-Approval + Two-Step Handover Transfer State Machine

- **Status:** Accepted
- **Date:** 2026-10-12
- **Context:** Project Rules #17 and #18 mandate that a stock transfer must never complete merely because one shop owner accepts it, nor complete until both parties have approved it AND physical handover/receipt confirmation has occurred.
- **Decision:** Enforce a strict state machine on `transfer_requests`:
  1. `PENDING_SOURCE_APPROVAL` (if proposed by recipient/system) or `PENDING_RECIPIENT_APPROVAL` (if proposed and pre-approved by source shop owner).
  2. **No Double-Offering Guard:** Sum of open pending offers on a batch cannot exceed `batch.available_qty`.
  3. `APPROVED_RESERVED`: Reached only when **both** Source Shop Owner (`source_approved_at`) and Recipient Shop Owner (`recipient_approved_at`) have approved. At this transition, `SELECT ... FOR UPDATE` moves `quantity` from `available_qty` to `reserved_qty` on the source batch.
  4. `DISPATCHED_IN_TRANSIT`: Source Shop confirms physical handover/dispatch. Stock remains in `reserved_qty`.
  5. `COMPLETED`: Recipient Shop confirms physical receipt. In one ACID transaction, `reserved_qty` is deducted from the source batch (`TRANSFER_OUT`) and added to a batch in the recipient shop (`TRANSFER_IN`).
  6. Terminal non-completion states (`REJECTED`, `CANCELLED`, `EXPIRED_OFFER`, `RETURNED_TO_SOURCE`) safely release `reserved_qty` back to `available_qty` (if not expired) with a `TRANSFER_RELEASE` movement.
- **Consequences:**
  - Impossible to fabricate inventory, double-spend a batch, or complete a transfer unilaterally.

---

### ADR-008: Strict Reservation of AI Development for Phase 8 Only

- **Status:** Accepted (Non-Negotiable Rule #3 & #4)
- **Date:** 2026-10-12
- **Context:** Early introduction of ML/AI models before transactional inventory, POS sales, and transfer workflows are rock-solid leads to brittle demos and untestable logic.
- **Decision:** Phases 1 through 7 are **100% deterministic and rule-based**. No ML libraries (`scikit-learn`, `statsmodels`, `lightgbm`, etc.), model weights, or AI endpoints may be added before Phase 8. Phases 3–5 collect clean historical `sales`, `stock_movements`, and `inventory_batches` data so Phase 8 has structured data ready for training and evaluation.
- **Consequences:**
  - Phases 5–7 deliver a fully functional, verifiable rule-based surplus detection and stock exchange platform even before Phase 8 begins.

---

### ADR-009: Classical Statistical & Tabular ML Over SLMs for Phase 8 Forecasting

- **Status:** Accepted (Proposed for Phase 8)
- **Date:** 2026-10-12
- **Context:** Section 5G requires evaluating whether statistical forecasting, conventional ML, an SLM, or a combination is appropriate for Phase 8 tasks on a dataset of 7–10 products across 3–5 shops.
- **Decision:** For Phase 8 numerical demand forecasting and batch sell-through estimation, use **classical statistical time-series methods** (Moving Average baseline, Exponential Smoothing / Holt-Winters) and **interpretable tabular ML** (Ridge/Poisson Regression or Gradient Boosted Trees via `scikit-learn`) combined with deterministic survival/depletion math (`days_to_expiry` vs. `predicted_daily_demand`), plus template-based feature-attribution explanations. Do **not** require a Small Language Model (SLM) or external LLM API for numerical forecasting.
- **Rationale:**
  - SLMs/LLMs are poorly suited for calibrated numerical demand forecasting on small tabular datasets, introduce high latency/VRAM overhead, and risk hallucinating numbers.
  - Classical statistical models and `scikit-learn` regressors are fast, deterministic, reproducible offline, require minimal compute, and produce verifiable MAE/RMSE/MAPE metrics against a naive moving-average baseline.

---

### ADR-010: Synchronous In-App Notifications Driven by Database Events

- **Status:** Accepted
- **Date:** 2026-10-12
- **Context:** Section 5E prioritizes reliable in-app notifications without unnecessary infrastructure (Redis/Celery/external SMS gateways).
- **Decision:** Persist notifications in the `notifications` PostgreSQL table within the same service transaction (for transfer lifecycle events) or via an idempotent threshold evaluation service (for `LOW_STOCK` and `NEAR_EXPIRY` alerts using `dedup_key`), surfaced in the UI navbar badge and notifications center via REST polling/page load.
- **Consequences:**
  - Zero external message broker dependencies; 100% transactional reliability.

---

## Part 2 — Open Decisions & Ambiguities for User Review (`OD-001` to `OD-006`)

Per **Section 3** and **Section 8**, the following requirements had potential ambiguity. We have documented a **Recommended Default** for each so the documentation is complete and cohesive. Please confirm or adjust these defaults before Phase 1 begins:

| ID | Topic | Ambiguity Identified | Recommended Default (Used in Docs) | Impacted Modules |
| :--- | :--- | :--- | :--- | :--- |
| **OD-001** | **Shop Owner & Staff Multi-Shop Assignment** (Section 5A) | Can a single `SHOP_OWNER` or `STAFF` user be assigned to multiple shops simultaneously, or is each non-admin user bound to exactly one shop? | **Default: 1 Active Shop per `SHOP_OWNER` and `STAFF` in MVP**, enforced via `user_shop_assignments` (`is_primary = true`), while each shop must have at least one `SHOP_OWNER`. The schema supports many-to-many assignments if multi-shop switching is desired later. `SUPER_ADMIN` has read/admin access across all shops in the organization. | Auth, RBAC, Session Context (`DATABASE_SCHEMA.md`, `SECURITY.md`) |
| **OD-002** | **Who Can Initiate a Transfer Proposal?** (Section 5D) | Can only a Source Shop Owner propose a transfer of surplus stock, or can a Recipient Shop Owner also request stock from another shop's published surplus? | **Default: Both directions supported**, plus only `SHOP_OWNER` (not `STAFF`) can approve/reject. If Source Owner initiates, `source_approved_at` is set immediately and status starts at `PENDING_RECIPIENT_APPROVAL`. If Recipient Owner initiates from a published surplus listing, `recipient_approved_at` is set and status starts at `PENDING_SOURCE_APPROVAL`. | Transfers (`API_SPECIFICATION.md`, `docs/BUSINESS_RULES.md`) |
| **OD-003** | **Cross-Shop Visibility of Surplus Stock** (Section 3 Rule 15 & Section 5D) | Rule 15 forbids one shop from reading another shop's private inventory or sales. How does a shop discover eligible transfer opportunities? | **Default: Scoped Surplus Discovery Projection.** Shops never see another shop's raw inventory, cost prices, or sales ledger. Instead, the backend Transfer Matchmaker API exposes only anonymized/scoped **Eligible Surplus Candidates** (or matches targeting the recipient shop) containing only `source_shop_name`, `product_sku`, `surplus_offer_qty`, `expiry_date`, and `proposed_transfer_price`. | Transfers, Security (`SECURITY.md`, `API_SPECIFICATION.md`) |
| **OD-004** | **Transfer Pricing Default Rule** (Section 5D) | At what unit price is stock transferred between two shops in the same organization? | **Default: Source Batch `unit_cost` (At-Cost Internal Transfer)** by default, with an optional negotiated discount percentage (`0%–50%`) for near-expiry stock proposed by the Source Shop Owner. Logistics `transfer_cost` is tracked separately as an operational expense so inventory cost basis is not artificially inflated. | Transfers, Valuation (`docs/BUSINESS_RULES.md`) |
| **OD-005** | **Partial Receipt Handling in Transfers** (Section 5D) | When a dispatched transfer arrives at the recipient shop, what if 2 out of 20 units were damaged in transit? | **Default: Full or Discrepancy-Logged Receipt.** Recipient can either (a) Confirm full receipt (`received_qty == dispatched_qty`), (b) Log transit damage during receipt confirmation (`received_good_qty` enters recipient `available_qty`, while `transit_damaged_qty` is recorded as `MARK_DAMAGED` on the source batch before closing), or (c) Raise `DISPUTED` status if shipment is missing/refused. | Transfers, Inventory (`docs/BUSINESS_RULES.md`, `DATABASE_SCHEMA.md`) |
| **OD-006** | **Sales Return Window & Condition** (Section 5C) | Under what conditions can a completed POS sale be returned or cancelled? | **Default:** Full sale cancellation is allowed on the same business day (`status = CANCELLED`), restoring stock to the exact batch(es) deducted if non-expired. Partial or later item returns (`SALE_RETURN`) require selecting condition: `RESELLABLE` (returns to original `batch_id` `available_qty` if not expired) or `DAMAGED` (increments `damaged_qty`). | Sales, Inventory (`docs/BUSINESS_RULES.md`) |
