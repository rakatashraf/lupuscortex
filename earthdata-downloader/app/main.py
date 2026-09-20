from __future__ import annotations

import asyncio
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .cmr import CMRClient
from .convert import combine_frames, convert_file
from .external import fetch as fetch_external
from .external import resolve as resolve_external


ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

app = FastAPI(
    title="NASA Earthdata CSV Downloader",
    version="1.1.0",
    description="Search NASA Earthdata, download matching granules, convert supported science formats to CSV, and fall back to selected public internet sources when NASA has no matching collection.",
)


class BBox(BaseModel):
    south: float = Field(ge=-90, le=90)
    west: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)

    def cmr(self) -> str:
        if self.south >= self.north:
            raise ValueError("South must be lower than north.")
        if self.west >= self.east:
            raise ValueError("West must be lower than east.")
        return f"{self.west},{self.south},{self.east},{self.north}"

    def dict4(self) -> dict[str, float]:
        return {
            "south": self.south,
            "west": self.west,
            "north": self.north,
            "east": self.east,
        }


class DateRange(BaseModel):
    start: date
    end: date


class TokenRequest(BaseModel):
    token: str


class CollectionRequest(BaseModel):
    token: str
    component: str
    bbox: Optional[BBox] = None
    platforms: list[str] = []
    instruments: list[str] = []
    page_size: int = 50


class GranuleRequest(BaseModel):
    token: str
    collection_id: str
    bbox: BBox
    date_range: DateRange
    platform: Optional[str] = None
    instrument: Optional[str] = None
    max_granules: int = 10
    fallback_latest: bool = True


class DownloadRequest(GranuleRequest):
    component: str
    collection_title: Optional[str] = None
    variable_filters: list[str] = []
    output_name: Optional[str] = None
    max_rows_per_variable: int = 0


class ExternalRequest(BaseModel):
    component: str
    bbox: BBox
    date_range: DateRange
    grid_points_per_axis: int = 3
    output_name: Optional[str] = None


def _safe_csv(name: Optional[str], fallback: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9._-]+", "_", name or fallback).strip("._")
    if not raw.lower().endswith(".csv"):
        raw += ".csv"
    return raw[:180] or "earthdata.csv"


def _session(token: str) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "Authorization": f"Bearer {token.strip()}",
        "User-Agent": "EarthdataCSVDownloader/1.1",
    })
    return session


def _filename(url: str, response: requests.Response, idx: int) -> str:
    cd = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", cd, flags=re.I)
    if match:
        name = unquote(match.group(1).strip().strip('"'))
    else:
        name = unquote(Path(urlparse(response.url or url).path).name or Path(urlparse(url).path).name)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or f"granule_{idx}"
    if "." not in Path(name).name:
        ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
        ext = {
            "application/x-netcdf": ".nc",
            "application/netcdf": ".nc",
            "application/x-hdf": ".hdf",
            "application/x-hdf5": ".h5",
            "image/tiff": ".tif",
            "text/csv": ".csv",
            "application/json": ".json",
            "application/zip": ".zip",
            "application/gzip": ".gz",
        }.get(ctype, "")
        name += ext
    return name[:180]


def _download_convert(req: DownloadRequest, granules: list[dict]) -> tuple[bytes, dict]:
    frames = []
    errors: list[dict[str, str]] = []
    session = _session(req.token)

    with tempfile.TemporaryDirectory(prefix="earthdata_") as td:
        work = Path(td)
        for idx, granule in enumerate(granules, 1):
            urls = granule.get("download_urls") or []
            if not urls and granule.get("primary_url"):
                urls = [granule["primary_url"]]
            if not urls:
                errors.append({"granule": str(granule.get("granule_ur")), "error": "No downloadable URL in CMR metadata."})
                continue

            success = False
            last_error = ""
            for url in urls:
                try:
                    with session.get(url, stream=True, timeout=(25, 240), allow_redirects=True) as response:
                        response.raise_for_status()
                        path = work / _filename(url, response, idx)
                        with path.open("wb") as handle:
                            for chunk in response.iter_content(chunk_size=1024 * 1024):
                                if chunk:
                                    handle.write(chunk)

                    meta = {
                        "source": "NASA Earthdata",
                        "component_query": req.component,
                        "collection_id": req.collection_id,
                        "collection_title": req.collection_title or "",
                        "granule_id": granule.get("concept_id") or "",
                        "granule_ur": granule.get("granule_ur") or "",
                        "satellite_platform": ";".join(granule.get("platforms") or []),
                        "instrument": ";".join(granule.get("instruments") or []),
                        "granule_begin": granule.get("begin") or "",
                        "granule_end": granule.get("end") or "",
                        "original_file": path.name,
                        "download_url": url,
                    }
                    converted = convert_file(
                        path,
                        meta=meta,
                        filters=req.variable_filters,
                        bbox=req.bbox.dict4(),
                        max_rows=max(0, int(req.max_rows_per_variable)),
                    )
                    frames.extend(converted)
                    success = True
                    break
                except Exception as exc:
                    last_error = str(exc)

            if not success:
                errors.append({"granule": str(granule.get("granule_ur")), "error": last_error or "Download/conversion failed."})

    combined = combine_frames(frames)
    if combined.empty:
        details = "; ".join(x["error"] for x in errors[:3])
        raise ValueError("NASA returned granules, but none could be converted to CSV. " + details)

    return combined.to_csv(index=False).encode("utf-8"), {
        "rows": len(combined),
        "converted_frames": len(frames),
        "errors": errors,
    }


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "earthdata-csv-downloader", "version": "1.1.0"}


