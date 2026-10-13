# Sanchay AI — Comprehensive Business Rules Specification

This document defines the canonical business rules (`BR-001` through `BR-020`), mathematical invariants, state transition tables, and failure-handling specifications for **Sanchay AI**. All backend services (`app/services/`) and database constraints (`DATABASE_SCHEMA.md`) must enforce these rules.

---

## 1. Master Index of Critical Business Rules (`BR-001` to `BR-020`)

| Rule ID | Category | Summary Statement | Primary Enforcement Layer |
| :--- | :--- | :--- | :--- |
| **BR-001** | Inventory & Batches | A product in a shop may have multiple batches with distinct batch codes, unit costs, and expiry dates. | `inventory_batches` schema + `InventoryService` |
| **BR-002** | Reconciliation | Shop product inventory quantities must reconcile 100% with batch buckets and immutable `stock_movements` ledger entries. | `InventoryService` ACID transactions |
| **BR-003** | Stock Buckets | Every batch partitions physical stock into four mutually exclusive non-negative buckets: `available_qty`, `reserved_qty`, `damaged_qty`, and `expired_qty`. | DB `CHECK` constraints + `InventoryService` |
| **BR-004** | Sales Validation | A POS sale cannot exceed currently available, non-expired sellable stock (`effective_sellable_qty`). | `SalesService` + `SELECT ... FOR UPDATE` |
| **BR-005** | Transfer Validation | A transfer offer or reservation cannot exceed eligible non-expired, unoffered `available_qty` on the source batch. | `TransferService` + `SELECT ... FOR UPDATE` |
| **BR-006** | Concurrency Safety | Concurrent POS sales, stock adjustments, and transfer reservations must serialize on batch rows and never create negative stock. | Row locking (`FOR UPDATE`) + DB `CHECK (>= 0)` |
| **BR-007** | Audit Ledger | Every stock-changing operation must write an immutable `stock_movements` row and an `audit_logs` entry within the same database transaction. | Service layer transaction helper |
| **BR-008** | Dual Approval | Both the Source Shop Owner and the Recipient Shop Owner must explicitly approve a proposed transfer before stock is reserved for dispatch. | `TransferService` state machine |
| **BR-009** | Handover & Receipt | A transfer must never be marked `COMPLETED` before Source dispatch confirmation (`DISPATCHED_IN_TRANSIT`) AND Recipient physical receipt confirmation (`COMPLETED`). | `TransferService` state machine |
| **BR-010** | Reservation Release | A rejected, cancelled, or expired transfer must safely release any reserved quantity (`reserved_qty -> available_qty`, or `expired_qty` if now expired). | `TransferService` release logic |
| **BR-011** | Idempotent Completion | A transfer must never be completed, dispatched, or released twice. Terminal states are immutable. | State transition lock (`FOR UPDATE` on transfer) |
| **BR-012** | Organization Scope | Both participating shops in a transfer must belong to the same active organization and both shops must have `status = 'ACTIVE'`. | `TransferService` validation |
| **BR-013** | Shop Data Isolation | A Shop Owner or Staff user must never read or modify another shop's private inventory, batches, cost prices, or sales records. | `@require_shop_access` + scoped queries |
| **BR-014** | Backend Super Admin | `SUPER_ADMIN` privileges and all role permissions (`SHOP_OWNER`, `STAFF`) must be enforced strictly on the backend. | `@require_role` decorator |
| **BR-015** | Data-Driven Alerts | Low-stock, near-expiry, expired-batch, and surplus alerts must be generated strictly from real PostgreSQL data and configured thresholds. | `NotificationService` & `AlertService` |
| **BR-016** | Failure Resilience | The system must gracefully handle unavailable recipients, insufficient stock, stale offers, expired batches, transit non-receipt, and DB rollbacks. | Domain exceptions + rollback handlers |
| **BR-017** | Financial Metrics | The system must strictly distinguish Gross Sales Revenue, Cost of Goods Sold (COGS), Gross Profit, Inventory Cost Valuation, and Retail Valuation. | `ReportService` formulas |
| **BR-018** | Honest Savings Reporting | Estimated wastage avoided and financial savings must be clearly labeled as rule-based/realized metrics and never fabricated. | `ReportService` & UI labels |
| **BR-019** | No Product Scanning | Barcode scanning, QR scanning, OCR, camera input, and image recognition are excluded from all workflows. | Manual SKU/search input only |
| **BR-020** | AI Phasing Boundary | AI forecasting, expiry-risk models, and smart recommendations are disabled and unimplemented until Phase 8. | `AI_ENABLED` flag + Phase 8 isolation |

