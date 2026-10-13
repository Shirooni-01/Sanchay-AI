# Sanchay AI — Phase 8 AI Inventory Intelligence Implementation Plan

> **CRITICAL GOVERNANCE GATE (`RULE-03`, `RULE-04`, `BR-020`):**  
> This document is a **Phase 8 Design Specification ONLY**.  
> **DO NOT implement, import, train, or wire any code from this plan during Phases 1 through 7.**  
> Phase 8 may begin only after Phases 1–7 are completely implemented, tested, and explicitly approved.

---

## 1. Objectives of Phase 8 AI Inventory Intelligence

Once the non-AI operational foundation (Phases 1–7) is verified in production/demo readiness, **Phase 8** upgrades Sanchay AI with data-driven predictive intelligence:

1. **Short-Horizon Demand Forecasting (`FR-061`):** Predict daily and 7/14-day product demand per shop ($\hat{y}_{s,p,t+1 \dots t+H}$) with calibrated prediction intervals (`lower_ci_90`, `upper_ci_90`) from real PostgreSQL sales records.
2. **Batch Sell-Through & Expiry-Risk Scoring (`FR-062`):** Estimate whether each active perishable batch $b$ will sell out before its `expiry_date` given its position in the shop's FEFO queue and forecasted demand distribution.
3. **Cross-Store Demand Matching & Smart Transfer Recommendations (`FR-063`, `FR-064`):** Recommend optimal `(source_shop, source_batch, recipient_shop, transfer_qty)` pairs that maximize expected wastage avoided net of transfer logistics cost, accompanied by transparent, human-readable explanations.
4. **Rigorous Model Evaluation & Safe Fallbacks (`FR-065`):** Evaluate candidate models against a naive baseline using walk-forward backtesting (`MAE`, `RMSE`, `sMAPE`) and automatically fall back to Phase 5 deterministic rules when data is sparse (`< 14` days).

---

## 2. Model Architecture Selection: Statistical vs. Tabular ML vs. SLM (`ADR-009`)

Per **Section 5G**, we evaluated three candidate modeling paradigms for Sanchay AI's problem structure (tabular time-series sales across 3–5 shops and 7–10 products):

| Modeling Paradigm | Suitability for Numerical Demand Forecasting | Suitability for Expiry-Risk & Transfer Optimization | Suitability for Explanations | Verdict for Phase 8 |
| :--- | :--- | :--- | :--- | :--- |
| **1. Classical Statistical Forecasting** (7-Day Moving Average Baseline, Holt-Winters / Exponential Smoothing) | **High** for short time series (`14–90` days); zero cold-start overfitting; fast & deterministic. | **High** when paired with analytical FEFO queue depletion math. | **High** (transparent trend & seasonal components). | **SELECTED** (Baseline + Primary Short-Series Forecaster) |
| **2. Conventional Tabular ML** (`scikit-learn` Ridge / Poisson Regressor / HistGradientBoostingRegressor) | **High** when `>= 30` days of history exist to capture day-of-week (`is_weekend`), lag features (`lag_1`, `lag_7`, `rolling_mean_7`), and price elasticity. | **High** (predicts expected demand + residual variance for Monte Carlo / analytical survival scoring). | **High** (feature contribution breakdown: velocity, weekend lift, days-to-expiry). | **SELECTED** (Primary Multi-Feature Forecaster when history `>= 30` days) |
| **3. Small Language Model (SLM) / LLM** | **Poor / Inappropriate.** Autoregressive text tokens are uncalibrated for numerical time-series regression, require heavy GPU/RAM, add latency, and risk hallucinating quantities. | **Poor.** Numerical optimization across batch constraints requires exact arithmetic, not text generation. | **Unnecessary.** Deterministic templates populated with exact model feature values are 100% faithful and never hallucinate numbers. | **REJECTED for Forecasting & Scoring** (`ADR-009`) |

---

## 3. Mathematical Formulation of Phase 8 Pipeline

### 3.1 Feature Engineering Pipeline (From PostgreSQL)
For each `(shop_id, product_id)`, aggregate completed POS sales (`sales` + `sale_items`, net of `sale_returns` and `CANCELLED` invoices) into a continuous daily time series $y_{s,p,t}$ for date $t \in [t_{\text{start}}, t_{\text{today}}]$ (filling zero-sale days with `0` unless the product was completely out of stock all day, which is flagged as censored demand).

