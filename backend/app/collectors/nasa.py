from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings
from app.schemas import AnalysisRequest


NASA_PRODUCTS: tuple[dict[str, Any], ...] = (
    {"component": "PM2.5", "short_name": "M2T1NXAER", "version": None, "kind": "reanalysis", "variables": ["DUSMASS25", "SSSMASS25", "BCSMASS", "OCSMASS", "SO4SMASS"]},
    {"component": "PM10", "short_name": "M2T1NXAER", "version": None, "kind": "reanalysis", "variables": ["DUSMASS", "SSSMASS", "BCSMASS", "OCSMASS", "SO4SMASS"]},
    {"component": "Air temperature", "short_name": "M2T1NXSLV", "version": None, "kind": "reanalysis", "variables": ["T2M"]},
    {"component": "Relative humidity", "short_name": "M2T1NXSLV", "version": None, "kind": "derived_from_reanalysis", "variables": ["T2M", "QV2M", "PS"]},
    {"component": "Precipitation", "short_name": "GPM_3IMERGHH", "version": "07", "kind": "satellite_precipitation", "variables": ["precipitation"]},
    {"component": "Land surface temperature", "short_name": "MOD11A1", "version": "061", "kind": "satellite", "variables": ["LST_Day_1km", "LST_Night_1km"]},
    {"component": "NDVI", "short_name": "MOD13Q1", "version": "061", "kind": "satellite", "variables": ["250m_16_days_NDVI"]},
    {"component": "AOD", "short_name": "MOD04_L2", "version": "061", "kind": "satellite", "variables": ["Optical_Depth_Land_And_Ocean"]},
    {"component": "Soil moisture", "short_name": "SPL3SMP_E", "version": None, "kind": "satellite", "variables": ["soil_moisture"]},
    {"component": "Terrestrial water storage", "short_name": "TELLUS_GRAC-GRFO_MASCON_CRI_GRID_RL06.3_V4", "version": None, "kind": "satellite_gravimetry", "variables": []},
    {"component": "Night-time lights", "short_name": "VNP46A2", "version": "002", "kind": "satellite", "variables": ["DNB_BRDF-Corrected_NTL"]},
    {"component": "Elevation", "short_name": "NASADEM_HGT", "version": "001", "kind": "static_satellite_derived", "variables": ["elevation"]},
)


class EarthdataAuthError(RuntimeError):
    pass


def _window(req: AnalysisRequest) -> tuple[datetime, datetime]:
    end = req.end_time or datetime.now(timezone.utc)
    start = req.start_time or (end - timedelta(days=90))
    if start >= end:
        raise ValueError("start_time must be earlier than end_time")
    return start, end


def _bbox(lat: float, lon: float, pad: float = 0.05) -> str:
    west = max(-180.0, lon - pad)
    south = max(-90.0, lat - pad)
    east = min(180.0, lon + pad)
    north = min(90.0, lat + pad)
    return f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}"


async def _search_product(
    client: httpx.AsyncClient,
    token: str,
    product: dict[str, Any],
    req: AnalysisRequest,
) -> dict[str, Any]:
    start, end = _window(req)
    params: list[tuple[str, str]] = [
        ("short_name", product["short_name"]),
        ("bounding_box", _bbox(req.lat, req.lon)),
        ("temporal", f"{start.isoformat().replace('+00:00', 'Z')},{end.isoformat().replace('+00:00', 'Z')}"),
        ("page_size", "1"),
        ("sort_key[]", "-start_date"),
    ]
    if product.get("version"):
        params.append(("version", str(product["version"])))

    response = await client.get(
        f"{settings.nasa_cmr_base}/granules.json",
        params=params,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "Lupus-Cortex/1.0",
        },
    )
    if response.status_code in {401, 403}:
        raise EarthdataAuthError("NASA Earthdata rejected the supplied token.")
    if response.status_code >= 400:
        body = response.text[:500]
        if "Token does not exist" in body:
            raise EarthdataAuthError(
                "NASA Earthdata reports that this token does not exist or is no longer valid."
            )
        response.raise_for_status()

    data = response.json()
    entries = data.get("feed", {}).get("entry", [])
    if not entries:
        return {
            **product,
            "status": "no_granule_found",
            "query_window": {"start": start.isoformat(), "end": end.isoformat()},
        }

    entry = entries[0]
    links = [
        {"href": link.get("href"), "rel": link.get("rel"), "title": link.get("title")}
        for link in entry.get("links", [])
        if link.get("href")
    ][:12]

    return {
        **product,
        "status": "granule_found",
        "granule": {
            "id": entry.get("id"),
            "title": entry.get("title"),
            "time_start": entry.get("time_start"),
            "time_end": entry.get("time_end"),
            "updated": entry.get("updated"),
            "links": links,
        },
        "query_window": {"start": start.isoformat(), "end": end.isoformat()},
    }


async def collect_nasa_catalog(req: AnalysisRequest, token: str) -> dict[str, Any]:
    token = token.strip()
    if not token:
        raise EarthdataAuthError("An Earthdata token is required.")
    if token.lower().startswith("bearer "):
        raise EarthdataAuthError("Enter only the Earthdata token, without the word 'Bearer'.")

    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        follow_redirects=True,
    ) as client:
        tasks = [_search_product(client, token, product, req) for product in NASA_PRODUCTS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    normalized: list[dict[str, Any]] = []
    for product, result in zip(NASA_PRODUCTS, results):
        if isinstance(result, EarthdataAuthError):
            raise result
        if isinstance(result, Exception):
            normalized.append({**product, "status": "source_error", "error": str(result)})
        else:
            normalized.append(result)

    return {
        "provider": "NASA Earthdata CMR",
        "authentication": "user_token_per_request",
        "token_persisted": False,
        "location": {"lat": req.lat, "lon": req.lon},
        "products": normalized,
        "note": (
            "CMR results provide authenticated NASA granule discovery and provenance. "
            "Numeric science-variable extraction is supplied by EARTHDATA_WORKER_URL when configured."
        ),
    }


async def collect_from_worker(
    req: AnalysisRequest,
    token: str,
) -> dict[str, Any] | None:
    if not settings.earthdata_worker_url:
        return None

    async with httpx.AsyncClient(
        timeout=settings.earthdata_worker_timeout_seconds,
        follow_redirects=True,
    ) as client:
        response = await client.post(
            f"{settings.earthdata_worker_url}/collect",
            json=req.model_dump(mode="json"),
            headers={"X-Earthdata-Token": token},
        )

    if response.status_code in {401, 403}:
        raise EarthdataAuthError(
            "The Earthdata worker rejected the supplied Earthdata token."
        )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Earthdata worker returned an invalid response.")
    return data
