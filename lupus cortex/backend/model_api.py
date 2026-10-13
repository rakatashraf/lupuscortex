#!/usr/bin/env python3
"""
Lupus Cortex Model API v2 — per-component Random Forest models.
Loads lupus_rf_per_component.pkl, serves predictions and analysis reports.
Schema version: 2.0
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

BASE = Path(os.environ.get("LUPUS_BASE", "/home/hatch/workspace/lupus_cortex"))
MODEL_PATH = BASE / "models" / "lupus_rf_per_component.pkl"
INDEX_PATH = BASE / "models" / "model_index.json"
REF_PATH = BASE / "reference_dataset" / "lupus_cortex_fixed_reference_dataset.csv"

FEATURES = ["value_lag1", "value_lag3", "value_lag7", "value_roll7", "value_roll30",
            "value_lag1_missing", "value_lag3_missing", "value_lag7_missing",
            "value_roll7_missing", "value_roll30_missing",
            "doy_sin", "doy_cos", "month", "latitude", "longitude"]

# Component ID -> (name, WHO guideline, unit)
COMPONENTS = {
    "1": ("PM2.5", 15, "µg/m³"), "2": ("PM10", 45, "µg/m³"),
    "3": ("NO2", 25, "µg/m³"), "4": ("O3", 100, "µg/m³"),
    "5": ("SO2", 40, "µg/m³"), "6": ("CO", 4, "mg/m³"),
    "7": ("AOD", None, "unitless"), "8": ("LST", None, "°C"),
    "9": ("Air Temperature", None, "°C"), "10": ("Humidity", None, "%"),
    "11": ("Precipitation", None, "mm"), "12": ("NDVI", None, "index"),
    "14": ("Soil Moisture", None, "m³/m³"), "15": ("Surface Water", None, "index"),
    "16": ("Flood Risk", None, "binary"), "17": ("Drought", None, "std_dev"),
    "25": ("Nightlights", None, "nW/cm²/sr"), "28": ("Disaster Exposure", None, "index"),
    "30": ("Urban Pressure", None, "index_0_100"),
}

app = FastAPI(title="Lupus Cortex Model API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

_models: dict = {}
_model_index: dict = {}


def _load():
    global _models, _model_index
    if not _models:
        if not MODEL_PATH.exists():
            raise RuntimeError(f"Model not found: {MODEL_PATH}. "
                               f"Download lupus_rf_per_component.pkl from Drive 07_Model_Artifacts.")
        _models = joblib.load(MODEL_PATH)
    if not _model_index and INDEX_PATH.exists():
        import json
        _model_index = json.loads(INDEX_PATH.read_text())


class PredictRequest(BaseModel):
    latitude: float
    longitude: float
    date: str
    component: str = "1"
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
    date: str | None = None


def _features_for(lat: float, lon: float, date: str, recent: list[float],
                  medians: dict) -> np.ndarray:
    dt = datetime.strptime(date, "%Y-%m-%d")
    doy = dt.timetuple().tm_yday
    rv = (recent + [float("nan")] * 30)[:30]
    def _lag(i):
        v = rv[i] if i < len(rv) else float("nan")
        return v
    lag1, lag3, lag7 = _lag(0), _lag(2), _lag(6)
    past7 = [x for x in rv[:7] if x == x]
    past30 = [x for x in rv[:30] if x == x]
    roll7 = float(np.mean(past7)) if len(past7) >= 3 else float("nan")
    roll30 = float(np.mean(past30)) if len(past30) >= 7 else float("nan")
    vals = {
        "value_lag1": lag1, "value_lag3": lag3, "value_lag7": lag7,
        "value_roll7": roll7, "value_roll30": roll30,
        "value_lag1_missing": 1 if lag1 != lag1 else 0,
        "value_lag3_missing": 1 if lag3 != lag3 else 0,
        "value_lag7_missing": 1 if lag7 != lag7 else 0,
        "value_roll7_missing": 1 if roll7 != roll7 else 0,
        "value_roll30_missing": 1 if roll30 != roll30 else 0,
        "doy_sin": math.sin(2 * math.pi * doy / 365.25),
        "doy_cos": math.cos(2 * math.pi * doy / 365.25),
        "month": dt.month, "latitude": lat, "longitude": lon,
    }
    # Median imputation (from training)
    row = [vals[c] if vals[c] == vals[c] else medians.get(c, 0) for c in FEATURES]
    import pandas as pd
    return pd.DataFrame([row], columns=FEATURES)


def _condition_label(value: float, comp: str) -> tuple[str, int]:
    info = COMPONENTS.get(comp)
    if not info or info[1] is None or value is None or value != value:
        return "Unknown", 50
    _, gl, _ = info
    ratio = value / gl if gl else 1
    if ratio <= 1.0: return "Perfect", 90
    if ratio <= 1.5: return "Well managed", 70
    if ratio <= 2.5: return "Needs improvement", 50
    if ratio <= 4.0: return "Poor", 30
    return "Harmful", 10


@app.get("/health")
def health():
    _load()
    return {"status": "ok", "model": "lupus_rf_per_component",
            "components": sorted(_models.keys()),
            "schema_version": "2.0",
            "utc": datetime.now(timezone.utc).isoformat()}


@app.post("/predict")
def predict(req: PredictRequest):
    _load()
    comp = str(req.component)
    if comp not in _models:
        raise HTTPException(400, f"Component {comp} not available. Available: {sorted(_models.keys())}")
    m = _models[comp]
    X = _features_for(req.latitude, req.longitude, req.date, req.recent_values, m["medians"])
    Xs = m["scaler"].transform(X)
    pred = float(m["model"].predict(Xs)[0])
    name, gl, unit = COMPONENTS.get(comp, (comp, None, ""))
    label, score = _condition_label(pred, comp)
    return {
        "schema_version": "2.0",
        "component": comp, "component_name": name,
        "latitude": req.latitude, "longitude": req.longitude, "date": req.date,
        "predicted_value": round(pred, 4), "unit": unit,
        "guideline_value": gl, "guideline_org": "WHO 2021" if gl else None,
        "condition_label": label, "condition_score": score,
        "model_version": "lupus_rf_per_component_v2",
    }


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    _load()
    date = req.date or "2025-12-31"
    if not date.startswith("2025"):
        date = "2025-12-31"

    components: dict[str, Any] = {}
    scores = []
    for comp in ["1", "2", "3", "4", "5", "6"]:
        if comp not in _models:
            continue
        m = _models[comp]
        X = _features_for(req.lat, req.lon, date, [], m["medians"])
        pred = float(m["model"].predict(m["scaler"].transform(X))[0])
        name, gl, unit = COMPONENTS[comp]
        label, score = _condition_label(pred, comp)
        # Test R² from training
        r2 = _model_index.get("results", {}).get(comp, {}).get("test_r2")
        components[comp] = {
            "name": name, "predicted_value": round(pred, 3), "unit": unit,
            "guideline_value": gl, "guideline_org": "WHO 2021" if gl else None,
            "condition_label": label, "condition_score": score,
            "test_r2": r2,
            "reliability": "high" if r2 and r2 > 0.7 else "moderate" if r2 and r2 > 0 else "low",
        }
        scores.append(score)

    hwi = round(sum(scores) / len(scores)) if scores else 50
    hwi_label = ("Perfect" if hwi >= 81 else "Well managed" if hwi >= 61
                 else "Needs improvement" if hwi >= 41 else "Poor" if hwi >= 21 else "Harmful")

    warnings = [f"{c['name']} predicted {c['condition_label'].lower()} "
                f"({c['predicted_value']} {c['unit']} vs guideline {c['guideline_value']})"
                for c in components.values() if c["condition_score"] <= 30]
    recommendations = []
    if any(c["condition_score"] <= 50 for c in components.values()):
        recommendations.append("Limit outdoor exertion during high-pollution hours.")
    recommendations.append("Estimates from 2025 training data; verify with live monitoring.")

    return {
        "schema_version": "2.0",
        "analysis_id": f"lc-{req.lat:.4f}-{req.lon:.4f}-{date}",
        "model_version": "lupus_rf_per_component_v2",
        "location": {"lat": req.lat, "lon": req.lon, "name": req.name},
        "time": {"date": date, "generated_utc": datetime.now(timezone.utc).isoformat()},
        "data_quality": {"training_rows": 84431, "components": len(_models),
                         "note": "Per-component RF models; leakage-free training"},
        "components": components,
        "hwi": {"score": hwi, "label": hwi_label},
        "analysis": {"summary": f"Healthy-city proxy {hwi}/100 ({hwi_label}) at {req.name} on {date}."},
        "warnings": warnings,
        "recommendations": recommendations,
        "provenance": {
            "model": "Per-component RandomForestRegressor (200 trees)",
            "training_period": "2025-01-01 to 2025-12-31",
            "region": "Central Bangladesh bbox",
            "leakage_controls": "Shifted lags/rolling, chronological splits, train-only scaling",
            "reference": "WHO global air quality guidelines 2021",
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100)
