"""Optional hybrid RF + sequence LSTM training primitives.

The caller is responsible for constructing leakage-safe, spatially grouped train/validation
splits and for supplying scientifically meaningful targets. This module does not create
labels or fabricate data. TensorFlow is an optional dependency; without it, LSTM training
fails explicitly and the caller must not label RF-only output as an ensemble.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


@dataclass
class EnsembleResult:
    rf_weight: float
    lstm_weight: float
    validation_mae: float
    validation_rmse: float


def build_temporal_features(
    series: np.ndarray,
    *,
    lags: tuple[int, ...] = (1, 2, 3, 7, 14),
    windows: tuple[int, ...] = (3, 7, 14),
) -> tuple[np.ndarray, np.ndarray]:
    """Create supervised features from a [time, feature] array.

    Rows with insufficient history are omitted. Callers must split chronologically
    before fitting downstream preprocessing, and must ensure target timestamps follow
    each feature timestamp.
    """
    data = np.asarray(series, dtype=float)
    if data.ndim != 2 or data.shape[0] < max((*lags, *windows)) + 1:
        raise ValueError("series must be [time, features] with enough history")
    if not np.isfinite(data).all():
        raise ValueError("series contains missing/non-finite values; clean with a documented policy first")
    max_history = max((*lags, *windows))
    x_rows: list[np.ndarray] = []
    y_rows: list[np.ndarray] = []
    for target_idx in range(max_history, len(data)):
        history = data[:target_idx]
        features: list[np.ndarray] = [data[target_idx - lag] for lag in lags]
        for width in windows:
            window = history[-width:]
            features.extend((window.mean(axis=0), window.std(axis=0)))
        # The last observed state is also an explicit baseline feature.
        features.append(data[target_idx - 1])
        x_rows.append(np.concatenate(features))
        y_rows.append(data[target_idx])
    return np.stack(x_rows), np.stack(y_rows)


def fit_random_forest(
    x_train: np.ndarray,
    y_train: np.ndarray,
    *,
    random_state: int = 42,
) -> RandomForestRegressor:
    """Fit a multi-output RF to engineered features; no random row split is performed here."""
    x = np.asarray(x_train, dtype=float)
    y = np.asarray(y_train, dtype=float)
    if x.ndim != 2 or y.ndim not in (1, 2) or len(x) != len(y) or len(x) < 10:
        raise ValueError("invalid or insufficient RF training arrays")
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("training arrays contain missing/non-finite values")
    model = RandomForestRegressor(
        n_estimators=300,
        min_samples_leaf=2,
        n_jobs=-1,
        random_state=random_state,
    )
    return model.fit(x, y)


def fit_spatiotemporal_lstm(
    sequences: np.ndarray,
    targets: np.ndarray,
    *,
    epochs: int = 40,
    batch_size: int = 32,
    random_state: int = 42,
) -> Any:
    """Fit an LSTM to [samples, timesteps, features] sequences.

    Geographic relationships are learned only if features encode a consistent spatial
    neighborhood/grid or spatial context. Latitude/longitude alone does not provide a
    neighborhood graph. TensorFlow is optional and absence is an explicit error.
    """
    try:
        import tensorflow as tf
    except ImportError as exc:
        raise RuntimeError(
            "TensorFlow is required for LSTM training. Install the optional ML requirements."
        ) from exc
    x = np.asarray(sequences, dtype=np.float32)
    y = np.asarray(targets, dtype=np.float32)
    if x.ndim != 3 or y.ndim not in (1, 2) or len(x) != len(y):
        raise ValueError("LSTM inputs must be [samples, timesteps, features] and aligned targets")
    if x.shape[0] < 20 or x.shape[1] < 2 or x.shape[2] < 1:
        raise ValueError("insufficient sequence samples, timesteps, or features")
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("LSTM arrays contain missing/non-finite values")
    tf.keras.utils.set_random_seed(random_state)
    model = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(x.shape[1], x.shape[2])),
        tf.keras.layers.LSTM(64, return_sequences=False),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.Dense(32, activation="relu"),
        tf.keras.layers.Dense(1 if y.ndim == 1 else y.shape[-1]),
    ])
    model.compile(optimizer="adam", loss="mse", metrics=["mae"])
    model.fit(
        x, y, epochs=epochs, batch_size=batch_size, shuffle=False,
        validation_split=0.0, verbose=0,
    )
    return model


def select_validation_ensemble(
    y_validation: np.ndarray,
    rf_predictions: np.ndarray,
    lstm_predictions: np.ndarray,
    *,
    step: float = 0.05,
) -> EnsembleResult:
    """Select convex blend weights by validation MAE, never by final test outcomes."""
    y = np.asarray(y_validation, dtype=float)
    rf = np.asarray(rf_predictions, dtype=float)
    lstm = np.asarray(lstm_predictions, dtype=float)
    if y.shape != rf.shape or y.shape != lstm.shape or y.size == 0:
        raise ValueError("validation targets and model predictions must have identical non-empty shapes")
    if not all(np.isfinite(a).all() for a in (y, rf, lstm)):
        raise ValueError("validation arrays contain missing/non-finite values")
    if not 0 < step <= 0.5:
        raise ValueError("step must be in (0, 0.5]")
    weights = np.arange(0.0, 1.0 + step / 2, step)
    candidates = []
    for rf_weight in weights:
        pred = rf_weight * rf + (1.0 - rf_weight) * lstm
        candidates.append((mean_absolute_error(y.reshape(-1), pred.reshape(-1)), float(rf_weight), pred))
    mae, rf_weight, pred = min(candidates, key=lambda item: item[0])
    rmse = float(np.sqrt(mean_squared_error(y.reshape(-1), pred.reshape(-1))))
    return EnsembleResult(
        rf_weight=rf_weight,
        lstm_weight=1.0 - rf_weight,
        validation_mae=float(mae),
        validation_rmse=rmse,
    )


def regression_metrics(y_true: np.ndarray, predictions: np.ndarray) -> dict[str, float]:
    """Compute regression metrics on a caller-defined holdout; not a validation splitter."""
    y = np.asarray(y_true, dtype=float).reshape(-1)
    p = np.asarray(predictions, dtype=float).reshape(-1)
    if y.shape != p.shape or not y.size or not np.isfinite(y).all() or not np.isfinite(p).all():
        raise ValueError("metrics require aligned, finite, non-empty arrays")
    return {
        "mae": float(mean_absolute_error(y, p)),
        "rmse": float(np.sqrt(mean_squared_error(y, p))),
        "r2": float(r2_score(y, p)) if len(y) > 1 else float("nan"),
    }
