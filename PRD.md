# Sanchay AI — Product Requirements Document (PRD)

**Document Version:** 1.1.0  
**Date:** 2026-10-15  
**Status:** Fully Implemented & Verified (132/132 Tests Passing)  
**Product Name:** Sanchay AI — Multi-Store Inventory & Stock Redistribution Platform

---

## 1. Executive Summary

**Sanchay AI** is a centralized, multi-store inventory management, point-of-sale (POS) billing, and inter-store stock redistribution platform built for retail organizations operating multiple shops (e.g., grocery chains, convenience marts, dairy/perishable outlets, or pharmacy networks).

While each shop independently manages its daily catalog, batch-level stock, expiry dates, and customer sales, Sanchay AI connects all shops under one organization to solve a critical retail inefficiency: **simultaneous localized overstock/expiry wastage in one shop and stockouts/unmet demand in another shop.**

Sanchay AI provides:
1. **Batch-Level FEFO Inventory & POS Billing:** Granular tracking of batches, expiry dates, purchase costs, and automatic First Expired, First Out (FEFO) stock deduction.
2. **Controlled Dual-Approval Stock Redistribution:** A transactional workflow that detects surplus or near-expiry stock in a source shop, identifies eligible recipient shops with demand, and executes transfers only when **both** participating Shop Owners approve and physical dispatch/receipt is confirmed.
3. **Phased Delivery with AI Reserved for Phase 8:** Phases 1–7 establish a rock-solid, deterministic, rule-based operational platform. In **Phase 8 (Final Phase)**, AI Inventory Intelligence adds statistical/ML demand forecasting, batch sell-through probability before expiry, and explainable transfer recommendations.

---

## 2. Problem Statement

Multi-store retail organizations face three interconnected operational failures:
1. **Localized Expiry Wastage:** Perishable and dated goods (dairy, bakery, beverages, packaged foods) often sit unsold in lower-velocity shops until they expire, resulting in direct write-offs of purchase capital.
2. **Simultaneous Sister-Store Stockouts:** At the exact same time, another shop belonging to the same organization just a few kilometers away may be out of stock or experiencing high demand for that exact SKU, losing sales and customer trust.
3. **Uncoordinated & Unsafe Ad-Hoc Transfers:** When store managers attempt informal stock transfers via phone calls or spreadsheets, inventory records become desynchronized, stock is double-promised or lost in transit, and shop owners lack approval control or audit trails.

---

## 3. Target Users, Stakeholders & Personas

| Persona | Role Enum | Primary Responsibilities | Key Pain Points | Goals in Sanchay AI |
| :--- | :--- | :--- | :--- | :--- |
| **Rajesh Sharma — Organization Director** | `SUPER_ADMIN` | Oversees all shops in the organization, manages shop onboarding, user provisioning, monitors organization-wide wastage, revenue, and inter-store transfers. | No unified view of which stores are losing money to expiry vs. stockouts; cannot audit inter-store disputes. | Real-time organization dashboard, RBAC governance, audit logs, and measurable wastage reduction reports. |
| **Priya Nair — Shop Owner & Store Operator** | `SHOP_OWNER` | Full operational control of assigned shop: catalog, pricing, batch receipts, POS billing, stock adjustments, offline sync, and dual-approval transfer agreements. | Learns about expiring batches too late; reluctant to part with stock without transparent pricing and formal approval; offline counter sales delays. | Proactive near-expiry & surplus alerts, fast FEFO POS with offline reconciliation, thermal/A4 invoices, full dual-approval authority, and shop P&L. |

---

## 4. Goals and Non-Goals

### 4.1 Project Goals
- **G-01:** Maintain 100% reconcilable batch-level inventory (`available`, `reserved`, `damaged`, `expired`) backed by PostgreSQL ACID transactions and an immutable `stock_movements` ledger.
- **G-02:** Automate FEFO batch deduction during POS billing to minimize in-store expiry.
- **G-03:** Enable safe, auditable inter-store stock transfers requiring **both** Source and Recipient Shop Owner approvals plus explicit physical dispatch and receipt confirmations.
- **G-04:** Enforce strict backend RBAC and shop-level tenant isolation so shops cannot view or tamper with each other's private inventory or sales.
- **G-05:** Deliver a reliable hackathon demonstration (3–5 shops, 7–10 products) in Phases 1–7 using deterministic business rules, followed by verified AI demand forecasting and expiry-risk intelligence in **Phase 8**.

