from __future__ import annotations

import pytest

from xspeed_probe.speedtest import SpeedtestResult


def test_speedtest_result_computes_mbps() -> None:
    # 100 Mbit == 12.5 MB == 12_500_000 bytes in 1 second.
    result = SpeedtestResult.from_throughput(
        bytes_downloaded=12_500_000,
        elapsed_seconds=1.0,
    )
    assert result.speed_mbps == pytest.approx(100.0, rel=1e-3)


def test_speedtest_result_rejects_zero_elapsed() -> None:
    with pytest.raises(ValueError, match="elapsed_seconds"):
        SpeedtestResult.from_throughput(
            bytes_downloaded=1024,
            elapsed_seconds=0.0,
        )


def test_speedtest_result_handles_fractional_seconds() -> None:
    result = SpeedtestResult.from_throughput(
        bytes_downloaded=1_250_000,
        elapsed_seconds=0.1,
    )
    assert result.speed_mbps == pytest.approx(100.0, rel=1e-3)
