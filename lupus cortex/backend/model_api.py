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


# ============================================================
# DASHBOARD ENDPOINT — 9 framework indices + full rich report
# ============================================================

def _predict_component(comp: str, lat: float, lon: float, date: str) -> float | None:
    """Predict one component value, or None if model unavailable."""
    if comp not in _models:
        return None
    m = _models[comp]
    X = _features_for(lat, lon, date, [], m["medians"])
    try:
        return float(m["model"].predict(m["scaler"].transform(X))[0])
    except Exception:
        return None


def _score_0_100(value: float | None, good_low: float, good_high: float,
                 bad_low: float, bad_high: float) -> float | None:
    """Map a value to 0-100 where good range -> 80-100, bad range -> 0-40."""
    if value is None or value != value:
        return None
    if value <= good_low:
        return 95.0
    if value >= bad_high:
        return 15.0
    if good_low < value < good_high:
        # interpolate 95 -> 70 across good range
        return 95 - 25 * (value - good_low) / max(1e-9, good_high - good_low)
    if bad_low < value < bad_high:
        # interpolate 40 -> 15 across bad range
        return 40 - 25 * (value - bad_low) / max(1e-9, bad_high - bad_low)
    # middle zone: 70 -> 40
    mid_low, mid_high = good_high, bad_low
    if mid_high <= mid_low:
        return 55.0
    return 70 - 30 * (value - mid_low) / (mid_high - mid_low)


def _label(score: float | None) -> str:
    if score is None or score != score:
        return "Data unavailable"
    if score >= 81: return "Perfect"
    if score >= 61: return "Well managed"
    if score >= 41: return "Needs improvement"
    if score >= 21: return "Poor"
    return "Harmful"


# Index definitions: key -> (name, category, description, target, component_ids)
INDEX_DEFS = {
    "AQHI": ("Air Quality Health Index", "Atmospheric Exposure",
             "Tracks particulate, gaseous, and aerosol exposure affecting population health.",
             78, ["1", "2", "3", "4", "5", "6", "7"]),
    "UHVI": ("Urban Heat Vulnerability Index", "Heat & Climate",
             "Combines thermal exposure, urban form, greenery, and population vulnerability.",
             76, ["8", "9", "10", "12"]),
    "GEAI": ("Green Environment Accessibility Index", "Ecological Condition",
             "Measures vegetation condition, green-space coverage, and green-space access.",
             78, ["12"]),
    "WSII": ("Water Security & Infrastructure Index", "Water & Infrastructure",
             "Assesses water availability, hydrological extremes, flood and drought exposure, and terrain.",
             75, ["11", "14", "15", "16", "17"]),
    "TAI": ("Transport Accessibility Index", "Access Systems",
            "Measures road-network availability and public-transport access.",
            78, ["30", "25"]),
    "HAI": ("Healthcare Accessibility Index", "Access Systems",
            "Measures access to hospitals, critical infrastructure, and the population served.",
            78, ["30", "25"]),
    "CRI": ("Climate Resilience Index", "Climate Resilience",
            "Combines hydrological hazards, terrain, soil moisture, and disaster readiness.",
            76, ["16", "17", "14", "28"]),
    "SCI": ("Social / Urban Vulnerability Index", "Social Vulnerability",
            "Measures social exposure through population, urban form, access, services, and activity.",
            74, ["25", "30", "28"]),
    "DRI": ("Disaster Readiness Index", "Disaster Readiness",
            "Measures preparedness alongside flood, heat, terrain, infrastructure, and population exposure.",
            76, ["28", "16", "8", "30"]),
}


