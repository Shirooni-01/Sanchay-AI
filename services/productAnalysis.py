"""Compatibility Wrapper: services/productAnalysis.py
===================================================

Exposes the Product Analysis module interface at `services/productAnalysis`
re-exporting all typed contracts, data models, and services from
`app.services.product_analysis_service`.

This ensures exact conformity with the project specification:
  - Input Schemas: ProductAnalysisInput
  - Output Schemas: ProductAnalysisResult, DemandProjection, ExpiryRiskAssessment, CandidateRecipientShop
  - Service Methods: analyze_product_for_cross_shop_sales, get_placeholder_overview
"""

from app.services.product_analysis_service import (
    CandidateRecipientShop,
    DemandProjection,
    ExpiryRiskAssessment,
    ProductAnalysisInput,
    ProductAnalysisResult,
    ProductAnalysisService,
    ShopWasteMitigationSummary,
)

__all__ = [
    "ProductAnalysisInput",
    "DemandProjection",
    "ExpiryRiskAssessment",
    "CandidateRecipientShop",
    "ProductAnalysisResult",
    "ProductAnalysisService",
    "ShopWasteMitigationSummary",
]
