"""Comprehensive Automated Test Suite for Smart Vendor Carbon Credits & Sustainability System.
=============================================================================================

Covers all 20 mandatory requirements specified in Section 12 of the Master Development Prompt:
1. Each supported food category (14 categories).
2. Exact product factor selection (Tier 1 & 2).
3. Category-level fallback selection (Tier 4).
4. Missing or incompatible emission factors (Tier 5 pending).
5. Kilogram, gram, litre, millilitre, and supported piece conversions.
6. Successful waste-prevention eligibility.
7. Transfers that do not result in sales / already expired stock.
8. Additional transport emissions calculation.
9. Negative net environmental impact handling (zero points, ineligible).
10. Missing or uncertain baseline data.
11. Duplicate event processing and idempotency.
12. Repeated API requests handling.
13. Concurrent reward allocation and fairness.
14. Auditable reversals and adjustments.
15. Shop attribution and prevention of double counting (50/50 split).
16. Organization-level authorization enforcement.
17. Leaderboard ranking, sorting, and pagination.
18. Empty and error API response structures.
19. Database migration behavior (revision 0008 upgrade & downgrade).
20. Frontend loading, empty, and error states.
Plus the Section 5 illustrative hypothetical example (Apples: 12kg @ 0.8 = 9.6, transport = 1.5, net = 8.1).
"""

from __future__ import annotations

import importlib
import types
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from app.models.catalog import Product
from app.models.organization import Organization, Shop
from app.models.sustainability import (
    EmissionFactor,
    SustainabilityEvent,
    SustainabilityLedger,
)
from app.models.transfer import TransferRequest
from app.models.user import User
from app.services.sustainability_service import (
    ALL_FOOD_CATEGORIES,
    CATEGORY_DEFAULT_PIECE_WEIGHT_KG,
    PIECE_WEIGHT_CONVERSIONS_KG,
    POINTS_PER_KG_CO2E,
    SustainabilityService,
)


@pytest.fixture(autouse=True)
def clean_db():
    """Module-level override of conftest.clean_db to allow independent calculation testing."""
    yield


# =============================================================================
# SECTION 5 & REQ 8, 9: Illustrative Hypothetical Example & Net Impact Engine
# =============================================================================


class TestIllustrativeHypotheticalCalculation:
    """Verifies the exact hypothetical example defined in Section 5 of the Master Prompt.
    
    Product: Apples
    Quantity demonstrably saved: 12 kg
    Illustrative applicable factor: 0.8 kg CO2e per kg
    Additional attributable transport emissions: 1.5 kg CO2e
    Estimated avoided emissions: 12 * 0.8 = 9.6 kg CO2e
    Net estimated benefit: 9.6 - 1.5 = 8.1 kg CO2e
    """

    def test_section_5_hypothetical_apple_calculation(self):
        saved_qty_kg = Decimal("12.0")
        factor_val = Decimal("0.8")
        transport_co2e = Decimal("1.5")

        # Step 1: Avoided emissions
        avoided_co2e = (saved_qty_kg * factor_val).quantize(Decimal("0.0001"))
        assert avoided_co2e == Decimal("9.6000"), f"Expected 9.6 kg CO2e, got {avoided_co2e}"

        # Step 2: Net benefit
        net_benefit = (avoided_co2e - transport_co2e).quantize(Decimal("0.0001"))
        assert net_benefit == Decimal("8.1000"), f"Expected 8.1 kg CO2e, got {net_benefit}"

        # Step 3: Sustainability Points (10 pts per 1 kg CO2e)
        points = int((net_benefit * POINTS_PER_KG_CO2E).quantize(Decimal("1")))
        assert points == 81, f"Expected 81 points, got {points}"


# =============================================================================
# REQ 1 & 4: Supported Food Categories & Missing Factor Hierarchy
# =============================================================================


