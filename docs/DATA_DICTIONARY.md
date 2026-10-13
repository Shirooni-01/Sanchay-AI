# Sanchay AI — Data Dictionary & Canonical Enum Reference

This document provides the canonical field definitions, data formatting standards, and enumerated values used across PostgreSQL tables (`DATABASE_SCHEMA.md`), REST API payloads (`API_SPECIFICATION.md`), and UI displays.

---

## 1. Global Data Formatting Standards

| Data Type | PostgreSQL Type | Python Type | JSON Serialization Format | Example |
| :--- | :--- | :--- | :--- | :--- |
| **Primary / Foreign Key ID** | `BIGINT` | `int` | Integer | `1042` |
| **Monetary / Price / Cost** | `NUMERIC(12, 2)` | `decimal.Decimal` | String or 2-decimal Number (`"145.50"`) | `"145.50"` |
| **Quantity (Stock / Sale / Transfer)** | `INTEGER` | `int` | Integer (Discrete units) | `25` |
| **Date (Expiry / Manufacture)** | `DATE` | `datetime.date` | ISO-8601 `YYYY-MM-DD` | `"2026-10-25"` |
| **Timestamp (Events / Audit)** | `TIMESTAMPTZ` | `datetime.datetime` (UTC) | ISO-8601 UTC `YYYY-MM-DDTHH:MM:SSZ` | `"2026-10-12T17:30:00Z"` |
| **Coordinates (Lat / Long)** | `NUMERIC(9, 6)` | `decimal.Decimal` | Float / Decimal | `19.113645` |

---

## 2. Canonical Enumerated Types Reference

### 2.1 User Roles (`users.role`)
| Enum Value | Scope | Description |
| :--- | :--- | :--- |
| `SUPER_ADMIN` | Organization-wide | Full administrative access to organization settings, shops, users, cross-shop reports, and audit logs. |
| `SHOP_OWNER` | Assigned Shop (`shop_id`) | Full operational control of assigned shop: catalog, pricing, batch receipts, stock adjustments, POS sales, and **exclusive authority to approve/reject/confirm stock transfers** for their shop. |
| `STAFF` | Assigned Shop (`shop_id`) | Day-to-day store execution: POS billing, viewing shop inventory, receiving batches, and recording physical transfer dispatch under owner approval. Cannot approve transfer offers or perform destructive adjustments. |

### 2.2 Product Units of Measure (`products.unit`)
- `pcs` — Individual pieces/items
- `pack` — Sealed retail packet/pouch
- `kg` — Kilogram
- `g` — Gram
- `litre` — Litre
- `ml` — Millilitre
- `box` — Carton/box

### 2.3 Batch Status (`inventory_batches.status`)
| Enum Value | Meaning |
| :--- | :--- |
| `ACTIVE` | Batch has positive `available_qty` or `reserved_qty` and is not expired. |
| `DEPLETED` | All units in the batch have been sold or transferred out (`on_hand_qty == 0`). |
| `EXPIRED` | Batch has passed its `expiry_date` (`expiry_date < CURRENT_DATE`); remaining `available_qty` was swept into `expired_qty`. |
| `ARCHIVED` | Administratively closed batch with zero `available_qty` and zero `reserved_qty`. |

### 2.4 Stock Movement Types (`stock_movements.movement_type`)
| Enum Value | Bucket Affected | Sign | Triggering Event |
| :--- | :--- | :--- | :--- |
| `RECEIPT` | `AVAILABLE` | `+` | New batch received into shop inventory |
| `SALE` | `AVAILABLE` | `-` | POS sale deducted via FEFO |
| `SALE_RETURN` | `AVAILABLE`, `DAMAGED`, or `EXPIRED` | `+` | Customer return (`RESELLABLE` -> `AVAILABLE` if unexpired else `EXPIRED`; `DAMAGED` -> `DAMAGED`; `EXPIRED` -> `EXPIRED`) |
| `SALE_CANCELLATION` | `AVAILABLE` or `EXPIRED` | `+` | Same-day POS invoice cancellation (restores to `AVAILABLE` if unexpired else `EXPIRED`) |
| `ADJUSTMENT_ADD` | `AVAILABLE` | `+` | Authorized cycle-count surplus correction by `SHOP_OWNER` |
| `ADJUSTMENT_SUBTRACT` | `AVAILABLE` | `-` | Authorized cycle-count shrinkage correction by `SHOP_OWNER` |
| `MARK_DAMAGED` | `AVAILABLE` (-), `DAMAGED` (+) | `-` / `+` | Two paired ledger entries moving units from `available_qty` (or `reserved_qty`) to `damaged_qty` |
| `MARK_EXPIRED` | `AVAILABLE` (-), `EXPIRED` (+) | `-` / `+` | Two paired ledger entries moving expired `available_qty` to `expired_qty` |
| `TRANSFER_RESERVE` | `AVAILABLE` (-), `RESERVED` (+) | `-` / `+` | Dual approval completed (`APPROVED_RESERVED`); locks stock for outgoing transfer |
| `TRANSFER_RELEASE` | `RESERVED` (-), `AVAILABLE` (+) | `-` / `+` | Transfer rejected, cancelled, expired, or returned to source |
| `TRANSFER_OUT` | `RESERVED` | `-` | Recipient confirmed physical receipt (`COMPLETED`); deducts source `reserved_qty` |
| `TRANSFER_IN` | `AVAILABLE` | `+` | Recipient confirmed physical receipt (`COMPLETED`); credits recipient batch `available_qty` |

