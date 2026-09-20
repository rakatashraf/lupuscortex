from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import requests


WEATHER_MAP = {
    "temperature": "temperature_2m",
    "air temperature": "temperature_2m",
    "relative humidity": "relative_humidity_2m",
    "humidity": "relative_humidity_2m",
    "precipitation": "precipitation",
    "rainfall": "precipitation",
    "rain": "rain",
    "pressure": "surface_pressure",
    "wind speed": "wind_speed_10m",
    "wind": "wind_speed_10m",
    "soil moisture": "soil_moisture_0_to_7cm",
    "soil temperature": "soil_temperature_0_to_7cm",
    "evapotranspiration": "et0_fao_evapotranspiration",
}

AIR_MAP = {
    "pm2.5": "pm2_5",
    "pm25": "pm2_5",
    "pm10": "pm10",
    "nitrogen dioxide": "nitrogen_dioxide",
    "no2": "nitrogen_dioxide",
    "sulfur dioxide": "sulphur_dioxide",
    "sulphur dioxide": "sulphur_dioxide",
    "so2": "sulphur_dioxide",
    "carbon monoxide": "carbon_monoxide",
    "co": "carbon_monoxide",
    "ozone": "ozone",
    "o3": "ozone",
    "aerosol optical depth": "aerosol_optical_depth",
    "aod": "aerosol_optical_depth",
    "dust": "dust",
}

OSM_MAP = {
    "road": 'way["highway"]',
    "roads": 'way["highway"]',
    "road density": 'way["highway"]',
    "hospital": 'nwr["amenity"="hospital"]',
    "hospital accessibility": 'nwr["amenity"="hospital"]',
    "public transport": 'nwr["public_transport"]',
    "transport accessibility": 'nwr["public_transport"]',
    "green space": 'nwr["leisure"="park"]',
    "park": 'nwr["leisure"="park"]',
    "water": 'nwr["natural"="water"]',
    "surface water": 'nwr["natural"="water"]',
    "critical infrastructure": 'nwr["amenity"~"hospital|fire_station|police|school|university"]',
}


def _key(component: str) -> str:
    return " ".join(component.lower().replace("_", " ").split())


def resolve(component: str) -> list[dict[str, str]]:
    key = _key(component)
    items: list[dict[str, str]] = []

    for phrase, variable in WEATHER_MAP.items():
        if phrase in key or key in phrase:
            items.append({
                "id": "open_meteo_weather",
                "provider": "Open-Meteo Historical Weather",
                "variable": variable,
                "description": "Hourly historical meteorology sampled across the selected bounding box.",
            })
            break

    for phrase, variable in AIR_MAP.items():
        if phrase in key or key in phrase:
            items.append({
                "id": "open_meteo_air",
                "provider": "Open-Meteo / CAMS Air Quality",
                "variable": variable,
                "description": "Hourly atmospheric composition data sampled across the selected bounding box.",
            })
            break

    for phrase, query in OSM_MAP.items():
        if phrase in key or key in phrase:
            items.append({
                "id": "openstreetmap",
                "provider": "OpenStreetMap / Overpass",
                "variable": phrase,
                "description": "Open geospatial infrastructure features intersecting the selected bounding box.",
            })
            break

    return items


def _grid(bbox: dict[str, float], points: int) -> list[tuple[float, float]]:
    points = max(1, min(points, 8))
    if points == 1:
        return [(
            (bbox["south"] + bbox["north"]) / 2,
            (bbox["west"] + bbox["east"]) / 2,
        )]
    lats = [
        bbox["south"] + i * (bbox["north"] - bbox["south"]) / (points - 1)
        for i in range(points)
    ]
    lons = [
        bbox["west"] + i * (bbox["east"] - bbox["west"]) / (points - 1)
        for i in range(points)
    ]
    return [(lat, lon) for lat in lats for lon in lons]


def _pick(mapping: dict[str, str], component: str) -> str:
    key = _key(component)
    for phrase, variable in mapping.items():
        if phrase in key or key in phrase:
            return variable
    raise ValueError(f"No external variable mapping exists for {component!r}.")


def _weather(
    component: str,
    bbox: dict[str, float],
    start: date,
    end: date,
    points: int,
) -> pd.DataFrame:
    variable = _pick(WEATHER_MAP, component)
    frames: list[pd.DataFrame] = []
    for lat, lon in _grid(bbox, points):
        r = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "hourly": variable,
                "timezone": "UTC",
            },
            timeout=90,
        )
        r.raise_for_status()
        payload = r.json()
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        vals = hourly.get(variable) or []
        if times and vals:
            frames.append(pd.DataFrame({
                "source": "Open-Meteo Historical Weather",
                "component_query": component,
                "latitude": lat,
                "longitude": lon,
                "observation_time": times[:len(vals)],
                "variable": variable,
                "value": vals[:len(times)],
                "unit": (payload.get("hourly_units") or {}).get(variable, ""),
            }))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _air(
    component: str,
    bbox: dict[str, float],
    start: date,
    end: date,
    points: int,
) -> pd.DataFrame:
    variable = _pick(AIR_MAP, component)
    frames: list[pd.DataFrame] = []
    for lat, lon in _grid(bbox, points):
        r = requests.get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "hourly": variable,
                "timezone": "UTC",
            },
            timeout=90,
        )
        r.raise_for_status()
        payload = r.json()
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        vals = hourly.get(variable) or []
        if times and vals:
            frames.append(pd.DataFrame({
                "source": "Open-Meteo / CAMS Air Quality",
                "component_query": component,
                "latitude": lat,
                "longitude": lon,
                "observation_time": times[:len(vals)],
                "variable": variable,
                "value": vals[:len(times)],
                "unit": (payload.get("hourly_units") or {}).get(variable, ""),
            }))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _osm(component: str, bbox: dict[str, float]) -> pd.DataFrame:
    key = _key(component)
    selector = None
    for phrase, query in OSM_MAP.items():
        if phrase in key or key in phrase:
            selector = query
            break
    if not selector:
        raise ValueError("No OpenStreetMap mapping exists for this component.")

    south, west, north, east = bbox["south"], bbox["west"], bbox["north"], bbox["east"]
    q = f"[out:json][timeout:50];({selector}({south},{west},{north},{east}););out center tags;"
    r = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": q},
        timeout=75,
        headers={"User-Agent": "EarthdataCSVDownloader/1.1"},
    )
    r.raise_for_status()
    rows: list[dict[str, Any]] = []
    for e in (r.json().get("elements") or []):
        center = e.get("center") or {}
        tags = e.get("tags") or {}
        row: dict[str, Any] = {
            "source": "OpenStreetMap / Overpass",
            "component_query": component,
            "osm_type": e.get("type"),
            "osm_id": e.get("id"),
            "latitude": e.get("lat", center.get("lat")),
            "longitude": e.get("lon", center.get("lon")),
            "name": tags.get("name", ""),
        }
        for k, v in tags.items():
            row[f"tag_{k}"] = v
        rows.append(row)
    return pd.DataFrame(rows)


def fetch(
    component: str,
    provider_id: str,
    bbox: dict[str, float],
    start: date,
    end: date,
    grid_points_per_axis: int = 3,
) -> pd.DataFrame:
    if provider_id == "open_meteo_weather":
        return _weather(component, bbox, start, end, grid_points_per_axis)
    if provider_id == "open_meteo_air":
        return _air(component, bbox, start, end, grid_points_per_axis)
    if provider_id == "openstreetmap":
        return _osm(component, bbox)
    raise ValueError(f"Unknown external provider: {provider_id}")
