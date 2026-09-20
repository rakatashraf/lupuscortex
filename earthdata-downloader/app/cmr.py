from __future__ import annotations

from typing import Any, Optional

import httpx

CMR = "https://cmr.earthdata.nasa.gov/search"


class CMRClient:
    def __init__(self, token: str):
        self.token = token.strip()
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Client-Id": "earthdata-csv-downloader",
            "User-Agent": "EarthdataCSVDownloader/1.1",
        }

    async def _get(self, path: str, params: list[tuple[str, str]]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(f"{CMR}/{path}", params=params, headers=self.headers)
        r.raise_for_status()
        return r.json()

    async def validate(self) -> dict[str, Any]:
        try:
            data = await self._get(
                "collections.umm_json",
                [("page_size", "1"), ("has_granules", "true")],
            )
            return {"valid": True, "hits": data.get("hits", 0)}
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            return {
                "valid": False,
                "status": code,
                "message": "Earthdata rejected the token." if code in (401, 403) else f"NASA CMR returned HTTP {code}.",
            }

    @staticmethod
    def _platforms(umm: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for p in umm.get("Platforms") or []:
            n = p.get("ShortName") or p.get("LongName")
            if n and n not in out:
                out.append(n)
        return out

    @staticmethod
    def _instruments(umm: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for p in umm.get("Platforms") or []:
            for i in p.get("Instruments") or []:
                n = i.get("ShortName") or i.get("LongName")
                if n and n not in out:
                    out.append(n)
        return out

    @staticmethod
    def _collection_time(umm: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        starts: list[str] = []
        ends: list[str] = []
        for extent in umm.get("TemporalExtents") or []:
            for r in extent.get("RangeDateTimes") or []:
                if r.get("BeginningDateTime"):
                    starts.append(r["BeginningDateTime"])
                if r.get("EndingDateTime"):
                    ends.append(r["EndingDateTime"])
            for s in extent.get("SingleDateTimes") or []:
                if s:
                    starts.append(s)
                    ends.append(s)
        return (min(starts) if starts else None, max(ends) if ends else None)

    async def collections(
        self,
        component: str,
        bbox: Optional[str] = None,
        platforms: Optional[list[str]] = None,
        instruments: Optional[list[str]] = None,
        page_size: int = 50,
    ) -> dict[str, Any]:
        params: list[tuple[str, str]] = [
            ("keyword", component.strip()),
            ("has_granules", "true"),
            ("page_size", str(max(1, min(page_size, 100)))),
        ]
        if bbox:
            params.append(("bounding_box", bbox))
        for p in platforms or []:
            if p.strip():
                params.append(("platform[]", p.strip()))
        for i in instruments or []:
            if i.strip():
                params.append(("instrument[]", i.strip()))

        data = await self._get("collections.umm_json", params)
        items: list[dict[str, Any]] = []
        for entry in data.get("items") or []:
            meta = entry.get("meta") or {}
            umm = entry.get("umm") or {}
            begin, end = self._collection_time(umm)
            items.append(
                {
                    "concept_id": meta.get("concept-id"),
                    "provider": meta.get("provider-id"),
                    "short_name": umm.get("ShortName"),
                    "version": umm.get("Version"),
                    "title": umm.get("EntryTitle") or umm.get("ShortName"),
                    "abstract": umm.get("Abstract") or umm.get("Purpose") or "",
                    "platforms": self._platforms(umm),
                    "instruments": self._instruments(umm),
                    "temporal_start": begin,
                    "temporal_end": end,
                    "processing_level": (umm.get("ProcessingLevel") or {}).get("Id"),
                    "cloud_hosted": bool(meta.get("cloud-hosted")),
                }
            )
        return {"hits": data.get("hits", len(items)), "items": items}

    @staticmethod
    def _download_urls(umm: dict[str, Any]) -> list[str]:
        scored: list[tuple[int, str]] = []
        for item in umm.get("RelatedUrls") or []:
            url = str(item.get("URL") or "")
            if not url.lower().startswith(("http://", "https://")):
                continue
            typ = str(item.get("Type") or "").upper()
            subtype = str(item.get("Subtype") or "").upper()
            desc = str(item.get("Description") or "").upper()
            if "GET DATA" not in typ and "DOWNLOAD" not in subtype and "DOWNLOAD" not in desc:
                continue
            lower = url.lower()
            if any(x in lower for x in ("opendap", "dods", "metadata", ".xml")):
                score = 5
            elif any(lower.split("?")[0].endswith(ext) for ext in (
                ".nc", ".nc4", ".cdf", ".h5", ".hdf5", ".he5", ".hdf",
                ".tif", ".tiff", ".csv", ".tsv", ".json", ".geojson", ".zip", ".gz"
            )):
                score = 0
            else:
                score = 2
            scored.append((score, url))
        out: list[str] = []
        seen: set[str] = set()
        for _, url in sorted(scored, key=lambda x: x[0]):
            if url not in seen:
                seen.add(url)
                out.append(url)
        return out

    async def granules(
        self,
        collection_id: str,
        bbox: str,
        start_date: str,
        end_date: str,
        platform: Optional[str] = None,
        instrument: Optional[str] = None,
        max_granules: int = 10,
        fallback_latest: bool = True,
    ) -> dict[str, Any]:
        max_granules = max(1, min(int(max_granules), 50))
        base: list[tuple[str, str]] = [
            ("collection_concept_id", collection_id),
            ("bounding_box", bbox),
            ("downloadable", "true"),
            ("page_size", str(max_granules)),
        ]
        if platform:
            base.append(("platform[]", platform))
        if instrument:
            base.append(("instrument[]", instrument))

        temporal = f"{start_date}T00:00:00Z,{end_date}T23:59:59Z"
        data = await self._get(
            "granules.umm_json",
            base + [("temporal", temporal), ("sort_key[]", "+start_date")],
        )
        raw = data.get("items") or []
        fallback_used = False

        if not raw and fallback_latest:
            data = await self._get(
                "granules.umm_json",
                base + [("sort_key[]", "-start_date")],
            )
            raw = data.get("items") or []
            fallback_used = bool(raw)

        items: list[dict[str, Any]] = []
        for entry in raw[:max_granules]:
            meta = entry.get("meta") or {}
            umm = entry.get("umm") or {}
            extent = (umm.get("TemporalExtent") or {}).get("RangeDateTime") or {}
            urls = self._download_urls(umm)
            dg = umm.get("DataGranule") or {}
            items.append(
                {
                    "concept_id": meta.get("concept-id"),
                    "granule_ur": umm.get("GranuleUR"),
                    "begin": extent.get("BeginningDateTime"),
                    "end": extent.get("EndingDateTime"),
                    "production_date": dg.get("ProductionDateTime"),
                    "size_mb": dg.get("SizeMBDataGranule"),
                    "platforms": self._platforms(umm),
                    "instruments": self._instruments(umm),
                    "download_urls": urls,
                    "primary_url": urls[0] if urls else None,
                }
            )
        return {
            "hits": data.get("hits", len(items)),
            "items": items,
            "fallback_used": fallback_used,
            "fallback_reason": "No data matched the requested dates, so the newest available granules for the same collection and area were returned." if fallback_used else None,
        }
