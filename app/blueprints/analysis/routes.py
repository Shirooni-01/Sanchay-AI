"""Routes and REST API endpoints for Rule-Based Stock Analysis & Risk Insights (Phase 5).

HTML Views:
- GET  /analysis                                                : Redirect to accessible shop analysis overview
- GET  /shops/<shop_id>/analysis                                : Inventory Risk Overview
- GET  /shops/<shop_id>/analysis/expiry-risk                    : Near-Expiry & Batch Expiry-Risk Analysis
- GET  /shops/<shop_id>/analysis/surplus                        : Potential Surplus Analysis
- GET  /shops/<shop_id>/analysis/low-stock                      : Low-Stock & Replenishment Analysis
- GET  /shops/<shop_id>/analysis/recommendations                : Rule-Based Recommendations & Explanations
- GET  /shops/<shop_id>/analysis/products/<product_id>          : Individual Product Analysis
- GET  /shops/<shop_id>/analysis/batches/<batch_id>             : Individual Batch Analysis

REST APIs:
- GET  /api/v1/shops/<shop_id>/surplus-candidates               (API-025)
- GET  /api/v1/shops/<shop_id>/analysis/overview                (API-025a)
- GET  /api/v1/shops/<shop_id>/analysis/expiry-risk             (API-025b)
- GET  /api/v1/shops/<shop_id>/analysis/surplus                 (API-025c)
- GET  /api/v1/shops/<shop_id>/analysis/low-stock               (API-025d)
- GET  /api/v1/shops/<shop_id>/analysis/recommendations         (API-025e)
- GET  /api/v1/shops/<shop_id>/analysis/products/<product_id>   (API-025f)
- GET  /api/v1/shops/<shop_id>/analysis/batches/<batch_id>      (API-025g)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import flash, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.analysis import analysis_bp
from app.services.surplus_service import StockAnalysisService
from app.utils.decorators import require_shop_access, verify_authenticated_user
from app.utils.errors import DomainError
from app.utils.validators import (
    VALID_EXPIRY_RISK_LEVELS,
    VALID_RECOMMENDATION_TYPES,
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_analysis_query_kwargs() -> dict[str, Any]:
    """Extract optional deterministic analysis configuration parameters from request.args."""
    raw_refresh = str(request.args.get("refresh", "")).strip().lower()
    force_refresh = raw_refresh in {"1", "true", "yes"}
    return {
        "lookback_days": request.args.get("lookback_days")
        or request.args.get("window_days"),
        "safety_stock_days": request.args.get("safety_stock_days"),
        "safety_stock_qty": request.args.get("safety_stock_qty"),
        "surplus_coverage_days": request.args.get("surplus_coverage_days"),
        "ref_date": request.args.get("ref_date"),
        "force_refresh": force_refresh,
    }


def _build_query_string_dict(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build clean query parameters dictionary for preserving filter controls across tabs."""
    params: dict[str, Any] = {}
    if kwargs.get("lookback_days"):
        params["lookback_days"] = kwargs["lookback_days"]
    if kwargs.get("safety_stock_days"):
        params["safety_stock_days"] = kwargs["safety_stock_days"]
    if kwargs.get("safety_stock_qty") is not None and kwargs.get("safety_stock_qty") != "":
        params["safety_stock_qty"] = kwargs["safety_stock_qty"]
    if kwargs.get("surplus_coverage_days"):
        params["surplus_coverage_days"] = kwargs["surplus_coverage_days"]
    if kwargs.get("ref_date"):
        params["ref_date"] = kwargs["ref_date"]
    return params


# ==============================================================================
# HTML VIEWS
# ==============================================================================


@analysis_bp.route("/analysis", methods=["GET"])
@login_required
def analysis_entry_redirect():
    """Redirect user to their primary/first accessible shop's Stock Analysis overview."""
    user = verify_authenticated_user()
    if user.organization_id is None:
        flash("Please configure or join an organization first.", "warning")
        return redirect(url_for("admin.organization_page"))

    shops = user.get_accessible_shops(include_inactive=True)
    if not shops:
        flash("Create or get assigned to a shop before viewing stock analysis.", "info")
        return redirect(url_for("admin.shops_page"))

    primary = user.get_primary_assignment()
    target_shop_id = primary.shop_id if primary else shops[0].id
    return redirect(url_for("analysis.overview_page", shop_id=target_shop_id))


