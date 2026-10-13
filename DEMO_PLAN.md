# Sanchay AI — Hackathon Demonstration Playbook (`DEMO_PLAN.md`)

> **IMPORTANT NOTICE (`RULE-11`, `RULE-12`):**  
> All shop names, product SKUs, batch quantities, expiry dates, and sales histories in this document are **explicitly marked as DEMONSTRATION DATA**.  
> None of this data may ever be hardcoded into Python application logic, HTML templates, or JavaScript files. In Phase 7, this scenario will be loaded into PostgreSQL solely via the explicit CLI seed script (`python -m flask seed-demo` / `scripts/seed_demo_data.py`).

---

## 1. Demonstration Overview

- **Organization:** `Sanchay Retail Cooperative Ltd.` (`ORG-SANCHAY-01`)
- **Scale:** **4 Demo Shops** across Mumbai and **9 Retail Products** (7 perishable, 2 long-shelf-life staples).
- **Primary Demo Goal:** Demonstrate end-to-end how Sanchay AI detects localized near-expiry surplus in one shop, matches it with high demand / low stock in a sister shop, enforces dual Shop Owner approval + physical dispatch/receipt confirmation, handles rejection and insufficient-stock failure paths safely, and quantifies wastage avoided.

---

## 2. Demo Shops & User Accounts (Demonstration Data)

| Shop ID | Code | Shop Name | City / Area | Coordinates (`Lat, Lon`) | Assigned Shop Owner | Assigned Staff |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | `SHP-MUM-01` | **Sanchay Mart — Andheri West** | Mumbai | `19.1197, 72.8464` | `owner_andheri` (Priya Nair) | `staff_andheri` (Amit Verma) |
| **2** | `SHP-MUM-02` | **Sanchay Mart — Bandra West** | Mumbai (`~6.2 km` from Shop 1) | `19.0596, 72.8295` | `owner_bandra` (Rohan Deshmukh) | `staff_bandra` (Neha Joshi) |
| **3** | `SHP-MUM-03` | **Sanchay Mart — Powai** | Mumbai (`~9.5 km` from Shop 1) | `19.1176, 72.9060` | `owner_powai` (Kavita Kulkarni) | `staff_powai` (Suresh Patil) |
| **4** | `SHP-MUM-04` | **Sanchay Mart — Dadar East** | Mumbai (`~11.8 km` from Shop 1) | `19.0178, 72.8478` | `owner_dadar` (Vikram Mehta) | `staff_dadar` (Pooja Sawant) |

- **Organization Super Admin:** `admin_rajesh` (Rajesh Sharma, `SUPER_ADMIN`).

---

## 3. Demo Product Catalog — 9 Products (Demonstration Data)

| # | SKU | Product Name | Category | Unit | Perishable? | Cost Price (INR) | Selling Price (INR) |
| :---: | :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| **1** | `SKU-DAIRY-PANEER-200G` | Fresh Malai Paneer (200g) | Dairy | `pack` | Yes | `72.00` | `95.00` |
| **2** | `SKU-DAIRY-GREEK-YOGURT` | Probiotic Greek Yogurt (400g) | Dairy | `pcs` | Yes | `65.00` | `90.00` |
| **3** | `SKU-DAIRY-CHEDDAR-200G` | Artisan Cheddar Cheese Block (200g) | Dairy | `pack` | Yes | `140.00` | `185.00` |
| **4** | `SKU-BAKERY-MULTIGRAIN` | Multigrain Sandwich Loaf (400g) | Bakery | `pack` | Yes | `42.00` | `60.00` |
| **5** | `SKU-BEV-COLD-COFFEE` | Arabica Cold Brew Bottle (250ml) | Beverages | `pcs` | Yes | `55.00` | `80.00` |
| **6** | `SKU-BEV-ALMOND-MILK-1L` | Unsweetened Almond Milk (1L) | Beverages | `litre` | Yes | `190.00` | `250.00` |
| **7** | `SKU-SNACK-HUMMUS-150G` | Classic Chickpea Hummus Dip (150g) | Fresh Snacks | `pcs` | Yes | `85.00` | `120.00` |
| **8** | `SKU-STAPLE-BASMATI-5KG` | Royal Aged Basmati Rice (5kg) | Staples | `pack` | No | `420.00` | `550.00` |
| **9** | `SKU-STAPLE-OLIVE-OIL-1L` | Extra Virgin Cold-Pressed Olive Oil (1L) | Staples | `litre` | No | `680.00` | `890.00` |

---

## 4. Initial Shop Inventory, Velocity & Surplus/Demand Imbalance Setup

Let $T_0$ represent the demonstration date. The seed script populates 60 days of historical POS transactions creating clear, realistic velocity differences across the 4 shops:

| Product SKU | Shop 1 (Andheri) State | Shop 2 (Bandra) State | Shop 3 (Powai) State | Shop 4 (Dadar) State |
| :--- | :--- | :--- | :--- | :--- |
| **`SKU-DAIRY-PANEER-200G`** | **SURPLUS AT RISK:** Batch `B-PNR-101` has **`45` packs** expiring in **`6` days** ($T_0 + 6$). Velocity: **`2.0`/day** (Expected to sell only `12`, leaving **`33` packs at risk of expiry** = INR `2,376` cost). | **HIGH DEMAND / LOW STOCK:** Only **`4` packs** left (below threshold of `10`). Velocity: **`7.5`/day** (Can easily sell `30` packs in `4` days). | Balanced (`18` packs, expires $T_0 + 12$, velocity `3.0`/day). | Balanced (`15` packs, expires $T_0 + 11$, velocity `2.5`/day). |
| **`SKU-BEV-ALMOND-MILK-1L`** | Low Stock (`3` litres left, velocity `3.5`/day). | Balanced (`14` litres). | **SURPLUS OVERSTOCK:** Batch `B-ALM-301` has **`40` litres** expiring in **`9` days** ($T_0 + 9$). Velocity: **`1.0`/day** (`31` litres surplus). | **HIGH DEMAND:** Only **`2` litres** left. Velocity: **`4.5`/day**. |
| **`SKU-SNACK-HUMMUS-150G`** | Balanced (`12` pcs). | **SURPLUS AT RISK:** Batch `B-HUM-201` has **`28` pcs** expiring in **`5` days** ($T_0 + 5$). Velocity: **`1.2`/day** (`22` pcs at risk). | **LOW STOCK:** `3` pcs left, velocity `5.0`/day. | Balanced (`10` pcs). |
| **`SKU-BAKERY-MULTIGRAIN`** | Batch `B-BRD-100` (`5` packs, expires $T_0 + 2$) + Batch `B-BRD-101` (`20` packs, expires $T_0 + 5$). Used for **FEFO POS Demo**. | `16` packs sellable. | `14` packs sellable. | `12` packs sellable. |

---

## 5. Step-by-Step Live Demonstration Walkthrough

### Scene 1: FEFO Point-of-Sale Billing (Shop 1 — Andheri West)
1. Log in as `staff_andheri` (Amit Verma, `STAFF` at Shop 1).
2. Open **Inventory Batches** for `SKU-BAKERY-MULTIGRAIN`:
   - Observe **Batch `B-BRD-100`**: `5` packs available, expires in `2` days ($T_0 + 2$), cost `42.00`.
   - Observe **Batch `B-BRD-101`**: `20` packs available, expires in `5` days ($T_0 + 5$), cost `42.00`.
   - Total Sellable Stock = `25` packs.
3. Open **POS Billing Counter** and create a sale for **`8` packs** of `Multigrain Sandwich Loaf` (`8 * 60.00 = INR 480.00`).
4. **Expected Verified Output:**
   - Invoice `INV-SHP1-...` is generated showing **automatic FEFO split**:
     - `5` packs deducted from earlier-expiring `B-BRD-100` (`available_qty` becomes `0`, status -> `DEPLETED`).
     - `3` packs deducted from `B-BRD-101` (`available_qty` becomes `17`).
   - Two `SALE` entries appear in `stock_movements`.

---

### Scene 2: Happy-Path Inter-Store Transfer (Surplus Detection -> Dual Approval -> Handover -> Sale)
1. **Step 2A — Surplus Detection & Proposal (Shop 1 Owner):**
   - Log in as `owner_andheri` (Priya Nair, `SHOP_OWNER` of Shop 1).
   - Navigate to **Surplus & Transfer Matchmaker**.
   - System highlights `SKU-DAIRY-PANEER-200G` (Batch `B-PNR-101`, `45` available, expires in `6` days, `33` projected unsold surplus) and ranks **Shop 2 (Bandra West, 6.2 km away, only 4 packs left, 7.5/day velocity)** as the #1 eligible recipient.
   - Priya proposes transferring **`25` packs** from Batch `B-PNR-101` to Shop 2 at unit transfer price `INR 72.00` (at-cost).
   - **State Change:** Transfer `TRF-20261012-0001` is created in `PENDING_RECIPIENT_APPROVAL` (`source_approved_at` recorded; `open_offered_qty = 25`).
2. **Step 2B — Recipient Shop Owner Approval & Stock Reservation (Shop 2 Owner):**
   - Log in as `owner_bandra` (Rohan Deshmukh, `SHOP_OWNER` of Shop 2).
   - Click the unread notification badge (`TRANSFER_OFFER_RECEIVED: 25 packs of Fresh Malai Paneer from Andheri West`).
   - Inspect batch expiry date ($T_0 + 6$), transfer price (`72.00`), and click **Approve Transfer Offer**.
   - **Expected Verified State Change:**
     - Transfer status transitions to **`APPROVED_RESERVED`** (NOT `COMPLETED`!).
     - On Shop 1's Batch `B-PNR-101`: `available_qty` drops from `45` to **`20`**, and `reserved_qty` increases from `0` to **`25`** (`TRANSFER_RESERVE` ledger movement).
     - Shop 2's sellable stock is **still `4`** because physical handover has not happened yet.
