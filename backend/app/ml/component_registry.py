"""Versioned, transparent component and index definitions for Lupus Cortex.

This registry defines metadata and mappings only. It does not imply that source data
is available or that a score is scientifically validated.
"""
from __future__ import annotations

from typing import Any

REGISTRY_VERSION = "1.0.0"

# identifier: unit, direction, cadence, preferred source family
_COMPONENT_ROWS = [
    ("PM2_5", "ug/m3", "lower_is_better", "daily", "GEOS-CF / Open-Meteo air quality"),
    ("PM10", "ug/m3", "lower_is_better", "daily", "GEOS-CF / Open-Meteo air quality"),
    ("NO2", "ug/m3", "lower_is_better", "daily", "OMI / Open-Meteo air quality"),
    ("O3", "ug/m3", "lower_is_better", "daily", "OMI / Open-Meteo air quality"),
    ("SO2", "ug/m3", "lower_is_better", "daily", "OMI / Open-Meteo air quality"),
    ("CO", "mg/m3", "lower_is_better", "daily", "AIRS / Open-Meteo air quality"),
    ("AEROSOL_INDEX", "index", "lower_is_better", "daily", "MODIS / OMI"),
    ("LST", "deg C or product-native", "contextual", "product_specific", "MODIS LST"),
    ("AIR_TEMP", "deg C", "contextual", "hourly_or_daily", "MERRA-2 / Open-Meteo"),
    ("REL_HUMIDITY", "%", "contextual", "hourly_or_daily", "MERRA-2 / Open-Meteo"),
    ("BUILTUP_PCT", "% area", "contextual", "annual_or_static", "Landsat / HLS"),
    ("IMPERVIOUS_PCT", "% cover", "contextual", "product_specific", "Landsat / HLS"),
    ("NDVI", "unitless [-1,1]", "higher_is_better", "product_specific", "MODIS / HLS"),
    ("POP_DENSITY", "people/km2", "contextual", "annual_or_static", "SEDAC / WorldPop"),
    ("VULNERABLE_AGE_PCT", "% population", "contextual", "annual_or_static", "SEDAC / official census"),
    ("GREEN_ACCESS_PCT", "% within defined access threshold", "higher_is_better", "product_specific", "OSM / green-space maps"),
    ("GREEN_SPACE_PCT", "% area", "higher_is_better", "product_specific", "Landsat / HLS / official GIS"),
    ("PRECIPITATION", "mm per product interval", "contextual", "daily_or_subdaily", "GPM IMERG / CHIRPS"),
    ("EXTREME_RAINFALL", "percentile or return period", "lower_is_better", "daily_or_event", "GPM / station records"),
    ("SOIL_MOISTURE", "m3/m3 or product-native", "contextual", "product_specific", "SMAP"),
    ("SURFACE_WATER_EXTENT", "km2 or % area", "contextual", "product_specific", "Landsat / OPERA / MODIS"),
    ("FLOOD_EXTENT", "km2 or % area", "lower_is_better", "event_based", "MODIS / VIIRS / OPERA"),
    ("DROUGHT_SPI", "standardized anomaly", "toward_zero", "monthly", "precipitation / reanalysis"),
    ("ELEVATION_SLOPE", "m and degrees", "contextual", "static", "NASADEM / SRTM"),
    ("ROAD_DENSITY", "km/km2", "contextual", "source_specific", "OpenStreetMap / official roads"),
    ("TRANSPORT_ACCESS_PCT", "% within defined access threshold", "higher_is_better", "source_specific", "OSM / GTFS"),
    ("HOSPITAL_ACCESS", "minutes or km", "lower_is_better", "source_specific", "facility data / routing"),
    ("CRIT_INFRA_DENSITY", "facilities/km2", "contextual", "source_specific", "OSM / official inventories"),
    ("NIGHT_LIGHTS", "product-native radiance", "contextual", "monthly", "VIIRS Black Marble"),
    ("DISASTER_READINESS", "documented readiness score", "higher_is_better", "source_specific", "official plans / validated surveys"),
]

COMPONENTS: dict[str, dict[str, str]] = {
    row[0]: {
        "unit": row[1],
        "direction": row[2],
        "cadence": row[3],
        "preferred_source_family": row[4],
    }
    for row in _COMPONENT_ROWS
}