def _compute_index(key: str, preds: dict, lat: float, lon: float) -> dict:
    name, category, desc, target, comp_ids = INDEX_DEFS[key]
    comp_scores = []
    comp_details = []
    for cid in comp_ids:
        val = preds.get(cid)
        cname = COMPONENTS.get(cid, (cid, None, ""))[0]
        unit = COMPONENTS.get(cid, (cid, None, ""))[2]
        if val is None:
            comp_details.append({"id": cid, "name": cname, "value": None,
                                 "unit": unit, "score": None, "label": "Data unavailable"})
            continue
        # Per-component scoring logic
        if cid in ("1", "2"):  # PM2.5, PM10 — lower is better
            s = _score_0_100(val, 12, 35, 55, 150)
        elif cid in ("3", "4", "5"):  # gases — lower is better
            s = _score_0_100(val, 20, 50, 80, 200)
        elif cid == "6":  # CO
            s = _score_0_100(val, 1, 4, 9, 30)
        elif cid == "7":  # AOD — lower is better
            s = _score_0_100(val, 0.1, 0.3, 0.6, 1.5)
        elif cid in ("8", "9"):  # LST, temp — moderate is good
            s = _score_0_100(val, 22, 30, 35, 45)
        elif cid == "10":  # humidity — moderate good
            s = _score_0_100(abs(val - 55), 0, 15, 25, 45)
        elif cid == "11":  # precipitation — moderate good (avoid extremes)
            s = _score_0_100(abs(val - 5), 0, 8, 20, 60)
        elif cid == "12":  # NDVI — higher is better (invert)
            s = _score_0_100(1 - val, 0, 0.4, 0.6, 1.0)
        elif cid == "14":  # soil moisture — moderate good
            s = _score_0_100(abs(val - 0.25), 0, 0.1, 0.2, 0.45)
        elif cid == "15":  # surface water — higher is better (invert)
            s = _score_0_100(1 - val, 0, 0.4, 0.7, 1.0)
        elif cid == "16":  # flood risk — lower is better
            s = _score_0_100(val, 0.1, 0.3, 0.6, 1.0)
        elif cid == "17":  # drought — near 0 is good
            s = _score_0_100(abs(val), 0, 0.8, 1.5, 3.0)
        elif cid == "25":  # nightlights — moderate good (proxy for development)
            s = _score_0_100(abs(val - 3), 0, 2, 4, 8)
        elif cid == "28":  # disaster exposure — lower is better
            s = _score_0_100(val, 0.1, 0.35, 0.6, 1.0)
        elif cid == "30":  # urban pressure — moderate good
            s = _score_0_100(abs(val - 50), 0, 20, 35, 80)
        else:
            s = 50.0
        if s is not None:
            comp_scores.append(s)
        comp_details.append({"id": cid, "name": cname, "value": round(val, 3),
                             "unit": unit, "score": round(s, 1) if s else None,
                             "label": _label(s)})
    score = round(sum(comp_scores) / len(comp_scores), 1) if comp_scores else None
    return {
        "key": key, "name": name, "category": category,
        "description": desc, "target": target,
        "score": score, "status": _label(score),
        "components": comp_details,
        "coverage_pct": round(100 * len(comp_scores) / len(comp_ids)) if comp_ids else 0,
    }


# Intervention library with real-world examples
INTERVENTIONS = {
    "government": {
        "short_term": [
            {"action": "Issue air quality health advisories and restrict outdoor school activities on high-pollution days",
             "example": "Delhi's Graded Response Action Plan (GRAP) — India restricts construction and diesel generators when AQI exceeds thresholds"},
            {"action": "Deploy mobile water quality testing and emergency flood drainage",
             "example": "Bangladesh's Flood Forecasting and Warning Centre (FFWC) — provides 5-day flood forecasts to 40M people"},
        ],
        "long_term": [
            {"action": "Enact clean air legislation with enforceable emission standards for industry and transport",
             "example": "China's Air Pollution Prevention and Control Action Plan (2013) — cut Beijing PM2.5 by 35% in 5 years"},
            {"action": "Build city-wide green infrastructure network (parks, urban forests, green corridors)",
             "example": "Singapore's City in Nature vision — 1M trees planted, park connector network spanning 300km"},
            {"action": "Invest in mass rapid transit to reduce private vehicle dependence",
             "example": "Bogotá's TransMilenio BRT — carries 2.4M passengers/day, cut commute emissions 40%"},
        ],
    },
    "industrial": {
        "short_term": [
            {"action": "Install continuous emission monitoring systems (CEMS) on all stacks",
             "example": "EU Industrial Emissions Directive — mandatory CEMS cut industrial SO2 by 70% since 2005"},
            {"action": "Switch to low-sulfur fuels during high-pollution episodes",
             "example": "London's Ultra Low Emission Zone — commercial fleets switched fuels, NO2 down 44%"},
        ],
        "long_term": [
            {"action": "Transition to circular economy: zero liquid discharge, waste-to-energy",
             "example": "Kalundborg Symbiosis, Denmark — industrial park where 9 companies exchange waste as resources since 1972"},
            {"action": "Adopt green building certifications for all new facilities",
             "example": "Infosys Hyderabad campus — LEED Platinum, 50% less energy, 60% less water"},
        ],
    },
    "individual": {
        "short_term": [
            {"action": "Use N95 masks outdoors and run indoor air purifiers on high-pollution days",
             "example": "Seoul's citizen mask distribution — 10M masks distributed during 2019 fine dust crisis"},
            {"action": "Reduce private car trips; carpool or use public transit twice a week",
             "example": "Paris car-free days — PM2.5 dropped 40% on car-free Sundays"},
        ],
        "long_term": [
            {"action": "Plant native trees in community spaces; join urban greening programs",
             "example": "Miyawaki forests in Indian cities — 10x faster growth, 30x denser than conventional planting"},
            {"action": "Install rooftop solar and rainwater harvesting at home",
             "example": "Freiburg Vauban district, Germany — 5,000 residents, solar roofs produce surplus energy"},
        ],
    },
}


