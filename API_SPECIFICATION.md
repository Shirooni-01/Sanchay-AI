# Sanchay AI — RESTful API Specification

**API Base Prefix:** `/api/v1`  
**Content-Type:** `application/json; charset=utf-8`  
**Authentication:** Server-side Session Cookie (`sanchay_session`) + `X-CSRFToken` header on all state-changing methods (`POST`, `PUT`, `PATCH`, `DELETE`).  
**Status:** Proposed Target Specification (Phases 1–8)

---

## 1. Global API Conventions

### 1.1 Standard Response Envelopes

#### Success Envelope (`200 OK`, `201 Created`)
```json
{
  "success": true,
  "data": { ... },
  "meta": {
    "page": 1,
    "per_page": 20,
    "total_items": 42,
    "total_pages": 3,
    "timestamp": "2026-10-12T17:30:00Z"
  }
}
```

#### Error Envelope (`400`, `401`, `403`, `404`, `409`, `422`, `500`)
```json
{
  "success": false,
  "error": {
    "code": "INSUFFICIENT_SELLABLE_STOCK",
    "message": "Requested quantity (25) exceeds available sellable stock (18) for SKU-DAIRY-PANEER-200G.",
    "details": {
      "product_id": 4,
      "requested_qty": 25,
      "available_sellable_qty": 18
    }
  }
}
```

### 1.2 Pagination, Sorting & Filtering Query Parameters
List endpoints support:
- `page` (int, default `1`, `>= 1`)
- `per_page` (int, default `20`, `1..100`)
- `q` (string, case-insensitive search on name/code/SKU)
- `sort_by` (whitelisted column name) & `sort_dir` (`asc` | `desc`)

### 1.3 Idempotency & Transaction Safety
All state-changing endpoints on inventory, sales, and transfers acquire row-level locks (`SELECT ... FOR UPDATE`) and support an optional `X-Idempotency-Key` header (or state-machine transition verification) to prevent duplicate execution from double-clicks or network retries.

---

## 2. Master Endpoint Catalog (`API-001` to `API-042`)

### Module A: Authentication, Session & Health (`Phase 1 — Implemented & Verified`)

| ID | Method & Route | Allowed Roles | Description |
| :--- | :--- | :--- | :--- |
| **API-000** | `GET /api/v1/health` | Public | Verify application status and PostgreSQL connectivity (`SELECT 1`). |
| **API-001** | `POST /api/v1/auth/login` | Public | Authenticate with `identifier` (`username` or `email`) and `password`; rotates `session_token` and sets session cookie. |
| **API-002** | `POST /api/v1/auth/logout` | Any Authenticated | Rotate server-side `session_token`, invalidate session, and clear cookie. |
| **API-003** | `GET /api/v1/auth/me` | Any Authenticated | Return current authenticated user profile (`id`, `username`, `email`, `full_name`, `role`, `is_active`, `last_login_at`). |
| **API-004** | `POST /api/v1/auth/change-password` | Any Authenticated | Verify `current_password`, enforce password strength on `new_password`, update `password_hash`, and rotate `session_token`. |

#### Example: `API-001` (`POST /api/v1/auth/login` — Verified Phase 1 Payload)
**Request:**
```json
{
  "identifier": "rajesh@sanchay.local",
  "password": "ValidPassword123"
}
```
**Response (`200 OK`):**
```json
{
  "success": true,
  "data": {
    "user": {
      "id": 1,
      "username": "admin_rajesh",
      "email": "rajesh@sanchay.local",
      "full_name": "Rajesh Sharma",
      "phone": "9876543210",
      "role": "SUPER_ADMIN",
      "is_active": true,
      "last_login_at": "2026-10-12T18:05:00+00:00",
      "created_at": "2026-10-12T18:00:00+00:00"
    }
  },
  "meta": {
    "timestamp": "2026-10-12T18:05:00+00:00"
  }
}
```
*(Note: `organization_id` and `assigned_shop` context will be appended to the user payload in Phase 2 when Organization and Shop Management are implemented.)*

---

### Module B: Organization, Shops & User Access (`Phase 2 — Implemented & Verified`)

