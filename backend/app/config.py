from __future__ import annotations

import os
from dataclasses import dataclass


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    app_name: str = "Lupus Cortex API"
    model_api_url: str = os.getenv("MODEL_API_URL", "").strip()
    model_api_key: str = os.getenv("MODEL_API_KEY", "").strip()
    nasa_cmr_base: str = os.getenv(
        "NASA_CMR_BASE",
        "https://cmr.earthdata.nasa.gov/search",
    ).rstrip("/")
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
    free_data_radius_m: int = int(os.getenv("FREE_DATA_RADIUS_M", "5000"))
    cors_origins: tuple[str, ...] = tuple(_csv_env("CORS_ORIGINS"))
    frontend_dir: str = os.getenv("FRONTEND_DIR", "/app/frontend")
    earthdata_worker_url: str = os.getenv("EARTHDATA_WORKER_URL", "").strip().rstrip("/")
    earthdata_worker_timeout_seconds: float = float(
        os.getenv("EARTHDATA_WORKER_TIMEOUT_SECONDS", "180")
    )


settings = Settings()