---

## 2. Detailed Mathematical Invariants & Formulas

### 2.1 Batch-to-Product Stock Reconciliation (`BR-001`, `BR-002`, `BR-003`)

Every unit of physical stock in shop $s$ for product $p$ resides in a specific batch $b \in B(s, p)$ in the `inventory_batches` table.

1. **Non-Negativity Constraint (Per Batch $b$):**
   $$\text{available\_qty}_b \ge 0, \quad \text{reserved\_qty}_b \ge 0, \quad \text{damaged\_qty}_b \ge 0, \quad \text{expired\_qty}_b \ge 0$$

2. **Total Physical On-Hand Quantity (Per Batch $b$):**
   $$\text{on\_hand\_qty}_b = \text{available\_qty}_b + \text{reserved\_qty}_b + \text{damaged\_qty}_b + \text{expired\_qty}_b$$

3. **Effective Sellable Quantity (Per Batch $b$ at Date $t_{\text{today}}$):**
   A batch is eligible for sale or outgoing transfer only if its status is `ACTIVE` and it has not reached or passed its expiry date (Wait: is a batch expiring *today* sellable today or expired? Standard retail rule: a batch with `expiry_date >= CURRENT_DATE` is valid through the end of `expiry_date`; once `CURRENT_DATE > expiry_date`, i.e., `expiry_date < CURRENT_DATE`, it is expired and unsellable):
   $$\text{effective\_sellable\_qty}_b = \begin{cases} \text{available\_qty}_b & \text{if } \text{status}_b = \text{'ACTIVE'} \text{ and } (\text{expiry\_date}_b \text{ is NULL or } \text{expiry\_date}_b \ge t_{\text{today}}) \\ 0 & \text{otherwise} \end{cases}$$

4. **Automatic Expiry Sweep Rule:**
   Whenever inventory is queried, or before any POS sale / transfer approval executes on a shop's batches, any batch with `expiry_date < CURRENT_DATE` and `available_qty > 0` automatically transitions its remaining `available_qty` to `expired_qty`:
   - $\Delta = \text{available\_qty}_b$
   - $\text{available\_qty}_b \leftarrow 0$
   - $\text{expired\_qty}_b \leftarrow \text{expired\_qty}_b + \Delta$
   - $\text{status}_b \leftarrow \text{'EXPIRED'}$ (if `reserved_qty == 0`)
   - Writes a `MARK_EXPIRED` record to `stock_movements` and generates an `EXPIRED_BATCH` notification.

5. **Shop-Level Product Totals (For Shop $s$, Product $p$):**
   - $\text{Total Sellable Stock}(s, p) = \sum_{b \in B(s, p)} \text{effective\_sellable\_qty}_b$
   - $\text{Total Reserved Stock}(s, p) = \sum_{b \in B(s, p)} \text{reserved\_qty}_b$
   - $\text{Total Damaged Stock}(s, p) = \sum_{b \in B(s, p)} \text{damaged\_qty}_b$
   - $\text{Total Expired Stock}(s, p) = \sum_{b \in B(s, p)} \text{expired\_qty}_b$
   - $\text{Total Physical Stock}(s, p) = \sum_{b \in B(s, p)} \text{on\_hand\_qty}_b$

6. **Stock Movement Ledger Invariant (`BR-002`, `BR-007`):**
   For every batch $b$ and bucket $k \in \{\text{AVAILABLE}, \text{RESERVED}, \text{DAMAGED}, \text{EXPIRED}\}$:
   $$\text{CurrentBucketQty}(b, k) = \sum_{m \in \text{Movements}(b, k)} m.\text{quantity\_change}$$

---

### 2.2 First Expired, First Out (FEFO) Deduction Algorithm (`BR-004`)

When a customer buys quantity $Q_{\text{req}} > 0$ of product $p$ at shop $s$:

1. **Expiry Sweep:** Run the expiry sweep on all batches of $(s, p)$ where `expiry_date < CURRENT_DATE`.
2. **Lock Eligible Batches:** Query and row-lock (`SELECT ... FOR UPDATE`) all batches $b \in B(s, p)$ where:
   - `status = 'ACTIVE'`
   - `available_qty > 0`
   - `expiry_date IS NULL OR expiry_date >= CURRENT_DATE`
   - **Order By:** `expiry_date ASC NULLS LAST, received_at ASC, id ASC` (FEFO with FIFO tie-breaker).