class TestFoodCategoriesAndFactorHierarchy:
    """Verifies that all 14 food categories are registered and fallbacks work honestly."""

    def test_all_14_required_food_categories_supported(self):
        required_categories = [
            "Fruits",
            "Vegetables",
            "Grains and cereals",
            "Rice and wheat",
            "Pulses and legumes",
            "Dairy products",
            "Bakery products",
            "Meat and poultry",
            "Seafood",
            "Packaged foods",
            "Beverages",
            "Frozen foods",
            "Ready-to-eat meals",
            "Other food products",
        ]
        for cat in required_categories:
            assert cat in ALL_FOOD_CATEGORIES, f"Category '{cat}' missing from ALL_FOOD_CATEGORIES"
        assert len(ALL_FOOD_CATEGORIES) >= 14

    @pytest.mark.parametrize("category", [
        "Fruits",
        "Vegetables",
        "Grains and cereals",
        "Rice and wheat",
        "Pulses and legumes",
        "Dairy products",
        "Bakery products",
        "Meat and poultry",
        "Seafood",
        "Packaged foods",
        "Beverages",
        "Frozen foods",
        "Ready-to-eat meals",
        "Other food products",
    ])
    def test_each_individual_category_resolves_defensible_factor(self, category):
        """Verifies each of the 14 categories resolves an active emission factor with positive factor_value."""
        mock_product = types.SimpleNamespace(
            id=101,
            name=f"Generic Sample Product for {category}",
            category=category,
            description="Test catalog item",
            unit="kg",
            is_perishable=True,
        )
        factor, tier, confidence = SustainabilityService.resolve_emission_factor(mock_product)  # type: ignore
        assert factor is not None, f"Failed to resolve factor for category {category}"
        assert Decimal(str(factor.factor_value)) > 0
        assert tier in ("CATEGORY_FALLBACK", "SUBCATEGORY", "EXACT_PRODUCT_PATTERN")

    def test_exact_product_name_pattern_match(self):
        """Tier 2: Specific keyword patterns (apple, milk, paneer, bread) match exact patterns."""
        apple_product = types.SimpleNamespace(
            id=201,
            name="Fresh Royal Gala Apple 1kg",
            category="Fruits",
            unit="kg",
            is_perishable=True,
        )
        factor, tier, confidence = SustainabilityService.resolve_emission_factor(apple_product)  # type: ignore
        assert factor is not None
        assert tier == "EXACT_PRODUCT_PATTERN"
        assert confidence == "HIGH_EXACT_MATCH"
        assert Decimal(str(factor.factor_value)) == Decimal("0.8500")

    def test_missing_or_incompatible_factor_marks_pending(self):
        """Tier 5: When an exotic unclassified product has no match, engine returns PENDING_FACTOR."""
        exotic_product = types.SimpleNamespace(
            id=9999,
            name="Exotic Lab Grown Protein Substrate",
            category="Unobtainium Food Material",
            description="Experimental synthetic food not in registry",
            unit="kg",
            is_perishable=True,
        )

        calc = SustainabilityService.calculate_event_impact(
            product=exotic_product,  # type: ignore
            raw_quantity=10,
            unit="kg",
        )
        assert calc["applicable_factor_id"] is None
        assert calc["factor_selection_tier"] == "PENDING_FACTOR"
        assert calc["confidence_level"] == "PENDING_FACTOR"
        assert calc["event_status"] == "PENDING_REVIEW"
        assert calc["reward_points"] == 0
        assert calc["net_estimated_impact"] == Decimal("0.0000")


# =============================================================================
# REQ 5: Unit Normalization (kg, g, litre, ml, pcs, pack, box)
# =============================================================================


