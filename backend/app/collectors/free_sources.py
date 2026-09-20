from __future__ import annotations

import asyncio
import math
from typing import Any

import httpx

from app.config import settings
from app.schemas import AnalysisRequest


USER_AGENT = "Lupus-Cortex/1.0 (urban-intelligence research)"


async def _reverse_geocode(
    client: httpx.AsyncClient,
    req: AnalysisRequest,
) -> dict[str, Any]:
    response = await client.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={
            "format": "jsonv2",
            "lat": req.lat,
            "lon": req.lon,
            "zoom": 12,
            "addressdetails": 1,
        },
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    data = response.json()
    return {
        "provider": "OpenStreetMap Nominatim",
        "display_name": data.get("display_name"),
        "address": data.get("address", {}),
        "licence": data.get("licence"),
    }


async def _open_meteo(
    client: httpx.AsyncClient,
    req: AnalysisRequest,
) -> dict[str, Any]:
    weather, air = await asyncio.gather(
        client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": req.lat,
                "longitude": req.lon,
                "current": (
                    "temperature_2m,relative_humidity_2m,precipitation,"
                    "wind_speed_10m,surface_pressure"
                ),
                "timezone": "UTC",
            },
        ),
        client.get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={
                "latitude": req.lat,
                "longitude": req.lon,
                "current": (
                    "pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,"
                    "sulphur_dioxide,ozone,aerosol_optical_depth"
                ),
                "timezone": "UTC",
            },
        ),
    )
    weather.raise_for_status()
    air.raise_for_status()
    return {
        "provider": "Open-Meteo",
        "weather": weather.json(),
        "air_quality": air.json(),
    }


async def _elevation(
    client: httpx.AsyncClient,
    req: AnalysisRequest,
) -> dict[str, Any]:
    response = await client.get(
        "https://api.open-meteo.com/v1/elevation",
        params={"latitude": req.lat, "longitude": req.lon},
    )
    response.raise_for_status()
    return {"provider": "Open-Meteo Elevation", **response.json()}


async def _overpass(
    client: httpx.AsyncClient,
    req: AnalysisRequest,
) -> dict[str, Any]:
    radius = settings.free_data_radius_m
    query = f"""
    [out:json][timeout:25];
    (
      way(around:{radius},{req.lat},{req.lon})[highway];
      node(around:{radius},{req.lat},{req.lon})[public_transport];
      node(around:{radius},{req.lat},{req.lon})[highway=bus_stop];
      node(around:{radius},{req.lat},{req.lon})[amenity=hospital];
      way(around:{radius},{req.lat},{req.lon})[amenity=hospital];
      node(around:{radius},{req.lat},{req.lon})[amenity=clinic];
      way(around:{radius},{req.lat},{req.lon})[leisure=park];
      way(around:{radius},{req.lat},{req.lon})[landuse=forest];
      way(around:{radius},{req.lat},{req.lon})[natural=water];
      way(around:{radius},{req.lat},{req.lon})[water];
    );
    out tags center;
    """
    response = await client.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    elements = response.json().get("elements", [])

    counts = {
        "road_features": 0,
        "public_transport_features": 0,
        "hospital_or_clinic_features": 0,
        "green_space_features": 0,
        "surface_water_features": 0,
    }

    for element in elements:
        tags = element.get("tags", {})
        if "highway" in tags and tags.get("highway") != "bus_stop":
            counts["road_features"] += 1
        if "public_transport" in tags or tags.get("highway") == "bus_stop":
            counts["public_transport_features"] += 1
        if tags.get("amenity") in {"hospital", "clinic"}:
            counts["hospital_or_clinic_features"] += 1
        if tags.get("leisure") == "park" or tags.get("landuse") == "forest":
            counts["green_space_features"] += 1
        if tags.get("natural") == "water" or "water" in tags:
            counts["surface_water_features"] += 1

    area_km2 = math.pi * (radius / 1000.0) ** 2
    return {
        "provider": "OpenStreetMap Overpass",
        "radius_m": radius,
        "approx_area_km2": area_km2,
        "feature_counts": counts,
        "normalized_feature_density_per_km2": {
            key.replace("_features", "_per_km2"): value / area_km2
            for key, value in counts.items()
        },
        "note": (
            "OSM feature counts are contextual GIS evidence, "
            "not direct satellite observations."
        ),
    }


async def collect_free_sources(req: AnalysisRequest) -> dict[str, Any]:
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        follow_redirects=True,
    ) as client:
        tasks = {
            "geocoding": _reverse_geocode(client, req),
            "open_meteo": _open_meteo(client, req),
            "elevation": _elevation(client, req),
            "osm_context": _overpass(client, req),
        }
        values = await asyncio.gather(*tasks.values(), return_exceptions=True)

    result: dict[str, Any] = {}
    for key, value in zip(tasks.keys(), values):
        if isinstance(value, Exception):
            result[key] = {"status": "source_error", "error": str(value)}
        else:
            result[key] = value
    return result