### 4.2 Explicit Non-Goals (Out of Scope)
- **NG-01 (No Scanning/Vision):** Barcode scanning, QR code scanning, camera-based input, OCR invoice reading, and image recognition models are **strictly out of scope** (`RULE-05`, `BR-019`).
- **NG-02 (No Early AI):** No AI/ML models, training pipelines, or AI recommendations before **Phase 8** (`RULE-03`, `BR-020`).
- **NG-03 (No External Payment Gateways):** POS billing records payment mode (`CASH`, `UPI_MANUAL`, `CARD_MANUAL`) for accounting only; live Stripe/Razorpay payment processing is out of scope unless separately approved.
- **NG-04 (No External Messaging Gateways by Default):** In-app notifications are mandatory; external SMS, WhatsApp, or email gateways are excluded from MVP scope unless separately approved.
- **NG-05 (No Cross-Organization Trading):** Transfers occur strictly between shops belonging to the same organization (`BR-012`).

---

## 5. Functional Requirements (`FR-001` to `FR-065`)

### Module A: Organization, Shop & Access Management (`FR-001` to `FR-012`)

| Req ID | Requirement Description | Priority | Phase | Related Rules |
| :--- | :--- | :--- | :--- | :--- |
| **FR-001** | The system shall support configuring an Organization record (`name`, `code`, `status`, default thresholds). | Must | Phase 2 | `BR-012` |
| **FR-002** | `SUPER_ADMIN` shall be able to register, view, edit, activate, and deactivate Shops within the organization, including address, city, coordinates (`latitude`, `longitude`), and contact info. | Must | Phase 2 | `BR-012`, `BR-014` |
| **FR-003** | The system shall support strictly two roles: `SUPER_ADMIN` and `SHOP_OWNER`. | Must | Phase 1/2 | `BR-014` |
| **FR-004** | `SUPER_ADMIN` shall be able to create and manage `SHOP_OWNER` users and assign them to specific shops (`user_shop_assignments`). | Must | Phase 2 | `OD-001`, `BR-014` |
| **FR-005** | `SHOP_OWNER` shall possess complete store operational authority (catalog pricing, batch inwarding, POS counter billing, stock adjustments, and transfer approvals). | Must | Phase 2 | `BR-013`, `BR-014` |
| **FR-006** | Users shall authenticate via email/username and password using secure server-side sessions and industry-standard password hashing (`scrypt`/`pbkdf2:sha256`). | Must | Phase 1 | `RULE-14` |
| **FR-007** | Authenticated users shall be able to log out (invalidating their session) and change their password. | Must | Phase 1 | `RULE-14` |
| **FR-008** | Deactivated users (`is_active = False`) or users assigned to an inactive shop (`status != 'ACTIVE'`) shall be blocked from logging in or executing API calls. | Must | Phase 1/2 | `BR-014` |
| **FR-009** | Every backend endpoint shall enforce role permissions and shop-level access isolation so a `SHOP_OWNER` can only access their assigned shop's data. | Must | Phase 2 | `BR-013`, `BR-014` |
| **FR-010** | `SUPER_ADMIN` shall have an organization-wide administrative overview across all shops. | Must | Phase 2/6 | `BR-014` |
| **FR-011** | Each shop shall have configurable default thresholds (`low_stock_default_threshold`, `near_expiry_days_threshold`). | Must | Phase 2 | `BR-015` |
| **FR-012** | All sensitive administrative actions (user creation, role changes, shop status changes) shall be recorded in `audit_logs`. | Must | Phase 2 | `BR-007` |

### Module B: Product Catalog, Batches & Inventory (`FR-013` to `FR-027`)