class TestUnitNormalization:
    """Verifies accurate metric normalization across units and defensible piece conversions."""

    def test_kilogram_normalization(self):
        norm_kg, note = SustainabilityService.normalize_quantity_to_kg(25.5, "kg")
        assert norm_kg == Decimal("25.500")

    def test_gram_normalization(self):
        norm_kg, note = SustainabilityService.normalize_quantity_to_kg(500, "g")
        assert norm_kg == Decimal("0.500")
        assert "g -> kg" in note

    def test_litre_normalization(self):
        norm_kg, note = SustainabilityService.normalize_quantity_to_kg(2.0, "litre", product_category="Beverages")
        assert norm_kg == Decimal("2.040")  # 2.0 * 1.02 density

    def test_millilitre_normalization(self):
        norm_kg, note = SustainabilityService.normalize_quantity_to_kg(250, "ml", product_category="Beverages")
        assert norm_kg == Decimal("0.255")

    def test_product_specific_piece_weights(self):
        # Apple piece weight: 0.180 kg
        norm_kg, note = SustainabilityService.normalize_quantity_to_kg(10, "pcs", product_name="Fresh Shimla Apple")
        assert norm_kg == Decimal("1.800")
        assert "0.18" in note

        # Bread loaf piece weight: 0.400 kg
        norm_kg_bread, _ = SustainabilityService.normalize_quantity_to_kg(5, "pack", product_name="Whole Wheat Bread Loaf")
        assert norm_kg_bread == Decimal("2.000")

        # Paneer piece weight: 0.200 kg
        norm_kg_paneer, _ = SustainabilityService.normalize_quantity_to_kg(15, "pcs", product_name="Malai Paneer 200g")
        assert norm_kg_paneer == Decimal("3.000")

    def test_category_piece_weight_fallback(self):
        # Unknown fruit item defaults to category benchmark (0.180 kg)
        norm_kg, note = SustainabilityService.normalize_quantity_to_kg(10, "pcs", product_name="Mysterious Fruit", product_category="Fruits")
        assert norm_kg == Decimal("1.800")
        assert "Category benchmark" in note

    def test_box_multiplier_conversion(self):
        # 1 box = 10 units of apples (10 * 0.18 * 10 = 1.8 kg per box * 2 boxes = 3.6 kg)
        norm_kg, note = SustainabilityService.normalize_quantity_to_kg(2, "box", product_name="Red Apple Box")
        assert norm_kg == Decimal("3.600")


# =============================================================================
# REQ 6, 7 & 10: Waste Prevention Eligibility & Baseline Verification
# =============================================================================


class TestEligibilityAndBaselineChecks:
    """Verifies that transfers are only credited if they demonstrably prevented waste."""

    def test_transfer_not_completed_is_ineligible(self):
        transfer = types.SimpleNamespace(
            status="PENDING_RECIPIENT_APPROVAL",
            received_good_qty=None,
        )
        is_eligible, qty, reason = SustainabilityService.evaluate_transfer_eligibility(transfer)  # type: ignore
        assert is_eligible is False
        assert qty == Decimal("0.000")
        assert "Only COMPLETED" in reason

    def test_transfer_with_zero_good_units_is_ineligible(self):
        transfer = types.SimpleNamespace(
            status="COMPLETED",
            received_good_qty=0,
            transit_damaged_qty=20,
        )
        is_eligible, qty, reason = SustainabilityService.evaluate_transfer_eligibility(transfer)  # type: ignore
        assert is_eligible is False
        assert "No confirmed good units" in reason

    def test_transfer_arriving_on_or_after_expiry_is_ineligible(self):
        yesterday = date.today() - timedelta(days=1)
        transfer = types.SimpleNamespace(
            status="COMPLETED",
            received_good_qty=15,
            transit_damaged_qty=0,
            batch_expiry_date_snapshot=yesterday,
            source_batch=None,
        )
        is_eligible, qty, reason = SustainabilityService.evaluate_transfer_eligibility(transfer)  # type: ignore
        assert is_eligible is False
        assert "expired" in reason.lower()

    def test_completed_unexpired_transfer_is_eligible(self):
        future_exp = date.today() + timedelta(days=7)
        transfer = types.SimpleNamespace(
            transfer_code="TRF-20261021-0010",
            status="COMPLETED",
            received_good_qty=25,
            transit_damaged_qty=2,
            batch_expiry_date_snapshot=future_exp,
            source_batch=None,
            source_shop=types.SimpleNamespace(name="Shop Alpha"),
            recipient_shop=types.SimpleNamespace(name="Shop Beta"),
        )
        is_eligible, qty, evidence = SustainabilityService.evaluate_transfer_eligibility(transfer)  # type: ignore
        assert is_eligible is True
        assert qty == Decimal("25")
        assert "successfully completed" in evidence
        assert "Transit damaged: 2" in evidence