@app.post("/api/token/validate")
async def token_validate(req: TokenRequest):
    if not req.token.strip():
        raise HTTPException(400, "Earthdata token is required.")
    try:
        return await CMRClient(req.token).validate()
    except Exception as exc:
        raise HTTPException(502, f"Could not reach NASA CMR: {exc}")


@app.post("/api/collections/search")
async def collections_search(req: CollectionRequest):
    if not req.component.strip():
        raise HTTPException(400, "Component is required.")
    try:
        nasa = await CMRClient(req.token).collections(
            component=req.component,
            bbox=req.bbox.cmr() if req.bbox else None,
            platforms=req.platforms,
            instruments=req.instruments,
            page_size=req.page_size,
        )
        external = resolve_external(req.component)
        return {
            "component": req.component,
            "nasa": nasa,
            "external_candidates": external if not nasa.get("items") else [],
            "external_candidates_always": external,
        }
    except Exception as exc:
        raise HTTPException(502, f"NASA collection search failed: {exc}")


@app.post("/api/granules/search")
async def granules_search(req: GranuleRequest):
    if req.date_range.start > req.date_range.end:
        raise HTTPException(400, "Start date must be on or before end date.")
    try:
        return await CMRClient(req.token).granules(
            collection_id=req.collection_id,
            bbox=req.bbox.cmr(),
            start_date=req.date_range.start.isoformat(),
            end_date=req.date_range.end.isoformat(),
            platform=req.platform,
            instrument=req.instrument,
            max_granules=req.max_granules,
            fallback_latest=req.fallback_latest,
        )
    except Exception as exc:
        raise HTTPException(502, f"NASA granule search failed: {exc}")


@app.post("/api/download/nasa")
async def download_nasa(req: DownloadRequest):
    try:
        result = await CMRClient(req.token).granules(
            collection_id=req.collection_id,
            bbox=req.bbox.cmr(),
            start_date=req.date_range.start.isoformat(),
            end_date=req.date_range.end.isoformat(),
            platform=req.platform,
            instrument=req.instrument,
            max_granules=req.max_granules,
            fallback_latest=req.fallback_latest,
        )
        granules = result.get("items") or []
        if not granules:
            raise HTTPException(404, "No downloadable granules were found for this collection and area.")

        content, report = await asyncio.to_thread(_download_convert, req, granules)
        name = _safe_csv(req.output_name, f"{req.component}_{req.collection_id}.csv")
        return Response(
            content=content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "X-Earthdata-Fallback-Used": str(bool(result.get("fallback_used"))).lower(),
                "X-Earthdata-Rows": str(report["rows"]),
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Download/conversion failed: {exc}")


@app.post("/api/download/external/{provider_id}")
async def download_external(provider_id: str, req: ExternalRequest):
    try:
        df = await asyncio.to_thread(
            fetch_external,
            req.component,
            provider_id,
            req.bbox.dict4(),
            req.date_range.start,
            req.date_range.end,
            req.grid_points_per_axis,
        )
        if df.empty:
            raise HTTPException(404, "The external provider returned no records.")
        name = _safe_csv(req.output_name, f"{req.component}_{provider_id}.csv")
        return Response(
            content=df.to_csv(index=False).encode("utf-8"),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "X-Earthdata-Rows": str(len(df)),
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"External data fetch failed: {exc}")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