| Req ID | Requirement Description | Priority | Phase | Related Rules |
| :--- | :--- | :--- | :--- | :--- |
| **FR-013** | Authorized users (`SUPER_ADMIN`, `SHOP_OWNER`) shall manually create, edit, and deactivate products with `name`, `sku`, `category`, `unit`, `is_perishable`, `purchase_price`, and `selling_price`. | Must | Phase 3 | `RULE-06`, `BR-019` |
| **FR-014** | Each shop shall maintain a `shop_products` configuration linking the product to shop-specific selling/purchase prices and alert thresholds (`low_stock_threshold`, `near_expiry_days`, `surplus_threshold`). | Must | Phase 3 | `ADR-006` |
| **FR-015** | Stock shall be received at the batch level (`inventory_batches`) with `batch_code`, `manufactured_date`, `expiry_date` (mandatory if `is_perishable=True`), `unit_cost`, and `initial_qty`. | Must | Phase 3 | `BR-001` |
| **FR-016** | Each batch shall track `available_qty`, `reserved_qty`, `damaged_qty`, and `expired_qty`, reconciling strictly with shop product totals. | Must | Phase 3 | `BR-002`, `BR-003` |
| **FR-017** | `SHOP_OWNER` shall be able to perform authorized manual stock adjustments (`ADJUSTMENT_ADD`, `ADJUSTMENT_SUBTRACT`, `MARK_DAMAGED`, `MARK_EXPIRED`) with a mandatory reason text. | Must | Phase 3 | `BR-003`, `BR-007` |
| **FR-018** | Batches whose `expiry_date < CURRENT_DATE` shall automatically be excluded from sellable stock and have their remaining `available_qty` transitioned to `expired_qty`. | Must | Phase 3 | `BR-003`, `BR-004` |
| **FR-019** | Every batch receipt, adjustment, expiry transition, sale, return, and transfer reservation/completion shall create an immutable `stock_movements` entry. | Must | Phase 3 | `BR-002`, `BR-007` |
| **FR-020** | The system shall compute real-time low-stock status when a product's total sellable stock in a shop is `<= low_stock_threshold`. | Must | Phase 3/6 | `BR-015` |
| **FR-021** | The system shall compute near-expiry status for active batches where `0 <= (expiry_date - CURRENT_DATE) <= near_expiry_days`. | Must | Phase 3/6 | `BR-015` |
| **FR-022** | The system shall calculate inventory valuation per shop at both Cost Value (`effective_sellable_qty * unit_cost`) and Retail Value (`effective_sellable_qty * selling_price`). | Must | Phase 3/6 | `BR-017` |
| **FR-023** | Users shall be able to search, filter (by category, stock status, expiry risk, active/inactive), and paginate product and batch lists. | Must | Phase 3 | `API-INV-*` |
| **FR-024** | Users shall be able to view the complete chronological `stock_movements` history for any product or batch in their shop. | Must | Phase 3 | `BR-007` |
| **FR-025** | Deactivating a product or batch shall be blocked if it has non-zero `reserved_qty` in an active transfer. | Must | Phase 3/5 | `BR-005` |

### Module C: Point-of-Sale (POS) Sales & Billing (`FR-028` to `FR-037`)

| Req ID | Requirement Description | Priority | Phase | Related Rules |
| :--- | :--- | :--- | :--- | :--- |
| **FR-028** | `SHOP_OWNER` shall be able to create POS sales transactions by selecting products from their shop's sellable inventory and specifying quantities. | Must | Phase 4 | `BR-004` |
| **FR-029** | The backend shall validate that requested quantity does not exceed `effective_sellable_qty` and that selling prices and discounts are valid non-negative numbers. | Must | Phase 4 | `BR-004`, `BR-006` |
| **FR-030** | Upon sale submission, the system shall automatically deduct stock from active, non-expired batches in **First Expired, First Out (FEFO)** order (`expiry_date ASC NULLS LAST, received_at ASC, id ASC`) inside an ACID transaction with `SELECT ... FOR UPDATE`. | Must | Phase 4 | `BR-004`, `BR-006` |
| **FR-031** | The system shall generate sequential, printable receipts including compact ESC/POS **Thermal Slips** (80mm & 58mm layouts with auto-print) and formal **A4 Tax Invoices** with GST breakdown, terms, and authorized signatures. | Must | Phase 4 | `BR-007` |
| **FR-032** | Each `sale_items` record shall store both `unit_selling_price` and the batch's `unit_cost_price` at the moment of sale to preserve historical gross profit accuracy. | Must | Phase 4 | `BR-017` |
| **FR-033** | `SHOP_OWNER` shall be able to process full sale cancellations or partial item returns (`RESELLABLE` vs. `DAMAGED` condition), restoring stock to the original batch (if `RESELLABLE` and not expired) or `damaged_qty` / `expired_qty`. | Must | Phase 4 | `OD-006`, `BR-007` |
| **FR-034** | Users shall be able to view, filter (by date range, invoice number, status), and paginate their shop's sales transaction history. | Must | Phase 4 | `BR-013` |
| **FR-035** | The system shall provide daily sales, COGS, gross profit summaries, and best-selling product rankings per shop. | Must | Phase 4/6 | `BR-017` |
| **FR-036** | The system shall provide an Offline POS Bill Queue & Reconciliation engine (`POST /api/v1/shops/<id>/sales/offline-reconcile`) supporting client UUID deduplication (`ALREADY_SYNCED`), isolated per-bill failure handling, and stockout conflict isolation. | Must | Phase 4 | `BR-004` |