| ID | Method & Route | Allowed Roles | Description |
| :--- | :--- | :--- | :--- |
| **API-005a** | `POST /api/v1/organizations` | `SUPER_ADMIN` | Create a new organization (`name`, `code`, `contact_email`, `contact_phone`, `address`, thresholds) and bind the creating Super Admin. |
| **API-005** | `GET /api/v1/organization` | Authenticated Org Member | View authorized organization configuration and summary counts (`shops_count`, `active_shops_count`, `users_count`). |
| **API-006** | `PATCH /api/v1/organization` | `SUPER_ADMIN` | Update organization details, default alert thresholds, or operational `status` (`ACTIVE` / `INACTIVE`). |
| **API-007** | `GET /api/v1/shops` | `SUPER_ADMIN` (all org shops), `SHOP_OWNER` / `STAFF` (assigned shops only) | List accessible shops in the user's organization (`?status=`, `?city=`, `?search=`). |
| **API-008** | `POST /api/v1/shops` | `SUPER_ADMIN` | Register a new shop (`name`, `code` unique per org, `address`, `city`, `pincode`, `latitude`, `longitude`, `contact_phone`, optional `owner_user_id`). |
| **API-009a** | `GET /api/v1/shops/{shop_id}` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Retrieve single shop details, peer shop Haversine distances (`peer_shops`), and personnel `assignments` (for Admin/Owner). |
| **API-009** | `PATCH /api/v1/shops/{shop_id}` | `SUPER_ADMIN` (all fields + `status`/`code`), Assigned `SHOP_OWNER` (address/contact/thresholds on `ACTIVE` shop) | Update shop details, thresholds, or `status` (`ACTIVE`, `INACTIVE`, `SUSPENDED`). |
| **API-009b** | `POST /api/v1/shops/{shop_id}/operational-check` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Verify that both the target shop and parent organization are `ACTIVE` (`403 INACTIVE_SHOP` or `403 INACTIVE_ORGANIZATION` otherwise). |
| **API-010** | `GET /api/v1/users` | `SUPER_ADMIN` (all org users), `SHOP_OWNER` (staff on owned shops) | List organization users and their active `assigned_shops` (`?role=`, `?shop_id=`). |
| **API-011** | `POST /api/v1/users` | `SUPER_ADMIN` (any role), `SHOP_OWNER` (`STAFF` for own active shop only) | Create user in the actor's organization and optionally create initial `user_shop_assignments` row. |
| **API-012a** | `GET /api/v1/users/{user_id}` | `SUPER_ADMIN`, `SHOP_OWNER` (own shop staff), or Self | Retrieve user profile and shop assignments within authorized tenant/shop scope. |
| **API-012** | `PATCH /api/v1/users/{user_id}` | `SUPER_ADMIN`, `SHOP_OWNER` (own shop `STAFF` only) | Activate/deactivate user (immediately rotating `session_token`) or update profile/role. |
| **API-012b** | `POST /api/v1/shops/{shop_id}/assignments` | `SUPER_ADMIN` (`SHOP_OWNER`/`STAFF`), Assigned `SHOP_OWNER` (`STAFF` only) | Assign an organization user to an active shop and enforce single primary active shop per user (`uq_user_active_primary_shop`). |
| **API-012c** | `PATCH /api/v1/shops/{shop_id}/assignments/{assignment_id}` | `SUPER_ADMIN`, Assigned `SHOP_OWNER` (`STAFF` assignments only) | Update `assignment_role`, `is_primary`, or `is_active` (revoke/restore shop access). |

---

### Module C: Product Catalog & Batch Inventory (`Phase 3`)

> **Shop Isolation Rule (`BR-013`):** For all `/api/v1/shops/{shop_id}/*` routes, the `@require_shop_access` decorator verifies that `current_user` is either `SUPER_ADMIN` or actively assigned to `{shop_id}`. Any mismatch immediately returns `403 FORBIDDEN_SHOP_ACCESS`.

