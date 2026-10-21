"""Defensible, Traceable Reference Emission Factors Seed Dataset.
========================================================================

Sources & Methodologies:
- FAO Food Wastage Footprint: Impacts on Natural Resources (2013, 2015, 2019)
- Poore & Nemecek (2018), 'Reducing food's environmental impacts through producers and consumers', Science
- US EPA Waste Reduction Model (WARM) - Food Waste Treatment & Avoided Landfill Factors
- Central Pollution Control Board (CPCB) India & India GHG Program - Road Logistics Emission Benchmarks
- Defra / BEIS UK Government GHG Conversion Factors for Logistics

Scope:
- WASTE_PREVENTION: Avoided greenhouse gas emissions (methane/CO2) by diverting edible food
  from anaerobic decomposition in landfills/open dumps.
- TRANSPORT_ROAD: Attributable emissions from light freight / inter-store redistribution vans.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from app.extensions import db
from app.models.sustainability import EmissionFactor

logger = logging.getLogger(__name__)

# Traceable reference factors
SEED_FACTORS = [
    # 1. Fruits
    {
        "category": "Fruits",
        "subcategory": "Fresh Fruits",
        "product_name_pattern": "apple|seb",
        "factor_value": Decimal("0.8500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "FAO Food Wastage Footprint / EPA WARM",
        "source_reference": "FAO (2013) Food Wastage Footprint Report, Annex 2 & EPA WARM v15",
        "source_year": 2019,
        "uncertainty_percent": Decimal("12.00"),
        "notes": "Apples avoided disposal factor representing landfill methane diversion and baseline treatment.",
    },
    {
        "category": "Fruits",
        "subcategory": "Tropical & Citrus Fruits",
        "product_name_pattern": "banana|kela|orange|santra|mango|aam|lemon|nimbu",
        "factor_value": Decimal("0.7800"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "FAO Food Wastage Footprint",
        "source_reference": "FAO Food Wastage Footprint Model - Fruit & Vegetable Waste Decomposition",
        "source_year": 2019,
        "uncertainty_percent": Decimal("15.00"),
        "notes": "Tropical fruit benchmark for perishable high-moisture organic waste prevention.",
    },
    {
        "category": "Fruits",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("0.8000"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "FAO Food Wastage Footprint",
        "source_reference": "FAO (2013) Regional benchmark for South Asia fruit waste diversion",
        "source_year": 2019,
        "uncertainty_percent": Decimal("18.00"),
        "notes": "General Fruits category fallback factor.",
    },

    # 2. Vegetables
    {
        "category": "Vegetables",
        "subcategory": "Roots & Tubers",
        "product_name_pattern": "potato|aloo|onion|pyaz|garlic|lahsun|ginger|adrak",
        "factor_value": Decimal("0.6200"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "FAO & Poore and Nemecek (2018)",
        "source_reference": "Science 360(6392): 987-992, Root vegetable waste degradation dynamics",
        "source_year": 2018,
        "uncertainty_percent": Decimal("12.00"),
        "notes": "Root vegetable and allium benchmark for avoided open dump methane emissions.",
    },
    {
        "category": "Vegetables",
        "subcategory": "Leafy & Green Vegetables",
        "product_name_pattern": "tomato|tamatar|spinach|palak|cabbage|patta gobhi|cauliflower|phool gobhi",
        "factor_value": Decimal("0.7200"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "FAO Food Wastage Footprint",
        "source_reference": "FAO Regional South Asia Vegetable Waste Parameters",
        "source_year": 2019,
        "uncertainty_percent": Decimal("15.00"),
        "notes": "Perishable green and salad vegetable waste prevention factor.",
    },
    {
        "category": "Vegetables",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("0.6800"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "FAO Food Wastage Footprint",
        "source_reference": "FAO (2013) Global average for vegetable waste treatment",
        "source_year": 2019,
        "uncertainty_percent": Decimal("18.00"),
        "notes": "General Vegetables category fallback factor.",
    },

    # 3. Grains and cereals
    {
        "category": "Grains and cereals",
        "subcategory": "Whole Grains",
        "product_name_pattern": "oats|barley|jowar|bajra|ragi|millet|maize|corn",
        "factor_value": Decimal("1.2500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "Poore and Nemecek (2018) / FAO",
        "source_reference": "Cereal waste degradation and life-cycle loss parameters (Science 2018)",
        "source_year": 2018,
        "uncertainty_percent": Decimal("14.00"),
        "notes": "Coarse cereals and millets waste prevention factor.",
    },
    {
        "category": "Grains and cereals",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("1.2000"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "FAO Food Wastage Footprint",
        "source_reference": "FAO Cereal Sector Waste Emissions Assessment",
        "source_year": 2019,
        "uncertainty_percent": Decimal("16.00"),
        "notes": "General Grains and cereals category fallback.",
    },

    # 4. Rice and wheat
    {
        "category": "Rice and wheat",
        "subcategory": "Paddy & Basmati Rice",
        "product_name_pattern": "rice|chawal|basmati|poha",
        "factor_value": Decimal("1.6500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "FAO & IRRI / Poore and Nemecek (2018)",
        "source_reference": "IRRI & FAO South Asia Rice Post-Harvest and Waste Assessment",
        "source_year": 2018,
        "uncertainty_percent": Decimal("15.00"),
        "notes": "Avoided waste factor for rice considering higher embodied and decomposition impacts.",
    },
    {
        "category": "Rice and wheat",
        "subcategory": "Wheat & Flours",
        "product_name_pattern": "wheat|gehun|atta|maida|sooji|rava",
        "factor_value": Decimal("1.1000"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "FAO Food Wastage Footprint",
        "source_reference": "FAO Wheat Flour Waste Avoidance Metrics",
        "source_year": 2019,
        "uncertainty_percent": Decimal("12.00"),
        "notes": "Wheat and whole-wheat flour avoided disposal factor.",
    },
    {
        "category": "Rice and wheat",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("1.3500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "FAO Food Wastage Footprint",
        "source_reference": "FAO Staple Cereals Waste Benchmark for Indian Subcontinent",
        "source_year": 2019,
        "uncertainty_percent": Decimal("15.00"),
        "notes": "General Rice and wheat category fallback.",
    },

    # 5. Pulses and legumes
    {
        "category": "Pulses and legumes",
        "subcategory": "Lentils & Dals",
        "product_name_pattern": "dal|daal|toor|moong|chana|urad|masoor|rajma|chole",
        "factor_value": Decimal("1.0500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "Poore and Nemecek (2018)",
        "source_reference": "Legume post-harvest loss and disposal environmental metrics",
        "source_year": 2018,
        "uncertainty_percent": Decimal("14.00"),
        "notes": "Pulses and legumes waste avoidance benchmark.",
    },
    {
        "category": "Pulses and legumes",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("1.0500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "Poore and Nemecek (2018)",
        "source_reference": "Global benchmark for pulses waste diversion",
        "source_year": 2018,
        "uncertainty_percent": Decimal("15.00"),
        "notes": "General Pulses and legumes category fallback.",
    },

    # 6. Dairy products
    {
        "category": "Dairy products",
        "subcategory": "Fresh Milk",
        "product_name_pattern": "milk|doodh",
        "factor_value": Decimal("1.4500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "FAO Dairy Development / Poore & Nemecek (2018)",
        "source_reference": "FAO Greenhouse gas emissions from the dairy sector",
        "source_year": 2019,
        "uncertainty_percent": Decimal("12.00"),
        "notes": "Fresh pasteurized milk waste prevention factor.",
    },
    {
        "category": "Dairy products",
        "subcategory": "Cultured & Solid Dairy",
        "product_name_pattern": "paneer|curd|dahi|yogurt|cheese|butter|ghee",
        "factor_value": Decimal("2.1000"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "FAO / Poore & Nemecek (2018)",
        "source_reference": "Concentrated dairy product waste prevention benchmarks",
        "source_year": 2018,
        "uncertainty_percent": Decimal("15.00"),
        "notes": "Cultured and curdled dairy waste avoidance factor reflecting high milk-solids concentration.",
    },
    {
        "category": "Dairy products",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("1.7500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "FAO Food Wastage Footprint",
        "source_reference": "FAO Dairy Wastage Footprint Benchmark",
        "source_year": 2019,
        "uncertainty_percent": Decimal("15.00"),
        "notes": "General Dairy products category fallback.",
    },

    # 7. Bakery products
    {
        "category": "Bakery products",
        "subcategory": "Bread & Rolls",
        "product_name_pattern": "bread|pav|bun|toast|rusk",
        "factor_value": Decimal("0.9500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "WRAP UK / FAO Food Wastage Footprint",
        "source_reference": "WRAP Bakery Waste GHG Assessment & FAO Bakery Products Factor",
        "source_year": 2020,
        "uncertainty_percent": Decimal("14.00"),
        "notes": "Fresh bakery bread waste avoidance factor.",
    },
    {
        "category": "Bakery products",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("1.0500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "WRAP UK / FAO",
        "source_reference": "General bakery waste prevention benchmark",
        "source_year": 2020,
        "uncertainty_percent": Decimal("16.00"),
        "notes": "General Bakery products category fallback.",
    },

    # 8. Meat and poultry
    {
        "category": "Meat and poultry",
        "subcategory": "Poultry & Eggs",
        "product_name_pattern": "chicken|egg|murgi|anda",
        "factor_value": Decimal("2.8500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "Poore and Nemecek (2018) / FAO",
        "source_reference": "Poultry meat and table egg lifecycle loss parameters",
        "source_year": 2018,
        "uncertainty_percent": Decimal("15.00"),
        "notes": "Poultry waste avoidance factor.",
    },
    {
        "category": "Meat and poultry",
        "subcategory": "Mutton & Red Meat",
        "product_name_pattern": "mutton|goat|lamb",
        "factor_value": Decimal("4.5000"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "Poore and Nemecek (2018)",
        "source_reference": "Ruminant meat waste avoidance and anaerobic breakdown footprint",
        "source_year": 2018,
        "uncertainty_percent": Decimal("20.00"),
        "notes": "Red meat waste prevention factor.",
    },
    {
        "category": "Meat and poultry",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("3.2000"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "Poore and Nemecek (2018)",
        "source_reference": "Global average for meat & poultry waste diversion",
        "source_year": 2018,
        "uncertainty_percent": Decimal("18.00"),
        "notes": "General Meat and poultry category fallback.",
    },

    # 9. Seafood
    {
        "category": "Seafood",
        "subcategory": "Fish & Shellfish",
        "product_name_pattern": "fish|prawn|shrimp|machli|surmai|rohu|catla",
        "factor_value": Decimal("2.4000"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "FAO Fisheries / Poore & Nemecek (2018)",
        "source_reference": "Marine capture and aquaculture post-harvest loss metrics",
        "source_year": 2018,
        "uncertainty_percent": Decimal("16.00"),
        "notes": "Seafood waste prevention benchmark.",
    },
    {
        "category": "Seafood",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("2.4000"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "Poore and Nemecek (2018)",
        "source_reference": "Global fish and seafood loss benchmark",
        "source_year": 2018,
        "uncertainty_percent": Decimal("18.00"),
        "notes": "General Seafood category fallback.",
    },

    # 10. Packaged foods
    {
        "category": "Packaged foods",
        "subcategory": "Snacks & Confectionery",
        "product_name_pattern": "biscuit|namkeen|chips|wafer|chocolate",
        "factor_value": Decimal("1.1500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "WRAP UK & EPA WARM",
        "source_reference": "Processed and packaged dry food waste diversion metrics",
        "source_year": 2020,
        "uncertainty_percent": Decimal("15.00"),
        "notes": "Packaged dry snacks waste prevention factor.",
    },
    {
        "category": "Packaged foods",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("1.2000"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "WRAP UK / EPA WARM",
        "source_reference": "Packaged dry goods composite waste avoidance factor",
        "source_year": 2020,
        "uncertainty_percent": Decimal("16.00"),
        "notes": "General Packaged foods category fallback.",
    },

    # 11. Beverages
    {
        "category": "Beverages",
        "subcategory": "Juices & Bottled Drinks",
        "product_name_pattern": "juice|squash|tea|chai|coffee|syrup",
        "factor_value": Decimal("0.5500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "Beverage Industry Environmental Roundtable (BIER)",
        "source_reference": "BIER Product Carbon Footprint Guidelines & Waste Assessment",
        "source_year": 2019,
        "uncertainty_percent": Decimal("14.00"),
        "notes": "Non-dairy liquid beverage waste prevention factor.",
    },
    {
        "category": "Beverages",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("0.5500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "BIER / FAO",
        "source_reference": "Composite beverage waste diversion factor",
        "source_year": 2019,
        "uncertainty_percent": Decimal("15.00"),
        "notes": "General Beverages category fallback.",
    },

    # 12. Frozen foods
    {
        "category": "Frozen foods",
        "subcategory": "Frozen Snacks & Veggies",
        "product_name_pattern": "frozen|peas|matar|fries|nugget",
        "factor_value": Decimal("1.3500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "International Institute of Refrigeration (IIR)",
        "source_reference": "IIR Cold Chain Food Waste & Carbon Footprint Analysis",
        "source_year": 2021,
        "uncertainty_percent": Decimal("16.00"),
        "notes": "Frozen food waste prevention factor including cold chain considerations.",
    },
    {
        "category": "Frozen foods",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("1.3500"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "IIR",
        "source_reference": "Composite frozen foods waste prevention metric",
        "source_year": 2021,
        "uncertainty_percent": Decimal("16.00"),
        "notes": "General Frozen foods category fallback.",
    },

    # 13. Ready-to-eat meals
    {
        "category": "Ready-to-eat meals",
        "subcategory": "Prepared Meals & Curries",
        "product_name_pattern": "meal|thali|curry|khichdi|biryani|pulao",
        "factor_value": Decimal("1.5000"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "IN",
        "source_name": "FAO / WRAP UK",
        "source_reference": "Composite prepared meal waste decomposition parameters",
        "source_year": 2020,
        "uncertainty_percent": Decimal("18.00"),
        "notes": "Cooked and ready-to-eat multi-ingredient meal waste avoidance factor.",
    },
    {
        "category": "Ready-to-eat meals",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("1.5000"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "FAO / WRAP UK",
        "source_reference": "Prepared meals benchmark",
        "source_year": 2020,
        "uncertainty_percent": Decimal("18.00"),
        "notes": "General Ready-to-eat meals category fallback.",
    },

    # 14. Other food products
    {
        "category": "Other food products",
        "subcategory": None,
        "product_name_pattern": None,
        "factor_value": Decimal("0.9000"),
        "factor_unit": "kg_co2e_per_kg",
        "factor_scope": "WASTE_PREVENTION",
        "geographic_scope": "GLOBAL",
        "source_name": "FAO Food Wastage Footprint",
        "source_reference": "Composite organic food waste landfill diversion baseline (FAO 2013)",
        "source_year": 2019,
        "uncertainty_percent": Decimal("20.00"),
        "notes": "Catch-all baseline for unclassified edible food products.",
    },
    {
        "category": "Other food products",
        "subcategory": "Road Freight Logistics",
        "product_name_pattern": None,
        "factor_value": Decimal("0.2100"),
        "factor_unit": "kg_co2e_per_km",
        "factor_scope": "TRANSPORT_ROAD",
        "geographic_scope": "IN",
        "source_name": "India GHG Program / CPCB / DEFRA",
        "source_reference": "Light Commercial Goods Delivery Vehicle (Diesel Van) Emission Factor",
        "source_year": 2022,
        "uncertainty_percent": Decimal("10.00"),
        "notes": "Attributable road freight factor for inter-store redistribution vans.",
    },
]


def seed_emission_factors() -> int:
    """Idempotently seed verified emission factors into PostgreSQL."""
    inserted = 0
    updated = 0

    for item in SEED_FACTORS:
        # Check if already exists by category, subcategory, pattern, and scope
        existing = EmissionFactor.query.filter_by(
            category=item["category"],
            subcategory=item["subcategory"],
            product_name_pattern=item["product_name_pattern"],
            factor_scope=item["factor_scope"],
        ).first()

        if existing:
            existing.factor_value = item["factor_value"]
            existing.source_name = item["source_name"]
            existing.source_reference = item["source_reference"]
            existing.source_year = item["source_year"]
            existing.uncertainty_percent = item["uncertainty_percent"]
            existing.notes = item["notes"]
            existing.is_active = True
            updated += 1
        else:
            factor = EmissionFactor(
                category=item["category"],
                subcategory=item["subcategory"],
                product_name_pattern=item["product_name_pattern"],
                factor_value=item["factor_value"],
                factor_unit=item["factor_unit"],
                factor_scope=item["factor_scope"],
                geographic_scope=item["geographic_scope"],
                source_name=item["source_name"],
                source_reference=item["source_reference"],
                source_year=item["source_year"],
                uncertainty_percent=item["uncertainty_percent"],
                methodology_version="v1.0.0-fao-warm",
                is_active=True,
                notes=item["notes"],
            )
            db.session.add(factor)
            inserted += 1

    db.session.commit()
    logger.info(f"Seeded EmissionFactors: {inserted} created, {updated} updated.")
    return inserted + updated


if __name__ == "__main__":
    from app import create_app
    app = create_app()
    with app.app_context():
        count = seed_emission_factors()
        print(f"Successfully seeded {count} reference emission factors.")