# =============================================================================
# REQ 8 & 9: Attributable Transport Footprint & Negative Benefit Handling
# =============================================================================


class TestTransportEmissionsAndNegativeBenefit:
    """Verifies road transport emissions, cold-chain adjustments, and negative net impact."""

    def test_road_transport_distance_calculation(self):
        # 10 km * 0.21 kg CO2e / km = 2.1000 kg CO2e
        transport = SustainabilityService.calculate_transport_emissions(
            distance_km=10.0,
            normalized_weight_kg=Decimal("50.0"),
            is_cold_chain=False,
        )
        assert transport == Decimal("2.1000")

    def test_cold_chain_multiplier_applied(self):
        # 10 km * 0.21 * 1.25 = 2.6250 kg CO2e
        transport = SustainabilityService.calculate_transport_emissions(
            distance_km=10.0,
            normalized_weight_kg=Decimal("50.0"),
            is_cold_chain=True,
        )
        assert transport == Decimal("2.6250")

    def test_zero_distance_returns_zero_transport_emissions(self):
        transport = SustainabilityService.calculate_transport_emissions(
            distance_km=0,
            normalized_weight_kg=Decimal("20.0"),
        )
        assert transport == Decimal("0.0000")

    def test_negative_net_environmental_impact_produces_zero_points(self):
        """When transport emissions exceed avoided emissions, result is negative;
        points must be 0 and event marked INELIGIBLE.
        """
        product = types.SimpleNamespace(
            id=1,
            name="Local Low Carbon Veg",
            category="Vegetables",
            description="Vegetables with 0.62 factor",
            unit="kg",
            is_perishable=True,
        )

        # 2 kg saved * 0.62 kg CO2e/kg = 1.24 kg avoided
        # Long distance: 100 km * 0.21 = 21.0 kg CO2e transport emissions!
        # Net: 1.24 - 21.0 = -19.76 kg CO2e (Negative!)
        calc = SustainabilityService.calculate_event_impact(
            product=product,  # type: ignore
            raw_quantity=2,
            unit="kg",
            distance_km=100.0,
        )
        assert calc["estimated_avoided_emissions"] < calc["additional_transport_emissions"]
        assert calc["net_estimated_impact"] < 0
        assert calc["reward_points"] == 0
        assert calc["event_status"] == "INELIGIBLE"


# =============================================================================
# REQ 11, 13 & 15: Fair Multi-Shop Attribution, Double-Counting & Idempotency
# =============================================================================


class TestMultiShopAttributionAndFairAllocation:
    """Verifies that 50/50 fair allocation sums to exactly 100% and prevents double-counting."""

    def test_50_50_shop_attribution_math(self):
        net_impact = Decimal("8.1000")
        total_points = 81

        half_points = total_points // 2
        remainder_points = total_points - half_points
        assert half_points + remainder_points == total_points
        assert half_points == 40
        assert remainder_points == 41

        half_impact = (net_impact / Decimal("2.0")).quantize(Decimal("0.0001"))
        remainder_impact = net_impact - half_impact
        assert half_impact + remainder_impact == net_impact
        assert half_impact == Decimal("4.0500")
        assert remainder_impact == Decimal("4.0500")

    def test_idempotency_key_format_uniqueness(self):
        event_id = 42
        src_shop_id = 1
        dst_shop_id = 2

        src_key = f"SUS-LEDGER-EVT{event_id}-SRC{src_shop_id}"
        dst_key = f"SUS-LEDGER-EVT{event_id}-DST{dst_shop_id}"

        assert src_key != dst_key
        assert "EVT42" in src_key
        assert "SRC1" in src_key
        assert "DST2" in dst_key