3. **Verify Aggregate Availability:**
   Let $Q_{\text{avail}} = \sum \text{available\_qty}_b$.
   If $Q_{\text{avail}} < Q_{\text{req}}$, abort transaction and raise `InsufficientStockError` (`409 Conflict`) returning `{ "requested_qty": Q_req, "available_qty": Q_avail }`.
4. **Iterative Allocation:**
   Set $Q_{\text{rem}} = Q_{\text{req}}$. For each locked batch $b$ in FEFO order while $Q_{\text{rem}} > 0$:
   - $q_{\text{take}} = \min(\text{available\_qty}_b, Q_{\text{rem}})$
   - $\text{available\_qty}_b \leftarrow \text{available\_qty}_b - q_{\text{take}}$
   - If $\text{on\_hand\_qty}_b == 0$, set $\text{status}_b = \text{'DEPLETED'}$.
   - Create a `sale_items` row linking `sale_id`, `batch_id = b.id`, `quantity = q_take`, `unit_selling_price`, and `unit_cost_price = b.unit_cost`.
   - Create a `stock_movements` row with `movement_type = 'SALE'`, `bucket_affected = 'AVAILABLE'`, `quantity_change = -q_take`, `balance_after = b.available_qty`.
   - $Q_{\text{rem}} \leftarrow Q_{\text{rem}} - q_{\text{take}}$.

---

### 2.3 Rule-Based Surplus & Eligible Recipient Detection (Phase 5 — Non-AI)

Before Phase 8 AI exists, Phase 5 uses deterministic, transparent business rules to identify surplus stock and match eligible recipient shops within the same organization:

1. **Historical Daily Sales Velocity ($V_{s,p}$):**
1. **Daily Sales Velocity ($V_{s,p}$):**
   Calculated from non-cancelled sales (`Sale.status != 'CANCELLED'`) in shop $s$ for product $p$ over the configurable lookback window $W$ (default $W = 14$ days), deducting item returns (`quantity - returned_quantity`):
   $$V_{s,p} = \frac{\sum_{i \in \text{ValidSaleItems}(s,p,W)} \max(0, \text{quantity}_i - \text{returned\_quantity}_i)}{W}$$
   - **Missing & Sparse History Safety:**
     - `NO_SALES_HISTORY`: Product has 0 sales in window $W$ and either was stocked $< \min(W, 7)$ days ago or the shop has $< 3$ total sales (`is_demand_reliable = False`, `insufficient_data = True`). **Missing sales history is never treated as proof of zero demand.**
     - `SPARSE_HISTORY`: Product has sales on fewer than `ANALYSIS_MIN_ACTIVE_SALE_DAYS` (default 2) distinct days or was stocked $< \min(W, 7)$ days ago (`is_demand_reliable = True`, `insufficient_data = True`, `demand_confidence = 'LOW'`).
     - `ESTABLISHED_ZERO_SALES`: Product has been stocked $\ge \min(W, 7)$ days in an active shop with 0 net units sold (`is_demand_reliable = True`, `zero_demand_proven = True`).
     - `SUFFICIENT_HISTORY`: Product has $\ge 2$ distinct sale days over $\ge \min(W, 7)$ stocked days (`is_demand_reliable = True`, `insufficient_data = False`, `demand_confidence = 'HIGH'`).

2. **Batch Days Until Expiry ($D_b$) & FEFO Queue-Aware Batch Demand:**
   $$D_b = (\text{expiry\_date}_b - t_{\text{today}}).\text{days}$$
   For batches ordered by FEFO (`expiry_date ASC NULLS LAST, received_at ASC, id ASC`), earlier-expiring sellable batches $B_{\text{earlier}}$ absorb projected product demand first:
   $$\text{ProductDemand}_b = \lfloor V_{s,p} \times \max(0, D_b) \rfloor, \quad \text{BatchDemand}_b = \max\left(0, \text{ProductDemand}_b - \sum_{e \in B_{\text{earlier}}} \text{SellableQty}_e\right)$$
   $$\text{EstimatedSellThrough}_b = \min(\text{SellableQty}_b, \text{BatchDemand}_b), \quad \text{EstimatedUnsold}_b = \max(0, \text{SellableQty}_b - \text{BatchDemand}_b)$$
   - `reserved_qty`, `damaged_qty`, and `expired_qty` (including past-due available stock) are strictly excluded from $\text{SellableQty}_b$.

