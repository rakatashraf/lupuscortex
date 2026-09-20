from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.collectors.nasa import EarthdataAuthError
from app.config import settings
from app.schemas import AnalysisRequest
from app.services.collector import collect_evidence
from app.services.model_gateway import (
    ModelNotConfigured,
    ModelResponseError,
    analyze_with_model,
)


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-Earthdata-Token",
        ],
    )


def _token(value: str | None) -> str:
    token = (value or "").strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail=(
                "NASA Earthdata token is required for this analysis request."
            ),
        )
    if token.lower().startswith("bearer "):
        raise HTTPException(
            status_code=400,
            detail=(
                "Send only the Earthdata token, without the word 'Bearer'."
            ),
        )
    return token


async def _collect(
    req: AnalysisRequest,
    token: str,
) -> dict:
    try:
        return await collect_evidence(req, token)
    except EarthdataAuthError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Evidence collection failed: {exc}",
        ) from exc


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "model_connected": bool(settings.model_api_url),
        "earthdata_worker_connected": bool(
            settings.earthdata_worker_url
        ),
    }


@app.get("/api/status")
async def status() -> dict:
    return {
        "api": "ready",
        "model_connected": bool(settings.model_api_url),
        "earthdata_worker_configured": bool(
            settings.earthdata_worker_url
        ),
        "earthdata_auth_mode": "per-request user token",
        "token_storage": "none",
    }


@app.post("/api/collect")
async def collect(
    req: AnalysisRequest,
    x_earthdata_token: Annotated[
        str | None,
        Header(alias="X-Earthdata-Token"),
    ] = None,
) -> dict:
    return await _collect(
        req,
        _token(x_earthdata_token),
    )


async def _analyze(
    req: AnalysisRequest,
    token: str,
    scenario: bool,
) -> dict:
    evidence = await _collect(req, token)

    model_payload = {
        "schema_version": "1.0",
        "request": req.model_dump(mode="json"),
        "collection_id": evidence["collection_id"],
        "evidence": evidence,
        "scenario": req.scenario if scenario else None,
    }

    try:
        return await analyze_with_model(model_payload)
    except ModelNotConfigured as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": str(exc),
                "collection_id": evidence["collection_id"],
                "evidence_collected": True,
            },
        ) from exc
    except ModelResponseError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc


@app.post("/api/analyze")
async def analyze(
    req: AnalysisRequest,
    x_earthdata_token: Annotated[
        str | None,
        Header(alias="X-Earthdata-Token"),
    ] = None,
) -> dict:
    return await _analyze(
        req,
        _token(x_earthdata_token),
        scenario=False,
    )


@app.post("/api/scenario")
async def scenario(
    req: AnalysisRequest,
    x_earthdata_token: Annotated[
        str | None,
        Header(alias="X-Earthdata-Token"),
    ] = None,
) -> dict:
    if not req.scenario:
        raise HTTPException(
            status_code=400,
            detail="Scenario details are required.",
        )
    return await _analyze(
        req,
        _token(x_earthdata_token),
        scenario=True,
    )


frontend = Path(settings.frontend_dir)
if frontend.exists():
    app.mount(
        "/",
        StaticFiles(
            directory=str(frontend),
            html=True,
        ),
        name="frontend",
    )