| ID | Method & Route | Allowed Roles | Description |
| :--- | :--- | :--- | :--- |
| **API-013** | `GET /api/v1/shops/{shop_id}/products` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, `STAFF` | Paginated list of shop products with reconciled stock totals (`sellable_qty`, `reserved_qty`, `damaged_qty`, `expired_qty`). |
| **API-014** | `POST /api/v1/shops/{shop_id}/products` | `SUPER_ADMIN`, Assigned `SHOP_OWNER` | Manually create or link a product SKU in the shop catalog (`FR-013`, `FR-014`). |
| **API-015** | `PATCH /api/v1/shops/{shop_id}/products/{shop_product_id}` | `SUPER_ADMIN`, Assigned `SHOP_OWNER` | Update shop selling price, purchase price, thresholds, or `is_active`. |
| **API-016** | `GET /api/v1/shops/{shop_id}/batches` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, `STAFF` | List batches in FEFO order; filter by `product_id`, `status`, `near_expiry=true`. |
| **API-017** | `POST /api/v1/shops/{shop_id}/batches` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, `STAFF` | Receive a new inventory batch (`RECEIPT` stock movement). |
| **API-018** | `POST /api/v1/shops/{shop_id}/batches/{batch_id}/adjust` | `SUPER_ADMIN`, Assigned `SHOP_OWNER` | Authorized stock adjustment (`ADJUSTMENT_ADD`, `ADJUSTMENT_SUBTRACT`, `MARK_DAMAGED`, `MARK_EXPIRED`) with mandatory `reason`. |
| **API-019** | `GET /api/v1/shops/{shop_id}/stock-movements` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, `STAFF` | Paginated immutable ledger of stock movements (`BR-007`). |

#### Example: `API-017` (`POST /api/v1/shops/1/batches`) — Receive Batch
**Request:**
```json
{
  "product_id": 4,
  "batch_code": "BATCH-PNR-202610-A",
  "manufactured_date": "2026-10-10",
  "expiry_date": "2026-10-20",
  "unit_cost": "72.00",
  "initial_qty": 45
}
```
**Validation Rules:**
- `initial_qty` must be an integer `> 0`.
- `unit_cost` must be a valid decimal `>= 0`.
- If `product.is_perishable == true`, `expiry_date` is mandatory and must be `>= CURRENT_DATE` (cannot receive already-expired goods as sellable stock).
- `(shop_id, product_id, batch_code)` must be unique (`409 DUPLICATE_BATCH_CODE` if existing).

**Response (`201 Created`):**
```json
{
  "success": true,
  "data": {
    "batch": {
      "id": 101,
      "shop_id": 1,
      "product_id": 4,
      "sku": "SKU-DAIRY-PANEER-200G",
      "batch_code": "BATCH-PNR-202610-A",
      "expiry_date": "2026-10-20",
      "days_to_expiry": 8,
      "unit_cost": "72.00",
      "initial_qty": 45,
      "available_qty": 45,
      "reserved_qty": 0,
      "damaged_qty": 0,
      "expired_qty": 0,
      "status": "ACTIVE"
    },
    "movement_id": 501
  }
}
```

---

### Module D: Point-of-Sale (POS) Sales & Billing (`Phase 4 — Implemented & Verified`)

| ID | Method & Route | Allowed Roles | Description |
| :--- | :--- | :--- | :--- |
| **API-020** | `POST /api/v1/shops/{shop_id}/sales` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, `STAFF` | Create a POS sale with server-side authoritative price lookup, expiry sweep, and atomic FEFO batch deduction (`FR-030`, `BR-004`). Supports `X-Idempotency-Key` / `idempotency_key`. |
| **API-021** | `GET /api/v1/shops/{shop_id}/sales` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, `STAFF` | List sales invoices with date range (`date_from`, `date_to`), `status`, `payment_mode`, and search (`q`) filters + financial/quantity `summary`. |
| **API-021b** | `GET /api/v1/shops/{shop_id}/sales/summary` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, `STAFF` | Retrieve aggregated gross revenue, returns, net revenue, COGS, gross profit, and unit quantities for the filtered period (`BR-017`). |
| **API-022** | `GET /api/v1/shops/{shop_id}/sales/{sale_id}` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, `STAFF` | Get full invoice details including FEFO batch breakdown (`allocations`), grouped `product_lines`, and `returns` history. |
| **API-022b** | `GET /api/v1/shops/{shop_id}/sales/{sale_id}/receipt` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, `STAFF` | Retrieve structured receipt payload (`receipt_number`, shop header, cashier, line items, FEFO batch details, totals, and return/cancellation summary). |
| **API-023** | `POST /api/v1/shops/{shop_id}/sales/{sale_id}/cancel` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, `STAFF` (same-day only) | Cancel a same-day completed sale (zero prior returns) and restore stock to original batches (`SALE_CANCELLATION` / `EXPIRED`). |
| **API-024** | `POST /api/v1/shops/{shop_id}/sales/{sale_id}/returns` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, `STAFF` | Process partial or full item return (`RESELLABLE`, `DAMAGED`, or `EXPIRED` condition) with atomic stock bucket restoration and pro-rata refund calculation. |
| **Guard** | `DELETE /api/v1/shops/{shop_id}/sales/{sale_id}` | Any | Always returns `409 Conflict` (`DESTRUCTIVE_DELETE_PROHIBITED`) to preserve financial and audit integrity (`RULE-17`). |