3. **Safety Stock, Shop Retention Floor & Non-Double-Counting Surplus Calculation (Phase 5 `StockAnalysisService`):**
   - **Dynamic Safety Stock:** $\text{SafetyStockQty}_{s,p} = \lceil V_{s,p} \times \text{safety\_stock\_days} \rceil$ (default `safety_stock_days = 2`), or $\max(\text{explicit\_override}, \lceil V_{s,p} \times \text{safety\_stock\_days} \rceil)$ when overridden.
   - **Shop Minimum Retention Floor:** A shop must always retain at least:
     $$\text{MinRetentionQty}_{s,p} = \max(\text{reorder\_threshold}_{s,p}, \text{SafetyStockQty}_{s,p})$$
     and the total product surplus pool is bounded by $\text{MaxSurplusPool}_{s,p} = \max(0, \text{TotalSellableQty}_{s,p} - \text{MinRetentionQty}_{s,p})$.
   - **Condition A (Near-Expiry Surplus — `NEAR_EXPIRY_SURPLUS`):** When $0 \le D_b \le \text{near\_expiry\_days}_{s,p}$ and `is_demand_reliable == True`, cumulative retention required up to batch $b$'s expiry is:
     $$\text{RequiredCumRetention}_b = \max(\text{MinRetentionQty}_{s,p}, \text{ProductDemand}_b + \text{SafetyStockQty}_{s,p})$$
     Deducting units already retained in earlier FEFO batches ($\text{EarlierRetained}_b$) prevents double-counting across batches:
     $$\text{NeededFromBatch}_b = \max(0, \text{RequiredCumRetention}_b - \text{EarlierRetained}_b)$$
     $$\text{PotentialSurplusQty}_b = \min(\max(0, \text{SellableQty}_b - \text{NeededFromBatch}_b), \text{RemainingSurplusPool})$$
   - **Condition B (Overstock Coverage Surplus — `OVERSTOCK_COVERAGE`):** When a batch is non-perishable or beyond `near_expiry_days`, and total sellable stock exceeds both `surplus_threshold` and $\text{OverstockFloor} = \max(\text{surplus\_threshold}, \text{MinRetentionQty}_{s,p}, \lceil V_{s,p} \times \text{surplus\_coverage\_days} \rceil + \text{SafetyStockQty}_{s,p})$.
   - **Read-Only Guarantee (Phase 5):** Running `StockAnalysisService.analyze_shop(...)` is 100% read-only and never modifies `inventory_batches` or `stock_movements`.
   - **Snapshot Freshness & Stale Invalidation:** Every shop analysis snapshot records a SHA-256 `data_fingerprint` over current batches, stock movements, sales, sale items, returns, and catalog thresholds, and automatically invalidates and recalculates whenever underlying PostgreSQL data changes.

4. **Eligible Recipient Shop Criteria (Phase 6 Inter-Store Stock Exchange — Recipient Shop $s_{\text{dst}}$):**
   A shop $s_{\text{dst}}$ in the same organization is an **Eligible Recipient** for product $p$ (and batch $b$ with remaining life $D_b$) if:
   - $s_{\text{dst}}.\text{id} \ne s_{\text{src}}.\text{id}$ and $s_{\text{dst}}.\text{status} = \text{'ACTIVE'}$.
   - **Stock Need OR High Velocity:** Either $\text{Total Sellable Stock}(s_{\text{dst}}, p) \le \text{low\_stock\_threshold}_{s_{\text{dst}}, p}$ OR $V_{s_{\text{dst}}, p} > V_{s_{\text{src}}, p}$.
   - **Absorption Capacity Before Expiry:** If batch $b$ has expiry $D_b$ days away, $s_{\text{dst}}$ can reasonably sell the proposed quantity before expiry (or $D_b \ge 3$ days minimum buffer).
   - **Distance & Cost Score:** If shop coordinates (`latitude`, `longitude`) are populated, compute Haversine distance $d(s_{\text{src}}, s_{\text{dst}})$ in km and estimated logistics cost:
     $$\text{estimated\_transfer\_cost} = d(s_{\text{src}}, s_{\text{dst}}) \times \text{TRANSFER\_COST\_PER\_KM\_INR}$$
     Rank candidate recipient shops by:
     1. Stockout urgency ($\text{Total Sellable Stock} == 0$ first, then below `low_stock_threshold`),
     2. Higher daily sales velocity $V_{s_{\text{dst}}, p}$,
     3. Shortest distance / lowest transfer cost.

