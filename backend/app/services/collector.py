from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from app.collectors.free_sources import collect_free_sources
from app.collectors.nasa import collect_from_worker, collect_nasa_catalog
from app.schemas import AnalysisRequest


async def collect_evidence(
    req: AnalysisRequest,
    earthdata_token: str,
) -> dict[str, Any]:
    nasa_catalog_task = collect_nasa_catalog(req, earthdata_token)
    free_task = collect_free_sources(req)
    nasa_catalog, free_sources = await asyncio.gather(
        nasa_catalog_task,
        free_task,
    )

    worker_data = None
    worker_error = None
    try:
        worker_data = await collect_from_worker(req, earthdata_token)
    except Exception as exc:
        worker_error = str(exc)

    return {
        "collection_id": str(uuid.uuid4()),
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "location": {
            "name": req.name,
            "lat": req.lat,
            "lon": req.lon,
        },
        "nasa": {
            "catalog": nasa_catalog,
            "extracted_values": worker_data,
            "worker_status": (
                "connected"
                if worker_data is not None
                else ("error" if worker_error else "not_configured")
            ),
            "worker_error": worker_error,
        },
        "free_sources": free_sources,
        "security": {
            "earthdata_token_persisted": False,
            "earthdata_token_forwarded_to_model": False,
        },
    }