@analysis_bp.route("/shops/<int:shop_id>/analysis", methods=["GET"])
@require_shop_access(allow_inactive=True)
def overview_page(shop_id: int):
    """Render the shop Inventory Risk Overview dashboard (Phase 5)."""
    shop = g.current_shop
    kwargs = _extract_analysis_query_kwargs()
    try:
        analysis = StockAnalysisService.analyze_shop(
            actor=current_user,
            shop_id=shop.id,
            **kwargs,
        )
    except DomainError as exc:
        flash(exc.message, "danger")
        analysis = StockAnalysisService.analyze_shop(
            actor=current_user,
            shop_id=shop.id,
        )
        kwargs = {}

    accessible_shops = current_user.get_accessible_shops(include_inactive=True)
    return render_template(
        "analysis/overview.html",
        shop=shop,
        accessible_shops=accessible_shops,
        analysis=analysis,
        query_params=_build_query_string_dict(kwargs),
        active_tab="overview",
    )


@analysis_bp.route("/shops/<int:shop_id>/analysis/expiry-risk", methods=["GET"])
@require_shop_access(allow_inactive=True)
def expiry_risk_page(shop_id: int):
    """Render the shop Near-Expiry & Batch Expiry-Risk Analysis page."""
    shop = g.current_shop
    kwargs = _extract_analysis_query_kwargs()
    risk_filter = (request.args.get("risk_level") or "").strip().upper()
    search_q = (request.args.get("q") or "").strip().lower()
    only_near_expiry = str(request.args.get("only_near_expiry", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }

    try:
        analysis = StockAnalysisService.analyze_shop(
            actor=current_user,
            shop_id=shop.id,
            **kwargs,
        )
    except DomainError as exc:
        flash(exc.message, "danger")
        analysis = StockAnalysisService.analyze_shop(
            actor=current_user,
            shop_id=shop.id,
        )
        kwargs = {}

    batches = list(analysis["batches"])
    if risk_filter and risk_filter in VALID_EXPIRY_RISK_LEVELS:
        batches = [b for b in batches if b["expiry_risk_level"] == risk_filter]
    if only_near_expiry:
        batches = [b for b in batches if b["is_near_expiry"] or b["is_expired"]]
    if search_q:
        batches = [
            b
            for b in batches
            if search_q in (b["sku"] or "").lower()
            or search_q in (b["product_name"] or "").lower()
            or search_q in (b["batch_code"] or "").lower()
        ]

    accessible_shops = current_user.get_accessible_shops(include_inactive=True)
    return render_template(
        "analysis/expiry_risk.html",
        shop=shop,
        accessible_shops=accessible_shops,
        analysis=analysis,
        batches=batches,
        risk_filter=risk_filter,
        search_q=request.args.get("q", ""),
        only_near_expiry=only_near_expiry,
        valid_risk_levels=sorted(VALID_EXPIRY_RISK_LEVELS),
        query_params=_build_query_string_dict(kwargs),
        active_tab="expiry_risk",
    )


@analysis_bp.route("/shops/<int:shop_id>/analysis/surplus", methods=["GET"])
@require_shop_access(allow_inactive=True)
def surplus_page(shop_id: int):
    """Render the shop Potential Surplus Analysis page."""
    shop = g.current_shop
    kwargs = _extract_analysis_query_kwargs()
    search_q = (request.args.get("q") or "").strip().lower()
    show_all_batches = str(request.args.get("show_all", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }

    try:
        analysis = StockAnalysisService.analyze_shop(
            actor=current_user,
            shop_id=shop.id,
            **kwargs,
        )
    except DomainError as exc:
        flash(exc.message, "danger")
        analysis = StockAnalysisService.analyze_shop(
            actor=current_user,
            shop_id=shop.id,
        )
        kwargs = {}

    batches = (
        list(analysis["batches"])
        if show_all_batches
        else list(analysis["surplus_batches"])
    )
    if search_q:
        batches = [
            b
            for b in batches
            if search_q in (b["sku"] or "").lower()
            or search_q in (b["product_name"] or "").lower()
            or search_q in (b["batch_code"] or "").lower()
        ]

    accessible_shops = current_user.get_accessible_shops(include_inactive=True)
    return render_template(
        "analysis/surplus.html",
        shop=shop,
        accessible_shops=accessible_shops,
        analysis=analysis,
        batches=batches,
        search_q=request.args.get("q", ""),
        show_all_batches=show_all_batches,
        query_params=_build_query_string_dict(kwargs),
        active_tab="surplus",
    )


@analysis_bp.route("/shops/<int:shop_id>/analysis/low-stock", methods=["GET"])
@require_shop_access(allow_inactive=True)
def low_stock_page(shop_id: int):
    """Render the shop Low-Stock & Replenishment Analysis page."""
    shop = g.current_shop
    kwargs = _extract_analysis_query_kwargs()
    status_filter = (request.args.get("status") or "").strip().upper()
    search_q = (request.args.get("q") or "").strip().lower()
    show_all = str(request.args.get("show_all", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }

    try:
        analysis = StockAnalysisService.analyze_shop(
            actor=current_user,
            shop_id=shop.id,
            **kwargs,
        )
    except DomainError as exc:
        flash(exc.message, "danger")
        analysis = StockAnalysisService.analyze_shop(
            actor=current_user,
            shop_id=shop.id,
        )
        kwargs = {}

    items = (
        list(analysis["all_low_stock_evaluations"])
        if show_all
        else list(analysis["low_stock_items"])
    )
    if status_filter:
        items = [i for i in items if i["replenishment_status"] == status_filter]
    if search_q:
        items = [
            i
            for i in items
            if search_q in (i["sku"] or "").lower()
            or search_q in (i["product_name"] or "").lower()
        ]

    accessible_shops = current_user.get_accessible_shops(include_inactive=True)
    return render_template(
        "analysis/low_stock.html",
        shop=shop,
        accessible_shops=accessible_shops,
        analysis=analysis,
        items=items,
        status_filter=status_filter,
        search_q=request.args.get("q", ""),
        show_all=show_all,
        query_params=_build_query_string_dict(kwargs),
        active_tab="low_stock",
    )


@analysis_bp.route("/shops/<int:shop_id>/analysis/recommendations", methods=["GET"])
@require_shop_access(allow_inactive=True)
def recommendations_page(shop_id: int):
    """Render the shop Rule-Based Recommendations & Explanations page."""
    shop = g.current_shop
    kwargs = _extract_analysis_query_kwargs()
    type_filter = (request.args.get("type") or "").strip().upper()
    severity_filter = (request.args.get("severity") or "").strip().upper()

    try:
        analysis = StockAnalysisService.analyze_shop(
            actor=current_user,
            shop_id=shop.id,
            **kwargs,
        )
    except DomainError as exc:
        flash(exc.message, "danger")
        analysis = StockAnalysisService.analyze_shop(
            actor=current_user,
            shop_id=shop.id,
        )
        kwargs = {}

    recs = list(analysis["recommendations"])
    if type_filter and type_filter in VALID_RECOMMENDATION_TYPES:
        recs = [r for r in recs if r["recommendation_type"] == type_filter]
    if severity_filter in {"CRITICAL", "WARNING", "INFO"}:
        recs = [r for r in recs if r["severity"] == severity_filter]

    accessible_shops = current_user.get_accessible_shops(include_inactive=True)
    return render_template(
        "analysis/recommendations.html",
        shop=shop,
        accessible_shops=accessible_shops,
        analysis=analysis,
        recommendations=recs,
        type_filter=type_filter,
        severity_filter=severity_filter,
        valid_rec_types=sorted(VALID_RECOMMENDATION_TYPES),
        query_params=_build_query_string_dict(kwargs),
        active_tab="recommendations",
    )


@analysis_bp.route(
    "/shops/<int:shop_id>/analysis/products/<int:product_id>",
    methods=["GET"],
)
@require_shop_access(allow_inactive=True)
def product_analysis_page(shop_id: int, product_id: int):
    """Render detailed deterministic analysis for a single product in shop_id."""
    shop = g.current_shop
    kwargs = _extract_analysis_query_kwargs()
    result = StockAnalysisService.get_product_analysis(
        actor=current_user,
        shop_id=shop.id,
        product_id=product_id,
        **kwargs,
    )
    accessible_shops = current_user.get_accessible_shops(include_inactive=True)
    return render_template(
        "analysis/product_detail.html",
        shop=shop,
        accessible_shops=accessible_shops,
        result=result,
        product=result["product_analysis"],
        query_params=_build_query_string_dict(kwargs),
        active_tab="overview",
    )


@analysis_bp.route(
    "/shops/<int:shop_id>/analysis/batches/<int:batch_id>",
    methods=["GET"],
)
@require_shop_access(allow_inactive=True)
def batch_analysis_page(shop_id: int, batch_id: int):
    """Render detailed deterministic FEFO, expiry-risk, and surplus analysis for a single batch."""
    shop = g.current_shop
    kwargs = _extract_analysis_query_kwargs()
    result = StockAnalysisService.get_batch_analysis(
        actor=current_user,
        shop_id=shop.id,
        batch_id=batch_id,
        **kwargs,
    )
    accessible_shops = current_user.get_accessible_shops(include_inactive=True)
    return render_template(
        "analysis/batch_detail.html",
        shop=shop,
        accessible_shops=accessible_shops,
        result=result,
        batch=result["batch_analysis"],
        query_params=_build_query_string_dict(kwargs),
        active_tab="expiry_risk",
    )


# ==============================================================================
# REST API ENDPOINTS (Phase 5)
# ==============================================================================


@analysis_bp.route("/api/v1/shops/<int:shop_id>/analysis/overview", methods=["GET"])
@require_shop_access(allow_inactive=True)
def api_shop_analysis_overview(shop_id: int):
    """API-025a: Return full deterministic shop stock analysis overview and KPIs."""
    kwargs = _extract_analysis_query_kwargs()
    result = StockAnalysisService.analyze_shop(
        actor=current_user,
        shop_id=shop_id,
        **kwargs,
    )
    return (
        jsonify(
            {
                "status": "success",
                "message": "Deterministic shop stock analysis computed.",
                "data": result,
                "meta": {
                    "timestamp": _utc_iso(),
                    "rule_version": result["rule_version"],
                    "analyzed_at": result["analyzed_at"],
                },
            }
        ),
        200,
    )


@analysis_bp.route("/api/v1/shops/<int:shop_id>/analysis/expiry-risk", methods=["GET"])
@require_shop_access(allow_inactive=True)
def api_shop_expiry_risk(shop_id: int):
    """API-025b: Return batch-level FEFO expiry-risk analysis for a shop."""
    kwargs = _extract_analysis_query_kwargs()
    risk_filter = (request.args.get("risk_level") or "").strip().upper()
    only_near_expiry = str(request.args.get("only_near_expiry", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }

    result = StockAnalysisService.analyze_shop(
        actor=current_user,
        shop_id=shop_id,
        **kwargs,
    )
    batches = list(result["batches"])
    if risk_filter and risk_filter in VALID_EXPIRY_RISK_LEVELS:
        batches = [b for b in batches if b["expiry_risk_level"] == risk_filter]
    if only_near_expiry:
        batches = [b for b in batches if b["is_near_expiry"] or b["is_expired"]]

    return (
        jsonify(
            {
                "status": "success",
                "message": "Batch expiry-risk analysis computed.",
                "data": {
                    "shop_id": result["shop_id"],
                    "business_date": result["business_date"],
                    "rule_version": result["rule_version"],
                    "analyzed_at": result["analyzed_at"],
                    "freshness": result["freshness"],
                    "configuration": result["configuration"],
                    "kpis": {
                        "total_batches_analyzed": result["kpis"]["total_batches_analyzed"],
                        "expired_batches_count": result["kpis"]["expired_batches_count"],
                        "critical_expiry_batches_count": result["kpis"][
                            "critical_expiry_batches_count"
                        ],
                        "high_expiry_batches_count": result["kpis"][
                            "high_expiry_batches_count"
                        ],
                        "medium_expiry_batches_count": result["kpis"][
                            "medium_expiry_batches_count"
                        ],
                        "near_expiry_batches_count": result["kpis"][
                            "near_expiry_batches_count"
                        ],
                        "total_estimated_unsold_units": result["kpis"][
                            "total_estimated_unsold_units"
                        ],
                        "total_at_risk_cost_value": result["kpis"][
                            "total_at_risk_cost_value"
                        ],
                    },
                    "batches": batches,
                },
                "meta": {
                    "timestamp": _utc_iso(),
                    "count": len(batches),
                    "rule_version": result["rule_version"],
                },
            }
        ),
        200,
    )


@analysis_bp.route("/api/v1/shops/<int:shop_id>/analysis/surplus", methods=["GET"])
@analysis_bp.route("/api/v1/shops/<int:shop_id>/surplus-candidates", methods=["GET"])
@require_shop_access(allow_inactive=True)
def api_shop_surplus_candidates(shop_id: int):
    """API-025 / API-025c: Return deterministic surplus stock candidates for a shop."""
    kwargs = _extract_analysis_query_kwargs()
    result = StockAnalysisService.analyze_shop(
        actor=current_user,
        shop_id=shop_id,
        **kwargs,
    )
    candidates = [
        {
            **b,
            "product": {
                "id": b["product_id"],
                "sku": b["sku"],
                "name": b["product_name"],
                "unit": b["unit"],
            },
            "batch": {
                "id": b["batch_id"],
                "batch_code": b["batch_code"],
                "available_qty": b["available_qty"],
                "eligible_sellable_qty": b["eligible_sellable_qty"],
                "expiry_date": b["expiry_date"],
                "days_to_expiry": b["days_to_expiry"],
            },
            "analysis": {
                "daily_sales_velocity": b["daily_sales_velocity"],
                "window_days": result["configuration"]["lookback_days"],
                "expected_demand_until_expiry": b[
                    "estimated_batch_demand_before_expiry"
                ],
                "safety_stock_qty": b["safety_stock_qty"],
                "reorder_threshold": b["reorder_threshold"],
                "retained_qty": b["retained_qty"],
                "surplus_qty": b["potential_surplus_qty"],
                "potential_surplus_qty": b["potential_surplus_qty"],
                "surplus_reason": b["surplus_reason"],
                "expiry_risk_level": b["expiry_risk_level"],
                "rule_version": b["rule_version"],
                "explanation": b["explanation"],
            },
        }
        for b in result["surplus_batches"]
    ]
    return (
        jsonify(
            {
                "status": "success",
                "message": "Deterministic surplus stock candidates computed.",
                "data": {
                    "shop_id": result["shop_id"],
                    "business_date": result["business_date"],
                    "rule_version": result["rule_version"],
                    "analyzed_at": result["analyzed_at"],
                    "freshness": result["freshness"],
                    "configuration": result["configuration"],
                    "kpis": {
                        "surplus_batches_count": result["kpis"]["surplus_batches_count"],
                        "surplus_products_count": result["kpis"]["surplus_products_count"],
                        "total_potential_surplus_units": result["kpis"][
                            "total_potential_surplus_units"
                        ],
                        "total_surplus_cost_value": result["kpis"][
                            "total_surplus_cost_value"
                        ],
                    },
                    "candidates": candidates,
                    "surplus_batches": result["surplus_batches"],
                },
                "meta": {
                    "timestamp": _utc_iso(),
                    "count": len(candidates),
                    "rule_version": result["rule_version"],
                },
            }
        ),
        200,
    )


@analysis_bp.route("/api/v1/shops/<int:shop_id>/analysis/low-stock", methods=["GET"])
@require_shop_access(allow_inactive=True)
def api_shop_low_stock(shop_id: int):
    """API-025d: Return low-stock and replenishment analysis for a shop."""
    kwargs = _extract_analysis_query_kwargs()
    show_all = str(request.args.get("show_all", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }
    result = StockAnalysisService.analyze_shop(
        actor=current_user,
        shop_id=shop_id,
        **kwargs,
    )
    items = (
        result["all_low_stock_evaluations"]
        if show_all
        else result["low_stock_items"]
    )
    return (
        jsonify(
            {
                "status": "success",
                "message": "Low-stock and replenishment analysis computed.",
                "data": {
                    "shop_id": result["shop_id"],
                    "business_date": result["business_date"],
                    "rule_version": result["rule_version"],
                    "analyzed_at": result["analyzed_at"],
                    "freshness": result["freshness"],
                    "configuration": result["configuration"],
                    "kpis": {
                        "low_stock_products_count": result["kpis"][
                            "low_stock_products_count"
                        ],
                        "out_of_stock_products_count": result["kpis"][
                            "out_of_stock_products_count"
                        ],
                    },
                    "items": items,
                },
                "meta": {
                    "timestamp": _utc_iso(),
                    "count": len(items),
                    "rule_version": result["rule_version"],
                },
            }
        ),
        200,
    )


@analysis_bp.route(
    "/api/v1/shops/<int:shop_id>/analysis/recommendations",
    methods=["GET"],
)
@require_shop_access(allow_inactive=True)
def api_shop_recommendations(shop_id: int):
    """API-025e: Return deduplicated rule-based recommendations and explanations."""
    kwargs = _extract_analysis_query_kwargs()
    type_filter = (request.args.get("type") or "").strip().upper()
    severity_filter = (request.args.get("severity") or "").strip().upper()

    result = StockAnalysisService.analyze_shop(
        actor=current_user,
        shop_id=shop_id,
        **kwargs,
    )
    recs = list(result["recommendations"])
    if type_filter and type_filter in VALID_RECOMMENDATION_TYPES:
        recs = [r for r in recs if r["recommendation_type"] == type_filter]
    if severity_filter in {"CRITICAL", "WARNING", "INFO"}:
        recs = [r for r in recs if r["severity"] == severity_filter]

    return (
        jsonify(
            {
                "status": "success",
                "message": "Deterministic recommendations retrieved.",
                "data": {
                    "shop_id": result["shop_id"],
                    "business_date": result["business_date"],
                    "rule_version": result["rule_version"],
                    "analyzed_at": result["analyzed_at"],
                    "freshness": result["freshness"],
                    "configuration": result["configuration"],
                    "recommendations": recs,
                },
                "meta": {
                    "timestamp": _utc_iso(),
                    "count": len(recs),
                    "rule_version": result["rule_version"],
                },
            }
        ),
        200,
    )


@analysis_bp.route(
    "/api/v1/shops/<int:shop_id>/analysis/products/<int:product_id>",
    methods=["GET"],
)
@require_shop_access(allow_inactive=True)
def api_product_analysis(shop_id: int, product_id: int):
    """API-025f: Return deterministic stock analysis for a single product in shop_id."""
    kwargs = _extract_analysis_query_kwargs()
    result = StockAnalysisService.get_product_analysis(
        actor=current_user,
        shop_id=shop_id,
        product_id=product_id,
        **kwargs,
    )
    return (
        jsonify(
            {
                "status": "success",
                "message": "Product stock analysis computed.",
                "data": result,
                "meta": {
                    "timestamp": _utc_iso(),
                    "rule_version": result["rule_version"],
                },
            }
        ),
        200,
    )


@analysis_bp.route(
    "/api/v1/shops/<int:shop_id>/analysis/batches/<int:batch_id>",
    methods=["GET"],
)
@require_shop_access(allow_inactive=True)
def api_batch_analysis(shop_id: int, batch_id: int):
    """API-025g: Return deterministic FEFO, expiry-risk, and surplus analysis for a single batch."""
    kwargs = _extract_analysis_query_kwargs()
    result = StockAnalysisService.get_batch_analysis(
        actor=current_user,
        shop_id=shop_id,
        batch_id=batch_id,
        **kwargs,
    )
    return (
        jsonify(
            {
                "status": "success",
                "message": "Batch stock analysis computed.",
                "data": result,
                "meta": {
                    "timestamp": _utc_iso(),
                    "rule_version": result["rule_version"],
                },
            }
        ),
        200,
    )
