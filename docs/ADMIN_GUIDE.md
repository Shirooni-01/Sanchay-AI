# Sanchay AI — Super Admin & Organization Governance Guide

**Target Audience:** `SUPER_ADMIN` (Organization Directors & Platform Administrators)  
**Status:** Production-Ready (`132/132` Tests Passing)  
**Security Model:** Strict 2-Role RBAC (`SUPER_ADMIN` and `SHOP_OWNER`)

---

## 1. Super Admin Responsibilities & Boundaries

As a `SUPER_ADMIN`, you oversee the entire multi-store organization (`organizations`):

- **What Super Admin CAN Do:**
  - Configure organization-wide defaults (`low_stock_default_threshold`, `near_expiry_days_threshold`).
  - Onboard, edit, suspend, or deactivate retail shops (`shops`).
  - Create and manage `SHOP_OWNER` user accounts and assign them to shops (`user_shop_assignments`).
  - View organization-wide inventory valuation, revenue, gross profit, near-expiry risk, and inter-store transfer analytics across all shops.
  - Broadcast organization-wide announcements (`ORG_ANNOUNCEMENT`).
  - Inspect the immutable `audit_logs` and resolve `DISPUTED` inter-store transfers.
  - Evaluate AI demand forecasting models and review cross-store redistribution opportunities.
- **What Super Admin Does NOT Bypass (`RULE-17`, `BR-008`):**
  - Super Admin does not unilaterally complete stock transfers without the participating Shop Owners' dual approval and physical handover confirmation.

---

## 2. Onboarding Shops & Assigning Store Owners (Phase 2+)

### 2.1 Registering a New Shop
1. Navigate to **Admin -> Shops Directory** and click **+ Register Shop**.
2. Provide:
   - **Shop Name** (e.g., `Sanchay Mart — Andheri West`)
   - **Shop Code** (unique per organization, e.g., `SHP-MUM-01`)
   - **Address, City, Pincode**
   - **Latitude & Longitude** (used by the Transfer Matchmaker to calculate Haversine distance in km and estimated logistics cost between stores)
   - **Contact Phone & Email**
   - **Default Low-Stock & Near-Expiry Thresholds**
3. Click **Create Shop**.

### 2.2 Creating & Assigning Users (`OD-001`)
1. Navigate to **Admin -> Users & Roles** and click **+ Create User**.
2. Enter **Full Name**, **Username**, **Email**, **Initial Password**, and select the **Role**:
   - `SHOP_OWNER`: Designate to specific shops (`user_shop_assignments`). Shop Owners possess complete operational control over their store (POS billing, batch receiving, stock adjustments, and transfer approvals).
   - `SUPER_ADMIN`: Grants organization-wide administrative oversight across all shops and network governance.
3. To revoke access when an employee leaves, toggle **Account Active** to `Off` (`is_active = False`). Note that user records are never physically deleted so historical sales and audit logs remain intact.

---

## 3. Monitoring Organization Analytics & Wastage Avoided (Phase 6+)

From the **Executive Overview Dashboard**, Super Admins can monitor:
1. **Cross-Shop Inventory Valuation:** Total sellable stock at Cost Value vs. Retail Value, plus capital currently locked in `reserved_qty` or lost to `expired_qty` / `damaged_qty` (`BR-017`).
2. **Wastage Mitigation Attribution (`BR-018`):**
   - **Transferred Near-Expiry Value (Estimated Mitigation):** Total cost value of near-expiry batches successfully redistributed between sister shops.
   - **Confirmed Realized Wastage Avoided:** Verified value of transferred near-expiry units that were subsequently **sold to customers via POS before their expiry date** at the recipient shop, net of transfer logistics cost.
3. **CSV & PDF Exports:** Download organization-wide reports from **Reports -> Export Center** for board reviews or accounting reconciliation.

---

## 4. Resolving Disputed Transfers & Reviewing Audit Logs

1. **Handling `DISPUTED` Transfers (`BR-016`):**
   - If a shipment in `DISPATCHED_IN_TRANSIT` exceeds the 72-hour receipt SLA or a Recipient Shop Owner reports a transit discrepancy, a critical `TRANSFER_DISPUTED` notification is sent to both Shop Owners and the Super Admin.
   - Coordinate with both Shop Owners to verify physical custody of the batch:
     - If delivered to the recipient shop: Recipient Shop Owner confirms receipt (`COMPLETED`, recording any `transit_damaged_qty`).
     - If returned to the source shop: Source Shop Owner confirms return (`RETURNED_TO_SOURCE`), which safely releases `reserved_qty` back to `available_qty` (or `damaged_qty`).
2. **Audit Log Inspection:**
   - Navigate to **Admin -> Audit Trail** to filter immutable `audit_logs` by shop, user, action type (`UNAUTHORIZED_SHOP_ACCESS`, `BATCH_ADJUSTED`, `SALE_CANCELLED`, `TRANSFER_APPROVED`), and date range.
