# Sanchay AI — Multi-Store Inventory & Stock Redistribution Platform

**Current Project Phase:** **Production-Ready — All Modules & AI Engines Complete (`v1.1.0`)**  
**Implementation Status:** All PRD Modules Implemented & Fully Verified (`132/132` `pytest` tests passing against PostgreSQL 18 local database). Features a strict 2-role architecture (`SUPER_ADMIN` and `SHOP_OWNER`), true Scikit-Learn Ridge Regression demand forecasting, Poisson survival waste mitigation, offline POS bill queue reconciliation, printable thermal slips (80mm/58mm), A4 tax invoicing, and automated multi-shop redistribution recommendations.

---

## 1. Project Overview & Problem Statement

Retail organizations operating multiple stores (grocery chains, dairy/perishable outlets, convenience marts, pharmacy networks) frequently suffer from a costly operational mismatch:
- **Shop A** has overstocked or near-expiry batches sitting unsold until they expire and are written off as dead capital.
- **Shop B** (belonging to the same organization just a few kilometers away) is simultaneously experiencing low stock or stockouts for that exact product.
- Informal store-to-store transfers via phone calls or spreadsheets lack dual-owner consent, double-promise inventory, corrupt stock ledgers, and fail to track physical dispatch vs. receipt.

### The Sanchay AI Solution & Key Differentiators
**Sanchay AI** connects multiple independent shops under one organization onto a single transactional platform backed by **PostgreSQL**:
1. **Batch-Level FEFO Inventory & POS Billing:** Every unit of stock is tracked by batch (`available`, `reserved`, `damaged`, `expired`) with automatic **First Expired, First Out (FEFO)** deduction at the POS counter and an immutable `stock_movements` audit ledger.
2. **Controlled Dual-Approval + Two-Step Handover Stock Exchange:** Identifies surplus/near-expiry stock in one shop, matches eligible recipient shops in the same organization, and enforces a strict transactional state machine:
   - Requires **explicit Source Shop Owner approval** AND **explicit Recipient Shop Owner approval** before reserving stock (`APPROVED_RESERVED`).
   - Requires **Source physical dispatch confirmation** (`DISPATCHED_IN_TRANSIT`) AND **Recipient physical receipt confirmation** (`COMPLETED`) before moving inventory balances.
3. **Strict 2-Role Security Architecture:** Platform governance and store execution are streamlined into two non-overlapping roles: **`SUPER_ADMIN`** (multi-tenant organization governance) and **`SHOP_OWNER`** (full operational control over assigned stores, POS billing, inventory, and transfers).
4. **Scikit-Learn AI Demand Forecasting & Waste Mitigation:** Statistical Ridge Regression forecasting with calendar day-of-week seasonality, Poisson survival analysis for batch expiry risk, and automated Haversine-optimized stock redistribution proposals—all operating with a 100% read-only AI guarantee (zero autonomous mutations).

---

## 2. Core Scope Rules & Exclusions

- **AI in Phase 8 Only (`RULE-03`):** Zero AI/ML features or dependencies are introduced in Phases 1–7.
- **No Barcode/Camera Scanning (`RULE-05`):** Product scanning, barcode/QR scanning, OCR, and image recognition are permanently **out of scope**. Products and batches are managed manually via validated forms and APIs.
- **No Hardcoded Business Data (`RULE-11`):** All runtime data resides in PostgreSQL. Demo data is loaded exclusively via explicit seed scripts (`scripts/seed_demo_data.py` in Phase 7).

---

## 3. Technology Stack

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Frontend** | HTML5, CSS3, Vanilla JavaScript (ES6+) | Responsive store UI, POS counter, transfer boards, and REST `fetch()` client |
| **Backend** | Python 3.11+, Flask 3.1 | Modular Blueprints, Service Layer, Session Auth (`Flask-Login`), CSRF (`Flask-WTF`) |
| **Database** | PostgreSQL 15+ (Verified on PostgreSQL 18) | Single source of truth, ACID transactions, `SELECT ... FOR UPDATE` row locks |
| **ORM & Migrations** | SQLAlchemy 2.x (`Flask-SQLAlchemy`), Alembic (`Flask-Migrate`) | Declarative schema models and versioned database migrations |
| **Testing** | `pytest`, `pytest-cov` | Unit, integration, RBAC isolation, FEFO, transfer lifecycle, and concurrency tests |
| **Reporting (Phase 6)** | CSV (`csv`) & PDF (`ReportLab` / `fpdf2`) | Auditable financial, inventory, near-expiry, and transfer exports |
| **AI (Phase 8 Only)** | Statistical Forecasting & `scikit-learn` | Short-horizon demand forecasting, expiry-risk scoring, and backtest evaluation |

---

## 4. Complete Documentation Index

