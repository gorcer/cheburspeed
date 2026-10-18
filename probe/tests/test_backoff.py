"""Tests for the additive jitter helper (DAT-459, item n)."""

from __future__ import annotations

from xspeed_probe.wsclient import _additive_jitter


def test_jitter_returns_value_in_expected_range() -> None:
    """The factor stays within [0.5, 1.0] × base across many draws."""
    samples = [_additive_jitter(2.0) for _ in range(200)]
    assert all(1.0 <= s <= 2.0 for s in samples), samples
    assert min(samples) < 1.05  # not stuck at the upper bound
    assert max(samples) > 1.95  # not stuck at the lower bound


def test_jitter_scales_with_base() -> None:
    samples = [_additive_jitter(10.0) for _ in range(50)]
    assert all(5.0 <= s <= 10.0 for s in samples)


def test_jitter_zero_base_returns_zero() -> None:
    assert _additive_jitter(0.0) == 0.0


__all__ = [
    "test_jitter_returns_value_in_expected_range",
    "test_jitter_scales_with_base",
    "test_jitter_zero_base_returns_zero",
]