### Module D: Inter-Store Stock Exchange (`FR-038` to `FR-050`)

| Req ID | Requirement Description | Priority | Phase | Related Rules |
| :--- | :--- | :--- | :--- | :--- |
| **FR-038** | In Phase 5, the system shall identify surplus and near-expiry at-risk batches in each shop using deterministic, rule-based velocity and expiry coverage formulas (Section 2.3 of `docs/BUSINESS_RULES.md`). | Must | Phase 5 | `BR-015`, `BR-020` |
| **FR-039** | The system shall identify and rank eligible recipient shops within the same organization based on recipient stock need (`<= low_stock_threshold`), sales velocity, remaining shelf life, Haversine distance, and estimated transfer cost. | Must | Phase 5 | `BR-012`, `OD-003` |
| **FR-040** | A Shop Owner shall be able to create a proposed transfer offer specifying `source_shop_id`, `recipient_shop_id`, `product_id`, `batch_id`, `quantity`, `unit_transfer_price`, `estimated_transfer_cost`, and `notes`. | Must | Phase 5 | `BR-005`, `OD-002` |
| **FR-041** | The system shall prevent double-offering the same batch stock by verifying that the sum of open pending offers on a batch plus the new offer does not exceed `batch.available_qty`. | Must | Phase 5 | `BR-005` |
| **FR-042** | Every transfer shall require explicit approval from **both** the Source Shop Owner (`source_approved_at`) and the Recipient Shop Owner (`recipient_approved_at`). | Must | Phase 5 | `BR-008`, `RULE-17` |
| **FR-043** | Upon the second Shop Owner's approval (completing dual approval), the system shall atomically lock the batch row (`SELECT ... FOR UPDATE`), verify `available_qty >= quantity` and non-expiry, move `quantity` from `available_qty` to `reserved_qty` (`TRANSFER_RESERVE`), and set status to `APPROVED_RESERVED`. | Must | Phase 5 | `BR-005`, `BR-006`, `BR-008` |
| **FR-044** | Either Shop Owner shall be able to reject (`REJECTED`) or cancel (`CANCELLED`) a transfer prior to physical dispatch, automatically releasing any reserved stock (`reserved_qty -> available_qty` via `TRANSFER_RELEASE`). | Must | Phase 5 | `BR-010` |
| **FR-045** | Pending offers not dual-approved within `TRANSFER_OFFER_EXPIRY_HOURS` (default 24h) or reserved transfers not dispatched within `TRANSFER_DISPATCH_SLA_HOURS` (default 48h) shall expire (`EXPIRED_OFFER`) and release any reservation. | Must | Phase 5 | `BR-010`, `BR-016` |
| **FR-046** | From `APPROVED_RESERVED`, the Source Shop Owner shall record physical dispatch (`DISPATCHED_IN_TRANSIT`) with dispatch notes/timestamp. | Must | Phase 5 | `BR-009` |
| **FR-047** | From `DISPATCHED_IN_TRANSIT`, the Recipient Shop Owner shall confirm physical receipt (`COMPLETED`), which atomically deducts `reserved_qty` from the source batch (`TRANSFER_OUT`) and creates/increments the batch in the recipient shop (`TRANSFER_IN`) with preserved `expiry_date` and `unit_cost = unit_transfer_price`. | Must | Phase 5 | `BR-009`, `BR-011`, `RULE-18` |
| **FR-048** | If a dispatched shipment is not received within `TRANSFER_RECEIPT_SLA_HOURS` (72h) or has a reported transit problem, the recipient or source owner can mark it `DISPUTED`, keeping stock locked in `reserved_qty` until resolved (`COMPLETED` or `RETURNED_TO_SOURCE`). | Must | Phase 5 | `BR-016`, `OD-005` |
| **FR-049** | Every state change in a transfer's lifecycle shall be recorded in `transfer_status_logs` and `audit_logs`. | Must | Phase 5 | `BR-007` |
| **FR-050** | The system shall provide an Automated Stock Redistribution Recommendation Engine (`GET /api/v1/shops/<id>/transfers/redistribution-recommendations`) pairing surplus batches with deficit shops, balancing Haversine transit costs against salvage margins, with 1-click proposal pre-fills under the read-only AI guarantee. | Must | Phase 5/6 | `BR-012`, `BR-020` |