Engineered features per day $t$:
- `day_of_week` ($0 \dots 6$) and `is_weekend` ($\{0, 1\}$)
- `lag_1`, `lag_7`, `lag_14`
- `rolling_mean_7`, `rolling_std_7`, `rolling_mean_14`
- `current_selling_price` relative to `default_selling_price`

### 3.2 Model Selection & Walk-Forward Evaluation (`evaluation.py`)
For each `(shop_id, product_id)` with $\ge 14$ days of history:
1. Split series chronologically: first $80\%$ (or up to $t_{\text{today}} - 14$) as **Training Set**, final $14$ days as **Walk-Forward Test Set**.
2. Evaluate:
   - **Baseline Model (`MA_7`):** $\hat{y}_{t+h} = \frac{1}{7}\sum_{i=0}^{6} y_{t-i}$
   - **Statistical Model (`EXP_SMOOTHING`):** Exponential smoothing with day-of-week seasonal adjustment.
   - **Tabular ML Model (`POISSON_GBR` / `RIDGE`):** Trained on lag and calendar features (if history $\ge 30$ days).
3. Compute evaluation metrics on the held-out window:
   $$\text{MAE} = \frac{1}{N}\sum_{t=1}^{N} |y_t - \hat{y}_t|, \quad \text{RMSE} = \sqrt{\frac{1}{N}\sum_{t=1}^{N} (y_t - \hat{y}_t)^2}, \quad \text{sMAPE} = \frac{100\%}{N}\sum_{t=1}^{N} \frac{|y_t - \hat{y}_t|}{(|y_t| + |\hat{y}_t|)/2 + \epsilon}$$
4. **Winner Selection Rule:** Persist metrics in `ai_model_runs`. Select the model with the lowest held-out `MAE` (if an ML model fails to beat `MA_7` on a noisy series, automatically select `MA_7` or `EXP_SMOOTHING`).

---

### 3.3 Batch Sell-Through & Expiry-Risk Scoring (`expiry_risk.py`)

Consider active batch $b$ of product $p$ in shop $s$ with:
- Remaining days until expiry: $D_b = (\text{expiry\_date}_b - t_{\text{today}}).\text{days}$
- Sellable units in batch $b$: $Q_b = \text{available\_qty}_b - \text{open\_offered\_qty}_b$
- Earlier-expiring sellable stock ahead of batch $b$ in the shop's FEFO queue: $Q_{\text{ahead}}(b) = \sum_{e \in B_{\text{earlier}}(b)} \text{effective\_sellable\_qty}_e$

Let $\hat{D}_{s,p}(D_b) = \sum_{h=1}^{D_b} \hat{y}_{s,p,t+h}$ be the cumulative forecasted demand over the next $D_b$ days, with standard error $\sigma_{D_b} = \sqrt{D_b} \cdot \hat{\sigma}_{\text{residual}}$.

1. **Expected Demand Available for Batch $b$ (After Clearing FEFO Queue Ahead):**
   $$\mu_{\text{clear}}(b) = \max\left(0, \hat{D}_{s,p}(D_b) - Q_{\text{ahead}}(b)\right)$$
2. **Predicted Unsold Surplus Units at Expiry:**
   $$\widehat{\text{UnsoldQty}}(b) = \max\left(0, Q_b - \mu_{\text{clear}}(b)\right)$$
3. **Expiry Risk Score ($\text{RiskScore}_b \in [0, 1]$):**
   $$\text{RiskScore}_b = \text{clip}\left(\frac{\widehat{\text{UnsoldQty}}(b)}{Q_b}, 0.0, 1.0\right)$$
   - `HIGH_RISK` if $\text{RiskScore}_b \ge 0.35$ or $\widehat{\text{UnsoldQty}}(b) \times \text{unit\_cost}_b \ge \text{INR } 500$.

---

### 3.4 Smart Cross-Store Transfer Recommendation & Net Benefit Optimization (`recommender.py`)

