from app.ml.component_registry import (
    COMPONENTS,
    HWI_WEIGHTS,
    INDEX_COMPONENTS,
    condition_label,
    equal_weight_index,
    validate_registry,
)


def test_registry_contains_exactly_30_components():
    validate_registry()
    assert len(COMPONENTS) == 30


def test_index_mappings_reference_registered_components():
    assert all(code in COMPONENTS for members in INDEX_COMPONENTS.values() for code in members)


def test_condition_labels_preserve_existing_thresholds():
    assert condition_label(81) == "Perfect"
    assert condition_label(61) == "Well managed"
    assert condition_label(41) == "Needs improvement"
    assert condition_label(21) == "Poor"
    assert condition_label(20) == "Harmful"


def test_index_does_not_hide_missing_coverage():
    result = equal_weight_index({"PM2_5": 90}, "AQHI", min_coverage=0.8)
    assert result["score"] is None
    assert result["status"] == "insufficient_data"
    assert result["coverage"] < 0.8


def test_index_score_uses_only_supplied_normalized_values_when_threshold_allows():
    result = equal_weight_index({"NDVI": 80, "GREEN_SPACE_PCT": 60, "GREEN_ACCESS_PCT": 70}, "GEA")
    assert result["score"] == 70
    assert result["coverage"] == 1.0


def test_hwi_weights_sum_to_one():
    assert abs(sum(HWI_WEIGHTS.values()) - 1.0) < 1e-9