def _build_suggestions(indices: dict, preds: dict) -> dict:
    """Generate suggestions, interventions, predictions, warnings."""
    weak = [k for k, v in indices.items()
            if v["score"] is not None and v["score"] < 61]
    critical = [k for k, v in indices.items()
                if v["score"] is not None and v["score"] < 41]

    suggestions = []
    if "AQHI" in weak:
        suggestions.append("Prioritize air quality: the Air Quality Health Index is below target. "
                           "Focus on PM2.5/PM10 sources — traffic, brick kilns, construction dust.")
    if "UHVI" in weak:
        suggestions.append("Address urban heat: increase tree canopy and cool-roof coverage in the hottest zones.")
    if "WSII" in weak:
        suggestions.append("Strengthen water security: the area shows flood/drought stress. "
                           "Improve drainage and rainwater capture.")
    if "GEAI" in weak:
        suggestions.append("Expand green space: NDVI indicates sparse vegetation. Target 9 m² of green space per capita (WHO).")
    if "DRI" in weak or "CRI" in weak:
        suggestions.append("Boost disaster readiness: early-warning coverage and evacuation routes need investment.")
    if not suggestions:
        suggestions.append("All indices are at or near target. Maintain monitoring and focus on prevention.")

    # Warnings
    warnings = []
    for k in critical:
        idx = indices[k]
        warnings.append(f"⚠️ {idx['name']} is {idx['status'].lower()} ({idx['score']}/100, target {idx['target']}). Immediate action recommended.")
    pm25 = preds.get("1")
    if pm25 and pm25 > 55:
        warnings.append(f"⚠️ PM2.5 at {pm25:.1f} µg/m³ exceeds WHO guideline (15 µg/m³) by {(pm25/15):.1f}x — "
                        "avoid outdoor exertion, especially for children and elderly.")
    flood = preds.get("16")
    if flood and flood > 0.6:
        warnings.append("⚠️ High flood risk detected. Review drainage infrastructure and emergency evacuation plans.")

    # Human wellbeing prediction
    hwi_scores = [v["score"] for v in indices.values() if v["score"] is not None]
    avg = sum(hwi_scores) / len(hwi_scores) if hwi_scores else 50
    if avg >= 70:
        wellbeing = ("If current trends hold, human wellbeing in this area is projected to remain stable or improve. "
                     "Continued investment in green infrastructure and clean air will compound health benefits over 5–10 years.")
    elif avg >= 50:
        wellbeing = ("Without intervention, human wellbeing is projected to stagnate. Vulnerable groups (children, elderly, "
                     "outdoor workers) face rising respiratory and heat-related illness risk over the next decade.")
    else:
        wellbeing = ("Human wellbeing is projected to decline without urgent action. Expect increased hospital admissions "
                     "for respiratory and cardiovascular conditions, reduced outdoor labor productivity, and widening health inequity.")

    # Biodiversity prediction
    ndvi = preds.get("12")
    if ndvi and ndvi > 0.5:
        biodiversity = ("Vegetation health (NDVI {:.2f}) suggests habitat quality sufficient to support urban biodiversity. "
                        "Protect existing green corridors to maintain bird and pollinator populations.".format(ndvi))
    elif ndvi and ndvi > 0.3:
        biodiversity = ("Moderate vegetation (NDVI {:.2f}) indicates fragmented habitat. Without green corridors, "
                        "urban bird diversity may decline 15–25% over a decade (based on global urban ecology studies).".format(ndvi))
    else:
        biodiversity = ("Sparse vegetation (NDVI {}) signals habitat loss pressure. Pollinators and insectivorous birds "
                        "are at risk of local decline; Miyawaki-style micro-forests can restore habitat in 3–5 years.".format(
                            f"{ndvi:.2f}" if ndvi else "unavailable"))

    return {
        "immediate_actions": suggestions,
        "interventions": INTERVENTIONS,
        "warnings": warnings,
        "predictions": {
            "human_wellbeing": wellbeing,
            "biodiversity": biodiversity,
        },
    }


