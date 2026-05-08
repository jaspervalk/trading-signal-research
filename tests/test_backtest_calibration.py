"""Unit tests for the calibration module."""

from __future__ import annotations

import pytest

from app.backtest.calibration import calibrate


def test_returns_empty_report_when_n_below_n_buckets():
    report = calibrate([0.5, 0.6, 0.7], [1, 0, 1], n_buckets=10)
    assert report.brier_score is None
    assert report.buckets == []


def test_perfectly_calibrated_curve_has_zero_miscalibration():
    # Each "score" equals the long-run hit rate: e.g., score=0.7 → 70% wins.
    scores = []
    hits = []
    for s in [0.1, 0.3, 0.5, 0.7, 0.9]:
        # 100 observations per bucket; hits roughly match score
        for i in range(100):
            scores.append(s)
            hits.append(1 if i < int(s * 100) else 0)
    report = calibrate(scores, hits, n_buckets=5, miscalibration_threshold=0.05)
    assert report.miscalibration_max is not None
    assert report.miscalibration_max < 0.05
    assert report.miscalibrated_buckets == []


def test_brier_score_zero_for_perfect_predictions():
    # Score of 1.0 always wins, score of 0.0 always loses.
    scores = [1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    hits = [1, 1, 1, 0, 0, 0, 1, 0, 1, 0]
    report = calibrate(scores, hits, n_buckets=2)
    assert report.brier_score == pytest.approx(0.0, abs=1e-6)


def test_brier_score_quarter_for_completely_wrong_predictions():
    # Every score is 1.0, every hit is 0 → squared error = 1 per obs.
    scores = [1.0] * 10
    hits = [0] * 10
    report = calibrate(scores, hits, n_buckets=2)
    assert report.brier_score == pytest.approx(1.0)


def test_miscalibrated_bucket_flagged():
    # Score=0.1 → expect 10% wins; actually wins 90% of the time.
    scores = [0.1] * 50 + [0.9] * 50
    hits = [1] * 45 + [0] * 5 + [1] * 45 + [0] * 5  # both buckets ~90% hit
    report = calibrate(scores, hits, n_buckets=2, miscalibration_threshold=0.20)
    # The 0.1 bucket should be flagged: predicted=0.1, realized=~0.9 → gap=0.8
    assert 1 in report.miscalibrated_buckets


def test_reliability_slope_unity_when_calibrated():
    # Build buckets where each predicted score equals realized.
    scores: list[float] = []
    hits: list[int] = []
    for s in [0.1, 0.3, 0.5, 0.7, 0.9]:
        for i in range(50):
            scores.append(s)
            hits.append(1 if i < int(s * 50) else 0)
    report = calibrate(scores, hits, n_buckets=5)
    assert report.reliability_slope is not None
    assert report.reliability_intercept is not None
    assert abs(report.reliability_slope - 1.0) < 0.05
    assert abs(report.reliability_intercept) < 0.05
