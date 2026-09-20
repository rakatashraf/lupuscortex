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


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_z(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _window(req: AnalysisRequest) -> tuple[datetime, datetime]:
    end = _utc(req.end_time) if req.end_time else datetime.now(timezone.utc)
    start = _utc(req.start_time) if req.start_time else (end - timedelta(days=90))
    if start >= end:
        raise ValueError("start_time must be earlier than end_time")
    return start, end


def _bbox(lat: float, lon: float, pad: float = 0.05) -> str:
    west = max(-180.0, lon - pad)
    south = max(-90.0, lat - pad)
    east = min(180.0, lon + pad)
    north = min(90.0, lat + pad)
    return f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}"


async def _cmr_search(
    client: httpx.AsyncClient,
    token: str,
    product: dict[str, Any],
    req: AnalysisRequest,
    *,
    temporal: str,
    version: str | None,
    page_size: int = 1,
) -> list[dict[str, Any]]:
    params: list[tuple[str, str]] = [
        ("short_name", product["short_name"]),
        ("bounding_box", _bbox(req.lat, req.lon)),
        ("temporal", temporal),
        ("page_size", str(page_size)),
        ("sort_key[]", "-start_date"),
    ]
    if version:
        params.append(("version", version))

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

    return response.json().get("feed", {}).get("entry", [])


def _result_from_entry(
    product: dict[str, Any],
    entry: dict[str, Any],
    *,
    start: datetime,
    end: datetime,
    selection_mode: str,
    searched_version: str | None,
) -> dict[str, Any]:
    links = [
        {"href": link.get("href"), "rel": link.get("rel"), "title": link.get("title")}
        for link in entry.get("links", [])
        if link.get("href")
    ][:12]

    source_time = _parse_time(entry.get("time_end")) or _parse_time(entry.get("time_start"))
    lag_seconds = None
    if source_time is not None:
        lag_seconds = max(0.0, (end - source_time).total_seconds())

    return {
        **product,
        "status": "granule_found",
        "selection_mode": selection_mode,
        "fallback_used": selection_mode.startswith("latest_before_due_date"),
        "requested_window": {
            "start": _iso_z(start),
            "end": _iso_z(end),
        },
        "requested_due_date": _iso_z(end),
        "searched_version": searched_version,
        "resolved_version": entry.get("version_id") or entry.get("version"),
        "source_timestamp": _iso_z(source_time) if source_time else None,
        "source_lag_seconds": lag_seconds,
        "source_lag_days": (lag_seconds / 86400.0) if lag_seconds is not None else None,
        "granule": {
            "id": entry.get("id"),
            "title": entry.get("title"),
            "time_start": entry.get("time_start"),
            "time_end": entry.get("time_end"),
            "updated": entry.get("updated"),
            "links": links,
        },
    }


async def _search_product(
    client: httpx.AsyncClient,
    token: str,
    product: dict[str, Any],
    req: AnalysisRequest,
) -> dict[str, Any]:
    start, end = _window(req)
    requested_version = str(product["version"]) if product.get("version") else None
    exact_temporal = f"{_iso_z(start)},{_iso_z(end)}"

    # 1) Prefer data inside the requested window using the configured product version.
    entries = await _cmr_search(
        client,
        token,
        product,
        req,
        temporal=exact_temporal,
        version=requested_version,
    )
    if entries:
        return _result_from_entry(
            product,
            entries[0],
            start=start,
            end=end,
            selection_mode="requested_window",
            searched_version=requested_version,
        )

    # 2) If a pinned version has no data for the requested window, keep the requested
    #    date as the priority and allow another available version of the same product.
    if requested_version:
        entries = await _cmr_search(
            client,
            token,
            product,
            req,
            temporal=exact_temporal,
            version=None,
        )
        if entries:
            return _result_from_entry(
                product,
                entries[0],
                start=start,
                end=end,
                selection_mode="requested_window_version_fallback",
                searched_version=None,
            )

    # 3) Requested-date data is unavailable. Search the open-ended interval ending
    #    at the due date and sort newest-first, so the selected granule is the most
    #    recent observation available at or before that date. This avoids using data
    #    from after a historical due date and prevents temporal leakage.
    fallback_temporal = f",{_iso_z(end)}"
    entries = await _cmr_search(
        client,
        token,
        product,
        req,
        temporal=fallback_temporal,
        version=requested_version,
    )
    if entries:
        return _result_from_entry(
            product,
            entries[0],
            start=start,
            end=end,
            selection_mode="latest_before_due_date",
            searched_version=requested_version,
        )

    # 4) Final catalog fallback: same product short name, any available version,
    #    still restricted to observations at or before the due date.
    if requested_version:
        entries = await _cmr_search(
            client,
            token,
            product,
            req,
            temporal=fallback_temporal,
            version=None,
        )
        if entries:
            return _result_from_entry(
                product,
                entries[0],
                start=start,
                end=end,
                selection_mode="latest_before_due_date_version_fallback",
                searched_version=None,
            )

    return {
        **product,
        "status": "no_granule_found",
        "selection_mode": "no_data_at_or_before_due_date",
        "fallback_used": True,
        "requested_window": {
            "start": _iso_z(start),
            "end": _iso_z(end),
        },
        "requested_due_date": _iso_z(end),
        "note": (
            "No matching granule was found in the requested window or in the "
            "available record at or before the due date."
        ),
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
        "date_selection_policy": {
            "primary": "requested_window",
            "fallback": "most_recent_available_at_or_before_due_date",
            "future_data_allowed_for_historical_due_date": False,
        },
        "products": normalized,
        "note": (
            "CMR results provide authenticated NASA granule discovery and provenance. "
            "If requested-date data is unavailable, each product falls back to the "
            "most recent granule available at or before the requested due date. "
            "Numeric science-variable extraction is supplied by EARTHDATA_WORKER_URL "
            "when configured."
        ),
    }


async def collect_from_worker(
    req: AnalysisRequest,
    token: str,
) -> dict[str, Any] | None:
    if not settings.earthdata_worker_url:
        return None

    worker_request = req.model_dump(mode="json")
    worker_request["date_selection_policy"] = {
        "primary": "requested_window",
        "fallback": "most_recent_available_at_or_before_due_date",
        "future_data_allowed_for_historical_due_date": False,
        "preserve_source_timestamp": True,
        "report_source_lag": True,
    }

    async with httpx.AsyncClient(
        timeout=settings.earthdata_worker_timeout_seconds,
        follow_redirects=True,
    ) as client:
        response = await client.post(
            f"{settings.earthdata_worker_url}/collect",
            json=worker_request,
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