### Module E: In-App Notifications (`FR-051` to `FR-055`)

| Req ID | Requirement Description | Priority | Phase | Related Rules |
| :--- | :--- | :--- | :--- | :--- |
| **FR-051** | The system shall generate persistent in-app notifications for `LOW_STOCK`, `NEAR_EXPIRY`, `EXPIRED_BATCH`, `SURPLUS_DETECTED`, and all transfer lifecycle events (`TRANSFER_OFFER_RECEIVED`, `TRANSFER_APPROVED`, `TRANSFER_REJECTED`, `TRANSFER_CANCELLED`, `TRANSFER_DISPATCHED`, `TRANSFER_COMPLETED`, `TRANSFER_DISPUTED`). | Must | Phase 6 | `BR-015` |
| **FR-052** | `SUPER_ADMIN` shall be able to broadcast organization-wide announcements (`ORG_ANNOUNCEMENT`) visible to all shops. | Should | Phase 6 | `FR-010` |
| **FR-053** | Threshold-based notifications (`LOW_STOCK`, `NEAR_EXPIRY`) shall use a daily `dedup_key` so users are not spammed with duplicate alerts for the same batch on the same day. | Must | Phase 6 | `ADR-010` |
| **FR-054** | Users shall see an unread notification count badge in the navigation header and be able to mark individual or all notifications as read. | Must | Phase 6 | `ADR-010` |

### Module F: Analytics, Dashboards & Reports (`FR-056` to `FR-060`)