---

### 2.4 Inter-Store Stock Transfer Lifecycle & State Machine (`BR-008` to `BR-012`, `BR-016`)

#### Valid States of `transfer_requests`
1. `PENDING_RECIPIENT_APPROVAL`: Offer created and approved by Source Shop Owner; awaiting Recipient Shop Owner approval.
2. `PENDING_SOURCE_APPROVAL`: Request initiated by Recipient Shop Owner (or Super Admin proposal); awaiting Source Shop Owner approval.
3. `APPROVED_RESERVED`: **Both** Source Shop Owner and Recipient Shop Owner have approved. Stock is locked in `inventory_batches.reserved_qty` on the source batch.
4. `DISPATCHED_IN_TRANSIT`: Source Shop has confirmed physical handover/dispatch. Goods are in transit. Stock remains locked in `reserved_qty` on the source batch.
5. `COMPLETED`: Recipient Shop Owner has confirmed physical receipt. Stock is deducted from source `reserved_qty` (`TRANSFER_OUT`) and added to recipient batch `available_qty` (`TRANSFER_IN`).
6. `REJECTED`: Rejected by either Shop Owner prior to dispatch. Any reservation is released.
7. `CANCELLED`: Cancelled by either Shop Owner prior to dispatch (`DISPATCHED_IN_TRANSIT`). Any reservation is released.
8. `EXPIRED_OFFER`: Offer timed out before dual approval (default 24h) or before dispatch (default 48h). Any reservation is released.
9. `DISPUTED`: Recipient reported non-receipt, severe discrepancy, or transit SLA (72h) expired after `DISPATCHED_IN_TRANSIT`. Stock remains locked in source `reserved_qty` until resolved.
10. `RETURNED_TO_SOURCE`: A `DISPUTED` shipment was physically returned to the source shop and verified by Source Shop Owner, releasing `reserved_qty` back to `available_qty` (or `damaged_qty` / `expired_qty`).

#### State Transition Rules & Failure Handling Matrix

| Edge Case / Failure Scenario | Required System Behavior |
| :--- | :--- |
| **Double-Offering Same Batch** | When creating an offer on batch $b$, query `SUM(quantity)` of open offers on batch $b$ in states `('PENDING_SOURCE_APPROVAL', 'PENDING_RECIPIENT_APPROVAL')`. Enforce `requested_qty <= batch.available_qty - open_offered_qty`. Reject with `409 OFFER_EXCEEDS_ELIGIBLE_STOCK` if exceeded. |
| **Insufficient Stock at Second Approval** | If local POS sales at Source Shop reduced `batch.available_qty` below `transfer.quantity` while the offer was pending, the second approval transaction (`SELECT ... FOR UPDATE` on batch $b$) rejects transition to `APPROVED_RESERVED` with `409 INSUFFICIENT_STOCK_AT_RESERVATION`. |
| **Batch Expires Before Completion** | If `batch.expiry_date < CURRENT_DATE` when attempting approval, dispatch, or receipt: block completion, transition transfer to `CANCELLED` (or `DISPUTED` if in transit), move reserved stock to `expired_qty`, and notify both Shop Owners. |
| **Rejected Offer (`REJECTED`)** | Allowed from `PENDING_SOURCE_APPROVAL`, `PENDING_RECIPIENT_APPROVAL`, or `APPROVED_RESERVED` (before dispatch). If in `APPROVED_RESERVED`, atomically restore `reserved_qty -> available_qty` (`TRANSFER_RELEASE`). |
| **Cancelled Offer (`CANCELLED`)** | Allowed by either party from `PENDING_*` or `APPROVED_RESERVED` before physical dispatch. Cannot cancel unilaterally once `DISPATCHED_IN_TRANSIT`. |
| **Transfer Never Received (`DISPUTED`)** | Once `DISPATCHED_IN_TRANSIT`, stock is neither in source `available_qty` nor in recipient inventory. If not confirmed within `TRANSFER_RECEIPT_SLA_HOURS` (72h) or if Recipient flags non-receipt, transfer enters `DISPUTED` and alerts Super Admin and both Shop Owners. |
| **Duplicate API Submission (Double Click)** | `SELECT ... FOR UPDATE` on `transfer_requests.id` checks current state first. If already `COMPLETED`, returns `409 TRANSFER_ALREADY_COMPLETED` without duplicating stock movements. |
| **Database Failure Mid-Transfer** | All multi-table updates (`transfer_requests`, source `inventory_batches`, recipient `inventory_batches`, `stock_movements`, `notifications`, `audit_logs`) share a single SQLAlchemy session transaction. Any exception triggers `db.session.rollback()`. |