For every batch $b$ in source shop $s_{\text{src}}$ with $\widehat{\text{UnsoldQty}}(b) \ge 1$ and $D_b \ge 2$:
1. Evaluate each candidate recipient shop $s_{\text{dst}} \ne s_{\text{src}}$ in the same organization:
   - Compute recipient's forecasted demand over $D_b$ days: $\hat{D}_{s_{\text{dst}}, p}(D_b)$.
   - Compute recipient's current sellable stock expiring on or before $\text{expiry\_date}_b$: $Q_{s_{\text{dst}}, \text{competing}}$.
   - **Recipient Absorption Capacity Before Expiry:**
     $$\text{Capacity}(s_{\text{dst}}, b) = \max\left(0, \lfloor \hat{D}_{s_{\text{dst}}, p}(D_b) - Q_{s_{\text{dst}}, \text{competing}} \rfloor\right)$$
2. **Recommended Transfer Quantity ($Q_{\text{rec}}$):**
   $$Q_{\text{rec}} = \min\left(\lceil \widehat{\text{UnsoldQty}}(b) \rceil, \text{Capacity}(s_{\text{dst}}, b)\right)$$
3. **Expected Financial Value & Net Wastage Avoided:**
   - $\text{GrossWastageAvoidedCost} = Q_{\text{rec}} \times \text{unit\_cost}_b$
   - $\text{LogisticsCost} = \text{distance\_km}(s_{\text{src}}, s_{\text{dst}}) \times \text{TRANSFER\_COST\_PER\_KM\_INR}$
   - $\text{ExpectedNetSavings} = \text{GrossWastageAvoidedCost} - \text{LogisticsCost}$
   - Only recommend transfers where $Q_{\text{rec}} > 0$ AND $\text{ExpectedNetSavings} > 0$.

---

### 3.5 Explainability, Confidence & Fallback Handling

Every recommendation stored in `ai_transfer_recommendations` includes a structured explanation object rendered in the UI:
- **Example Explanation Output:**
  > *"Batch `B-PNR-101` (45 packs, expires in 6 days) at **Andheri West** has a forecasted demand of **12.4 ± 2.8 packs** over the next 6 days (`Holt-Winters`, MAE: `0.85`), leaving **~32 packs (INR 2,304 at cost)** at high risk of expiry (`Risk Score: 0.71`). **Bandra West** (`6.2 km` away) has only **4 packs** in stock and a forecasted 6-day demand of **44.8 ± 4.1 packs**, giving it capacity to sell **25 transferred packs** before expiry for an estimated net wastage mitigation of **INR 1,750.40** after **INR 49.60** transport cost."*

- **Confidence & Fallback Rules (`FR-065`):**
  - `HIGH` Confidence: $\ge 30$ days history AND held-out $\text{sMAPE} \le 25\%$.
  - `MEDIUM` Confidence: $14\text{–}29$ days history OR $25\% < \text{sMAPE} \le 40\%$.
  - `LOW / FALLBACK`: $< 14$ days history OR $\text{sMAPE} > 40\%$ -> UI displays badge `RULE-BASED FALLBACK (Insufficient History for ML)` and uses Phase 5's deterministic `SurplusService`.

---

## 4. Documented Limitations of Synthetic / Sparse Hackathon Demo Data

In compliance with **Section 5G** and **Project Rule #20**, the following limitations must be transparently documented in the Phase 8 UI and reports:
1. **Seeded History Nature:** Because a fresh hackathon installation does not have months of organic brick-and-mortar store traffic, Phase 7's `seed_demo_data.py` generates 60 days of realistic synthetic POS transactions (incorporating weekday/weekend seasonality, store-specific base velocities, and random Poisson noise).
2. **Generalization Caveat:** Model accuracy metrics (`MAE`, `RMSE`, `sMAPE`) measured on seeded demo data validate that the forecasting pipeline, walk-forward backtester, and FEFO risk math function correctly on structured time series, but real-world retail deployment requires 4–8 weeks of live store POS data collection to calibrate promotional spikes, local holidays, and weather effects.
3. **Human-in-the-Loop Mandate:** Because forecasts have inherent statistical uncertainty, AI recommendations **never** execute transfers or reserve stock automatically; both Shop Owners must review and approve every transfer via the Phase 5 workflow.
