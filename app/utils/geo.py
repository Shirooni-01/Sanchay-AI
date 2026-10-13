"""Geographic coordinate and Haversine distance utilities for Sanchay AI (Phase 2)."""

from __future__ import annotations

import math
from decimal import Decimal

EARTH_RADIUS_KM = 6371.0088


def haversine_distance_km(
    lat1: float | Decimal | None,
    lon1: float | Decimal | None,
    lat2: float | Decimal | None,
    lon2: float | Decimal | None,
) -> float | None:
    """Compute great-circle distance in kilometers between two (lat, lon) pairs.

    Returns None if any coordinate is None.
    """
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None

    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    delta_phi = math.radians(float(lat2) - float(lat1))
    delta_lambda = math.radians(float(lon2) - float(lon1))

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return round(EARTH_RADIUS_KM * c, 2)