INDEX_COMPONENTS: dict[str, tuple[str, ...]] = {
    "AQHI": ("PM2_5", "PM10", "NO2", "O3", "SO2", "CO", "AEROSOL_INDEX"),
    "UHVI": ("LST", "AIR_TEMP", "REL_HUMIDITY", "BUILTUP_PCT", "IMPERVIOUS_PCT", "NDVI", "POP_DENSITY", "VULNERABLE_AGE_PCT", "GREEN_ACCESS_PCT"),
    "GEA": ("NDVI", "GREEN_SPACE_PCT", "GREEN_ACCESS_PCT"),
    "WSII": ("PRECIPITATION", "EXTREME_RAINFALL", "SOIL_MOISTURE", "SURFACE_WATER_EXTENT", "FLOOD_EXTENT", "DROUGHT_SPI", "ELEVATION_SLOPE"),
    "TAI": ("ROAD_DENSITY", "TRANSPORT_ACCESS_PCT"),
    "HAI": ("HOSPITAL_ACCESS", "CRIT_INFRA_DENSITY", "POP_DENSITY"),
    "CRI": ("EXTREME_RAINFALL", "FLOOD_EXTENT", "DROUGHT_SPI", "SOIL_MOISTURE", "ELEVATION_SLOPE", "DISASTER_READINESS"),
    "SCI": ("POP_DENSITY", "VULNERABLE_AGE_PCT", "ROAD_DENSITY", "BUILTUP_PCT", "NIGHT_LIGHTS", "CRIT_INFRA_DENSITY", "GREEN_ACCESS_PCT", "GREEN_SPACE_PCT", "TRANSPORT_ACCESS_PCT"),
    "DRI": ("DISASTER_READINESS", "FLOOD_EXTENT", "EXTREME_RAINFALL", "ELEVATION_SLOPE", "CRIT_INFRA_DENSITY", "POP_DENSITY", "AIR_TEMP", "LST"),
}

HWI_WEIGHTS = {
    "AQHI": 0.12, "UHVI": 0.12, "GEA": 0.12, "WSII": 0.12, "DRI": 0.12,
    "TAI": 0.10, "HAI": 0.10, "CRI": 0.10, "SCI": 0.10,
}

CONDITION_LABELS = (
    (81, 100, "Perfect"),
    (61, 80, "Well managed"),
    (41, 60, "Needs improvement"),
    (21, 40, "Poor"),
    (0, 20, "Harmful"),
)


def validate_registry() -> None:
    """Fail fast if component membership drifts from the existing frontend contract."""
    if len(COMPONENTS) != 30:
        raise ValueError(f"Expected 30 component identifiers, found {len(COMPONENTS)}")
    unknown = {
        component
        for members in INDEX_COMPONENTS.values()
        for component in members
        if component not in COMPONENTS
    }
    if unknown:
        raise ValueError(f"Index mapping contains unknown components: {sorted(unknown)}")
    if abs(sum(HWI_WEIGHTS.values()) - 1.0) > 1e-9:
        raise ValueError("HWI domain weights must sum to 1")


def condition_label(score: float) -> str:
    """Map an already-normalized 0-100 score to the existing display labels."""
    if not 0 <= score <= 100:
        raise ValueError("score must be within [0, 100]")
    for low, high, label in CONDITION_LABELS:
        if low <= score <= high:
            return label
    raise ValueError("score is not covered by condition thresholds")


def equal_weight_index(
    values: dict[str, float | None],
    index_code: str,
    *,
    min_coverage: float = 1.0,
) -> dict[str, Any]:
    """Aggregate normalized component scores without hiding missing coverage.

    Inputs must already be direction-aware normalized scores on [0,100]. No raw
    physical values are normalized here because valid reference distributions and
    thresholds are source-, region- and season-specific.
    """
    if index_code not in INDEX_COMPONENTS:
        raise KeyError(f"Unknown index: {index_code}")
    if not 0 < min_coverage <= 1:
        raise ValueError("min_coverage must be in (0, 1]")
    members = INDEX_COMPONENTS[index_code]
    present = [float(values[k]) for k in members if values.get(k) is not None]
    if any(v < 0 or v > 100 for v in present):
        raise ValueError("normalized component scores must be within [0, 100]")
    coverage = len(present) / len(members)
    if coverage < min_coverage or not present:
        return {
            "index": index_code,
            "score": None,
            "status": "insufficient_data",
            "coverage": coverage,
            "present_components": len(present),
            "expected_components": len(members),
        }
    score = sum(present) / len(present)
    return {
        "index": index_code,
        "score": score,
        "status": condition_label(score),
        "coverage": coverage,
        "present_components": len(present),
        "expected_components": len(members),
    }


validate_registry()
