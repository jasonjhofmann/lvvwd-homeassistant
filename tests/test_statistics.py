"""Tests for the external-statistics writers.

These run against the real recorder test harness (via the ``recorder_mock``
fixture from pytest-homeassistant-custom-component) so the cumulative-sum
anchoring through ``get_last_statistics`` is exercised end to end.
"""

from __future__ import annotations

from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.lvvwd.const import statistic_id
from custom_components.lvvwd.statistics import async_write_statistics

UNIQUE_ID = "testuser"

MONTHLY = [(2026, 4, 44000), (2026, 5, 51000), (2026, 6, 58000)]
# Use clearly past dates so none is filtered as "the current Pacific day".
DAILY: dict[str, int | None] = {
    "2026-01-05": 1100,
    "2026-01-06": 980,
    "2026-01-07": None,  # not yet posted -> skipped
    "2026-01-08": 1500,
}


async def _series(hass: HomeAssistant, suffix: str) -> list[dict]:
    stat_id = statistic_id(UNIQUE_ID, suffix)
    start = dt_util.utc_from_timestamp(0)
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        None,
        {stat_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return stats.get(stat_id, [])


async def test_writes_monthly_and_daily(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """Usage statistics land with a monotonically increasing cumulative sum."""
    await async_write_statistics(hass, UNIQUE_ID, MONTHLY, DAILY, enable_cost=False)
    await async_wait_recording_done(hass)

    monthly = await _series(hass, "water")
    assert [row["state"] for row in monthly] == [44000.0, 51000.0, 58000.0]
    assert [row["sum"] for row in monthly] == [44000.0, 95000.0, 153000.0]

    daily = await _series(hass, "water_daily")
    # The None day is skipped; the two posted days accumulate.
    assert [row["state"] for row in daily] == [1100.0, 980.0, 1500.0]
    assert [row["sum"] for row in daily] == [1100.0, 2080.0, 3580.0]

    # Cost is opt-out by default -> no cost series.
    assert await _series(hass, "water_cost") == []


async def test_daily_is_append_only(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """A second run with overlapping days does not rewrite stored sums."""
    await async_write_statistics(hass, UNIQUE_ID, MONTHLY, DAILY, enable_cost=False)
    await async_wait_recording_done(hass)

    extended = dict(DAILY)
    extended["2026-01-09"] = 1200  # one new day
    await async_write_statistics(hass, UNIQUE_ID, MONTHLY, extended, enable_cost=False)
    await async_wait_recording_done(hass)

    daily = await _series(hass, "water_daily")
    # Original three rows untouched + one new row continuing the cumulative.
    assert [row["state"] for row in daily] == [1100.0, 980.0, 1500.0, 1200.0]
    assert daily[-1]["sum"] == 4780.0


async def test_cost_series_written_when_enabled(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """Opting in writes a USD cost series whose cumulative never decreases."""
    await async_write_statistics(
        hass, UNIQUE_ID, MONTHLY, DAILY, enable_cost=True, meter_size="0.625"
    )
    await async_wait_recording_done(hass)

    cost = await _series(hass, "water_cost")
    assert cost, "expected a cost series when enable_cost=True"
    sums = [row["sum"] for row in cost]
    # ``sum`` carries the cost-to-date (the bill if the meter were read that
    # day). It is NOT strictly monotonic — a growing period can shift units
    # into cheaper tiers, so a day's delta can be slightly negative (documented
    # in tariff.accrue_period). What must hold: the series is non-empty, the
    # day deltas (state) telescope back to the final cost-to-date, and the
    # final cost-to-date is positive.
    states = [row["state"] for row in cost]
    assert abs(sum(states) - sums[-1]) < 1e-6
    assert sums[-1] > 0


async def test_no_statistics_when_empty(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """Empty scrape data writes nothing (no crash, no rows)."""
    await async_write_statistics(hass, UNIQUE_ID, [], {}, enable_cost=True)
    await async_wait_recording_done(hass)
    assert await _series(hass, "water") == []
    assert await _series(hass, "water_daily") == []
    assert await _series(hass, "water_cost") == []