---

### 2.5 Financial & Reporting Formulas (`BR-017`, `BR-018`)

To prevent misleading financial reporting, all analytics and exports must use these exact definitions:

1. **Gross Sales Revenue (Shop $s$, Period $[t_1, t_2]$):**
   $$\text{Gross Revenue} = \sum_{\text{sale} \in \text{Sales}(s, t_1, t_2)} (\text{total\_amount} - \text{returned\_amount})$$
2. **Cost of Goods Sold — COGS (Shop $s$, Period $[t_1, t_2]$):**
   $$\text{COGS} = \sum_{\text{item} \in \text{SaleItems}(s, t_1, t_2)} (\text{item.quantity} - \text{item.returned\_quantity}) \times \text{item.unit\_cost\_price}$$
3. **Gross Profit:**
   $$\text{Gross Profit} = \text{Gross Revenue} - \text{COGS}$$
4. **Inventory Valuation (At Cost vs. At Retail):**
   - **Sellable Inventory Cost Value:** $\sum_{b} \text{effective\_sellable\_qty}_b \times \text{unit\_cost}_b$
   - **Sellable Inventory Retail Value:** $\sum_{b} \text{effective\_sellable\_qty}_b \times \text{selling\_price}_{b}$
   - **Locked/Reserved Inventory Cost Value:** $\sum_{b} \text{reserved\_qty}_b \times \text{unit\_cost}_b$
   - **Recorded Unsellable Loss (Expired + Damaged Cost Value):** $\sum_{b} (\text{expired\_qty}_b + \text{damaged\_qty}_b) \times \text{unit\_cost}_b$
5. **Wastage Avoided & Financial Savings Attribution (`BR-018`):**
   - **Transferred Near-Expiry Stock Value (Potential Wastage Mitigated):** Sum of `quantity * source_unit_cost` for completed transfers where the transferred batch was within `near_expiry_days` at transfer time.
   - **Confirmed Realized Wastage Avoided (Verified Sold Before Expiry):** Sum of `sold_qty * unit_cost` (and net revenue recovered) for transferred batches that were **subsequently sold via POS at the recipient shop prior to their `expiry_date`**, minus `transfer_cost`.
   - Reports must display **both** *"Transferred At-Risk Inventory Value (Estimated Mitigation)"* and *"Confirmed Sold Transferred Stock (Realized Savings)"* so estimates are never misrepresented as realized cash.

---

### 2.6 In-App Notifications & Event Alert Delivery Rules (`BR-015`, Phase 7 Implemented)

1. **Authorized Recipient Isolation:**
   - Notifications are private to the recipient user (`recipient_user_id`).
   - Store events (low stock, batch expiry, transfers) broadcast exclusively to users actively assigned to the shop with roles `SHOP_OWNER` or `STAFF`.
   - Users belonging to other organizations or unassigned stores cannot read, list, or mark notifications as read (`403 FORBIDDEN_NOTIFICATION_ACCESS`).
2. **Atomic In-Transaction Triggering:**
   - All event notifications (transfer offers, approvals, rejections, cancellations, dispatches, receipts) are created within the transaction boundary of the business operation.
   - If stock checks, concurrency locks, or validations fail, the transaction rolls back atomically, ensuring zero false-positive notifications.
3. **Deterministic Idempotency & Deduplication:**
   - Daily automated and on-demand alert evaluations compute a standard `dedup_key` formatted as `{EVENT_TYPE}:{shop_id}:{entity_id}:{YYYY-MM-DD}:user:{user_id}`.
   - Unique database constraint `uq_notifications_user_dedup` prevents duplicate alerts from being inserted for the same recipient on the same day.
4. **Lightweight Client Polling & Non-Intrusive Delivery:**
   - Polling runs via simple client-side HTTP queries on `/api/v1/notifications/unread-count` (45-second intervals).
   - Zero external messaging gateways (SMS, WhatsApp, Email, WebPush) are used, adhering strictly to core architectural rules.

