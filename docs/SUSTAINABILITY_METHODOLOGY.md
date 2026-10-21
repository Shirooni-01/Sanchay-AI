# Smart Vendor Carbon Accounting & Sustainability Methodology Specification

**System Version:** `v1.0.0-fao-warm`  
**Reward Rule Version:** `v1.0.0-10pts-per-kg`  
**Applicability:** Multi-Store Retail Food Inventory & Waste Prevention  

---

## 1. Core Principles

The Smart Vendor sustainability engine enforces a strict distinction between three separate concepts:

1. **Estimated Net Emissions Avoided ($\text{kg CO}_2\text{e}$):**  
   The scientific estimate of greenhouse gas emissions prevented by diverting perishable, edible food from anaerobic decomposition in municipal landfills or open dump sites, net of attributable road logistics emissions.
2. **Smart Vendor Sustainability Points:**  
   Internal gamification and cooperative performance ranking units awarded to participating stores (10 points per $1\text{ kg CO}_2\text{e}$ of verified net positive benefit).
3. **Official Carbon Credits:**  
   Distinct regulatory, tradable environmental commodities. **Smart Vendor internal points and estimates are NOT officially certified or tradable carbon offsets.** No claims of compliance with UNFCCC, Verra, or Gold Standard are made without formal third-party physical audit.

---

## 2. Emission Factors & Scientific Datasets

Reference factors are versioned, documented, and traceable to established public datasets:

| Food Category | Factor ($\text{kg CO}_2\text{e} / \text{kg}$) | Scope | Geographic Benchmark | Source Citation |
| :--- | :--- | :--- | :--- | :--- |
| **Fruits** | 0.8500 (Apples) / 0.8000 (Gen) | Waste Prevention | India / South Asia | FAO Food Wastage Footprint (2013) & EPA WARM v15 |
| **Vegetables** | 0.6200 (Roots) / 0.6800 (Gen) | Waste Prevention | India / South Asia | FAO (2013) & Poore & Nemecek (2018), *Science* 360 |
| **Grains and cereals** | 1.2500 (Oats/Millet) / 1.2000 | Waste Prevention | Global / India | Poore & Nemecek (2018) & FAO Cereal Assessment |
| **Rice and wheat** | 1.6500 (Rice) / 1.1000 (Wheat) | Waste Prevention | India | IRRI & FAO South Asia Post-Harvest & Waste Assessment |
| **Pulses and legumes** | 1.0500 (Dals/Lentils) | Waste Prevention | India / Global | Poore & Nemecek (2018) Legume Degradation Metrics |
| **Dairy products** | 1.4500 (Milk) / 2.1000 (Paneer/Curd) | Waste Prevention | India | FAO Dairy Sector GHG Emissions & Poore & Nemecek (2018) |
| **Bakery products** | 0.9500 (Bread) / 1.0500 (Gen) | Waste Prevention | Global / India | WRAP UK Bakery Waste GHG Assessment |
| **Meat and poultry** | 2.8500 (Poultry/Eggs) / 4.5000 (Mutton) | Waste Prevention | India / Global | Poore & Nemecek (2018) Lifecycle Loss Parameters |
| **Seafood** | 2.4000 (Fish/Prawns) | Waste Prevention | Global / South Asia | FAO Fisheries & Aquaculture Post-Harvest Metrics |
| **Packaged foods** | 1.1500 (Snacks) / 1.2000 (Gen) | Waste Prevention | Global | WRAP UK & US EPA WARM Food Waste Diversion |
| **Beverages** | 0.5500 (Juices/Syrups) | Waste Prevention | India / Global | Beverage Industry Environmental Roundtable (BIER) |
| **Frozen foods** | 1.3500 (Snacks/Veggies) | Waste Prevention | Global | International Institute of Refrigeration (IIR) |
| **Ready-to-eat meals**| 1.5000 (Curries/Meals) | Waste Prevention | India / Global | FAO & WRAP Prepared Meals Benchmark |
| **Other food products**| 0.9000 (Composite Baseline) | Waste Prevention | Global | FAO (2013) Organic Food Waste Landfill Baseline |
| **Road Freight Logistics**| 0.2100 $\text{kg CO}_2\text{e} / \text{km}$ | Transport Road | India | India GHG Program / Central Pollution Control Board (CPCB) |