### 2.5 Transfer Request Lifecycle States (`transfer_requests.status`)
| Enum Value | Is Terminal? | Stock Reserved in Source? | Description |
| :--- | :--- | :--- | :--- |
| `PENDING_RECIPIENT_APPROVAL` | No | No (Counted in open offer cap) | Initiated & approved by Source Shop Owner; awaiting Recipient Shop Owner approval. |
| `PENDING_SOURCE_APPROVAL` | No | No (Counted in open offer cap) | Initiated & approved by Recipient Shop Owner; awaiting Source Shop Owner approval. |
| `APPROVED_RESERVED` | No | **Yes (`reserved_qty`)** | Both Shop Owners approved; stock locked on source batch awaiting physical dispatch. |
| `DISPATCHED_IN_TRANSIT` | No | **Yes (`reserved_qty`)** | Source Shop confirmed physical handover; goods in transit to recipient shop. |
| `COMPLETED` | **Yes** | Deducted & Transferred | Recipient Shop Owner confirmed physical receipt; inventory updated in both shops. |
| `REJECTED` | **Yes** | Released (`0`) | Rejected by either Shop Owner before dispatch. |
| `CANCELLED` | **Yes** | Released (`0`) | Cancelled by either Shop Owner before dispatch. |
| `EXPIRED_OFFER` | **Yes** | Released (`0`) | Timed out awaiting approval (24h) or dispatch (48h). |
| `DISPUTED` | No | **Yes (`reserved_qty`)** | Dispatched transfer reported missing/discrepant or exceeded 72h transit SLA. |
| `RETURNED_TO_SOURCE` | **Yes** | Released (`0`) | Disputed transfer physically returned to source shop and verified by Source Shop Owner. |

---

## 3. Key Computed Domain Fields (Returned by Service/API Layer)

These fields are computed dynamically from canonical PostgreSQL columns to prevent denormalization drift:

| Computed Field Name | Formula / Definition | Used In |
| :--- | :--- | :--- |
| `effective_sellable_qty` | `batch.available_qty` if `batch.status == 'ACTIVE'` and `(batch.expiry_date is None or batch.expiry_date >= CURRENT_DATE)` else `0` | Inventory API, POS Billing, Surplus Detection |
| `days_to_expiry` | `(batch.expiry_date - CURRENT_DATE).days` (or `null` if non-perishable) | Batch List, Near-Expiry Alerts, Transfer Cards |
| `open_offered_qty` | `SUM(transfer_requests.quantity)` for active `PENDING_*` offers on `batch.id` | Transfer Creation Validation (`BR-005`) |
| `eligible_transfer_qty` | `max(0, effective_sellable_qty - open_offered_qty)` | Surplus & Transfer Proposal Modal |
| `daily_sales_velocity` | `SUM(max(0, sale_item.quantity - sale_item.returned_quantity) on non-cancelled sales in window W) / W` | Phase 5 `StockAnalysisService` |
| `estimated_batch_demand_before_expiry` | `max(0, floor(daily_sales_velocity * max(0, days_to_expiry)) - earlier_batches_sellable_qty)` | Phase 5 FEFO Batch Expiry-Risk Evaluation |
| `estimated_unsold_at_expiry_qty` | `max(0, effective_sellable_qty - estimated_batch_demand_before_expiry)` | Phase 5 Batch Expiry-Risk & Wastage Exposure |
| `safety_stock_qty` | `ceil(daily_sales_velocity * safety_stock_days)` (or explicit `safety_stock_qty` override) | Phase 5 Surplus & Replenishment Analysis |
| `min_product_retention_qty` | `max(reorder_threshold, safety_stock_qty)` | Phase 5 Shop Retention Floor |
| `potential_surplus_qty` | `min(max(0, effective_sellable_qty - max(0, required_cum_retention - earlier_batches_retained_qty)), remaining_surplus_pool)` | Phase 5 Surplus Candidate Identification |
| `data_fingerprint` | 24-char SHA-256 digest of shop batches, stock movements, sales, sale items, returns, and catalog thresholds | Phase 5 Snapshot Freshness & Stale Invalidation |
| `gross_profit_amount` | `sale.total_amount - sale.returned_amount - sale.total_cost_amount` | Sales Summary & Executive Reports (`BR-017`) |

