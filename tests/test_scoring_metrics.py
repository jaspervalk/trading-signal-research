"""Tests for scoring primitives — Wilson CI, expectancy, sharpe-like."""

from __future__ import annotations

import pytest

from app.scoring.metrics import (
    expectancy,
    hit_rate_with_ci,
    median,
    sharpe_like,
    std,
    wilson_ci,
)


def test_wilson_ci_basic():
    lo, hi = wilson_ci(8, 10)
    # Known values: Wilson 95% CI for 8/10 ≈ (0.49, 0.94)
    assert 0.45 < lo < 0.55
    assert 0.90 < hi < 0.97


def test_wilson_ci_extreme():
    lo, hi = wilson_ci(0, 5)
    assert lo == 0.0
    assert 0.4 < hi < 0.6  # rough — Wilson keeps the upper bound away from 0
    lo, hi = wilson_ci(5, 5)
    assert hi == 1.0
    assert 0.4 < lo < 0.6


def test_wilson_ci_empty():
    lo, hi = wilson_ci(0, 0)
    assert (lo, hi) == (0.0, 1.0)


def test_hit_rate_with_ci_filters_none():
    p, lo, hi, n = hit_rate_with_ci([0.05, -0.02, None, 0.10, None])
    assert n == 3
    assert p == pytest.approx(2 / 3)


def test_hit_rate_with_ci_all_none():
    p, lo, hi, n = hit_rate_with_ci([None, None])
    assert (p, lo, hi, n) == (None, None, None, 0)


def test_expectancy_filters_none():
    assert expectancy([1.0, 2.0, None]) == pytest.approx(1.5)
    assert expectancy([]) is None
    assert expectancy([None, None]) is None


def test_std_requires_min_observations():
    assert std([1.0]) is None
    assert std([1.0, 1.0]) == 0.0
    assert std([1.0, 3.0]) == pytest.approx(2 ** 0.5, rel=0.01)


def test_sharpe_like():
    # mean=0.5, std (ddof=1) = √0.5 → sharpe = 0.5 / √0.5 = √0.5 ≈ 0.707
    s = sharpe_like([0.0, 1.0])
    assert s == pytest.approx(0.5 ** 0.5, rel=0.01)


def test_sharpe_like_zero_std_returns_none():
    assert sharpe_like([1.0, 1.0, 1.0]) is None


def test_median():
    assert median([3.0, 1.0, 2.0]) == 2.0
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert median([None, None]) is None
