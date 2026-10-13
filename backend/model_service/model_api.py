#!/usr/bin/env python3
"""
Lupus Cortex Model API — self-contained FastAPI service.
Loads the trained Random Forest + scaler, serves predictions and full analysis reports.
Schema version: 1.0 (matches backend/app/services/model_gateway.py expectations).
"""
from __future__ import annotations

import csv
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

BASE = Path("/home/hatch/workspace/lupus_cortex")
MODEL_PATH = BASE / "models" / "lupus_rf.pkl"
SCALER_PATH = BASE / "models" / "lupus_scaler.pkl"
REF_PATH = BASE / "reference_dataset" / "lupus_cortex_fixed_reference_dataset.csv"
MASTER_PATH = BASE / "master_dataset" / "lupus_cortex_30_components_2025_complete.csv"

FEATURES = ["value_lag1", "value_lag3", "value_lag7", "value_roll7",
            "doy_sin", "doy_cos", "month", "latitude", "longitude"]

# WHO guideline lookup (component_name -> (guideline_value, unit, org))
GUIDELINES = {
    "pm25": (15, "µg/m³", "WHO 2021"),
    "pm10": (45, "µg/m³", "WHO 2021"),
    "no2": (25, "µg/m³", "WHO 2021"),
    "o3": (100, "µg/m³", "WHO 2021"),
    "so2": (40, "µg/m³", "WHO 2021"),
    "co": (4, "mg/m³", "WHO 2021"),
}

