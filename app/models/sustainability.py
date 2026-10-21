"""SQLAlchemy models for Carbon Accounting, Emission Factors, and Sustainability Ledger.
======================================================================================

Core Principles:
1. Strict separation between:
   - Estimated net emissions avoided (kg CO2e).
   - Smart Vendor sustainability points (gamification & ranking).
   - Official carbon credits (explicitly non-tradable, internal estimates).
2. Traceable, versioned emission factors with source citations, scopes, and uncertainty.
3. Multi-tenant organization isolation and auditable, idempotent ledger transactions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.extensions import db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EmissionFactor(db.Model):
    """Configurable, documented GHG emission factors for food waste prevention and logistics.
    
    Adheres to GHG Protocol Agricultural Guidance & FAO / Lifecycle assessment sources.
    Scope distinguishes waste disposal avoidance from production lifecycle and transport.
    """

    __tablename__ = "emission_factors"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    category = db.Column(db.String(80), nullable=False)
    subcategory = db.Column(db.String(80), nullable=True)
    product_name_pattern = db.Column(db.String(150), nullable=True)  # Regex or exact pattern match
    product_id = db.Column(
        db.BigInteger,
        db.ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    factor_value = db.Column(db.Numeric(10, 4), nullable=False)  # e.g., 0.8500 kg CO2e / kg
    factor_unit = db.Column(db.String(32), nullable=False, default="kg_co2e_per_kg")
    factor_scope = db.Column(
        db.String(40),
        nullable=False,
        default="WASTE_PREVENTION",
    )  # WASTE_PREVENTION, LIFECYCLE_PRODUCTION, TRANSPORT_ROAD
    geographic_scope = db.Column(db.String(32), nullable=False, default="IN")  # IN, GLOBAL, ASIA
    source_name = db.Column(db.String(150), nullable=False)  # e.g. FAO Food Wastage Footprint
    source_reference = db.Column(db.String(255), nullable=True)  # Academic paper / dataset citation
    source_year = db.Column(db.Integer, nullable=True)
    uncertainty_percent = db.Column(db.Numeric(5, 2), nullable=True, default=Decimal("15.00"))
    methodology_version = db.Column(db.String(32), nullable=False, default="v1.0.0-fao-warm")
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.text("true"),
    )
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=db.func.now(),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        server_default=db.func.now(),
    )

    product = db.relationship("Product", foreign_keys=[product_id])

    __table_args__ = (
        db.CheckConstraint("factor_value >= 0", name="ck_emission_factor_value_positive"),
        db.CheckConstraint(
            "factor_scope IN ('WASTE_PREVENTION', 'LIFECYCLE_PRODUCTION', 'TRANSPORT_ROAD', 'TRANSPORT_COLD_CHAIN')",
            name="ck_emission_factor_scope_valid",
        ),
        db.Index("idx_emission_factors_lookup", "category", "subcategory", "is_active"),
        db.Index("idx_emission_factors_product", "product_id", "is_active"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "subcategory": self.subcategory,
            "product_name_pattern": self.product_name_pattern,
            "product_id": self.product_id,
            "factor_value": float(self.factor_value) if self.factor_value is not None else 0.0,
            "factor_unit": self.factor_unit,
            "factor_scope": self.factor_scope,
            "geographic_scope": self.geographic_scope,
            "source_name": self.source_name,
            "source_reference": self.source_reference,
            "source_year": self.source_year,
            "uncertainty_percent": float(self.uncertainty_percent) if self.uncertainty_percent else None,
            "methodology_version": self.methodology_version,
            "is_active": self.is_active,
            "notes": self.notes,
        }


class SustainabilityEvent(db.Model):
    """Canonical environmental benefit event recording verified food waste prevention.
    
    A stock transfer alone does NOT create an environmental benefit. It requires eligible
    evidence (transfer completion, absorption by recipient shop, or markdown sale before expiry).
    """

    __tablename__ = "sustainability_events"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    organization_id = db.Column(
        db.BigInteger,
        db.ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_shop_id = db.Column(
        db.BigInteger,
        db.ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    destination_shop_id = db.Column(
        db.BigInteger,
        db.ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=True,
    )
    product_id = db.Column(
        db.BigInteger,
        db.ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id = db.Column(
        db.BigInteger,
        db.ForeignKey("inventory_batches.id", ondelete="RESTRICT"),
        nullable=True,
    )
    stock_transfer_id = db.Column(
        db.BigInteger,
        db.ForeignKey("transfer_requests.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sale_id = db.Column(
        db.BigInteger,
        db.ForeignKey("sales.id", ondelete="RESTRICT"),
        nullable=True,
    )

    event_type = db.Column(
        db.String(40),
        nullable=False,
        default="TRANSFER_REDISTRIBUTION",
    )  # TRANSFER_REDISTRIBUTION, NEAR_EXPIRY_SALE, SURPLUS_PREVENTION

    eligible_saved_quantity = db.Column(db.Numeric(12, 3), nullable=False)
    quantity_unit = db.Column(db.String(20), nullable=False)  # kg, g, litre, ml, pcs
    normalized_weight_kg = db.Column(db.Numeric(12, 3), nullable=False)

    applicable_factor_id = db.Column(
        db.BigInteger,
        db.ForeignKey("emission_factors.id", ondelete="RESTRICT"),
        nullable=True,
    )
    factor_selection_tier = db.Column(
        db.String(32),
        nullable=False,
        default="CATEGORY_FALLBACK",
    )  # EXACT_PRODUCT, SUBCATEGORY, CATEGORY_FALLBACK, PENDING_FACTOR

    estimated_avoided_emissions = db.Column(db.Numeric(12, 4), nullable=False, default=Decimal("0.0000"))
    additional_transport_emissions = db.Column(db.Numeric(12, 4), nullable=False, default=Decimal("0.0000"))
    net_estimated_impact = db.Column(db.Numeric(12, 4), nullable=False, default=Decimal("0.0000"))  # Avoided - Transport

    confidence_level = db.Column(
        db.String(32),
        nullable=False,
        default="MEDIUM_ESTIMATE",
    )  # HIGH_EXACT_MATCH, MEDIUM_ESTIMATE, LOW_ESTIMATE, PENDING_FACTOR

    event_status = db.Column(
        db.String(32),
        nullable=False,
        default="FINALIZED",
    )  # FINALIZED, PENDING_REVIEW, INELIGIBLE, REVERSED

    evidence_summary = db.Column(db.Text, nullable=True)
    methodology_version = db.Column(db.String(32), nullable=False, default="v1.0.0-fao-warm")
    reward_points_awarded = db.Column(db.BigInteger, nullable=False, default=0)

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=db.func.now(),
    )
    finalized_at = db.Column(db.DateTime(timezone=True), nullable=True)

    organization = db.relationship("Organization")
    source_shop = db.relationship("Shop", foreign_keys=[source_shop_id])
    destination_shop = db.relationship("Shop", foreign_keys=[destination_shop_id])
    product = db.relationship("Product")
    batch = db.relationship("InventoryBatch")
    stock_transfer = db.relationship("TransferRequest")
    sale = db.relationship("Sale")
    emission_factor = db.relationship("EmissionFactor")
    ledger_entries = db.relationship("SustainabilityLedger", back_populates="sustainability_event", lazy="dynamic")

    __table_args__ = (
        db.CheckConstraint("eligible_saved_quantity >= 0", name="ck_sustainability_qty_nonnegative"),
        db.CheckConstraint("normalized_weight_kg >= 0", name="ck_sustainability_norm_kg_nonnegative"),
        db.CheckConstraint(
            "event_status IN ('FINALIZED', 'PENDING_REVIEW', 'INELIGIBLE', 'REVERSED')",
            name="ck_sustainability_event_status",
        ),
        db.Index("idx_sustainability_events_org", "organization_id", "created_at"),
        db.Index("idx_sustainability_events_source_shop", "source_shop_id", "created_at"),
        db.Index("idx_sustainability_events_transfer", "stock_transfer_id"),
        db.Index("idx_sustainability_events_product", "product_id"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "source_shop_id": self.source_shop_id,
            "source_shop_name": self.source_shop.name if self.source_shop else None,
            "destination_shop_id": self.destination_shop_id,
            "destination_shop_name": self.destination_shop.name if self.destination_shop else None,
            "product_id": self.product_id,
            "product_name": self.product.name if self.product else None,
            "product_category": self.product.category if self.product else None,
            "batch_id": self.batch_id,
            "stock_transfer_id": self.stock_transfer_id,
            "event_type": self.event_type,
            "eligible_saved_quantity": float(self.eligible_saved_quantity),
            "quantity_unit": self.quantity_unit,
            "normalized_weight_kg": float(self.normalized_weight_kg),
            "factor_selection_tier": self.factor_selection_tier,
            "estimated_avoided_emissions_kg_co2e": float(self.estimated_avoided_emissions),
            "additional_transport_emissions_kg_co2e": float(self.additional_transport_emissions),
            "net_estimated_impact_kg_co2e": float(self.net_estimated_impact),
            "confidence_level": self.confidence_level,
            "event_status": self.event_status,
            "reward_points_awarded": self.reward_points_awarded,
            "evidence_summary": self.evidence_summary,
            "methodology_version": self.methodology_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "finalized_at": self.finalized_at.isoformat() if self.finalized_at else None,
        }


class SustainabilityLedger(db.Model):
    """Immutable double-entry sustainability points and emission impact attribution ledger.
    
    Guarantees:
    - Zero double-counting across sister shops.
    - Idempotency key per shop and event.
    - Complete auditability for reversals or administrative recalculations.
    """

    __tablename__ = "sustainability_ledger"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    organization_id = db.Column(
        db.BigInteger,
        db.ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    shop_id = db.Column(
        db.BigInteger,
        db.ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sustainability_event_id = db.Column(
        db.BigInteger,
        db.ForeignKey("sustainability_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    transaction_type = db.Column(
        db.String(40),
        nullable=False,
    )  # CREDIT_EARNED, ALLOCATION_SOURCE, ALLOCATION_RECIPIENT, REVERSAL, ADJUSTMENT

    points_delta = db.Column(db.BigInteger, nullable=False)  # Signed points (+ or -)
    attributed_impact_kg_co2e = db.Column(db.Numeric(12, 4), nullable=False)  # Signed kg CO2e
    reward_rule_version = db.Column(db.String(32), nullable=False, default="v1.0.0-10pts-per-kg")
    idempotency_key = db.Column(db.String(120), unique=True, nullable=False)
    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=db.func.now(),
    )

    organization = db.relationship("Organization")
    shop = db.relationship("Shop")
    sustainability_event = db.relationship("SustainabilityEvent", back_populates="ledger_entries")

    __table_args__ = (
        db.Index("idx_sustainability_ledger_shop_points", "shop_id", "created_at"),
        db.Index("idx_sustainability_ledger_org", "organization_id", "created_at"),
        db.Index("idx_sustainability_ledger_event", "sustainability_event_id"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "shop_id": self.shop_id,
            "shop_name": self.shop.name if self.shop else None,
            "sustainability_event_id": self.sustainability_event_id,
            "transaction_type": self.transaction_type,
            "points_delta": self.points_delta,
            "attributed_impact_kg_co2e": float(self.attributed_impact_kg_co2e),
            "reward_rule_version": self.reward_rule_version,
            "idempotency_key": self.idempotency_key,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
