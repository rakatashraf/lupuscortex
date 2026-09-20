from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class ModelNotConfigured(RuntimeError):
    pass


class ModelResponseError(RuntimeError):
    pass


async def analyze_with_model(
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not settings.model_api_url:
        raise ModelNotConfigured(
            "Environmental evidence was collected, but MODEL_API_URL is not "
            "configured yet. Connect the trained Lupus Cortex model service "
            "before requesting a final analysis report."
        )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if settings.model_api_key:
        headers["Authorization"] = f"Bearer {settings.model_api_key}"

    async with httpx.AsyncClient(
        timeout=180,
        follow_redirects=True,
    ) as client:
        response = await client.post(
            settings.model_api_url,
            json=payload,
            headers=headers,
        )

    if response.status_code >= 400:
        raise ModelResponseError(
            f"Model service returned HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    report = response.json()
    if not isinstance(report, dict):
        raise ModelResponseError(
            "Model service returned a non-object response."
        )
    if report.get("schema_version") != "1.0":
        raise ModelResponseError(
            "Model report must use Lupus Cortex schema_version 1.0."
        )

    required = {
        "analysis_id",
        "model_version",
        "location",
        "time",
        "data_quality",
        "components",
        "indices",
        "hwi",
        "analysis",
        "history",
        "forecasts",
        "warnings",
        "recommendations",
        "provenance",
    }
    missing = sorted(required.difference(report.keys()))
    if missing:
        raise ModelResponseError(
            "Model report is missing required fields: " + ", ".join(missing)
        )
    return report