@app.post("/api/dashboard")
def dashboard(req: AnalyzeRequest):
    """Full dashboard analysis: 9 indices + rich report for the frontend dashboard."""
    _load()
    date = req.date or "2025-12-31"
    if not date.startswith("2025"):
        date = "2025-12-31"

    # Predict all available components
    preds = {}
    for comp in _models.keys():
        v = _predict_component(str(comp), req.lat, req.lon, date)
        if v is not None:
            preds[str(comp)] = v

    # Compute 9 indices
    indices = {}
    for key in INDEX_DEFS:
        indices[key] = _compute_index(key, preds, req.lat, req.lon)

    # Overall Wellbeing Score = mean of all index scores
    valid_scores = [v["score"] for v in indices.values() if v["score"] is not None]
    overall = round(sum(valid_scores) / len(valid_scores), 1) if valid_scores else None

    # Human Wellbeing Index = weighted: AQHI 25%, UHVI 20%, HAI 15%, WSII 15%, GEAI 10%, SCI 15%
    weights = {"AQHI": 0.25, "UHVI": 0.20, "HAI": 0.15, "WSII": 0.15, "GEAI": 0.10, "SCI": 0.15}
    hwi_num = sum(indices[k]["score"] * w for k, w in weights.items()
                  if indices[k]["score"] is not None)
    hwi_den = sum(w for k, w in weights.items() if indices[k]["score"] is not None)
    hwi_score = round(hwi_num / hwi_den, 1) if hwi_den else None

    report = _build_suggestions(indices, preds)

    return {
        "schema_version": "2.1",
        "analysis_id": f"lc-dash-{req.lat:.4f}-{req.lon:.4f}-{date}",
        "model_version": "lupus_rf_per_component_v2",
        "location": {"lat": req.lat, "lon": req.lon, "name": req.name},
        "time": {"date": date, "generated_utc": datetime.now(timezone.utc).isoformat()},
        "overall_score": overall,
        "overall_label": _label(overall),
        "hwi": {"score": hwi_score, "label": _label(hwi_score), "status": _label(hwi_score)},
        "indices": indices,
        "components": {cid: {"name": COMPONENTS.get(cid, (cid, None, ""))[0],
                                      "value": round(v, 3),
                                      "unit": COMPONENTS.get(cid, (cid, None, ""))[2],
                                      "label": _label(_score_0_100(v, 0, 1, 0, 1))}
                       for cid, v in preds.items()},
        "suggestions": report["immediate_actions"],
        "interventions": report["interventions"],
        "warnings": report["warnings"],
        "predictions": report["predictions"],
        "data_quality": {
            "training_rows": 84431,
            "components_predicted": len(preds),
            "indices_computed": len([v for v in indices.values() if v["score"] is not None]),
            "coverage_pct": round(100 * len([v for v in indices.values() if v["score"] is not None]) / 9),
        },
        "provenance": {
            "model": "Per-component RandomForestRegressor (200 trees)",
            "training_period": "2025-01-01 to 2025-12-31",
            "region": "Central Bangladesh bbox",
        },
    }



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100)