3. **Step 2C — Source Physical Dispatch Confirmation (Shop 1):**
   - Log in as `owner_andheri` (or `staff_andheri`).
   - Open `TRF-20261012-0001` and click **Confirm Physical Dispatch** with note `"Dispatched via insulated bike courier MH-02-AB-1234"`.
   - **Expected Verified State Change:**
     - Transfer status transitions to **`DISPATCHED_IN_TRANSIT`**.
     - `25` units remain locked in Shop 1's `reserved_qty`.
4. **Step 2D — Recipient Physical Receipt Confirmation & Completion (Shop 2 Owner):**
   - Log in as `owner_bandra` (Rohan Deshmukh).
   - Open `TRF-20261012-0001` and click **Confirm Physical Receipt** (`received_good_qty = 25`).
   - **Expected Verified State Change (Single ACID Transaction):**
     - Transfer status transitions to **`COMPLETED`**.
     - Shop 1's Batch `B-PNR-101`: `reserved_qty` drops from `25` to **`0`** (`TRANSFER_OUT` movement of `-25`). Total remaining in Shop 1 = `20` available.
     - Shop 2 receives new Batch `B-PNR-101-TRF1` with `available_qty = 25`, `expiry_date = T_0 + 6`, `unit_cost = 72.00` (`TRANSFER_IN` movement of `+25`). Shop 2's total sellable Paneer increases from `4` to **`29` packs**.
5. **Step 2E — Realized Wastage Avoided Proof:**
   - Shop 2 processes a POS sale of `15` packs of Paneer.
   - Navigate to **Analytics & Reports**:
     - **Estimated At-Risk Value Mitigated (Transferred):** `25 packs * INR 72.00 = INR 1,800.00`.
     - **Confirmed Realized Wastage Avoided (Sold Before Expiry at Recipient):** `15 packs` (`11` from transferred batch after `4` older units sold) verified from actual POS `sale_items` linked to `source_transfer_id`.

---

### Scene 3: Failure, Rejection & Insufficient-Stock Edge Cases
1. **Edge Case A — Double-Offering Prevention (`BR-005`):**
   - In Shop 3 (Powai), Batch `B-ALM-301` has `40` available litres of Almond Milk.
   - Shop 3 Owner creates an offer for `25` litres to Shop 4 (`PENDING_RECIPIENT_APPROVAL`).
   - Shop 3 Owner immediately attempts to create a second offer for `20` litres from the same batch to Shop 1.
   - **Verified Result:** Rejected with `409 OFFER_EXCEEDS_ELIGIBLE_STOCK` (`Available: 40, Open Offered: 25, Max Eligible: 15`).
2. **Edge Case B — Rejection & Safe Reservation Release (`BR-010`):**
   - Shop 2 Owner proposes transferring `20` pcs of Hummus (`B-HUM-201`) to Shop 3, and Shop 3 Owner approves (`APPROVED_RESERVED`: `20` pcs moved to `reserved_qty` in Shop 2).
   - Before dispatch, Shop 3 Owner cancels the transfer (`reason: "Refrigerator maintenance issue"`).
   - **Verified Result:** Transfer transitions to `CANCELLED`, and Shop 2's `reserved_qty` (`20`) is atomically released back to `available_qty` (`TRANSFER_RELEASE`).
3. **Edge Case C — Local POS Sale Depletes Stock Before Recipient Accepts (`BR-016`):**
   - Shop 1 has `20` packs of Paneer left in `B-PNR-101` and offers `15` packs to Shop 3 (`PENDING_RECIPIENT_APPROVAL`).
   - Before Shop 3 Owner approves, a walk-in restaurant customer at Shop 1 buys `12` packs via POS (leaving only `8` packs in `available_qty`).
   - When Shop 3 Owner clicks **Approve** on the `15`-pack offer, the backend locks `B-PNR-101` (`SELECT ... FOR UPDATE`), detects `available_qty (8) < transfer.quantity (15)`, and returns `409 INSUFFICIENT_STOCK_AT_RESERVATION` without creating negative stock.
4. **Edge Case D — Cross-Shop Unauthorized Access Attempt (`BR-013`):**
   - While logged in as `owner_andheri` (Shop 1), attempt to request `/api/v1/shops/2/batches` or approve a transfer on behalf of Shop 2.
   - **Verified Result:** Backend returns `403 FORBIDDEN_SHOP_ACCESS` and logs the violation in `audit_logs`.
