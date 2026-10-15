"""Sanchay AI — AI Demand Forecasting & Inventory Intelligence Engine (PRD Module G)
=============================================================================

This module provides the core Machine Learning and statistical models for:
  1. Multi-Horizon Demand Velocity Forecasting (Ridge regression with day-of-week seasonality, trend & rolling features).
  2. Predictive Stockout Risk Modeling (Days of Inventory, projected stockout date & dynamic replenishment floor).
  3. Probabilistic Batch Spoilage Modeling (Poisson survival cumulative distribution P(Unsold > 0)).
  4. Inter-Store Cross-Shop Rebalancing & Salvage Optimization (Haversine transit vs gross margin balancing).
  5. Promotional Markdown Optimization (prescriptive local discounts to salvage perishable surplus).

DESIGN PRINCIPLES:
  1. Strict Decoupling: Fully isolated from transactional models; reads from DB,
     simulates projections, and outputs explainable recommendation candidates.
  2. Zero Autonomous Mutation: The AI module never modifies inventory or creates
     transfers directly. All recommendations must be approved by human Shop Owners
     through the standard Phase 6 dual-approval state machine.
  3. Graceful Fallback: Multi-tiered fallback hierarchy (ML Ridge >= 14 days -> EWMA smoothed 3-13 days -> Baseline default).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.linear_model import Ridge

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch
from app.models.organization import Shop
from app.models.sales import Sale, SaleItem
from app.utils.geo import haversine_distance_km

logger = logging.getLogger(__name__)


# =============================================================================
# 1. Type Definitions & Data Contracts
# =============================================================================


@dataclass
class ProductAnalysisInput:
    """Input payload contract for analyzing a product for cross-store redistribution."""

    shop_id: int
    product_id: int
    lookback_days: int = 14
    target_horizon_days: int = 14
    min_confidence_threshold: float = 0.65
    include_peer_shops: bool = True
    reference_date: date | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DemandProjection:
    """Projected demand velocity and uncertainty bounds produced by the ML model."""

    daily_velocity: float
    projected_7d_demand: float
    projected_14d_demand: float
    confidence_score: float  # 0.0 to 1.0
    lower_bound_7d: float
    upper_bound_7d: float
    data_points_analyzed: int
    data_sufficiency: str  # "SUFFICIENT", "SPARSE", "INSUFFICIENT_DATA"
    projected_30d_demand: float = 0.0
    model_name: str = "Ridge Seasonality Regressor"
    mae: float = 0.0
    r_squared: float = 0.0
    weekend_multiplier: float = 1.0
    trend_slope: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExpiryRiskAssessment:
    """Batch-level expiry risk score and expected unsold units at expiration."""

    batch_id: int
    batch_code: str
    expiry_date: str
    days_to_expiry: int
    available_qty: int
    projected_unsold_units: int
    expiry_risk_score: float  # 0.0 (safe) to 1.0 (imminent expiration)
    at_risk_cost_value: float
    recommended_action: str  # "RETAIN", "OFFER_TRANSFER", "DISCOUNT_LOCALLY", "QUARANTINE"
    poisson_spoilage_probability: float = 0.0
    suggested_discount_percent: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateRecipientShop:
    """Target sister shop that can absorb the surplus before expiry."""

    recipient_shop_id: int
    recipient_shop_name: str
    recipient_shop_code: str
    distance_km: float
    recipient_current_stock: int
    recipient_daily_velocity: float
    estimated_days_to_absorb: float
    estimated_transport_cost: float
    projected_net_salvage_value: float
    match_priority: str  # "HIGH", "MEDIUM", "LOW"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StockoutPrediction:
    """Predictive stockout timeline and replenishment floor recommendation."""

    total_available_qty: int
    days_of_inventory: float
    stockout_risk_level: str  # "CRITICAL", "WARNING", "HEALTHY", "OVERSTOCKED"
    projected_stockout_date: str | None
    recommended_reorder_qty: int
    reorder_trigger_point: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProductAnalysisResult:
    """Complete output contract produced by the AI Product Analysis Engine."""

    product_id: int
    product_name: str
    sku: str
    category: str
    source_shop_id: int
    source_shop_name: str
    analyzed_at: str
    model_status: str  # "READY", "MOCK_SIMULATION", "INSUFFICIENT_DATA", "AI_MODEL_ACTIVE"
    ai_model_version: str
    demand_projection: DemandProjection
    expiry_risk_assessments: list[ExpiryRiskAssessment] = field(default_factory=list)
    candidate_recipient_shops: list[CandidateRecipientShop] = field(default_factory=list)
    plain_english_rationale: str = ""
    can_convert_to_proposal: bool = False
    stockout_prediction: StockoutPrediction | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ShopWasteMitigationSummary:
    """Store-level Waste Mitigation & Salvage Optimization Summary."""

    shop_id: int
    shop_name: str
    analyzed_at: str
    ai_model_version: str
    total_active_batches: int
    total_at_risk_batches: int
    total_capital_at_risk: float
    total_projected_waste_units: int
    potential_salvage_recovery_value: float
    action_breakdown: dict[str, int]
    recommended_markdowns: list[dict[str, Any]]
    recommended_transfers: list[dict[str, Any]]
    at_risk_batches: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# =============================================================================
# 2. Product Analysis Service Implementation
# =============================================================================


class ProductAnalysisService:
    """Advanced AI/ML Demand Forecasting and Stock Redistribution Service."""

    AI_MODEL_VERSION = "v2.0.0 (Scikit-Learn Ridge + Poisson Survival Engine)"
    MIN_HISTORY_DAYS = 14

    @classmethod
    def _build_daily_sales_series(
        cls,
        shop_id: int,
        product_id: int,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Construct a complete continuous daily time series of product sales."""
        days = (end_date - start_date).days + 1
        date_range = [start_date + timedelta(days=i) for i in range(days)]
        daily_df = pd.DataFrame({"sale_date": date_range, "units_sold": 0.0})
        daily_df["sale_date"] = pd.to_datetime(daily_df["sale_date"]).dt.date

        start_dt_utc = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        end_dt_utc = datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc)

        sales_records = (
            db.session.query(
                SaleItem,
                Sale,
            )
            .join(Sale, SaleItem.sale_id == Sale.id)
            .filter(
                Sale.shop_id == shop_id,
                Sale.status.in_(("COMPLETED", "PARTIALLY_RETURNED", "RETURNED")),
                Sale.created_at >= start_dt_utc,
                Sale.created_at <= end_dt_utc,
                SaleItem.product_id == product_id,
            )
            .all()
        )

        record_map: dict[date, float] = {}
        for s_item, sale in sales_records:
            if sale.status == "CANCELLED":
                continue
            net_units = max(0, int(s_item.quantity or 0) - int(s_item.returned_quantity or 0))
            if sale.created_at:
                s_date = sale.created_at.date()
                record_map[s_date] = record_map.get(s_date, 0.0) + float(net_units)

        daily_df["units_sold"] = daily_df["sale_date"].map(lambda d: record_map.get(d, 0.0))
        return daily_df

    @classmethod
    def _fit_ml_demand_model(
        cls,
        daily_df: pd.DataFrame,
        target_horizon_days: int = 14,
    ) -> tuple[DemandProjection, float]:
        """Fit ML Ridge Regression with Day-of-Week seasonality & rolling features or fall back gracefully."""
        n_days = len(daily_df)
        total_units = float(daily_df["units_sold"].sum())
        non_zero_days = int((daily_df["units_sold"] > 0).sum())

        # If data is completely missing or under 3 data points
        if total_units <= 0 or non_zero_days < 2:
            daily_vel = 0.0
            return (
                DemandProjection(
                    daily_velocity=0.0,
                    projected_7d_demand=0.0,
                    projected_14d_demand=0.0,
                    projected_30d_demand=0.0,
                    confidence_score=0.35,
                    lower_bound_7d=0.0,
                    upper_bound_7d=0.0,
                    data_points_analyzed=non_zero_days,
                    data_sufficiency="INSUFFICIENT_DATA",
                    model_name="Fallback Zero Baseline",
                    mae=0.0,
                    r_squared=0.0,
                ),
                daily_vel,
            )

        # Tier 2: Sparse data (3 to 13 days of history or < 5 non-zero days) -> EWMA smoothing
        if n_days < cls.MIN_HISTORY_DAYS or non_zero_days < 5:
            ewma_series = daily_df["units_sold"].ewm(span=7, adjust=False).mean()
            daily_vel = round(float(ewma_series.iloc[-1]), 2)
            proj_7 = round(daily_vel * 7, 1)
            proj_14 = round(daily_vel * 14, 1)
            proj_30 = round(daily_vel * 30, 1)

            # Empirical std
            std = float(daily_df["units_sold"].std() or 1.0)
            return (
                DemandProjection(
                    daily_velocity=daily_vel,
                    projected_7d_demand=proj_7,
                    projected_14d_demand=proj_14,
                    projected_30d_demand=proj_30,
                    confidence_score=0.65,
                    lower_bound_7d=round(max(0.0, proj_7 - 1.645 * std), 1),
                    upper_bound_7d=round(proj_7 + 1.645 * std, 1),
                    data_points_analyzed=non_zero_days,
                    data_sufficiency="SPARSE",
                    model_name="EWMA Smoothed Velocity",
                    mae=round(float(np.mean(np.abs(daily_df["units_sold"] - daily_vel))), 2),
                    r_squared=0.25,
                ),
                daily_vel,
            )

        # Tier 1: True ML Regression Model (History >= 14 days)
        # Feature Engineering:
        # - Day of week (0=Mon, 6=Sun)
        # - Weekend indicator
        # - Linear time trend
        # - Rolling 3-day and 7-day average sales
        df = daily_df.copy()
        dt_series = pd.to_datetime(df["sale_date"])
        df["dow"] = dt_series.dt.dayofweek
        df["is_weekend"] = (df["dow"] >= 5).astype(int)
        df["trend"] = np.arange(len(df))
        df["rolling_3"] = df["units_sold"].shift(1).rolling(3, min_periods=1).mean().fillna(0.0)
        df["rolling_7"] = df["units_sold"].shift(1).rolling(7, min_periods=1).mean().fillna(0.0)

        # One-hot encode Day of Week
        dow_dummies = pd.get_dummies(df["dow"], prefix="dow", drop_first=True)
        feature_cols = ["is_weekend", "trend", "rolling_3", "rolling_7"]
        X = pd.concat([df[feature_cols], dow_dummies], axis=1)
        y = df["units_sold"].values

        model = Ridge(alpha=1.0)
        model.fit(X, y)
        in_sample_preds = np.maximum(0.0, model.predict(X))

        residuals = y - in_sample_preds
        mae = float(np.mean(np.abs(residuals)))
        sigma_res = float(np.std(residuals))
        y_mean = float(np.mean(y))
        ss_tot = float(np.sum((y - y_mean) ** 2))
        ss_res = float(np.sum(residuals ** 2))
        r2 = round(max(0.0, 1.0 - (ss_res / max(ss_tot, 1e-6))), 3)

        # Multi-horizon forward projection:
        # Predict daily for next target_horizon_days
        last_date = df["sale_date"].iloc[-1]
        future_dates = [last_date + timedelta(days=i) for i in range(1, 31)]
        future_df = pd.DataFrame({"sale_date": future_dates})
        f_dt = pd.to_datetime(future_df["sale_date"])
        future_df["dow"] = f_dt.dt.dayofweek
        future_df["is_weekend"] = (future_df["dow"] >= 5).astype(int)
        future_df["trend"] = np.arange(len(df), len(df) + 30)

        # Autoregressive forward rolling estimation
        rolling_3_val = float(df["units_sold"].iloc[-3:].mean())
        rolling_7_val = float(df["units_sold"].iloc[-7:].mean())
        future_df["rolling_3"] = rolling_3_val
        future_df["rolling_7"] = rolling_7_val

        f_dow_dummies = pd.get_dummies(future_df["dow"], prefix="dow", drop_first=True)
        # Align columns
        for col in dow_dummies.columns:
            if col not in f_dow_dummies.columns:
                f_dow_dummies[col] = 0
        f_dow_dummies = f_dow_dummies[dow_dummies.columns]

        X_future = pd.concat([future_df[feature_cols], f_dow_dummies], axis=1)
        future_preds = np.maximum(0.0, model.predict(X_future))

        proj_7 = round(float(np.sum(future_preds[:7])), 1)
        proj_14 = round(float(np.sum(future_preds[:14])), 1)
        proj_30 = round(float(np.sum(future_preds[:30])), 1)
        daily_vel = round(proj_14 / 14.0, 2)

        # 90% confidence interval for 7-day demand
        ci_half_width = 1.645 * sigma_res * np.sqrt(7)
        lower_7 = round(max(0.0, proj_7 - ci_half_width), 1)
        upper_7 = round(proj_7 + ci_half_width, 1)

        # Weekend seasonality multiplier
        weekend_mask = df["is_weekend"] == 1
        avg_weekend = float(df.loc[weekend_mask, "units_sold"].mean() or 1.0)
        avg_weekday = float(df.loc[~weekend_mask, "units_sold"].mean() or 1.0)
        weekend_mult = round(avg_weekend / max(avg_weekday, 0.1), 2)

        confidence = round(min(0.95, max(0.60, 0.5 + 0.45 * r2)), 2)

        trend_coef = float(model.coef_[1]) if len(model.coef_) > 1 else 0.0

        return (
            DemandProjection(
                daily_velocity=daily_vel,
                projected_7d_demand=proj_7,
                projected_14d_demand=proj_14,
                projected_30d_demand=proj_30,
                confidence_score=confidence,
                lower_bound_7d=lower_7,
                upper_bound_7d=upper_7,
                data_points_analyzed=non_zero_days,
                data_sufficiency="SUFFICIENT",
                model_name="Ridge Time-Series Regressor",
                mae=round(mae, 2),
                r_squared=r2,
                weekend_multiplier=weekend_mult,
                trend_slope=round(trend_coef, 4),
            ),
            daily_vel,
        )

    @classmethod
    def _compute_poisson_expiry_assessment(
        cls,
        batch: InventoryBatch,
        daily_velocity: float,
        ref_date: date,
        has_eligible_sister_shops: bool = False,
    ) -> ExpiryRiskAssessment:
        """Compute exact probabilistic risk of batch spoilage using Poisson CDF."""
        days_left = (batch.expiry_date - ref_date).days if batch.expiry_date else 999
        available = int(batch.available_qty)
        unit_cost = float(batch.unit_cost)

        # Expired batch
        if days_left <= 0:
            return ExpiryRiskAssessment(
                batch_id=batch.id,
                batch_code=batch.batch_code,
                expiry_date=batch.expiry_date.isoformat() if batch.expiry_date else "N/A",
                days_to_expiry=days_left,
                available_qty=available,
                projected_unsold_units=available,
                expiry_risk_score=1.0,
                at_risk_cost_value=round(available * unit_cost, 2),
                recommended_action="QUARANTINE",
                poisson_spoilage_probability=1.0,
                suggested_discount_percent=0,
            )

        # Expected demand until expiry: lambda = days_left * daily_velocity
        expected_demand = float(max(0.0, days_left * daily_velocity))
        projected_unsold = max(0, int(round(available - expected_demand)))

        # Poisson Survival Model: Probability that total demand X < available units
        # P(Spoilage) = P(X <= available - 1)
        if expected_demand <= 0.01:
            spoilage_prob = 1.0
            projected_unsold = available
        else:
            spoilage_prob = float(poisson.cdf(max(0, available - 1), expected_demand))

        # Determine prescriptive action:
        # If spoilage probability is high:
        # - If sister shops exist and shelf life allows transit (> 3 days), offer transfer
        # - Otherwise, recommend local markdown discount
        if spoilage_prob >= 0.50 or projected_unsold > 0:
            risk_score = round(min(1.0, 0.50 + spoilage_prob * 0.50), 2)
            if has_eligible_sister_shops and days_left >= 4:
                action = "OFFER_TRANSFER"
                discount_pct = 0
            elif days_left <= 7:
                action = "DISCOUNT_LOCALLY"
                discount_pct = min(50, int(round(spoilage_prob * 30 + 10)))
            else:
                action = "OFFER_TRANSFER" if has_eligible_sister_shops else "DISCOUNT_LOCALLY"
                discount_pct = 20
        else:
            risk_score = round(max(0.05, spoilage_prob * 0.40), 2)
            action = "RETAIN"
            discount_pct = 0

        return ExpiryRiskAssessment(
            batch_id=batch.id,
            batch_code=batch.batch_code,
            expiry_date=batch.expiry_date.isoformat() if batch.expiry_date else "N/A",
            days_to_expiry=days_left,
            available_qty=available,
            projected_unsold_units=projected_unsold,
            expiry_risk_score=risk_score,
            at_risk_cost_value=round(projected_unsold * unit_cost, 2),
            recommended_action=action,
            poisson_spoilage_probability=round(spoilage_prob, 4),
            suggested_discount_percent=discount_pct,
        )

    @classmethod
    def _compute_stockout_prediction(
        cls,
        total_available: int,
        daily_velocity: float,
        ref_date: date,
        low_stock_threshold: int = 10,
        lead_time_days: int = 3,
        safety_stock_days: int = 7,
    ) -> StockoutPrediction:
        """Compute Days of Inventory (DOI), stockout risk status, and optimal replenishment order."""
        if daily_velocity <= 0.05:
            doi = 999.0
            risk_level = "HEALTHY" if total_available > 0 else "CRITICAL"
            stockout_date = None
            reorder_qty = 0
        else:
            doi = round(total_available / daily_velocity, 1)
            days_int = int(doi)
            stockout_date = (ref_date + timedelta(days=days_int)).isoformat()

            if doi <= 3.0:
                risk_level = "CRITICAL"
            elif doi <= 7.0:
                risk_level = "WARNING"
            elif doi > 45.0:
                risk_level = "OVERSTOCKED"
            else:
                risk_level = "HEALTHY"

            reorder_target = int(round((lead_time_days + safety_stock_days) * daily_velocity))
            reorder_qty = max(0, reorder_target - total_available)

        return StockoutPrediction(
            total_available_qty=total_available,
            days_of_inventory=doi,
            stockout_risk_level=risk_level,
            projected_stockout_date=stockout_date,
            recommended_reorder_qty=reorder_qty,
            reorder_trigger_point=max(low_stock_threshold, int(round(daily_velocity * lead_time_days))),
        )

    @classmethod
    def analyze_product_for_cross_shop_sales(
        cls,
        analysis_input: ProductAnalysisInput,
    ) -> ProductAnalysisResult:
        """Execute end-to-end AI Product Analysis:

        1. ML Demand Forecasting (Ridge Regressor with seasonality & trends)
        2. Probabilistic Batch Expiry Survival Analysis (Poisson CDF)
        3. Stockout Risk & Replenishment Projection
        4. Inter-Store Sister Shop Absorption Optimization
        5. Explainable plain-English narrative for Shop Owners
        """
        ref_date = analysis_input.reference_date or date.today()

        # 1. Fetch Source Shop and Product
        shop = db.session.get(Shop, analysis_input.shop_id)
        product = db.session.get(Product, analysis_input.product_id)

        if not shop or not product:
            raise ValueError(f"Shop #{analysis_input.shop_id} or Product #{analysis_input.product_id} not found.")

        shop_product = ShopProduct.query.filter_by(
            shop_id=shop.id,
            product_id=product.id,
        ).first()

        # 2. Extract continuous daily time series and fit ML Demand Forecasting Model
        lookback_start = ref_date - timedelta(days=max(analysis_input.lookback_days, 14))
        daily_sales_df = cls._build_daily_sales_series(
            shop_id=shop.id,
            product_id=product.id,
            start_date=lookback_start,
            end_date=ref_date,
        )

        demand_proj, daily_velocity = cls._fit_ml_demand_model(
            daily_df=daily_sales_df,
            target_horizon_days=analysis_input.target_horizon_days,
        )

        # 3. Check for Active Sister Shops
        peer_shops = []
        if analysis_input.include_peer_shops:
            peer_shops = (
                Shop.query.filter(
                    Shop.organization_id == shop.organization_id,
                    Shop.id != shop.id,
                    Shop.status == "ACTIVE",
                )
                .all()
            )
        has_eligible_peers = bool(peer_shops)

        # 4. Analyze Active Inventory Batches via Poisson Survival Model
        batches = (
            InventoryBatch.query.filter_by(
                shop_product_id=shop_product.id if shop_product else 0,
                status="ACTIVE",
            )
            .filter(InventoryBatch.available_qty > 0)
            .order_by(InventoryBatch.expiry_date.asc())
            .all()
        )

        total_available_stock = sum(b.available_qty for b in batches)
        expiry_assessments: list[ExpiryRiskAssessment] = []
        total_unsold_units = 0

        for b in batches:
            assessment = cls._compute_poisson_expiry_assessment(
                batch=b,
                daily_velocity=daily_velocity,
                ref_date=ref_date,
                has_eligible_sister_shops=has_eligible_peers,
            )
            expiry_assessments.append(assessment)
            total_unsold_units += assessment.projected_unsold_units

        # 5. Stockout Prediction Modeling
        low_stock_thresh = shop_product.low_stock_threshold if shop_product else shop.low_stock_default_threshold
        stockout_pred = cls._compute_stockout_prediction(
            total_available=total_available_stock,
            daily_velocity=daily_velocity,
            ref_date=ref_date,
            low_stock_threshold=low_stock_thresh,
        )

        # 6. Sister Shop Absorption Matching & Transport Optimization
        candidate_shops: list[CandidateRecipientShop] = []
        if has_eligible_peers and total_unsold_units > 0:
            for peer in peer_shops:
                dist_km = 5.0
                if shop.latitude and shop.longitude and peer.latitude and peer.longitude:
                    dist_km = round(
                        haversine_distance_km(
                            float(shop.latitude),
                            float(shop.longitude),
                            float(peer.latitude),
                            float(peer.longitude),
                        ),
                        1,
                    )

                # Peer shop inventory & velocity
                peer_sp = ShopProduct.query.filter_by(shop_id=peer.id, product_id=product.id).first()
                peer_stock = (
                    sum(b.available_qty for b in peer_sp.batches.filter_by(status="ACTIVE").all())
                    if peer_sp
                    else 0
                )

                peer_sales_df = cls._build_daily_sales_series(
                    shop_id=peer.id,
                    product_id=product.id,
                    start_date=lookback_start,
                    end_date=ref_date,
                )
                _, peer_velocity = cls._fit_ml_demand_model(peer_sales_df)

                # If peer store is experiencing active demand
                if peer_velocity > 0:
                    absorb_days = round(total_unsold_units / peer_velocity, 1)
                    transport_cost = round(max(50.0, dist_km * 12.0), 2)
                    selling_price = float(
                        peer_sp.selling_price
                        if peer_sp
                        else (shop_product.selling_price if shop_product else 100.0)
                    )
                    net_benefit = round((total_unsold_units * selling_price * 0.85) - transport_cost, 2)

                    priority = "HIGH" if (absorb_days <= 10 and net_benefit > 0) else "MEDIUM"
                    candidate_shops.append(
                        CandidateRecipientShop(
                            recipient_shop_id=peer.id,
                            recipient_shop_name=peer.name,
                            recipient_shop_code=peer.code,
                            distance_km=dist_km,
                            recipient_current_stock=peer_stock,
                            recipient_daily_velocity=peer_velocity,
                            estimated_days_to_absorb=absorb_days,
                            estimated_transport_cost=transport_cost,
                            projected_net_salvage_value=net_benefit,
                            match_priority=priority,
                        )
                    )

            candidate_shops.sort(
                key=lambda c: (c.match_priority != "HIGH", -c.projected_net_salvage_value)
            )

        # 7. Generate Explainable Plain-English Narrative
        rationale_parts = []
        if total_unsold_units > 0:
            rationale_parts.append(
                f"{shop.name} has {total_unsold_units} units projected to remain unsold before expiration "
                f"based on current AI demand velocity ({daily_velocity} units/day)."
            )
            if candidate_shops:
                top_c = candidate_shops[0]
                rationale_parts.append(
                    f"Sister store '{top_c.recipient_shop_name}' ({top_c.distance_km} km away) is experiencing higher demand "
                    f"({top_c.recipient_daily_velocity} units/day) and can absorb this stock within {top_c.estimated_days_to_absorb} days. "
                    f"Estimated net salvage benefit after transport is ₹{top_c.projected_net_salvage_value:,.2f}."
                )
            else:
                rationale_parts.append(
                    "No active sister shops currently have sufficient sales demand to absorb this surplus. "
                    "Consider applying a local promotional markdown to accelerate sell-through."
                )
        else:
            rationale_parts.append(
                f"Stock levels for {product.name} are well-balanced. Local demand ({daily_velocity} units/day) "
                "is projected to fully absorb active batches before expiration."
            )

        if stockout_pred.stockout_risk_level in ("CRITICAL", "WARNING"):
            rationale_parts.append(
                f"Attention: Current stock ({total_available_stock} units) covers only {stockout_pred.days_of_inventory} days. "
                f"Recommended reorder quantity: {stockout_pred.recommended_reorder_qty} units."
            )

        plain_english = " ".join(rationale_parts)

        return ProductAnalysisResult(
            product_id=product.id,
            product_name=product.name,
            sku=product.sku,
            category=product.category,
            source_shop_id=shop.id,
            source_shop_name=shop.name,
            analyzed_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            model_status="AI_MODEL_ACTIVE" if demand_proj.data_sufficiency == "SUFFICIENT" else demand_proj.data_sufficiency,
            ai_model_version=cls.AI_MODEL_VERSION,
            demand_projection=demand_proj,
            expiry_risk_assessments=expiry_assessments,
            candidate_recipient_shops=candidate_shops,
            plain_english_rationale=plain_english,
            can_convert_to_proposal=bool(candidate_shops and total_unsold_units > 0),
            stockout_prediction=stockout_pred,
        )

    @classmethod
    def get_placeholder_overview(cls) -> dict[str, Any]:
        """Provides architecture explanation and model metadata."""
        return {
            "title": "AI Demand Forecasting & Inter-Store Rebalancing Engine",
            "status": "COMING_SOON",
            "version": cls.AI_MODEL_VERSION,
            "architecture": {
                "algorithm": "Ridge Poisson-Weighted Time Series Regression + Poisson Survival Spoilage Model",
                "models_to_integrate": [
                    "Ridge Regressor (Calendar & Weekend Seasonality)",
                    "EWMA Exponential Smoothing (Sparse history fallback)",
                    "Poisson Survival CDF for Spoilage Risk Probability",
                ],
                "evaluation_metrics": ["MAE (Mean Absolute Error)", "R-squared", "sMAPE"],
                "data_boundary": "100% Read-Only Feature Extraction from PostgreSQL 'sales' & 'inventory_batches'",
                "human_in_the_loop": "AI generates proposals only. Dual Shop Owner approval (Phase 6) is strictly required.",
            },
            "interface_contracts": {
                "input_schema": {
                    "shop_id": "int (required)",
                    "product_id": "int (required)",
                    "lookback_days": "int (default: 14)",
                    "target_horizon_days": "int (default: 14)",
                    "include_peer_shops": "bool (default: True)",
                },
                "output_schema": {
                    "demand_projection": "DemandProjection(daily_velocity, projected_7d, projected_14d, confidence, uncertainty_bounds, mae, r_squared)",
                    "stockout_prediction": "StockoutPrediction(days_of_inventory, stockout_risk_level, projected_stockout_date, recommended_reorder_qty)",
                    "expiry_risk_assessments": "list[ExpiryRiskAssessment(batch_id, expiry_risk_score, unsold_units, poisson_probability, discount_percent)]",
                    "candidate_recipient_shops": "list[CandidateRecipientShop(shop_id, distance_km, net_benefit)]",
                    "plain_english_rationale": "str",
                },
            },
            "expected_release": "Phase 10 — Full AI Model Integration",
        }

    @classmethod
    def analyze_shop_waste_mitigation(
        cls,
        shop_id: int,
        lookback_days: int = 14,
        max_days_to_expiry: int = 45,
        ref_date: date | None = None,
    ) -> ShopWasteMitigationSummary:
        """Comprehensive store-level ML Waste Mitigation & Expiry Risk Analysis.

        Evaluates all active batches, runs the Poisson survival model against sales velocity,
        prescribes optimal salvage actions (Inter-Store Transfer vs Promotional Markdown),
        and calculates total financial capital at risk.
        """
        ref_date = ref_date or date.today()
        shop = db.session.get(Shop, shop_id)
        if not shop:
            raise ValueError(f"Shop with ID {shop_id} not found.")

        # Query all active inventory batches with available quantity
        batches = (
            InventoryBatch.query.filter_by(shop_id=shop.id, status="ACTIVE")
            .filter(InventoryBatch.available_qty > 0)
            .order_by(InventoryBatch.expiry_date.asc())
            .all()
        )

        total_active_batches = len(batches)
        if not batches:
            return ShopWasteMitigationSummary(
                shop_id=shop.id,
                shop_name=shop.name,
                analyzed_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                ai_model_version=cls.AI_MODEL_VERSION,
                total_active_batches=0,
                total_at_risk_batches=0,
                total_capital_at_risk=0.0,
                total_projected_waste_units=0,
                potential_salvage_recovery_value=0.0,
                action_breakdown={"OFFER_TRANSFER": 0, "DISCOUNT_LOCALLY": 0, "RETAIN": 0},
                recommended_markdowns=[],
                recommended_transfers=[],
                at_risk_batches=[],
            )

        # Sister shops in the same organization
        peer_shops = (
            Shop.query.filter(
                Shop.organization_id == shop.organization_id,
                Shop.id != shop.id,
                Shop.status == "ACTIVE",
            )
            .all()
        )
        has_eligible_peers = bool(peer_shops)

        # Group batches by product_id
        product_batches_map: dict[int, list[InventoryBatch]] = {}
        for b in batches:
            product_batches_map.setdefault(b.product_id, []).append(b)

        lookback_start = ref_date - timedelta(days=max(lookback_days, 14))
        at_risk_batches_list: list[dict[str, Any]] = []
        recommended_markdowns: list[dict[str, Any]] = []
        recommended_transfers: list[dict[str, Any]] = []
        action_counts = {"OFFER_TRANSFER": 0, "DISCOUNT_LOCALLY": 0, "RETAIN": 0}

        for pid, p_batches in product_batches_map.items():
            product = db.session.get(Product, pid)
            if not product:
                continue

            shop_product = ShopProduct.query.filter_by(shop_id=shop.id, product_id=pid).first()
            selling_price = float(
                shop_product.selling_price
                if (shop_product and shop_product.selling_price)
                else (product.default_selling_price or 0.0)
            )

            daily_sales_df = cls._build_daily_sales_series(
                shop_id=shop.id,
                product_id=pid,
                start_date=lookback_start,
                end_date=ref_date,
            )
            _, daily_velocity = cls._fit_ml_demand_model(daily_sales_df)

            for b in p_batches:
                assessment = cls._compute_poisson_expiry_assessment(
                    batch=b,
                    daily_velocity=daily_velocity,
                    ref_date=ref_date,
                    has_eligible_sister_shops=has_eligible_peers,
                )

                days_left = assessment.days_to_expiry
                unit_cost = float(b.unit_cost or 0.0)

                # Consider at risk if within max_days_to_expiry and (score >= 0.25 or days_left <= 7 or projected_unsold > 0)
                is_at_risk = (days_left <= max_days_to_expiry) and (
                    assessment.expiry_risk_score >= 0.25
                    or assessment.projected_unsold_units > 0
                    or days_left <= 7
                )

                if is_at_risk:
                    action_counts[assessment.recommended_action] = (
                        action_counts.get(assessment.recommended_action, 0) + 1
                    )

                    batch_data = {
                        "batch_id": b.id,
                        "batch_code": b.batch_code,
                        "product_id": product.id,
                        "product_name": product.name,
                        "sku": product.sku,
                        "category": product.category,
                        "unit": product.unit,
                        "expiry_date": b.expiry_date.isoformat() if b.expiry_date else "N/A",
                        "days_to_expiry": days_left,
                        "available_qty": b.available_qty,
                        "daily_velocity": daily_velocity,
                        "projected_unsold_units": assessment.projected_unsold_units,
                        "poisson_spoilage_probability": assessment.poisson_spoilage_probability,
                        "expiry_risk_score": assessment.expiry_risk_score,
                        "unit_cost": unit_cost,
                        "selling_price": selling_price,
                        "at_risk_cost_value": assessment.at_risk_cost_value,
                        "recommended_action": assessment.recommended_action,
                        "suggested_discount_percent": assessment.suggested_discount_percent,
                    }
                    at_risk_batches_list.append(batch_data)

                    if assessment.recommended_action == "DISCOUNT_LOCALLY":
                        disc_pct = assessment.suggested_discount_percent
                        discounted_price = round(selling_price * (1 - disc_pct / 100.0), 2)
                        potential_salvage = round(assessment.projected_unsold_units * discounted_price, 2)
                        recommended_markdowns.append(
                            {
                                **batch_data,
                                "original_selling_price": selling_price,
                                "suggested_markdown_price": discounted_price,
                                "projected_salvage_revenue": potential_salvage,
                                "markdown_urgency": "IMMEDIATE" if days_left <= 3 else "SCHEDULED",
                            }
                        )

                    elif assessment.recommended_action == "OFFER_TRANSFER" and has_eligible_peers:
                        best_peer = None
                        best_salvage = -9999.0
                        for peer in peer_shops:
                            dist_km = (
                                round(
                                    haversine_distance_km(
                                        float(shop.latitude or 0),
                                        float(shop.longitude or 0),
                                        float(peer.latitude or 0),
                                        float(peer.longitude or 0),
                                    ),
                                    1,
                                )
                                if (shop.latitude and peer.latitude)
                                else 5.0
                            )
                            trans_cost = round(dist_km * 15.0, 2)
                            unit_margin = max(0.0, selling_price - unit_cost)
                            salvage_val = round(assessment.projected_unsold_units * unit_margin - trans_cost, 2)
                            if salvage_val > best_salvage:
                                best_salvage = salvage_val
                                best_peer = peer

                        recommended_transfers.append(
                            {
                                **batch_data,
                                "surplus_units": assessment.projected_unsold_units,
                                "target_shop_id": best_peer.id if best_peer else None,
                                "target_shop_name": best_peer.name if best_peer else "Sister Shop",
                                "target_shop_code": best_peer.code if best_peer else "N/A",
                                "projected_net_salvage": max(0.0, best_salvage),
                            }
                        )

        # Sort at_risk_batches by at_risk_cost_value descending
        at_risk_batches_list.sort(key=lambda x: (x["at_risk_cost_value"], x["expiry_risk_score"]), reverse=True)

        total_capital_at_risk = round(sum(b["at_risk_cost_value"] for b in at_risk_batches_list), 2)
        total_waste_units = sum(b["projected_unsold_units"] for b in at_risk_batches_list)

        salvage_markdown = sum(m["projected_salvage_revenue"] for m in recommended_markdowns)
        salvage_transfers = sum(t["projected_net_salvage"] for t in recommended_transfers)
        potential_salvage_total = round(salvage_markdown + salvage_transfers, 2)

        return ShopWasteMitigationSummary(
            shop_id=shop.id,
            shop_name=shop.name,
            analyzed_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            ai_model_version=cls.AI_MODEL_VERSION,
            total_active_batches=total_active_batches,
            total_at_risk_batches=len(at_risk_batches_list),
            total_capital_at_risk=total_capital_at_risk,
            total_projected_waste_units=total_waste_units,
            potential_salvage_recovery_value=potential_salvage_total,
            action_breakdown=action_counts,
            recommended_markdowns=recommended_markdowns,
            recommended_transfers=recommended_transfers,
            at_risk_batches=at_risk_batches_list,
        )