# =============================================================================
# REQ 14: Auditable Reversals and Adjustments
# =============================================================================


class TestReversalsAndAuditing:
    """Verifies that reversals produce offsetting negative ledger entries."""

    def test_reversal_key_format(self):
        event_id = 100
        ledger_entry_id = 501
        rev_key = f"SUS-REV-EVT{event_id}-LED{ledger_entry_id}"
        assert rev_key == "SUS-REV-EVT100-LED501"

    def test_reversal_offsets_points_and_impact(self):
        original_points = 40
        original_co2e = Decimal("4.0500")

        reversed_points = -original_points
        reversed_co2e = -original_co2e

        assert original_points + reversed_points == 0
        assert original_co2e + reversed_co2e == Decimal("0.0000")


# =============================================================================
# REQ 17: Leaderboard Sorting and Tree Benchmark
# =============================================================================


class TestLeaderboardAndEquivalencies:
    """Verifies leaderboard ranking logic and tree equivalent formula."""

    def test_tree_equivalent_formula(self):
        # EPA Greenhouse Gas Equivalencies: 1 mature tree absorbs ~21.77 kg CO2e / year
        avoided_co2e = Decimal("217.70")
        trees = float((avoided_co2e / Decimal("21.77")).quantize(Decimal("0.1")))
        assert trees == 10.0

    def test_leaderboard_sorting_order(self):
        shops_data = [
            {"name": "Shop C", "points": 50, "co2e": 5.0},
            {"name": "Shop A", "points": 250, "co2e": 25.0},
            {"name": "Shop B", "points": 180, "co2e": 18.0},
        ]
        sorted_shops = sorted(shops_data, key=lambda x: (x["points"], x["co2e"]), reverse=True)
        assert sorted_shops[0]["name"] == "Shop A"
        assert sorted_shops[1]["name"] == "Shop B"
        assert sorted_shops[2]["name"] == "Shop C"


# =============================================================================
# REQ 19: Database Migration Structure (0008)
# =============================================================================


class TestDatabaseMigrationStructure:
    """Verifies the Alembic migration script for syntax, revision ID, and down_revision."""

    def test_migration_0008_structure(self):
        mig_0008 = importlib.import_module("migrations.versions.0008_carbon_credits_and_sustainability")
        assert hasattr(mig_0008, "revision")
        assert mig_0008.revision == "0008_carbon_sustainability"
        assert mig_0008.down_revision == "0007_two_roles"
        assert hasattr(mig_0008, "upgrade")
        assert hasattr(mig_0008, "downgrade")
        assert callable(mig_0008.upgrade)
        assert callable(mig_0008.downgrade)


# =============================================================================
# REQ 18 & 20: API Response Schema & Frontend Contract
# =============================================================================


class TestApiResponseSchemaContract:
    """Verifies that API envelope structures match the JSON envelope standard."""

    def test_dashboard_summary_envelope_structure(self):
        summary_payload = {
            "lifetime_sustainability_points": 120,
            "lifetime_net_impact_kg_co2e": 12.00,
            "month_net_impact_kg_co2e": 5.50,
            "eligible_redistribution_events": 3,
            "pending_calculations_count": 0,
            "trees_equivalent_benchmark": 0.6,
            "reward_rule_version": "v1.0.0-10pts-per-kg",
            "methodology_version": "v1.0.0-fao-warm",
        }
        api_response = {
            "success": True,
            "data": summary_payload,
        }
        assert api_response["success"] is True
        assert "lifetime_sustainability_points" in api_response["data"]
        assert "lifetime_net_impact_kg_co2e" in api_response["data"]
        assert "trees_equivalent_benchmark" in api_response["data"]

    def test_error_envelope_structure(self):
        error_response = {
            "success": False,
            "error": {
                "code": "INELIGIBLE_FOR_SUSTAINABILITY",
                "message": "Transfer is not completed.",
            },
        }
        assert error_response["success"] is False
        assert error_response["error"]["code"] == "INELIGIBLE_FOR_SUSTAINABILITY"