#### Example: `API-020` (`POST /api/v1/shops/1/sales`) — Create POS Sale (FEFO)
**Request:**
```json
{
  "customer_name": "Walk-in Customer",
  "payment_mode": "UPI_MANUAL",
  "idempotency_key": "pos-1-20261013-001",
  "items": [
    {
      "product_id": 4,
      "quantity": 12,
      "line_discount": "0.00"
    }
  ]
}
```
**Response (`201 Created`):**
```json
{
  "success": true,
  "data": {
    "sale": {
      "id": 301,
      "invoice_number": "INV-SHP1-20261012-0014",
      "receipt_number": "INV-SHP1-20261012-0014",
      "shop_id": 1,
      "status": "COMPLETED",
      "subtotal_amount": 1080.0,
      "discount_amount": 0.0,
      "total_amount": 1080.0,
      "total_cost_amount": 864.0,
      "returned_amount": 0.0,
      "net_revenue_amount": 1080.0,
      "gross_profit_amount": 216.0,
      "payment_mode": "UPI_MANUAL",
      "created_at": "2026-10-12T17:45:00Z",
      "allocations": [
        {
          "sale_item_id": 410,
          "product_id": 4,
          "sku": "SKU-DAIRY-PANEER-200G",
          "batch_id": 100,
          "batch_code": "BATCH-PNR-202610-OLD",
          "expiry_date": "2026-10-15",
          "quantity": 8,
          "quantity_deducted": 8,
          "unit_selling_price": 90.0,
          "line_total": 720.0
        },
        {
          "sale_item_id": 411,
          "product_id": 4,
          "sku": "SKU-DAIRY-PANEER-200G",
          "batch_id": 101,
          "batch_code": "BATCH-PNR-202610-A",
          "expiry_date": "2026-10-20",
          "quantity": 4,
          "quantity_deducted": 4,
          "unit_selling_price": 90.0,
          "line_total": 360.0
        }
      ]
    }
  }
}
```

---

### Module E1: Rule-Based Stock Analysis & Risk Insights (`Phase 5 — Implemented`)

All Phase 5 endpoints accept optional query parameters: `lookback_days` (`1..365`, default `14`), `safety_stock_days` (`0..90`, default `2`), `safety_stock_qty` (`0..1000000`), `surplus_coverage_days` (`1..365`, default `30`), `ref_date` (`YYYY-MM-DD`), and `refresh=1`. Running any Phase 5 endpoint is 100% read-only and never modifies stock quantities or ledger movements.