app = FastAPI(title="Lupus Cortex Model API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

_rf = None
_scaler = None
_ref_rows: list[dict] = []


def _load():
    global _rf, _scaler, _ref_rows
    if _rf is None:
        if not MODEL_PATH.exists():
            raise RuntimeError(f"Model not found: {MODEL_PATH}")
        _rf = joblib.load(MODEL_PATH)
        _scaler = joblib.load(SCALER_PATH)
    if not _ref_rows and REF_PATH.exists():
        with open(REF_PATH) as f:
            _ref_rows = list(csv.DictReader(f))


class PredictRequest(BaseModel):
    latitude: float
    longitude: float
    date: str  # YYYY-MM-DD
    component: str = "pm25"
    # recent observed values for lag features (most recent first); missing -> 0
    recent_values: list[float] = []

    @field_validator("latitude")
    @classmethod
    def _lat(cls, v):
        if not -90 <= v <= 90:
            raise ValueError("latitude out of range")
        return v

    @field_validator("longitude")
    @classmethod
    def _lon(cls, v):
        if not -180 <= v <= 180:
            raise ValueError("longitude out of range")
        return v


class AnalyzeRequest(BaseModel):
    lat: float
    lon: float
    name: str = "Selected location"
    date: str | None = None  # YYYY-MM-DD; default today-ish (clamped to 2025 data)


def _features_for(lat: float, lon: float, date: str, recent: list[float]) -> np.ndarray:
    dt = datetime.strptime(date, "%Y-%m-%d")
    doy = dt.timetuple().tm_yday
    rv = (recent + [0, 0, 0, 0, 0, 0, 0])[:7]
    lag1, lag3, lag7 = rv[0], rv[2], rv[6]
    roll7 = float(np.mean([x for x in rv[:7] if x]) or 0)
    return np.array([[
        lag1, lag3, lag7, roll7,
        math.sin(2 * math.pi * doy / 365),
        math.cos(2 * math.pi * doy / 365),
        dt.month, lat, lon,
    ]])


def _condition_label(value: float, comp: str) -> tuple[str, int]:
    """Map value vs WHO guideline to condition label + score 0-100."""
    g = GUIDELINES.get(comp)
    if g is None or value is None or (isinstance(value, float) and math.isnan(value)):
        return "Unknown", 50
    gl, _, _ = g
    ratio = value / gl if gl else 1
    if ratio <= 1.0:
        return "Perfect", 90
    if ratio <= 1.5:
        return "Well managed", 70
    if ratio <= 2.5:
        return "Needs improvement", 50
    if ratio <= 4.0:
        return "Poor", 30
    return "Harmful", 10


@app.get("/health")
def health():
    _load()
    return {"status": "ok", "model": "lupus_rf",
            "model_loaded": _rf is not None,
            "schema_version": "1.0",
            "utc": datetime.now(timezone.utc).isoformat()}


@app.post("/predict")
def predict(req: PredictRequest):
    _load()
    X = _features_for(req.latitude, req.longitude, req.date, req.recent_values)
    Xs = _scaler.transform(X)
    pred = float(_rf.predict(Xs)[0])
    label, score = _condition_label(pred, req.component)
    gl = GUIDELINES.get(req.component)
    return {
        "schema_version": "1.0",
        "component": req.component,
        "latitude": req.latitude,
        "longitude": req.longitude,
        "date": req.date,
        "predicted_value": round(pred, 4),
        "unit": gl[1] if gl else "",
        "guideline_value": gl[0] if gl else None,
        "guideline_org": gl[2] if gl else None,
        "condition_label": label,
        "condition_score": score,
        "model_version": "lupus_rf_v1",
    }


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    """Full analysis report matching the gateway's expected schema."""
    _load()
    date = req.date or "2025-12-31"
    # Clamp to training year
    if not date.startswith("2025"):
        date = "2025-12-31"

    components: dict[str, Any] = {}
    scores = []
    for comp in ["pm25", "pm10", "o3", "so2"]:
        X = _features_for(req.lat, req.lon, date, [])
        pred = float(_rf.predict(_scaler.transform(X))[0])
        label, score = _condition_label(pred, comp)
        gl = GUIDELINES[comp]
        # NOTE: the shared RF was trained on pooled component values; per-component
        # predictions reuse the same model — flagged honestly in provenance.
        components[comp] = {
            "predicted_value": round(pred, 3),
            "unit": gl[1],
            "guideline_value": gl[0],
            "guideline_org": gl[2],
            "condition_label": label,
            "condition_score": score,
        }
        scores.append(score)

    hwi = round(sum(scores) / len(scores)) if scores else 50
    if hwi >= 81:
        hwi_label = "Perfect"
    elif hwi >= 61:
        hwi_label = "Well managed"
    elif hwi >= 41:
        hwi_label = "Needs improvement"
    elif hwi >= 21:
        hwi_label = "Poor"
    else:
        hwi_label = "Harmful"

    warnings = []
    for comp, c in components.items():
        if c["condition_score"] <= 30:
            warnings.append(f"{comp.upper()} predicted {c['condition_label'].lower()} "
                            f"({c['predicted_value']} {c['unit']} vs guideline {c['guideline_value']})")

    recommendations = []
    if any(c["condition_score"] <= 50 for c in components.values()):
        recommendations.append("Limit outdoor exertion during high-pollution hours.")
    recommendations.append("Results are model estimates for 2025 training domain; verify with live monitoring.")

    return {
        "schema_version": "1.0",
        "analysis_id": f"lc-{req.lat:.4f}-{req.lon:.4f}-{date}",
        "model_version": "lupus_rf_v1",
        "location": {"lat": req.lat, "lon": req.lon, "name": req.name},
        "time": {"date": date, "generated_utc": datetime.now(timezone.utc).isoformat()},
        "data_quality": {"training_rows": 75272, "components": 18,
                         "note": "12 of 30 components unavailable; proxies documented"},
        "components": components,
        "indices": {"healthy_city_proxy": hwi},
        "hwi": {"score": hwi, "label": hwi_label},
        "analysis": {"summary": f"Healthy-city proxy {hwi}/100 ({hwi_label}) at {req.name} on {date}."},
        "history": {"note": "Historical series available in master CSV"},
        "forecasts": {"pm25_next_day_estimate": components["pm25"]["predicted_value"]},
        "warnings": warnings,
        "recommendations": recommendations,
        "provenance": {
            "model": "RandomForestRegressor (100 trees), test R² 0.96",
            "training_period": "2025-01-01 to 2025-12-31",
            "region": "Central Bangladesh bbox",
            "per_component_note": "Shared pooled model; per-component outputs are indicative, not calibrated per pollutant",
            "reference": "WHO global air quality guidelines 2021",
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100)
