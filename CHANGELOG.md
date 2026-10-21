# Changelog

All notable changes to **Sanchay AI** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to sequential phase-gated delivery as defined in [`IMPLEMENTATION_ROADMAP.md`](file:///d:/Ashutosh_01/Sanchay%20AI/IMPLEMENTATION_ROADMAP.md).

---

## [1.2.0] — 2026-10-21 (Carbon Credits & Sustainability Impact System)

### Added & Enhanced
- **Comprehensive Multi-Category Food Emission Registry:**
  - Designed and implemented `EmissionFactor` model ([`app/models/sustainability.py`](file:///c:/omkar/Sanchay%20AI/app/models/sustainability.py)) supporting all 14 food categories: Fruits, Vegetables, Grains and cereals, Rice and wheat, Pulses and legumes, Dairy products, Bakery products, Meat and poultry, Seafood, Packaged foods, Beverages, Frozen foods, Ready-to-eat meals, and Other food products.
  - Authored extensible reference seed dataset ([`scripts/seed_emission_factors.py`](file:///c:/omkar/Sanchay%20AI/scripts/seed_emission_factors.py)) providing 24 defensible, traceable emission factors derived from published FAO, Poore & Nemecek (2018 Science), and EPA WARM lifecycle benchmarks.
  - Extensible architecture allowing addition of new products and categories via database records without changing calculation logic.
- **Scientific Environmental Impact Calculation Engine:**
  - Implemented `SustainabilityService` ([`app/services/sustainability_service.py`](file:///c:/omkar/Sanchay%20AI/app/services/sustainability_service.py)) with strict conceptual separation between:
    1. Estimated Net Avoided Emissions ($\text{kg CO}_2\text{e}$).
    2. Smart Vendor Sustainability Points (configurable 10 pts per $1\text{ kg CO}_2\text{e}$ net positive benefit).
    3. Official Carbon Credits (1 credit = $1{,}000\text{ kg CO}_2\text{e}$ non-tradable internal estimate).
  - Robust 5-tier emission factor resolution: Exact product name $\to$ Substring keyword match $\to$ Subcategory fallback $\to$ Category fallback $\to$ In-memory seed fallback $\to$ `PENDING` status.
  - Unit normalizer converting grams, kilograms, millilitres, litres, and discrete pieces (using defensible per-item piece weights: apples 180g, eggs 50g, bread 450g, etc.) with high-precision Decimal arithmetic.
- **Rigorous Eligibility & Transport Deduction Rules:**
  - Stock transfers are not assumed to prevent waste automatically. Transfers must reach `COMPLETED` status with unexpired batches, positive verified received quantity, and valid origin/destination shops.
  - Attributable transport emissions calculation using inter-store Haversine distance, default road freight emission factor ($0.21\text{ kg CO}_2\text{e/km}$ for light commercial vehicles), and refrigerated cold-chain multiplier ($+18\%$ for dairy, meat, seafood, frozen, ready-to-eat).
  - Non-negativity guard: If additional transport emissions exceed avoided emissions ($\text{Net} \le 0$), 0 sustainability points are awarded and the event is recorded as `INELIGIBLE`.
- **Fair 50/50 Multi-Store Attribution & Reversible Audit Ledger:**
  - Fair 50/50 split of net avoided emissions and sustainability points between the origin/sending store (salvaging at-risk inventory) and destination/receiving store (re-routing to active demand without new production).
  - Append-only transactional audit trail in `SustainabilityLedger` with unique event constraints preventing duplicate point awards.
  - Full Super Admin reversal capability (`POST /api/v1/sustainability/reversals/<event_id>`) generating offsetting ledger entries (`IS_REVERSED`) without rewriting historical records.
- **Automated Workflow Hook into Stock Transfers:**
  - Integrated `SustainabilityService.process_transfer_sustainability_event` directly into `TransferService.complete_transfer` ([`app/services/transfer_service.py`](file:///c:/omkar/Sanchay%20AI/app/services/transfer_service.py)) on physical delivery and batch receipt.
- **RESTful API & Scoped RBAC:**
  - Registered `sustainability_bp` under `/api/v1/sustainability/` ([`app/blueprints/sustainability/routes.py`](file:///c:/omkar/Sanchay%20AI/app/blueprints/sustainability/routes.py)):
    - `GET /api/v1/sustainability/dashboard` — High-level organization or store KPI metrics.
    - `GET /api/v1/sustainability/leaderboard` — Store sustainability rankings with net emissions, points, and event counts.
    - `GET /api/v1/sustainability/history` — Paginated and filtered event ledger.
    - `GET /api/v1/sustainability/breakdown` — Category-wise environmental impact distribution.
    - `GET /api/v1/sustainability/factors` — Reference emission factor registry.
    - `GET /api/v1/sustainability/events/<id>` — Detailed calculation trace for auditability.
    - `POST /api/v1/sustainability/process-transfer/<id>` — Manual transfer evaluation trigger.
    - `POST /api/v1/sustainability/reversals/<id>` — Super Admin reversal and adjustment endpoint.
  - Strict tenant isolation using `@verify_organization_access` and `@verify_shop_access`.
- **Interactive Sustainability Web Dashboard:**
  - Responsive Jinja2 dashboard ([`app/templates/sustainability/dashboard.html`](file:///c:/omkar/Sanchay%20AI/app/templates/sustainability/dashboard.html)) adhering strictly to Sanchay AI dark/emerald design tokens:
    - 5 Real-time KPI summary cards (Net Avoided Emissions, Sustainability Points, Food Saved, Active Stores, Equivalent Official Credits).
    - Sustainability Leaderboard table with rank badges, store metrics, and progress visualization.
    - Category Impact progress bars.
    - Complete Sustainability Audit Event Ledger with confidence badges and interactive calculation trace modal.
    - Factor Registry view with source citations, uncertainty ranges, and methodology version disclosures.
  - Added primary navigation links in [`app/templates/base.html`](file:///c:/omkar/Sanchay%20AI/app/templates/base.html) with custom Leaf/Eco SVG icon for both `SUPER_ADMIN` and `SHOP_OWNER`.
- **Alembic Database Migration:**
  - Created migration `0008_carbon_credits_and_sustainability.py` ([`migrations/versions/0008_carbon_credits_and_sustainability.py`](file:///c:/omkar/Sanchay%20AI/migrations/versions/0008_carbon_credits_and_sustainability.py)) adding `emission_factors`, `sustainability_events`, and `sustainability_ledger` with proper foreign keys, indices, and check constraints.
- **Automated Test Suite & Documentation:**
  - Implemented 42 automated tests in [`tests/test_sustainability_carbon_credits.py`](file:///c:/omkar/Sanchay%20AI/tests/test_sustainability_carbon_credits.py) covering all 20 specification criteria, Section 5 Apple calculation ($12\text{ kg} \times 0.8 - 1.5 = 8.1\text{ kg CO}_2\text{e} \to 81\text{ pts}$), unit conversions, cold-chain transport, multi-store splits, reversals, and REST APIs. **100% pass rate (42 passed in 0.18s)**.
  - Authored comprehensive scientific methodology document [`docs/SUSTAINABILITY_METHODOLOGY.md`](file:///c:/omkar/Sanchay%20AI/docs/SUSTAINABILITY_METHODOLOGY.md).
  - Updated [`DATABASE_SCHEMA.md`](file:///c:/omkar/Sanchay%20AI/DATABASE_SCHEMA.md) (Section 2.13) and [`API_SPECIFICATION.md`](file:///c:/omkar/Sanchay%20AI/API_SPECIFICATION.md) (Module H).

---

## [1.1.0] — 2026-10-15 (Strict 2-Role RBAC, AI Demand Forecasting, Waste Mitigation, Offline POS Sync, Invoicing & Stock Redistribution)

### Added & Enhanced
- **Strict 2-Role Security Architecture (`SUPER_ADMIN` and `SHOP_OWNER`):**
  - Streamlined RBAC by completely removing the `STAFF` role across migrations (`0007_two_roles.py`), SQLAlchemy models (`User`, `Organization`), service layer, form validators, HTML templates, and CLI tools.
  - Shop Owners have complete operational control over their assigned stores: catalog pricing, batch inwarding, POS counter checkout, offline sync, stock adjustments, and dual-approval transfers.
- **Scikit-Learn AI Demand Forecasting & Predictive Stockout Engine:**
  - Implemented `ProductAnalysisService` in [`app/services/product_analysis_service.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/product_analysis_service.py) with Ridge regression, calendar seasonality (day-of-week one-hot encoding), linear trend, and rolling sales velocity.
  - Tiered fallback: Ridge regression for $\ge 14$ days, EWMA for 3–13 days, moving average baseline for $< 3$ days.
  - Projections across 7-day, 14-day, and 30-day forecast horizons with $90\%$ confidence intervals.
  - Days of Inventory (DOI) computation and predicted calendar date of stockout.
  - Added REST API endpoint: `GET /api/v1/shops/<shop_id>/products/<product_id>/forecast`.
  - Verified with 6 tests in [`tests/test_ai_demand_forecasting.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_ai_demand_forecasting.py).
- **Poisson Survival Modeling & Waste Mitigation Engine:**
  - Added `analyze_shop_waste_mitigation` computing batch-level Poisson cumulative survival probabilities modeling the chance of expiry before sale.
  - Calculates at-risk inventory capital, projected waste losses vs potential salvage value, and recommends prioritized actions (promotional markdowns, inter-shop transfers, bundling).
  - Web view at `/shops/<shop_id>/waste-mitigation` and JSON API `GET /api/v1/shops/<shop_id>/waste-mitigation`.
  - Verified with 3 tests in [`tests/test_waste_mitigation_engine.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_waste_mitigation_engine.py).
- **Offline POS Counter Queue & Reconciliation:**
  - Added `SalesService.reconcile_offline_sales` and REST endpoint `POST /api/v1/shops/<shop_id>/sales/offline-reconcile`.
  - Handles client UUID deduplication (`ALREADY_SYNCED`), atomic FEFO batch deductions, and isolated per-bill stockout conflict resolution (`CONFLICT_INSUFFICIENT_STOCK`).
  - Verified with 3 tests in [`tests/test_offline_pos_reconciliation.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_offline_pos_reconciliation.py).
- **Printable Invoices: Thermal Slips & A4 Tax Invoices:**
  - Added ESC/POS monospaced thermal slip (`GET /shops/<shop_id>/sales/<sale_id>/thermal-slip`) supporting 80mm and 58mm layouts (`?width=58`) with optional auto-print query parameter (`?autoprint=1`).
  - Added formal A4 Tax Invoice (`GET /shops/<shop_id>/sales/<sale_id>/tax-invoice`) with GST breakdown, terms, and authorized signatory blocks.
  - Linked new formats in receipt and invoice views.
  - Verified with 3 tests in [`tests/test_invoicing_thermal_slip.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_invoicing_thermal_slip.py).
- **Automated Stock Redistribution Recommendation Engine:**
  - Added `TransferService.get_redistribution_recommendations` pairing surplus source batches with stockout shops within the organization.
  - Evaluates Haversine geographical distance and transit costs against gross salvage margins to prioritize transfers (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`).
  - 1-click proposal creation pre-filling transfer parameters with strict read-only AI guarantee.
  - Web view at `/shops/<shop_id>/transfers/recommendations` and JSON API `GET /api/v1/shops/<shop_id>/transfers/redistribution-recommendations`.
  - Verified with 3 tests in [`tests/test_redistribution_recommendations.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_redistribution_recommendations.py).
- **Full Automated Test Suite:**
  - 100% pass rate: **132 passed, 0 failed** in `pytest`.

---

## [1.0.0] — 2026-10-15 (Production Polish, Design System Overhaul & Decoupled Product Analysis Module)

### Added & Enhanced
- **Strict Color Tokens & Design System Overhaul:**
  - Centrally defined CSS custom properties in [`app/static/css/main.css`](file:///d:/Ashutosh_01/Sanchay%20AI/app/static/css/main.css) adhering strictly to product design requirements:
    - **Dark Theme (Default):** Deep green-black background (`#0F1412`), surface (`#171E1B`), elevated surface (`#1F2824`), border (`#2C3733`), text primary (`#ECF1EE`), text secondary (`#9AA9A2`), primary emerald accent (`#2DD4A0`), hover (`#25B88B`), secondary amber accent (`#F5B544`), danger (`#EF6B5B`), success (`#4ADE80`), and info (`#5EC4D9`).
    - **Light Theme:** Warm off-white background (`#F6F4EF`), ink text (`#1B2420`), matching emerald and amber accents.
  - Eliminated all purple/violet gradients and generic white-and-blue SaaS styling across all components.
  - Immediate `<head>` script in [`app/templates/base.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/base.html) reads `localStorage.getItem('sanchay_theme')` synchronously to prevent any flash of unstyled theme (FOUC).
  - Added interactive Theme Toggle button with animated Sun/Moon icons in the main navigation.
- **Decoupled AI Product Analysis Module:**
  - Cleanly decoupled architecture with zero external AI/ML dependencies in this release:
    - [`app/services/product_analysis_service.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/product_analysis_service.py) — Core service engine defining typed dataclasses: `ProductAnalysisInput`, `DemandProjection`, `ExpiryRiskAssessment`, `CandidateRecipientShop`, and `ProductAnalysisResult`.
    - [`services/productAnalysis.py`](file:///d:/Ashutosh_01/Sanchay%20AI/services/productAnalysis.py) — Compatibility facade re-exporting the service and contracts for easy future plug-and-play AI model integration.
  - Dedicated "Product Analysis" navigation item with an AI pill badge in [`app/templates/base.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/base.html).
  - Dedicated placeholder UI at `/product-analysis` and `/shops/<shop_id>/product-analysis` ([`app/templates/analysis/product_analysis_placeholder.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/analysis/product_analysis_placeholder.html)):
    - Highlights the future AI redistribution engine's three architectural pillars: Demand Forecasting, Expiry-Risk Trajectory, and Smart Inter-Store Matching.
    - Live interactive simulation sandbox allowing shop owners to test projections on real store products with zero transactional mutations.
    - Documented JSON input/output schema contracts for future model developers.
  - Preview REST API endpoint: `GET /api/v1/shops/<shop_id>/product-analysis/preview`.
  - Comprehensive test suite in [`tests/test_product_analysis_placeholder.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_product_analysis_placeholder.py) (3 passed).
- **UX, Interaction & Error Handling Polish:**
  - Double-click form submission guard in [`app/static/js/api.js`](file:///d:/Ashutosh_01/Sanchay%20AI/app/static/js/api.js) automatically preventing duplicate submissions across all forms.
  - Lightweight toast notification system (`window.showToast(message, type, duration)`) for immediate non-intrusive feedback.
  - Responsive vector charts in [`app/static/js/charts.js`](file:///d:/Ashutosh_01/Sanchay%20AI/app/static/js/charts.js) dynamically adapting to the active theme palette (`getThemeColors()`).
  - Enhanced form validation, accessible focus states, and contextual empty states across all views.
- **Automated Regression Suite:**
  - Full test suite passing 100%: **114 passed, 0 failed** across 9 test files.

---

## [0.9.0] — 2026-10-14 (Phase 8: Dashboards, Real-Time Analytics & CSV Reports)

### Added
- **Super Admin Organization Dashboard:**
  - Route `/admin/dashboard` in [`app/blueprints/admin/routes.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/blueprints/admin/routes.py) displaying total shops, active products, total stock quantity, gross sales revenue, and completed transfers.
  - Organization-wide inventory risk overview: near-expiry batches, low-stock products, and surplus stock.
  - Single-click CSV export: `GET /admin/dashboard/export`.
- **Shop Owner Store Dashboard:**
  - Route `/shops/<shop_id>/dashboard` displaying shop-scoped sales, inventory stock levels, low-stock alerts, and near-expiry warnings.
  - Inbound and outbound transfer summary cards with direct action links to the transfer workflow.
  - Recent sales and stock movements table with status badges.
  - Single-click CSV export: `GET /shops/<shop_id>/dashboard/export`.
- **Real-Time Analytics & SVG Vector Charts:**
  - [`app/static/js/charts.js`](file:///d:/Ashutosh_01/Sanchay%20AI/app/static/js/charts.js) — Lightweight, responsive SVG vector charts with zero external charting dependencies:
    - Daily and monthly sales trend line/bar charts (`GET /api/v1/shops/<id>/analytics/sales-trends`).
    - Best-selling products horizontal bar chart (`GET /api/v1/shops/<id>/analytics/top-products`).
    - Stock distribution by category donut chart (`GET /api/v1/shops/<id>/analytics/stock-distribution`).
    - Inter-store transfer activity breakdown (`GET /api/v1/shops/<id>/analytics/transfers`).
- **Reports Engine & CSV Streaming Export:**
  - [`app/services/report_service.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/report_service.py) — Transactional reporting engine supporting 4 report types:
    - Sales Summary Report (gross sales, discounts, refunds, net revenue, payment breakdown).
    - Inventory Valuation & Stock Status Report (available, reserved, damaged, total units, valuation at purchase and selling price).
    - Near-Expiry & Waste Risk Report (batch code, expiry date, days to expiry, value at risk).
    - Inter-Store Transfer Activity Report (transfer codes, source/destination shops, quantities, status, transit damage).
  - Filtering by date range (7d, 30d, 90d, custom date range), shop, product, and category.
  - [`app/blueprints/reports/routes.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/blueprints/reports/routes.py) & [`app/templates/reports/index.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/reports/index.html).
  - Streaming CSV export endpoint: `GET /api/v1/reports/export-csv`.
- **Automated Testing:**
  - [`tests/test_phase8_reports_dashboards.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_phase8_reports_dashboards.py) — 14 comprehensive tests (`14 passed`) validating RBAC access, metric accuracy against raw PostgreSQL records, filter boundaries, CSV export contents, and shop data isolation.

---

## [0.8.0] — 2026-10-13 (Phase 7: Notifications & Alerts — In-App Event Alerts & Lifecycle Tracking)

### Added
- **PostgreSQL Database Model & Migration (`0006_phase7_notifications`):**
  - [`app/models/notification.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/notification.py) — Declarative [`Notification`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/notification.py) model with `recipient_user_id`, `organization_id`, `shop_id`, `notification_type`, `severity`, `title`, `message`, `is_read`, `read_at`, `related_entity_type`, `related_entity_id`, and `dedup_key`.
  - Database-level constraints: `uq_notifications_user_dedup UNIQUE(recipient_user_id, dedup_key)` and `ck_notifications_severity CHECK(severity IN ('INFO', 'WARNING', 'CRITICAL'))`.
  - Optimized indexes: `idx_notifications_user_unread`, `idx_notifications_user_created`, `idx_notifications_shop`, `idx_notifications_org`, and `idx_notifications_dedup`.
  - [`migrations/versions/0006_phase7_notifications.py`](file:///d:/Ashutosh_01/Sanchay%20AI/migrations/versions/0006_phase7_notifications.py) — Reversible Alembic migration script (`upgrade` and `downgrade` verified against local PostgreSQL database).
- **Notification Service Layer (`app/services/notification_service.py`):**
  - [`NotificationService`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/notification_service.py) providing:
    - `create_notification`: Idempotent creation enforcing unique `dedup_key` per user.
    - `notify_shop_users`: Shop-scoped recipient resolution for active `SHOP_OWNER` and `STAFF` users via `UserShopAssignment`.
    - `list_user_notifications`: User-scoped paginated listing with read status and event type filters.
    - `get_unread_count`: High-performance indexed count for navbar live badge polling.
    - `mark_as_read` & `mark_all_as_read`: Strict ownership validation and atomic `read_at` timestamping.
    - `check_and_notify_inventory_alerts`: Evaluates low-stock and batch expiry conditions with daily deduplication.
    - `check_and_notify_stock_risk_alerts`: Evaluates Phase 5 critical risk and surplus candidates.
- **In-Transaction Transfer Lifecycle Integration (`app/services/transfer_service.py`):**
  - Integrated notification dispatches directly into `create_transfer_proposal`, `approve_transfer`, `reject_transfer`, `cancel_transfer`, `dispatch_transfer`, and `confirm_receipt` within the same transaction boundary. If any step fails, notifications roll back atomically.
- **Notifications Blueprint, REST APIs & UI:**
  - [`app/blueprints/notifications/routes.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/blueprints/notifications/routes.py) — Web UI route `/notifications` and REST APIs (`GET /api/v1/notifications`, `GET /api/v1/notifications/unread-count`, `POST /api/v1/notifications/{id}/read`, `POST /api/v1/notifications/read-all`, `POST /api/v1/shops/{id}/inventory/check-alerts`).
  - [`app/templates/notifications/list.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/notifications/list.html) — Full Notification Center with filter bar, mark-as-read AJAX, evaluate shop alerts button, and direct links to transfers/products/batches.
  - [`app/templates/base.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/base.html) — Navbar bell icon with live unread badge and lightweight 45-second client polling.
  - [`app/static/css/main.css`](file:///d:/Ashutosh_01/Sanchay%20AI/app/static/css/main.css) — Dedicated styling for notification cards, severity accents, and badge counters.
- **Automated Tests:**
  - [`tests/test_phase7_notifications.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_phase7_notifications.py) — 12 comprehensive tests covering all required notification lifecycle scenarios (`12 passed`). Full regression suite passes `97/97` tests across all Phases 1–7.

---

## [0.7.0] — 2026-10-13 (Phase 6: Inter-Store Stock Exchange — Fast MVP Dual-Approval Workflow)

### Added
- **PostgreSQL Schema & Reversible Alembic Migration (`0005_phase6_transfers`):**
  - [`app/models/transfer.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/transfer.py) — Declarative models for [`TransferRequest`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/transfer.py) (`transfer_requests` table with `uq_transfer_requests_code`, `ck_transfer_different_shops`, `ck_transfer_quantity_positive`, `ck_transfer_price_nonnegative`, `ck_transfer_status_valid`) and [`TransferStatusLog`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/transfer.py) (`transfer_status_logs` audit trail with `ck_status_logs_status_valid`).
  - [`migrations/versions/0005_phase6_transfers.py`](file:///d:/Ashutosh_01/Sanchay%20AI/migrations/versions/0005_phase6_transfers.py) — Verified reversible migration forward (`upgrade`) and backward (`downgrade`) preserving Phases 1–5 data.
- **Transactional Inter-Store Transfer Domain Service:**
  - [`app/services/transfer_service.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/transfer_service.py) — Comprehensive [`TransferService`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/transfer_service.py) implementing:
    - [`get_eligible_recipients`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/transfer_service.py): Scoped sister-store discovery within the same organization, surfacing current sellable stock and replenishment deficit indicators without exposing private sales history.
    - [`create_transfer_proposal`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/transfer_service.py) (`API-027`): Generates sequential `TRF-YYYYMMDD-XXXX` codes, enforces active product/shop checks, non-expired batch validation, positive quantity validation, and anti-double-offering checks against open pending proposals (`PENDING_RECIPIENT_APPROVAL` if initiated by source owner; `PENDING_SOURCE_APPROVAL` if initiated by recipient owner).
    - [`approve_transfer`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/transfer_service.py) (`API-030`, `BR-008`): Enforces counterparty `SHOP_OWNER` approval, locks source batch via `SELECT ... FOR UPDATE`, checks available stock and expiry, atomically transitions `available_qty -> reserved_qty` with `TRANSFER_RESERVE` ledger entries, preventing double-selling in POS.
    - [`reject_transfer`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/transfer_service.py) & [`cancel_transfer`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/transfer_service.py) (`API-031`, `API-032`, `BR-010`): Allows rejection or cancellation before dispatch, safely releasing reserved units back to available (`reserved_qty -> available_qty`, `TRANSFER_RELEASE`).
    - [`dispatch_transfer`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/transfer_service.py) (`API-033`, `BR-009`): Confirms physical handover by Source `SHOP_OWNER` or Source `STAFF`, moving status to `DISPATCHED_IN_TRANSIT` with transport notes.
    - [`confirm_receipt`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/transfer_service.py) (`API-034`, `BR-009`, `BR-011`): Recipient `SHOP_OWNER` confirms physical receipt (`COMPLETED`); in a single ACID transaction, deducts source `reserved_qty` (`TRANSFER_OUT`), creates or increments recipient batch (`TRANSFER_IN`), and writes immutable `stock_movements` while strictly preserving the original batch code and expiry date. Handles transit damage quarantine into `damaged_qty`.
    - [`dispute_transfer`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/transfer_service.py) (`API-035`, `BR-016`): Flags transit discrepancies or non-receipt as `DISPUTED` while keeping stock safely isolated in source `reserved_qty`.
- **Transfers Blueprint, UI Views & REST API:**
  - [`app/blueprints/transfers/routes.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/blueprints/transfers/routes.py) — HTML views (`/transfers`, `/shops/<id>/transfers`, `/create`, `/transfers/<id>`) and REST APIs (`API-026` to `API-035`).
  - [`app/templates/transfers/list.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/transfers/list.html), [`app/templates/transfers/create.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/transfers/create.html), and [`app/templates/transfers/detail.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/transfers/detail.html).
  - Navigation updates in [`app/templates/base.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/base.html) and [`app/templates/admin/dashboard.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/admin/dashboard.html).
- **Automated Testing:**
  - [`tests/test_phase6_transfers.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_phase6_transfers.py) — 12 comprehensive test functions (`12 passed`) covering eligible recipient discovery, dual-approval state machine, anti-double-offering, reservation locking, anti-overselling POS guard, physical dispatch, atomic receipt with batch code & expiry date preservation, transit damage, rejection/cancellation reservation release, and HTML views. Full regression suite passes `85/85` tests across all Phases 1–6.

---

## [0.6.0] — 2026-10-13 (Phase 5: Rule-Based Stock Analysis — Velocity, Expiry Risk, Surplus & Recommendations)

### Added
- **Deterministic Rule-Based Stock Analysis Engine (`app/services/surplus_service.py`):**
  - [`StockAnalysisService`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/surplus_service.py#L96-L1834) (and alias `SurplusService`) implementing 100% read-only, deterministic PostgreSQL inventory and sales analysis (`rule_version = "v1.0.0-phase5-deterministic"`):
    - [`calculate_product_sales_history`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/surplus_service.py#L316-L611) & [`calculate_product_sales_velocity`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/surplus_service.py#L614-L629): Shop-scoped sales velocity over configurable `lookback_days` (default `14`), excluding `CANCELLED` sales, deducting item returns (`quantity - returned_quantity`), and safely classifying `SUFFICIENT_HISTORY`, `SPARSE_HISTORY`, `NO_SALES_HISTORY`, and `ESTABLISHED_ZERO_SALES` (never treating missing sales history as proof of zero demand).
    - [`_analyze_single_shop_product`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/surplus_service.py#L632-L1391): FEFO queue-aware batch expiry-risk calculation (`EXPIRED`, `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `NON_PERISHABLE`, `DEPLETED`, `INSUFFICIENT_DATA`), exclusion of `reserved_qty`, `damaged_qty`, and `expired_qty`, dynamic safety-stock & reorder retention floor (`min_product_retention_qty = max(reorder_threshold, safety_stock_qty)`), non-double-counting surplus detection (`NEAR_EXPIRY_SURPLUS` and `OVERSTOCK_COVERAGE`), and low-stock replenishment analysis (`OUT_OF_STOCK`, `LOW_STOCK`, `BELOW_SAFETY_STOCK`, `ADEQUATE`).
    - [`compute_shop_data_fingerprint`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/surplus_service.py#L105-L221) & [`check_snapshot_freshness`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/surplus_service.py#L238-L313): SHA-256 fingerprint freshness verification that automatically invalidates and recalculates cached snapshots whenever a sale, return, stock receipt, or stock adjustment occurs (`DATA_CHANGED`).
    - Deduplicated, explainable recommendations keyed by `recommendation_key` (`MONITOR_NEAR_EXPIRY`, `REVIEW_SURPLUS`, `REPLENISHMENT_NEEDED`, `INSUFFICIENT_SALES_HISTORY`, `QUARANTINE_EXPIRED`).
- **Configuration & Input Validators:**
  - [`app/config.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/config.py#L104-L132) — Added `ANALYSIS_RULE_VERSION`, `ANALYSIS_LOOKBACK_DAYS`, `ANALYSIS_MIN_HISTORY_DAYS`, `ANALYSIS_MIN_ACTIVE_SALE_DAYS`, `ANALYSIS_DEFAULT_SAFETY_STOCK_DAYS`, `ANALYSIS_SURPLUS_COVERAGE_DAYS`, and `ANALYSIS_STALE_AFTER_SECONDS`.
  - [`app/utils/validators.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/validators.py#L1486-L1556) — Added `VALID_EXPIRY_RISK_LEVELS`, `VALID_RECOMMENDATION_TYPES`, and `validate_analysis_params`.
- **Analysis Blueprint, REST APIs & HTML Views:**
  - [`app/blueprints/analysis/routes.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/blueprints/analysis/routes.py) — HTML routes (`/analysis`, `/shops/<id>/analysis`, `/expiry-risk`, `/surplus`, `/low-stock`, `/recommendations`, `/products/<id>`, `/batches/<id>`) and REST API endpoints (`API-025` `/surplus-candidates` and `API-025a`–`API-025g` under `/api/v1/shops/<id>/analysis/*`).
  - Templates under [`app/templates/analysis/`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/analysis/overview.html) (`_header.html`, `overview.html`, `expiry_risk.html`, `surplus.html`, `low_stock.html`, `recommendations.html`, `product_detail.html`, `batch_detail.html`) and navigation links in [`app/templates/base.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/base.html) and [`app/templates/admin/dashboard.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/admin/dashboard.html).
- **Automated Testing:**
  - [`tests/test_phase5_stock_analysis.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_phase5_stock_analysis.py) — `7 passed` covering all 15 Phase 5 scenarios + `66 passed` across Phases 1–4 (`73 passed` total against `sanchay_ai_test`, `92%` coverage on `surplus_service.py`, `84%` overall coverage).

---

## [0.5.0] — 2026-10-13 (Phase 4: Sales, Billing & POS with Automatic FEFO Deduction)

### Added
- **PostgreSQL Schema & Reversible Alembic Migration (`0004_phase4_sales_billing`):**
  - [`app/models/sales.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/sales.py) — Declarative models for [`Sale`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/sales.py#L16-L266) (`sales` invoice header with `uq_sales_shop_invoice`, `uq_sales_shop_idempotency`, `ck_sales_status_valid`, `ck_sales_payment_mode_valid`, `ck_sales_amounts_nonnegative`, `ck_sales_discount_bounds`, `ck_sales_returned_bounds`), [`SaleItem`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/sales.py#L269-L407) (`sale_items` batch-level FEFO allocations with `ck_sale_items_qty_positive`, `ck_sale_items_returned_qty_bounds`, `ck_sale_items_prices_nonnegative`, `ck_sale_items_line_total_math`), and [`SaleReturn`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/sales.py#L410-L515) (`sale_returns` condition-based customer returns with `uq_sale_returns_shop_idempotency`, `ck_sale_returns_condition_valid`, `ck_sale_returns_bucket_valid`, `ck_sale_returns_refund_nonnegative`).
  - [`migrations/versions/0004_phase4_sales_billing.py`](file:///d:/Ashutosh_01/Sanchay%20AI/migrations/versions/0004_phase4_sales_billing.py) — Verified reversible migration (`upgrade` and `downgrade`) preserving Phases 1–3 data.
- **Transactional POS Billing, FEFO Allocation, Cancellations & Returns Service:**
  - [`app/services/sales_service.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/sales_service.py) — [`SalesService`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/sales_service.py#L50-L1145) implementing:
    - [`create_sale`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/sales_service.py#L147-L491) (`API-020`, `BR-004`, `BR-006`): Pre-sale expiry sweep, row-locked shop invoice generation (`INV-{SHOP_CODE}-{YYYYMMDD}-{NNNN}`), idempotency key duplicate guard (`409 DUPLICATE_SALE_SUBMISSION`), authoritative server-side `ShopProduct.selling_price` lookup (ignoring client-submitted prices/totals), deterministic FEFO batch locking (`SELECT ... FOR UPDATE` ordered by `expiry_date ASC NULLS LAST, received_at ASC, id ASC`), multi-batch split allocation, `SALE` stock movement ledger writes, and full rollback on any failure.
    - [`cancel_sale`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/sales_service.py#L494-L664) (`API-023`, `OD-006`): Same-day completed sale cancellation restoring stock to original batches (`SALE_CANCELLATION`).
    - [`process_return`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/sales_service.py#L667-L953) (`API-024`, `OD-006`): Partial or full item return with `RESELLABLE`, `DAMAGED`, and `EXPIRED` condition routing, over-return prevention (`409 RETURN_EXCEEDS_SOLD_QUANTITY`), pro-rata refund calculation, and `SALE_RETURN` ledger writes.
    - [`compute_sales_summary_from_rows`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/sales_service.py#L966-L1038) & [`list_sales`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/sales_service.py#L1041-L1145) (`API-021`, `BR-017`): Date-range, status, payment mode, and search filtering with gross revenue, returned amount, net revenue, COGS, gross profit, and unit summaries.
  - [`app/utils/validators.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/validators.py) — Added [`validate_sale_create_input`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/validators.py#L1213-L1350), [`validate_sale_cancel_input`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/validators.py#L1353-L1364), and [`validate_sale_return_input`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/validators.py#L1367-L1484).
- **Sales Blueprint, POS Counter UI, Printable Receipts & Sales History:**
  - [`app/blueprints/sales/routes.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/blueprints/sales/routes.py) — HTML views (`/pos`, `/sales`, `/shops/<id>/pos`, `/shops/<id>/sales`, `/shops/<id>/sales/<sale_id>`, `/shops/<id>/sales/<sale_id>/receipt`) and REST API endpoints (`API-020` to `API-024`, `/summary`, `/receipt`, and `DELETE` blocker).
  - [`app/templates/sales/pos.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/sales/pos.html), [`app/templates/sales/history.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/sales/history.html), [`app/templates/sales/invoice_detail.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/sales/invoice_detail.html), [`app/templates/sales/receipt.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/sales/receipt.html), [`app/static/js/pos.js`](file:///d:/Ashutosh_01/Sanchay%20AI/app/static/js/pos.js), and print-ready receipt styles in [`app/static/css/main.css`](file:///d:/Ashutosh_01/Sanchay%20AI/app/static/css/main.css).
- **Automated Testing:**
  - [`tests/test_phase4_sales_fefo.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_phase4_sales_fefo.py) — `8 passed` in Phase 4 (including multi-threaded PostgreSQL `SELECT ... FOR UPDATE` concurrent checkout verification) + `9 passed` in Phase 3 + `19 passed` in Phase 2 + `30 passed` in Phase 1 (`66 passed` total against `sanchay_ai_test`, `82%` overall coverage).

---

## [0.4.0] — 2026-10-13 (Phase 3: Product Catalog, Inventory, Batches & Expiry Tracking)

### Added
- **PostgreSQL Schema & Reversible Alembic Migration (`0003_phase3_catalog_inventory`):**
  - [`app/models/catalog.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/catalog.py) — Declarative models for [`Product`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/catalog.py#L14-L98) (`products` organization-wide catalog) and [`ShopProduct`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/catalog.py#L101-L202) (`shop_products` shop-level pricing, reorder threshold, and near-expiry threshold) with `CHECK` constraints (`ck_products_unit_valid`, `ck_shop_products_prices_non_negative`, `ck_shop_products_thresholds_valid`) and composite unique keys (`uq_products_org_sku`, `uq_shop_products_shop_prod`).
  - [`app/models/inventory.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/inventory.py) — Declarative models for [`InventoryBatch`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/inventory.py#L16-L163) (`inventory_batches` with non-negative `available_qty`, `reserved_qty`, `damaged_qty`, `expired_qty` buckets and FEFO composite index `idx_batches_fefo`) and [`StockMovement`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/inventory.py#L166-L270) (`stock_movements` append-only ledger enforcing `ck_movements_balance_consistency`).
  - [`migrations/versions/0003_phase3_catalog_batches_movements.py`](file:///d:/Ashutosh_01/Sanchay%20AI/migrations/versions/0003_phase3_catalog_batches_movements.py) — Verified reversible migration (`upgrade` and `downgrade`) preserving Phase 1 and Phase 2 data.
- **Transactional Catalog & Inventory Domain Services:**
  - [`app/services/catalog_service.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/catalog_service.py) — [`CatalogService`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/catalog_service.py#L31-L598) supporting organization-wide SKU creation, shop product enrollment, price/threshold editing, soft-deactivation (`is_active = False`), and protection against destructive deletion (`409 DESTRUCTIVE_DELETE_PROHIBITED`).
  - [`app/services/inventory_service.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/inventory_service.py) — [`InventoryService`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/inventory_service.py#L46-L785) implementing batch receipts (`RECEIPT`), row-locked adjustments (`ADJUSTMENT_ADD`, `ADJUSTMENT_SUBTRACT`, `MARK_DAMAGED`, `MARK_EXPIRED` via PostgreSQL `SELECT ... FOR UPDATE`), automatic expired-batch quarantine sweep (`sweep_expired_batches`), deterministic FEFO sorting (`expiry_date ASC NULLS LAST, received_at ASC, id ASC`), low-stock & near-expiry rules (`evaluate_shop_inventory_alerts`), and mathematical ledger reconciliation (`verify_shop_ledger_reconciliation`).
  - [`app/utils/validators.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/validators.py) & [`app/utils/errors.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/errors.py) — Added product, batch receipt, batch adjustment, and pagination validators plus [`InsufficientStockError`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/errors.py#L96-L108), [`BatchExpiredError`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/errors.py#L111-L122), and [`ResourceConflictError`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/errors.py#L125-L136).
- **Blueprints, REST APIs & Responsive Store Inventory Views:**
  - [`app/blueprints/catalog/routes.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/blueprints/catalog/routes.py) — HTML views (`/shops/<id>/catalog`, `/shops/<id>/catalog/<id>`) and REST endpoints (`API-013` to `API-015` plus organization catalog endpoints `/api/v1/products`).
  - [`app/blueprints/inventory/routes.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/blueprints/inventory/routes.py) — HTML views (`/shops/<id>/inventory`, `/receive`, `/adjust`, `/batches`, `/batches/<id>`, `/expiry`, `/low-stock`, `/movements`) and REST endpoints (`API-016` to `API-019` plus `/overview` and `/alerts`).
  - Templates under [`app/templates/catalog/`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/catalog/products.html) and [`app/templates/inventory/`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/inventory/overview.html), client helper [`app/static/js/inventory.js`](file:///d:/Ashutosh_01/Sanchay%20AI/app/static/js/inventory.js), and Phase 3 styles in [`app/static/css/main.css`](file:///d:/Ashutosh_01/Sanchay%20AI/app/static/css/main.css).
- **Automated Testing:**
  - [`tests/test_phase3_inventory_batches.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_phase3_inventory_batches.py) — `9 passed` in Phase 3 (including multi-threaded PostgreSQL `SELECT ... FOR UPDATE` concurrency verification) + `19 passed` in Phase 2 + `30 passed` in Phase 1 (`58 passed` total against `sanchay_ai_test`).

---

## [0.3.0] — 2026-10-13 (Phase 2: Organization, Shops & Permissions)

### Added
- **PostgreSQL Schema & Reversible Alembic Migration (`0002_phase2_org_shops`):**
  - [`app/models/organization.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/organization.py) — Declarative models for [`Organization`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/organization.py#L16-L116) (`organizations`), [`Shop`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/organization.py#L119-L250) (`shops`), and [`UserShopAssignment`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/organization.py#L253-L355) (`user_shop_assignments`) with `CHECK` constraints, per-organization unique shop code (`uq_shops_org_code`), and partial unique index `uq_user_active_primary_shop`.
  - Extended [`User`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/user.py) (`users.organization_id`) and [`AuditLog`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/audit.py) (`audit_logs.organization_id`, `audit_logs.shop_id`) without dropping Phase 1 data via [`migrations/versions/0002_phase2_org_shops_assignments.py`](file:///d:/Ashutosh_01/Sanchay%20AI/migrations/versions/0002_phase2_org_shops_assignments.py).
- **Backend RBAC, Organization Isolation & Shop Isolation:**
  - [`app/utils/decorators.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/decorators.py) — Implemented [`require_role`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/decorators.py#L266-L289), [`require_organization_access`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/decorators.py#L292-L320), [`require_shop_access`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/decorators.py#L323-L359), [`verify_organization_access`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/decorators.py#L75-L129), and [`verify_shop_access`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/decorators.py#L132-L263) with automatic security audit logging (`UNAUTHORIZED_ORG_ACCESS`, `UNAUTHORIZED_SHOP_ACCESS`, `INSUFFICIENT_ROLE_PERMISSION`, `INACTIVE_SHOP`, `INACTIVE_ORGANIZATION`).
  - [`app/services/org_service.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/org_service.py) — Transactional [`OrganizationService`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/org_service.py#L37-L253), [`ShopService`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/org_service.py#L256-L651), and [`MembershipService`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/org_service.py#L654-L1260).
  - [`app/utils/geo.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/geo.py) — Haversine great-circle distance calculator (`haversine_distance_km`) for peer shops.
  - Extended [`app/utils/validators.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/validators.py) and [`app/cli.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/cli.py) (`--org-code` / `--org-name` bootstrap support).
- **Routes, REST APIs & Responsive HTML Views:**
  - [`app/blueprints/admin/routes.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/blueprints/admin/routes.py) — Phase 2 HTML views (`/dashboard`, `/organization`, `/shops`, `/shops/<id>`, `/users`) and REST JSON endpoints (`/api/v1/organizations`, `/api/v1/organization`, `/api/v1/shops`, `/api/v1/shops/<id>`, `/api/v1/shops/<id>/operational-check`, `/api/v1/shops/<id>/assignments`, `/api/v1/users`, `/api/v1/users/<id>`).
  - [`app/templates/admin/dashboard.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/admin/dashboard.html), [`app/templates/admin/organization.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/admin/organization.html), [`app/templates/admin/shops.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/admin/shops.html), [`app/templates/admin/shop_detail.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/admin/shop_detail.html), and [`app/templates/admin/users.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/admin/users.html).
- **Automated Testing:**
  - [`tests/test_phase2_rbac_shops.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_phase2_rbac_shops.py) — `19 passed` in Phase 2 + `30 passed` in Phase 1 (`49 passed` total against `sanchay_ai_test`, `81%` overall coverage).

---

## [0.2.0] — 2026-10-12 (Phase 1: Project Foundation, Configuration, Database & Authentication)

### Added
- **Application Foundation:**
  - [`app/__init__.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/__init__.py) — Flask Application Factory (`create_app`), security headers (`CSP`, `X-Content-Type-Options`, `X-Frame-Options`, `Cache-Control: no-store`), and rotating file logger.
  - [`app/config.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/config.py) — `DevelopmentConfig`, `TestingConfig`, and `ProductionConfig` with strict PostgreSQL URI enforcement (prohibiting SQLite fallbacks) and production `SECRET_KEY` validation.
  - [`app/extensions.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/extensions.py) — Initialized `SQLAlchemy`, `Migrate`, `LoginManager`, and `CSRFProtect`.
  - [`app/cli.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/cli.py) — Added `flask create-user` (safe CLI user provisioning without hardcoded credentials) and `flask db-check` commands.
  - [`wsgi.py`](file:///d:/Ashutosh_01/Sanchay%20AI/wsgi.py), [`requirements.txt`](file:///d:/Ashutosh_01/Sanchay%20AI/requirements.txt), and [`pytest.ini`](file:///d:/Ashutosh_01/Sanchay%20AI/pytest.ini).
- **PostgreSQL Schema & Migrations (Phase 1 Scope Only):**
  - [`app/models/user.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/user.py) — `User` model (`users` table) with `scrypt` password hashing and `session_token` rotation.
  - [`app/models/audit.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/models/audit.py) — `AuditLog` model (`audit_logs` table) with PostgreSQL `JSONB` state snapshots.
  - [`migrations/versions/0001_phase1_users_and_audit_logs.py`](file:///d:/Ashutosh_01/Sanchay%20AI/migrations/versions/0001_phase1_users_and_audit_logs.py) — Verified Alembic `upgrade` and `downgrade` migration.
- **Authentication, Security & Error Handling:**
  - [`app/services/auth_service.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/auth_service.py) & [`app/services/audit_service.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/audit_service.py) — Anti-enumeration authentication, constant-time dummy hash check, server-side session invalidation on logout/password change, and sensitive field redaction in audit logs.
  - [`app/blueprints/auth/routes.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/blueprints/auth/routes.py) — HTML routes (`/`, `/auth/login`, `/auth/logout`, `/auth/change-password`) and JSON API endpoints (`/api/v1/health`, `API-001` to `API-004`).
  - [`app/utils/errors.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/errors.py) & [`app/utils/validators.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/utils/validators.py) — Standardized error handlers with automatic `db.session.rollback()` and server-side input validation.
- **Frontend Templates & Static Assets:**
  - [`app/templates/base.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/base.html), [`app/templates/auth/login.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/auth/login.html), [`app/templates/auth/landing.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/auth/landing.html), [`app/templates/errors/error.html`](file:///d:/Ashutosh_01/Sanchay%20AI/app/templates/errors/error.html), [`app/static/css/main.css`](file:///d:/Ashutosh_01/Sanchay%20AI/app/static/css/main.css), and [`app/static/js/api.js`](file:///d:/Ashutosh_01/Sanchay%20AI/app/static/js/api.js).
- **Automated Testing:**
  - [`tests/conftest.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/conftest.py) and [`tests/test_phase1_auth.py`](file:///d:/Ashutosh_01/Sanchay%20AI/tests/test_phase1_auth.py) — `30 passed` against isolated `sanchay_ai_test` PostgreSQL 18 database (`86%` total coverage).

---

## [0.1.0-docs] — 2026-10-12 (Phase 0: Repository Audit & Documentation Baseline)

### Added
- **Repository Audit:** Audited the greenfield workspace (`d:\Ashutosh_01\Sanchay AI`) and recorded baseline findings in [`docs/REPOSITORY_AUDIT.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/REPOSITORY_AUDIT.md).
- **Root Documentation Package:**
  - [`README.md`](file:///d:/Ashutosh_01/Sanchay%20AI/README.md) — Project overview, architecture summary, setup guide, and documentation index.
  - [`PRD.md`](file:///d:/Ashutosh_01/Sanchay%20AI/PRD.md) — Product Requirements Document with functional requirements (`FR-*`), non-functional requirements (`NFR-*`), user stories, acceptance criteria (`AC-*`), and open questions (`OD-*`).
  - [`IMPLEMENTATION_ROADMAP.md`](file:///d:/Ashutosh_01/Sanchay%20AI/IMPLEMENTATION_ROADMAP.md) — Sequential 9-phase roadmap (Phase 0 to Phase 8) with strict gates and AI isolated to Phase 8.
  - [`PROJECT_RULES.md`](file:///d:/Ashutosh_01/Sanchay%20AI/PROJECT_RULES.md) — 24 non-negotiable engineering, security, data integrity, and scope governance rules.
  - [`ARCHITECTURE.md`](file:///d:/Ashutosh_01/Sanchay%20AI/ARCHITECTURE.md) — Modular Flask architecture, component boundaries, Mermaid sequence/state diagrams, and Phase 8 AI boundary.
  - [`DATABASE_SCHEMA.md`](file:///d:/Ashutosh_01/Sanchay%20AI/DATABASE_SCHEMA.md) — PostgreSQL relational schema, constraints, indexes, batch reconciliation model, and migration strategy.
  - [`API_SPECIFICATION.md`](file:///d:/Ashutosh_01/Sanchay%20AI/API_SPECIFICATION.md) — RESTful JSON API contracts (`API-*`), payloads, status codes, RBAC matrix, and idempotency rules.
  - [`SECURITY.md`](file:///d:/Ashutosh_01/Sanchay%20AI/SECURITY.md) — Threat model (STRIDE), session security, password hashing, tenant/shop isolation, CSRF/XSS defenses, and audit trail design.
  - [`TESTING_STRATEGY.md`](file:///d:/Ashutosh_01/Sanchay%20AI/TESTING_STRATEGY.md) — `pytest` strategy covering unit, integration, RBAC isolation, FEFO concurrency, transfer state machine, and Phase 8 AI backtesting.
  - [`DEVELOPMENT_SETUP.md`](file:///d:/Ashutosh_01/Sanchay%20AI/DEVELOPMENT_SETUP.md) — Windows PowerShell setup guide for Python, PostgreSQL, virtual environments, migrations, and test execution.
  - [`.env.example`](file:///d:/Ashutosh_01/Sanchay%20AI/.env.example) & [`.gitignore`](file:///d:/Ashutosh_01/Sanchay%20AI/.gitignore) — Safe configuration template and Git exclusion rules.
  - [`DECISIONS.md`](file:///d:/Ashutosh_01/Sanchay%20AI/DECISIONS.md) — Architecture Decision Records (`ADR-001` through `ADR-010`) and open decisions (`OD-001` through `OD-006`).
  - [`DEMO_PLAN.md`](file:///d:/Ashutosh_01/Sanchay%20AI/DEMO_PLAN.md) — End-to-end hackathon demonstration playbook with 4 shops and 9 products.
- **Extended Documentation (`docs/`):**
  - [`docs/BUSINESS_RULES.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/BUSINESS_RULES.md) — 20 formal business rules (`BR-001` to `BR-020`), invariants, and edge-case specifications.
  - [`docs/DATA_DICTIONARY.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/DATA_DICTIONARY.md) — Field-by-field data dictionary and enum definitions.
  - [`docs/ERROR_HANDLING.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/ERROR_HANDLING.md) — Standard JSON error envelope, domain error catalog, and transaction rollback policies.
  - [`docs/USER_GUIDE.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/USER_GUIDE.md) — Operational guide for Shop Owners and Shop Staff.
  - [`docs/ADMIN_GUIDE.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/ADMIN_GUIDE.md) — Organization administration, shop onboarding, and governance guide for Super Admins.
  - [`docs/DEPLOYMENT.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/DEPLOYMENT.md) — Local, staging, and production deployment specifications with backup/restore procedures.
  - [`docs/AI_IMPLEMENTATION_PLAN.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/AI_IMPLEMENTATION_PLAN.md) — Phase 8 design blueprint for demand forecasting, expiry-risk scoring, and transfer recommendation evaluation.

---

## Planned Future Releases (Pending Phase-by-Phase Approval)

- **[0.6.0] — Phase 5:** Rule-Based Surplus Detection and Dual-Approval Inter-Store Stock Transfer Workflow.
- **[0.7.0] — Phase 6:** In-App Notifications, Role Dashboards, Analytics, and CSV/PDF Reporting.
- **[0.8.0] — Phase 7:** End-to-End Integration, Concurrency & Security Hardening, and Hackathon Demo Seeding.
- **[1.0.0] — Phase 8:** AI Inventory Intelligence, Demand Forecasting, Expiry Risk Scoring, and Model Evaluation.