| Req ID | Requirement Description | Priority | Phase | Related Rules |
| :--- | :--- | :--- | :--- | :--- |
| **FR-056** | Each Shop Owner shall have a Shop Dashboard showing KPI cards (sellable inventory value, today's revenue/profit, low-stock count, near-expiry batch count, active transfers). | Must | Phase 6/8 | `BR-013`, `BR-017` |
| **FR-057** | `SUPER_ADMIN` shall have an Organization Executive Dashboard aggregating inventory value, revenue, wastage write-offs, and inter-store transfer metrics across all shops. | Must | Phase 6/8 | `BR-014`, `BR-017` |
| **FR-058** | Reports shall clearly separate **Confirmed Realized Wastage Avoided** (transferred near-expiry stock subsequently sold before expiry at the recipient shop) from **Estimated At-Risk Value Transferred**. | Must | Phase 6/8 | `BR-018` |
| **FR-059** | Authorized users shall be able to export Inventory Valuation, Near-Expiry Risk, Sales Summary, and Transfer History reports as **CSV** and printable **PDF** documents. | Must | Phase 6/8 | Section 4 & 5F |

### Module G: AI Inventory Intelligence (`FR-061` to `FR-065`) — FULLY IMPLEMENTED & VERIFIED

> **STATUS (`RULE-03`, `BR-020`):** Requirements `FR-061` through `FR-065` are fully implemented in `app/services/product_analysis_service.py` and `app/services/transfer_service.py`, powered by Scikit-Learn Ridge Regression with Poisson survival modeling, and 100% verified across unit and integration test suites.

| Req ID | Requirement Description | Priority | Phase | Related Rules |
| :--- | :--- | :--- | :--- | :--- |
| **FR-061** | The system shall train and evaluate statistical/ML demand forecasting models per `(shop_id, product_id)` using real PostgreSQL sales history to predict 7-day and 14-day demand with confidence intervals. | Must | **Phase 8/9 (VERIFIED)** | `BR-020`, `ADR-009` |
| **FR-062** | The system shall compute batch-level sell-through probability and expected unsold units at `expiry_date` (`expiry_risk_score` $\in [0, 1]$), accounting for FEFO queue order. | Must | **Phase 8/9 (VERIFIED)** | `BR-020` |
| **FR-063** | The system shall generate AI-ranked Cross-Store Transfer Recommendations pairing predicted surplus batches with recipient shops projected to absorb the stock before expiry, including net financial benefit after transfer cost. | Must | **Phase 8/9 (VERIFIED)** | `BR-020` |
| **FR-064** | Every AI recommendation shall display a human-readable explanation (key numerical drivers, predicted sell-through, confidence score) and allow a Shop Owner to convert it into a standard `transfer_requests` proposal (which still requires full dual approval). | Must | **Phase 8/9 (VERIFIED)** | `BR-008`, `BR-020` |
| **FR-065** | When historical sales data for a `(shop, product)` is below `AI_MIN_HISTORY_DAYS` (14 days), the AI module shall explicitly report `INSUFFICIENT_DATA` / `LOW_CONFIDENCE` and fall back transparently to deterministic EWMA or baseline rules. | Must | **Phase 8/9 (VERIFIED)** | `BR-020` |

---

## 6. Non-Functional Requirements (`NFR-001` to `NFR-012`)

| NFR ID | Category | Requirement Specification | Target Metric / Verification |
| :--- | :--- | :--- | :--- |
| **NFR-001** | **Data Integrity** | Zero negative stock quantities and 100% reconciliation between `inventory_batches` and `stock_movements`. | Verified by PostgreSQL `CHECK` constraints and automated reconciliation tests. |
| **NFR-002** | **Concurrency** | Simultaneous POS sales and transfer reservations on the same batch must serialize cleanly without race conditions or lost updates. | Verified by multi-threaded/concurrent `pytest` tests using PostgreSQL `SELECT ... FOR UPDATE`. |
| **NFR-003** | **Security & Isolation** | Zero cross-shop data leakage (`IDOR`); 100% server-side RBAC, CSRF protection, and `scrypt`/`pbkdf2` password hashing. | Verified by negative authorization tests in `tests/test_security_isolation.py`. |
| **NFR-004** | **Performance** | Standard POS billing, inventory search, and transfer approval API responses complete within `< 300ms` p95 for up to 50 shops and 10,000 batches. | Indexed foreign keys and composite B-tree indexes in PostgreSQL. |
| **NFR-005** | **Auditability** | Every state-changing action on users, inventory, sales, and transfers logs actor, timestamp, and before/after state in `audit_logs`. | Verified in integration tests. |
| **NFR-006** | **Maintainability** | Modular Flask Blueprints + Service Layer with clear separation of concerns and zero hardcoded business data. | Code structure audit in Phase 7. |
| **NFR-007** | **Testability** | Automated `pytest` suite running against an isolated `sanchay_ai_test` PostgreSQL database with `>= 85%` service-layer branch coverage. | Executed at every phase gate. |
| **NFR-008** | **Usability** | Clean, responsive HTML5/CSS3/JS interface usable on desktop/laptop screens (`1280x720` and above) without external SPA build chains. | Manual walkthrough + E2E verification. |

---

## 7. User Stories & Acceptance Criteria (`US-001` to `US-012`)

### US-001: Multi-Shop RBAC & Tenant Isolation
- **As a** Shop Owner of Shop 1, **I want** my inventory, batch costs, and POS sales to be private to Shop 1 **so that** other shops cannot view or alter my store's operations.
- **Acceptance Criteria (`AC-001`):**
  1. Logging in as Shop 1 Owner displays only Shop 1's products, batches, and sales.
  2. Sending `GET /api/v1/shops/2/inventory` or `POST /api/v1/shops/2/sales` with Shop 1 Owner's session returns `403 Forbidden` and logs an unauthorized access attempt.

### US-002: Batch-Level Stock Receipt & Reconciliation
- **As a** Shop Owner, **I want** to record incoming product deliveries with batch codes, unit purchase costs, and expiry dates **so that** my total stock is accurately tracked by batch.
- **Acceptance Criteria (`AC-002`):**
  1. Receiving 50 units of `SKU-MILK-1L` in batch `B-101` (expiring in 10 days) and 30 units in batch `B-102` (expiring in 20 days) shows total sellable stock = `80` units across 2 batches.
  2. Two `RECEIPT` rows are written to `stock_movements` summing to `+80`.

### US-003: POS Sale with Automatic FEFO Deduction
- **As a** Shop Owner or Store Operator, **I want** the POS system to automatically deduct sold units from the earliest-expiring active batch **so that** older batches sell first without manual guesswork.
- **Acceptance Criteria (`AC-003`):**
  1. If Batch `B-101` (expires in 5 days) has `10` available units and Batch `B-102` (expires in 15 days) has `20` available units, selling `15` units automatically deducts `10` units from `B-101` (depleting it to `0`) and `5` units from `B-102` (leaving `15`).
  2. If Batch `B-100` is already expired (`expiry_date < CURRENT_DATE`), it is skipped and moved to `expired_qty`.
  3. Attempting to sell `35` units when only `30` sellable units exist returns `409 Conflict` (`INSUFFICIENT_STOCK`) and leaves all batch quantities unchanged.

### US-004: Rule-Based Surplus Identification & Transfer Proposal
- **As a** Source Shop Owner with surplus near-expiry stock, **I want** to see eligible sister shops that need this product and propose a stock transfer **so that** the stock can be sold instead of expiring.
- **Acceptance Criteria (`AC-004`):**
  1. Shop 1 has 40 units of Paneer expiring in 6 days with daily sales of 2 units/day (projected unsold surplus = 28 units).
  2. Shop 2 has only 3 units of Paneer (below its threshold of 10) and sells 6 units/day.
  3. The system highlights Shop 1's batch as surplus and recommends Shop 2 as an eligible recipient.
  4. When Shop 1 Owner proposes transferring 25 units to Shop 2, the transfer enters `PENDING_RECIPIENT_APPROVAL` with `source_approved_at` populated.
  5. Shop 1 cannot create a second offer for another 20 units from that same 40-unit batch while the 25-unit offer is open (`409 OFFER_EXCEEDS_ELIGIBLE_STOCK`).

### US-005: Dual Approval & Stock Reservation
- **As a** Recipient Shop Owner, **I want** to review incoming transfer offers (product, expiry date, quantity, transfer unit price) and explicitly approve or reject them **so that** no unwanted stock is forced onto my store.
- **Acceptance Criteria (`AC-005`):**
  1. When Shop 2 Owner approves the 25-unit transfer offer, the transfer status transitions to `APPROVED_RESERVED` (never directly to `COMPLETED`).
  2. Exactly 25 units on Shop 1's batch move from `available_qty` to `reserved_qty` (`TRANSFER_RESERVE`), preventing Shop 1's POS from selling those 25 units.
  3. If Shop 2 Owner rejects the offer instead, status becomes `REJECTED` and Shop 1's `available_qty` remains unreserved.

### US-006: Two-Step Physical Handover (Dispatch & Receipt Confirmation)
- **As** participating Shop Owners, **we want** inventory balances to transfer only after Source confirms dispatch and Recipient confirms physical receipt **so that** goods in transit or lost shipments never corrupt store books.
- **Acceptance Criteria (`AC-006`):**
  1. Attempting to call the `confirm-receipt` endpoint while status is `APPROVED_RESERVED` (before dispatch) fails with `409 INVALID_TRANSFER_STATE_TRANSITION`.
  2. Source Shop marks the transfer `DISPATCHED_IN_TRANSIT`; the 25 units remain in Source Shop's `reserved_qty`.
  3. Recipient Shop Owner confirms receipt (`COMPLETED`); in a single ACID transaction, Source Shop's `reserved_qty` decreases by 25 (`TRANSFER_OUT`) and Recipient Shop gains a batch of 25 sellable units (`TRANSFER_IN`) with the original expiry date preserved.
  4. Attempting to call `confirm-receipt` a second time returns `409 TRANSFER_ALREADY_COMPLETED` with zero duplicate inventory changes.

### US-007: Phase 8 AI Demand Forecasting & Explainable Transfer Recommendations
- **As a** Shop Owner (in Phase 8), **I want** data-driven demand forecasts and expiry-risk explanations backed by historical sales **so that** I can anticipate surplus earlier than static thresholds alone.
- **Acceptance Criteria (`AC-007` — Phase 8 Only):**
  1. Model evaluation pipeline compares statistical/ML forecast error (MAE, RMSE, MAPE) against a 7-day moving average baseline on held-out test windows.
  2. Each AI recommendation displays predicted daily demand, days to expiry, expected unsold units, confidence level, and plain-English feature explanation.
  3. AI recommendations cannot auto-execute transfers; clicking "Create Transfer Proposal" enters the standard dual-approval workflow (`FR-042`).

---

## 8. Success Metrics

1. **Inventory Reconciliation Accuracy:** `100%` match between batch buckets (`available + reserved + damaged + expired`) and `stock_movements` sum across all automated tests.
2. **Transfer Safety Compliance:** `0` unilateral transfer completions and `0` negative stock occurrences under concurrent load testing.
3. **RBAC Isolation Score:** `100%` of cross-shop unauthorized API requests blocked with `403 Forbidden`.
4. **Demo Effectiveness:** Successful end-to-end demonstration of the 4-shop, 9-product hackathon scenario (`DEMO_PLAN.md`) showing measurable reduction in near-expiry wastage through inter-store redistribution.

---

## 9. Risks, Assumptions & Open Questions

### 9.1 Risks & Mitigations
- **Risk R-01 (Sparse Historical Data for Phase 8 AI):** A demo with 7–10 products and 3–5 shops has limited real-world history.
  - *Mitigation:* Seed script (`scripts/seed_demo_data.py` in Phase 7) will populate 60–90 days of realistic, seasonal/weekday-patterned PostgreSQL sales records per shop, and Phase 8 will use statistical/tabular models suited for small sample sizes with explicit `INSUFFICIENT_DATA` fallbacks (`ADR-009`).
- **Risk R-02 (Concurrent Sale vs. Transfer Approval Race Condition):** A cashier sells the last units of a batch at the exact millisecond a recipient shop approves a transfer offer.
  - *Mitigation:* Both operations lock the target `inventory_batches` row via `SELECT ... FOR UPDATE` ordered by `batch.id`. The second transaction sees the updated balance and returns a clean `409 Conflict`.

### 9.2 Open Questions Requiring Stakeholder Approval
All identified ambiguities (`OD-001` through `OD-006`) and their recommended defaults are documented in [`DECISIONS.md` — Part 2](file:///d:/Ashutosh_01/Sanchay%20AI/DECISIONS.md).

---

## 10. MVP Scope (Phases 1–7) vs. Phase 8 Scope vs. Future Post-Hackathon Scope

| Feature Area | MVP Scope (Phases 1–7) | Final Phase Scope (Phase 8) | Post-Hackathon Future Scope (Out of Scope Now) |
| :--- | :--- | :--- | :--- |
| **Catalog & Inventory** | Manual entry, multi-batch, FEFO, expiry sweep, stock ledger | Feeds historical features to AI models | Supplier purchase orders, automated EDI restocking |
| **Sales & Billing** | Manual POS, FEFO deduction, printable invoices, returns | Provides daily demand time series | Hardware thermal printer drivers, live UPI/card gateway |
| **Surplus & Transfers** | Rule-based velocity/expiry surplus detection, dual-approval + dispatch/receipt state machine | AI expiry-risk scoring & smart transfer ranking with explanations | Multi-stop delivery routing optimization, third-party courier APIs |
| **Notifications** | Synchronous in-app notifications with deduplication | High-risk AI expiry prediction alerts | WhatsApp/SMS/Email external push channels |