---

## 3. Tiered Resolution Hierarchy

When evaluating inventory items, the engine applies a strict 5-tier resolution sequence:

1. **Tier 1 (Exact Product ID):** Specific product-linked factor matching `product.id`.
2. **Tier 2 (Product Keyword Pattern):** Regex match against canonical product name (e.g. `apple`, `paneer`, `milk`, `bread`).
3. **Tier 3 (Subcategory Benchmark):** Specific food subcategory matching (e.g. `Cultured & Solid Dairy`, `Roots & Tubers`).
4. **Tier 4 (Broader Category Fallback):** Category fallback factor, labeled as `MEDIUM_ESTIMATE` with disclosed uncertainty.
5. **Tier 5 (Pending Review):** If no defensible factor exists, the calculation is marked `PENDING_REVIEW` with 0 benefit awarded until administratively configured.

---

## 4. Five-Step Environmental Calculation Engine

### Step 1: Metric Normalization
All inventory units are converted to kilograms ($\text{kg}$):
- Metric weights: $\text{kg} \times 1.0$, $\text{g} / 1000.0$.
- Volumes: $\text{litre} \times 1.02$ (density adjustment for dairy/beverage), $\text{ml} \times 0.00102$.
- Count units (`pcs`, `pack`, `box`): Product-specific piece weights (Apple: $0.180\text{ kg}$, Bread: $0.400\text{ kg}$, Paneer: $0.200\text{ kg}$) or category benchmarks.

### Step 2: Waste Prevention Eligibility Check
A transfer is **never** proof of waste prevention by default. An event is eligible only when:
- Transfer status is `COMPLETED`.
- Quantity received in good condition $> 0$ (transit damages are deducted).
- Stock arrived prior to batch expiration ($\text{Days to Expiry} > 0$).
- Receiving store has demonstrated sales velocity to absorb the surplus stock before expiry.

### Step 3: Attributable Logistics Emissions
$$\text{Transport Emissions} = \text{Distance (km)} \times 0.2100\text{ kg CO}_2\text{e/km} \times \text{Cold Chain Multiplier (1.25 if chilled/frozen)}$$

### Step 4: Net Environmental Benefit
$$\text{Net Benefit} = \text{Avoided Emissions} - \text{Attributable Transport Emissions}$$
- If $\text{Net Benefit} \le 0$, points awarded = 0 and status is marked `INELIGIBLE`.

### Step 5: Sustainability Points
$$\text{Points} = \max\left(0, \text{round}\left(\text{Net Benefit} \times 10.0\right)\right)$$

---

## 5. Fair Multi-Shop Attribution & Anti-Double-Counting

To prevent double counting across sister shops in the same organization:
- Total attributed impact equals exactly $100\%$ of the organization-level net benefit.
- **50% Attributed to Supplying Store:** Credit for identifying surplus and preventing local spoilage.
- **50% Attributed to Receiving Store:** Credit for accepting, displaying, and selling the inventory.
- Both allocations are recorded atomically inside `sustainability_ledger` with unique idempotency keys (`SUS-LEDGER-EVT{id}-SRC{shop_id}`, `SUS-LEDGER-EVT{id}-DST{shop_id}`).
- Repeated submissions or transfer retries trigger database constraint protection.

---

## 6. Auditable Reversals

Historical ledger entries are never silently edited or deleted. If a transfer is disputed or reversed:
- Offsetting negative ledger records are created with transaction type `REVERSAL`.
- Sum of ledger records always reflects the authoritative point balance and carbon impact.
