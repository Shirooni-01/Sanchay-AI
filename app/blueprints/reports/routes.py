"""HTTP and REST JSON API routes for Phase 8: Dashboards, Analytics & Reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from flask import (
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.blueprints.reports import reports_bp
from app.models.catalog import Product
from app.models.organization import Organization, Shop
from app.models.user import User
from app.services.inventory_service import get_business_today
from app.services.org_service import ShopService
from app.services.report_service import ReportService, _parse_date
from app.utils.decorators import (
    require_role,
    verify_authenticated_user,
    verify_shop_access,
)
from app.utils.errors import (
    AuthorizationError,
    DomainError,
    ResourceNotFoundError,
    ValidationError,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ==============================================================================
# HTML VIEWS: Reports & Analytics Hub
# ==============================================================================


@reports_bp.route("/analytics", methods=["GET"])
@login_required
def analytics_redirect():
    """Redirect /analytics to unified reports and analytics hub."""
    return redirect(url_for("reports.reports_page", **request.args))


@reports_bp.route("/reports", methods=["GET"])
@login_required
def reports_page():
    """Render the centralized Reports & Analytics Hub."""
    user: User = current_user._get_current_object()
    today = get_business_today()

    # Get accessible shops for dropdown
    accessible_shops = ShopService.list_shops(actor=user)

    # Parse query parameters
    report_type = str(request.args.get("type", "SALES")).strip().upper()
    if report_type not in {"SALES", "INVENTORY", "NEAR_EXPIRY", "TRANSFERS"}:
        report_type = "SALES"

    raw_shop_id = request.args.get("shop_id")
    selected_shop_id: int | None = None
    if raw_shop_id and raw_shop_id.strip():
        try:
            selected_shop_id = int(raw_shop_id.strip())
        except ValueError:
            selected_shop_id = None

    # Enforce role scoping on shop selection
    if user.role != "SUPER_ADMIN" and selected_shop_id is None:
        if accessible_shops:
            selected_shop_id = accessible_shops[0].id

    start_date_raw = request.args.get("start_date")
    end_date_raw = request.args.get("end_date")
    category = request.args.get("category")
    if category:
        category = category.strip() or None

    # Generate Report Data
    report_data = None
    try:
        report_data = ReportService.generate_report(
            actor=user,
            report_type=report_type,
            start_date=start_date_raw,
            end_date=end_date_raw,
            shop_id=selected_shop_id,
            category=category,
        )
    except DomainError as err:
        flash(f"Report generation notice: {err.message}", "warning")
        # Fall back to default 30 days without crash
        try:
            report_data = ReportService.generate_report(
                actor=user,
                report_type=report_type,
                start_date=today - timedelta(days=30),
                end_date=today,
                shop_id=selected_shop_id,
                category=category,
            )
        except Exception:
            report_data = {
                "report_type": report_type,
                "scope": "Error generating report",
                "filters": {
                    "start_date": (today - timedelta(days=30)).isoformat(),
                    "end_date": today.isoformat(),
                    "shop_id": selected_shop_id,
                    "category": category,
                },
                "columns": [],
                "rows": [],
                "summary": {},
            }

    # Fetch Analytics for embedded charts
    sales_trends = []
    top_products = {"by_quantity": [], "by_revenue": []}
    stock_distribution = {"by_category": [], "by_status": [], "by_expiry_horizon": []}
    transfer_activity = {}

    try:
        sales_trends = ReportService.get_sales_trends(
            actor=user,
            shop_id=selected_shop_id,
            days=30,
            group_by="daily",
        )
        top_products = ReportService.get_top_selling_products(
            actor=user,
            shop_id=selected_shop_id,
            limit=5,
        )
        stock_distribution = ReportService.get_stock_distribution(
            actor=user,
            shop_id=selected_shop_id,
        )
        transfer_activity = ReportService.get_transfer_activity(
            actor=user,
            shop_id=selected_shop_id,
        )
    except DomainError as err:
        # Graceful degradation if store has empty data
        pass

    # Unique product categories for filter dropdown
    categories_query = Product.query.with_entities(Product.category).distinct()
    if user.organization_id:
        categories_query = categories_query.filter_by(organization_id=user.organization_id)
    categories = sorted([c[0] for c in categories_query.all() if c[0]])

    return render_template(
        "reports/reports.html",
        user=user,
        shops=accessible_shops,
        selected_shop_id=selected_shop_id,
        report_type=report_type,
        categories=categories,
        selected_category=category,
        start_date=report_data["filters"]["start_date"] if report_data else (today - timedelta(days=30)).isoformat(),
        end_date=report_data["filters"]["end_date"] if report_data else today.isoformat(),
        report=report_data,
        sales_trends=sales_trends,
        top_products=top_products,
        stock_distribution=stock_distribution,
        transfer_activity=transfer_activity,
    )


# ==============================================================================
# REST APIS: Module F Analytics & Reports (API-038, API-039, API-040)
# ==============================================================================


@reports_bp.route("/api/v1/reports/shop/<int:shop_id>/summary", methods=["GET"])
@login_required
def api_shop_summary(shop_id: int):
    """API-038: Shop financial and inventory KPIs."""
    user: User = current_user._get_current_object()
    try:
        data = ReportService.get_shop_dashboard_metrics(actor=user, shop_id=shop_id)
        return jsonify({"success": True, "data": data}), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@reports_bp.route("/api/v1/reports/organization/summary", methods=["GET"])
@login_required
def api_organization_summary():
    """API-039: Organization-wide executive summary across all shops."""
    user: User = current_user._get_current_object()
    if user.role != "SUPER_ADMIN":
        return (
            jsonify(
                {
                    "success": False,
                    "error": {
                        "code": "AUTH_FORBIDDEN",
                        "message": "Only Super Admins can access organization summary.",
                    },
                }
            ),
            403,
        )

    try:
        data = ReportService.get_organization_dashboard_metrics(actor=user)
        return jsonify({"success": True, "data": data}), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@reports_bp.route("/api/v1/reports/export", methods=["GET"])
@login_required
def api_export_report():
    """API-040: Export report to RFC 4180 CSV."""
    user: User = current_user._get_current_object()

    export_format = request.args.get("format", "csv").strip().lower()
    if export_format != "csv":
        return (
            jsonify(
                {
                    "success": False,
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": f"Unsupported export format '{export_format}'. Only 'csv' is currently supported.",
                    },
                }
            ),
            400,
        )

    report_type = request.args.get("type", request.args.get("report_type", "SALES"))
    raw_shop_id = request.args.get("shop_id")
    shop_id = int(raw_shop_id) if raw_shop_id and raw_shop_id.strip() else None
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    category = request.args.get("category")
    product_id_raw = request.args.get("product_id")
    product_id = int(product_id_raw) if product_id_raw and product_id_raw.strip() else None

    try:
        csv_content, filename = ReportService.generate_csv_export(
            actor=user,
            report_type=report_type,
            start_date=start_date,
            end_date=end_date,
            shop_id=shop_id,
            category=category,
            product_id=product_id,
        )

        response = Response(csv_content, mimetype="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@reports_bp.route("/api/v1/reports/analytics/trends", methods=["GET"])
@login_required
def api_analytics_trends():
    """REST endpoint for sales trend series (daily/monthly)."""
    user: User = current_user._get_current_object()
    raw_shop_id = request.args.get("shop_id")
    shop_id = int(raw_shop_id) if raw_shop_id and raw_shop_id.strip() else None
    days = int(request.args.get("days", 30))
    group_by = request.args.get("group_by", "daily").strip().lower()

    try:
        trends = ReportService.get_sales_trends(
            actor=user,
            shop_id=shop_id,
            days=days,
            group_by=group_by,
        )
        return jsonify({"success": True, "data": {"trends": trends}}), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@reports_bp.route("/api/v1/reports/analytics/top-products", methods=["GET"])
@login_required
def api_analytics_top_products():
    """REST endpoint for top-selling products by quantity and revenue."""
    user: User = current_user._get_current_object()
    raw_shop_id = request.args.get("shop_id")
    shop_id = int(raw_shop_id) if raw_shop_id and raw_shop_id.strip() else None
    limit = int(request.args.get("limit", 5))
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    try:
        data = ReportService.get_top_selling_products(
            actor=user,
            shop_id=shop_id,
            limit=limit,
            start_date=_parse_date(start_date) if start_date else None,
            end_date=_parse_date(end_date) if end_date else None,
        )
        return jsonify({"success": True, "data": data}), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@reports_bp.route("/api/v1/reports/analytics/stock-distribution", methods=["GET"])
@login_required
def api_analytics_stock_distribution():
    """REST endpoint for stock distribution by category, status, and horizon."""
    user: User = current_user._get_current_object()
    raw_shop_id = request.args.get("shop_id")
    shop_id = int(raw_shop_id) if raw_shop_id and raw_shop_id.strip() else None

    try:
        data = ReportService.get_stock_distribution(
            actor=user,
            shop_id=shop_id,
        )
        return jsonify({"success": True, "data": data}), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@reports_bp.route("/api/v1/reports/analytics/transfers", methods=["GET"])
@login_required
def api_analytics_transfers():
    """REST endpoint for transfer throughput and status distribution."""
    user: User = current_user._get_current_object()
    raw_shop_id = request.args.get("shop_id")
    shop_id = int(raw_shop_id) if raw_shop_id and raw_shop_id.strip() else None

    try:
        data = ReportService.get_transfer_activity(
            actor=user,
            shop_id=shop_id,
        )
        return jsonify({"success": True, "data": data}), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )


@reports_bp.route("/api/v1/reports/data", methods=["GET"])
@login_required
def api_report_data():
    """REST endpoint for structured tabular report data."""
    user: User = current_user._get_current_object()
    report_type = request.args.get("type", "SALES")
    raw_shop_id = request.args.get("shop_id")
    shop_id = int(raw_shop_id) if raw_shop_id and raw_shop_id.strip() else None
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    category = request.args.get("category")
    product_id_raw = request.args.get("product_id")
    product_id = int(product_id_raw) if product_id_raw and product_id_raw.strip() else None

    try:
        data = ReportService.generate_report(
            actor=user,
            report_type=report_type,
            start_date=start_date,
            end_date=end_date,
            shop_id=shop_id,
            category=category,
            product_id=product_id,
        )
        return jsonify({"success": True, "data": data}), 200
    except DomainError as err:
        return (
            jsonify({"success": False, "error": {"code": err.code, "message": err.message}}),
            err.http_status,
        )