### Root-Level Documentation
1. **[`PRD.md`](file:///d:/Ashutosh_01/Sanchay%20AI/PRD.md)** — Executive summary, personas, Functional Requirements (`FR-001` to `FR-065`), Non-Functional Requirements (`NFR-001` to `NFR-008`), user stories (`US-001` to `US-007`), and acceptance criteria.
2. **[`IMPLEMENTATION_ROADMAP.md`](file:///d:/Ashutosh_01/Sanchay%20AI/IMPLEMENTATION_ROADMAP.md)** — Dependency-aware 9-phase implementation plan (Phase 0 to Phase 8) with deliverables, test gates, and Definition of Done per phase.
3. **[`PROJECT_RULES.md`](file:///d:/Ashutosh_01/Sanchay%20AI/PROJECT_RULES.md)** — 24 non-negotiable project rules (`RULE-01` to `RULE-24`), coding conventions, and prohibited shortcuts.
4. **[`ARCHITECTURE.md`](file:///d:/Ashutosh_01/Sanchay%20AI/ARCHITECTURE.md)** — Modular Flask architecture, directory layout, Mermaid sequence/state diagrams, and Phase 8 AI isolation boundary.
5. **[`DATABASE_SCHEMA.md`](file:///d:/Ashutosh_01/Sanchay%20AI/DATABASE_SCHEMA.md)** — PostgreSQL ERD, table definitions, `CHECK` constraints, indexes, and migration strategy.
6. **[`API_SPECIFICATION.md`](file:///d:/Ashutosh_01/Sanchay%20AI/API_SPECIFICATION.md)** — RESTful JSON API contracts (`API-001` to `API-042`), request/response payloads, RBAC rules, and idempotency.
7. **[`SECURITY.md`](file:///d:/Ashutosh_01/Sanchay%20AI/SECURITY.md)** — STRIDE threat model, session/password security, RBAC & shop isolation matrix, CSRF/XSS/SQLi defenses, and audit logging.
8. **[`TESTING_STRATEGY.md`](file:///d:/Ashutosh_01/Sanchay%20AI/TESTING_STRATEGY.md)** — `pytest` strategy covering unit, integration, RBAC isolation, FEFO reconciliation, transfer edge cases, concurrency, and Phase 8 AI backtesting.
9. **[`DEVELOPMENT_SETUP.md`](file:///d:/Ashutosh_01/Sanchay%20AI/DEVELOPMENT_SETUP.md)** — Windows PowerShell setup guide for Python venv, PostgreSQL databases, `.env`, migrations, user provisioning, and tests.
10. **[`DECISIONS.md`](file:///d:/Ashutosh_01/Sanchay%20AI/DECISIONS.md)** — Architecture Decision Records (`ADR-001` to `ADR-010`) and Open Decisions (`OD-001` to `OD-006`) for stakeholder review.
11. **[`DEMO_PLAN.md`](file:///d:/Ashutosh_01/Sanchay%20AI/DEMO_PLAN.md)** — End-to-end hackathon demonstration playbook (4 shops, 9 products, FEFO POS, happy-path transfer, and failure edge cases).
12. **[`CHANGELOG.md`](file:///d:/Ashutosh_01/Sanchay%20AI/CHANGELOG.md)** — Versioned log of documentation and phase releases.
13. **[`.env.example`](file:///d:/Ashutosh_01/Sanchay%20AI/.env.example)** & **[`.gitignore`](file:///d:/Ashutosh_01/Sanchay%20AI/.gitignore)** — Environment variable template and Git exclusion rules.

### Extended Specifications (`docs/`)
14. **[`docs/REPOSITORY_AUDIT.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/REPOSITORY_AUDIT.md)** — Phase 0 workspace audit report separating verified current state from proposed target architecture.
15. **[`docs/BUSINESS_RULES.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/BUSINESS_RULES.md)** — Formal specification of `BR-001` to `BR-020`, reconciliation math, FEFO algorithm, surplus rules, and financial formulas.
16. **[`docs/DATA_DICTIONARY.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/DATA_DICTIONARY.md)** — Data formatting standards, canonical enums, and computed domain fields.
17. **[`docs/ERROR_HANDLING.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/ERROR_HANDLING.md)** — JSON error envelope, domain error catalog, and database transaction rollback pattern.
18. **[`docs/USER_GUIDE.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/USER_GUIDE.md)** — Operational manual for Shop Owners and Staff.
19. **[`docs/ADMIN_GUIDE.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/ADMIN_GUIDE.md)** — Governance and administration manual for Super Admins.
20. **[`docs/DEPLOYMENT.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/DEPLOYMENT.md)** — WSGI deployment (`Waitress`/`Gunicorn`), production checklist, and `pg_dump` backup/restore procedures.
21. **[`docs/AI_IMPLEMENTATION_PLAN.md`](file:///d:/Ashutosh_01/Sanchay%20AI/docs/AI_IMPLEMENTATION_PLAN.md)** — **Phase 8 ONLY** design document for demand forecasting, expiry-risk scoring, and model evaluation.

---

## 5. Running the Application (Single Documented Command)

Once dependencies and PostgreSQL are configured, run the production-grade application on Windows with a single command:

```powershell
.\.venv\Scripts\python -m flask --app wsgi:app run --host=127.0.0.1 --port=5000
```

Open your browser to: **`http://127.0.0.1:5000`**

### Initial Setup & Verification (PowerShell)
```powershell
# 1. Activate Python virtual environment
.\.venv\Scripts\Activate.ps1

# 2. Verify database connection and apply migrations
python -m flask --app wsgi:app db-check
python -m flask --app wsgi:app db upgrade

# 3. Run complete automated regression test suite (132 tests)
pytest -q

# 4. Launch the application
python -m flask --app wsgi:app run --host=127.0.0.1 --port=5000
```

---

## 6. Design System & Color Tokens

Sanchay AI implements a clean, accessible design system strictly avoiding purple/violet gradients and generic SaaS blues:

- **Theme Palette (Tokens defined centrally in `app/static/css/main.css`):**
  - **Dark Mode (Default):**
    - Background: `#0F1412` (Deep green-black)
    - Surface: `#171E1B`
    - Elevated Surface: `#1F2824`
    - Border: `#2C3733`
    - Text Primary: `#ECF1EE` | Secondary: `#9AA9A2`
    - Primary Accent: `#2DD4A0` (Emerald) | Hover: `#25B88B`
    - Secondary Accent: `#F5B544` (Amber for highlights/warnings)
    - Danger: `#EF6B5B` | Success: `#4ADE80` | Info: `#5EC4D9`
  - **Light Mode (Warm Off-White):**
    - Background: `#F6F4EF` (Warm off-white)
    - Surface: `#FFFFFF`
    - Ink / Primary Text: `#1B2420`
    - Same Emerald (`#2DD4A0`) and Amber (`#F5B544`) accents
  - **Theme Switcher:** Navbar toggle button with animated Sun/Moon icons, persisted in `localStorage` and executed synchronously in `<head>` to prevent flash-of-unstyled-theme.

---

## 7. AI Demand Forecasting & Waste Mitigation Engines

In accordance with strict modular architecture and the zero-autonomous-mutation guarantee:
- **Core Engine:** [`app/services/product_analysis_service.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/product_analysis_service.py) (re-exported via [`services/productAnalysis.py`](file:///d:/Ashutosh_01/Sanchay%20AI/services/productAnalysis.py)).
- **Predictive Demand Forecasting:** Employs Scikit-Learn `Ridge` regression with calendar seasonality (day-of-week one-hot encoding), rolling velocity, and linear trends, with deterministic tiered fallbacks (EWMA for 3–13 days, moving baseline for <3 days).
- **Poisson Spoilage Survival Modeling:** Evaluates batch quantities against projected daily run-rates to compute exact probabilities of expiry before sale.
- **Waste Mitigation Engine:** Evaluates near-expiry batch capital risks, calculates potential salvage vs write-off losses, and suggests targeted actions.
- **Automated Stock Redistribution Engine:** Located in [`app/services/transfer_service.py`](file:///d:/Ashutosh_01/Sanchay%20AI/app/services/transfer_service.py), pairs surplus source batches with deficit/stockout recipient shops within the organization, factoring in Haversine distance and transit costs.
- **Read-Only AI Guarantee:** All AI models provide read-only projections and 1-click proposal drafts; they **never autonomously alter inventory ledgers** without explicit Shop Owner confirmation.

---

## 8. Implementation Roadmap Summary

| Phase | Name | Status |
| :--- | :--- | :--- |
| **Phase 0** | Repository Audit and Documentation Baseline | **COMPLETED** |
| **Phase 1** | Project Foundation, Configuration, Database, and Authentication | **COMPLETED & VERIFIED** |
| **Phase 2** | Organization, Shop Management, and Role-Based Access Control | **COMPLETED & VERIFIED** |
| **Phase 3** | Product Catalog, Inventory, Batches, and Expiry Tracking | **COMPLETED & VERIFIED** |
| **Phase 4** | Sales, Billing, and Transaction History (FEFO POS) | **COMPLETED & VERIFIED** |
| **Phase 5** | Rule-Based Stock Analysis (Velocity, Expiry Risk, Surplus & Recommendations) | **COMPLETED & VERIFIED** |
| **Phase 6** | Inter-Store Stock Exchange (Dual Approval & Handover Workflow) | **COMPLETED & VERIFIED** |
| **Phase 7** | In-App Notifications & Alerts Lifecycle | **COMPLETED & VERIFIED** |
| **Phase 8** | Dashboards, Real-Time Analytics & CSV Reports | **COMPLETED & VERIFIED** |
| **Phase 9** | AI Demand Forecasting, Spoilage Modeling & Redistribution Engine | **COMPLETED & VERIFIED** |