| ID | Method & Route | Allowed Roles | Description |
| :--- | :--- | :--- | :--- |
| **API-025** | `GET /api/v1/shops/{shop_id}/surplus-candidates` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Identify rule-based surplus batches in `{shop_id}` respecting FEFO queue order, safety stock, and reorder retention floors (`FR-038`). |
| **API-025a** | `GET /api/v1/shops/{shop_id}/analysis/overview` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Complete deterministic shop stock analysis overview, KPIs, freshness fingerprint metadata, product summaries, batch risks, surplus candidates, low-stock items, and deduplicated recommendations. |
| **API-025b** | `GET /api/v1/shops/{shop_id}/analysis/expiry-risk` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Batch-level FEFO expiry-risk analysis (`EXPIRED`, `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `NON_PERISHABLE`, `DEPLETED`, `INSUFFICIENT_DATA`) with optional `risk_level` and `only_near_expiry=1` filters. |
| **API-025c** | `GET /api/v1/shops/{shop_id}/analysis/surplus` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Batch-level potential surplus candidates (`NEAR_EXPIRY_SURPLUS`, `OVERSTOCK_COVERAGE`) with cumulative retention and safety stock breakdown. |
| **API-025d** | `GET /api/v1/shops/{shop_id}/analysis/low-stock` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Product-level low-stock and replenishment analysis (`OUT_OF_STOCK`, `LOW_STOCK`, `BELOW_SAFETY_STOCK`, `ADEQUATE`) with deficit and recommended replenishment quantities. |
| **API-025e** | `GET /api/v1/shops/{shop_id}/analysis/recommendations` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Deduplicated rule-based recommendations (`MONITOR_NEAR_EXPIRY`, `REVIEW_SURPLUS`, `REPLENISHMENT_NEEDED`, `INSUFFICIENT_SALES_HISTORY`, `QUARANTINE_EXPIRED`) with explanations, DB values, assumptions, `rule_version`, and `analyzed_at`. |
| **API-025f** | `GET /api/v1/shops/{shop_id}/analysis/products/{product_id}` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Detailed deterministic analysis for a single product in `{shop_id}` (velocity, stock summary, thresholds, FEFO batches, recommendations). |
| **API-025g** | `GET /api/v1/shops/{shop_id}/analysis/batches/{batch_id}` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Detailed deterministic FEFO, expiry-risk, and surplus analysis for a single batch in `{shop_id}`. |

### Module E2: Inter-Store Stock Transfers (`Phase 6 — Implemented & Verified`)

| ID | Method & Route | Allowed Roles | Description |
| :--- | :--- | :--- | :--- |
| **API-026** | `GET /api/v1/shops/{shop_id}/network-surplus-offers` | `SUPER_ADMIN`, Assigned `SHOP_OWNER` | Scoped view (`OD-003`) of eligible surplus stock in sister shops that matches `{shop_id}`'s low-stock/demand needs (never exposes private sales or full inventory). |
| **API-027** | `POST /api/v1/transfers` | `SHOP_OWNER` (must own `source_shop_id` or `recipient_shop_id`) | Create a transfer proposal (`PENDING_RECIPIENT_APPROVAL` if source owner; `PENDING_SOURCE_APPROVAL` if recipient owner). |
| **API-028** | `GET /api/v1/transfers` | `SUPER_ADMIN` (all), `SHOP_OWNER`/`STAFF` (transfers involving own shop) | List incoming and outgoing transfers with status filters. |
| **API-029** | `GET /api/v1/transfers/{transfer_id}` | `SUPER_ADMIN`, Participating `SHOP_OWNER`/`STAFF` | View full transfer details and `transfer_status_logs` timeline. |
| **API-030** | `POST /api/v1/transfers/{transfer_id}/approve` | Counterparty `SHOP_OWNER` only | Record second Shop Owner approval, atomically reserve stock (`available_qty -> reserved_qty`), and transition to `APPROVED_RESERVED` (`BR-008`). |
| **API-031** | `POST /api/v1/transfers/{transfer_id}/reject` | Counterparty `SHOP_OWNER` only | Reject pending/reserved transfer (`REJECTED`) and release any reservation (`BR-010`). |
| **API-032** | `POST /api/v1/transfers/{transfer_id}/cancel` | Source or Recipient `SHOP_OWNER` | Cancel transfer prior to dispatch (`CANCELLED`) and release any reservation (`BR-010`). |
| **API-033** | `POST /api/v1/transfers/{transfer_id}/dispatch` | Source `SHOP_OWNER` or Source `STAFF` | Confirm physical handover from `APPROVED_RESERVED` -> `DISPATCHED_IN_TRANSIT` (`BR-009`). |
| **API-034** | `POST /api/v1/transfers/{transfer_id}/confirm-receipt` | Recipient `SHOP_OWNER` only | Confirm physical receipt from `DISPATCHED_IN_TRANSIT` -> `COMPLETED`; executes `TRANSFER_OUT` and `TRANSFER_IN` (`BR-009`, `BR-011`). |
| **API-035** | `POST /api/v1/transfers/{transfer_id}/dispute` | Source or Recipient `SHOP_OWNER` | Flag an in-transit transfer as `DISPUTED` (non-receipt or discrepancy) or resolve via `RETURNED_TO_SOURCE` (`BR-016`). |

#### Example: `API-030` (`POST /api/v1/transfers/15/approve`) — Second Owner Approves & Locks Reservation
**Response (`200 OK`):**
```json
{
  "success": true,
  "data": {
    "transfer": {
      "id": 15,
      "transfer_code": "TRF-20261012-0001",
      "source_shop_id": 1,
      "recipient_shop_id": 2,
      "product_id": 4,
      "source_batch_id": 101,
      "quantity": 25,
      "unit_transfer_price": "72.00",
      "status": "APPROVED_RESERVED",
      "source_approved_by_user_id": 2,
      "source_approved_at": "2026-10-12T18:00:00Z",
      "recipient_approved_by_user_id": 3,
      "recipient_approved_at": "2026-10-12T18:12:00Z",
      "reserved_at": "2026-10-12T18:12:00Z"
    },
    "source_batch_balance": {
      "batch_id": 101,
      "available_qty": 16,
      "reserved_qty": 25
    }
  }
}
```

---

### Module F: Notifications & Alerts (Phase 7 Implemented) & Reports (Phase 8 Pending)

#### Phase 7 Notifications & Alerts (Implemented)
| ID | Method & Route | Allowed Roles | Description | Status |
| :--- | :--- | :--- | :--- | :--- |
| **API-036** | `GET /api/v1/notifications` | Any Authenticated | List paginated notifications strictly scoped to authenticated user + unread count. Supports `is_read`, `type`, `page`, `per_page`. | **Phase 7 Implemented** |
| **API-036a** | `GET /api/v1/notifications/unread-count` | Any Authenticated | Fast indexed count of unread notifications for navbar bell polling. | **Phase 7 Implemented** |
| **API-037** | `POST /api/v1/notifications/{id}/read` | Any Authenticated (Owner Only) | Mark specific notification as read, validating recipient user ownership and recording `read_at`. | **Phase 7 Implemented** |
| **API-037a** | `POST /api/v1/notifications/read-all` | Any Authenticated | Mark all unread notifications for authenticated user as read in bulk. | **Phase 7 Implemented** |
| **API-037b** | `POST /api/v1/shops/{shop_id}/inventory/check-alerts` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Trigger on-demand shop low-stock and batch expiry alert evaluation with daily deduplication. | **Phase 7 Implemented** |

#### Phase 8 Analytics & Reports (Implemented & Verified)
| ID | Method & Route | Allowed Roles | Description | Status |
| :--- | :--- | :--- | :--- | :--- |
| **API-038** | `GET /api/v1/reports/shop/{shop_id}/summary` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Shop financial & inventory operational KPIs (revenue, COGS, gross profit, stock valuation, transfer pipeline, alerts). | **Phase 8 Implemented** |
| **API-039** | `GET /api/v1/reports/organization/summary` | `SUPER_ADMIN` | Organization-wide executive summary across all shops (shops breakdown, aggregate stock, profit margins). | **Phase 8 Implemented** |
| **API-040** | `GET /api/v1/reports/export` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | RFC 4180 streaming CSV export (`type=SALES|INVENTORY|NEAR_EXPIRY|TRANSFERS`, filtered by date, shop, category). | **Phase 8 Implemented** |
| **API-040a** | `GET /api/v1/reports/analytics/trends` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Daily/monthly revenue and profit trend series with zero-filled continuous date sequences. | **Phase 8 Implemented** |
| **API-040b** | `GET /api/v1/reports/analytics/top-products` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Top-selling products ranked by sales revenue and quantity volume. | **Phase 8 Implemented** |
| **API-040c** | `GET /api/v1/reports/analytics/stock-distribution` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Breakdown of stock value/quantity by category, batch status, and expiry risk horizons. | **Phase 8 Implemented** |
| **API-040d** | `GET /api/v1/reports/analytics/transfers` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Inter-store transfer throughput, status distribution, and partner exchange metrics. | **Phase 8 Implemented** |
| **API-040e** | `GET /api/v1/reports/analytics/data` | `SUPER_ADMIN`, Assigned `SHOP_OWNER`, Assigned `STAFF` | Tabular report dataset payload with summary statistics for the web reporting console. | **Phase 8 Implemented** |

---

### Module G: AI Inventory Intelligence (`PHASE 8 ONLY — DO NOT IMPLEMENT IN PHASES 1–7`)

| ID | Method & Route | Allowed Roles | Description |
| :--- | :--- | :--- | :--- |
| **API-041** | `POST /api/v1/ai/train-and-evaluate` | `SUPER_ADMIN` | Trigger forecasting model training & backtest evaluation against baseline (`FR-061`). |
| **API-042** | `GET /api/v1/ai/shops/{shop_id}/recommendations` | `SUPER_ADMIN`, Assigned `SHOP_OWNER` | Retrieve demand forecasts, batch expiry-risk scores, and explainable transfer recommendations (`FR-062` to `FR-065`). |
