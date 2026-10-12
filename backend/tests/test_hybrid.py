import numpy as np
import pytest

from app.ml.hybrid import build_temporal_features, select_validation_ensemble


def test_temporal_features_align_target_after_history():
    values = np.arange(60, dtype=float).reshape(30, 2)
    x, y = build_temporal_features(values, lags=(1, 2), windows=(3, 5))
    assert x.shape[0] == y.shape[0]
    assert y[0, 0] == values[5, 0]
    assert x.shape[1] > values.shape[1]


def test_temporal_features_reject_nan_instead_of_silently_zero_filling():
    values = np.ones((20, 2), dtype=float)
    values[4, 1] = np.nan
    with pytest.raises(ValueError):
        build_temporal_features(values, lags=(1, 2), windows=(3,))


def test_ensemble_weights_sum_to_one_and_favor_better_model():
    y = np.array([1.0, 2.0, 3.0, 4.0])
    rf = y.copy()
    lstm = y + 3
    result = select_validation_ensemble(y, rf, lstm, step=0.1)
    assert result.rf_weight == 1.0
    assert result.lstm_weight == 0.0
    assert result.validation_mae == 0.0
