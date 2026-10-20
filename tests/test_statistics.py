"""Tests for the external-statistics writers.

These run against the real recorder test harness (via the ``recorder_mock``
fixture from pytest-homeassistant-custom-component) so the cumulative-sum
anchoring through ``get_last_statistics`` is exercised end to end.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.lvvwd import tariff
from custom_components.lvvwd.const import statistic_id
from custom_components.lvvwd.statistics import (
    PACIFIC,
    async_remove_cost_state,
    async_write_statistics,
)

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
    # One row per calendar day from the first posted day to the last (the
    # unposted day still accrues its day charges), never falling.
    assert len(cost) == 4
    assert sums == sorted(sums)
    assert all(row["state"] >= 0 for row in cost)
    states = [row["state"] for row in cost]
    assert abs(sum(states) - sums[-1]) < 1e-6
    assert sums[-1] > 0


def _window(first: str, days: int, gallons: int = 900) -> dict[str, int | None]:
    start = date.fromisoformat(first)
    return {(start + timedelta(days=i)).isoformat(): gallons for i in range(days)}


async def _write_cost(hass: HomeAssistant, daily: dict[str, int | None]) -> None:
    await async_write_statistics(
        hass, UNIQUE_ID, [], daily, enable_cost=True, meter_size="0.625"
    )
    await async_wait_recording_done(hass)


async def test_cost_history_survives_the_window_sliding(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """A later poll whose window starts later leaves written days alone.

    The portal only serves the last ~4 weeks. Recomputing the cost from that
    window alone re-based the cumulative to $0 at the window's first day on
    every poll, so days that slid out kept a stale sum and the Energy
    dashboard showed about $0 a day for them.
    """
    await _write_cost(hass, _window("2026-01-05", 8))  # Jan 5-12
    first = await _series(hass, "water_cost")
    await _write_cost(hass, _window("2026-01-09", 8))  # Jan 9-16
    second = await _series(hass, "water_cost")

    assert second[: len(first)] == first
    assert len(second) == 12
    expected, _ = tariff.accrue_months(
        tariff.AccrualState.starting(date(2026, 1, 5)),
        _window("2026-01-05", 12),
        date(2026, 1, 16),
        "0.625",
    )
    assert [row["sum"] for row in second] == [float(p[2]) for p in expected]


async def test_cost_continues_after_rows_with_no_saved_state(
    recorder_mock: Recorder, hass: HomeAssistant, hass_storage: dict
) -> None:
    """Rows written before 0.3.0 (or a lost state file) are continued, untouched."""
    await _write_cost(hass, _window("2026-01-05", 4))  # Jan 5-8
    before = await _series(hass, "water_cost")
    hass_storage.pop(f"lvvwd.cost_{UNIQUE_ID}")

    await _write_cost(hass, _window("2026-01-05", 6))  # adds Jan 9-10
    after = await _series(hass, "water_cost")

    assert after[:4] == before
    restart, _ = tariff.accrue_months(
        tariff.AccrualState.starting(date(2026, 1, 9), base=before[-1]["sum"]),
        _window("2026-01-09", 2),
        date(2026, 1, 10),
        "0.625",
    )
    assert [row["sum"] for row in after[4:]] == [float(p[2]) for p in restart]


async def test_cost_restarts_after_a_gap_longer_than_the_window(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """Unknown days are left out, not priced as zero use; the sum carries on."""
    await _write_cost(hass, _window("2026-01-05", 4))  # Jan 5-8
    before = await _series(hass, "water_cost")
    await _write_cost(hass, _window("2026-03-02", 3))  # Mar 2-4
    after = await _series(hass, "water_cost")

    assert len(after) == 7
    assert after[:4] == before
    assert after[4]["start"] == dt_util.as_timestamp(
        datetime(2026, 3, 2, tzinfo=PACIFIC)
    )
    assert after[4]["sum"] > before[-1]["sum"]


async def test_removing_the_entry_state_helper_deletes_the_store(
    recorder_mock: Recorder, hass: HomeAssistant, hass_storage: dict
) -> None:
    """async_remove_cost_state drops the saved accrual state."""
    await _write_cost(hass, _window("2026-01-05", 2))
    assert f"lvvwd.cost_{UNIQUE_ID}" in hass_storage
    await async_remove_cost_state(hass, UNIQUE_ID)
    assert f"lvvwd.cost_{UNIQUE_ID}" not in hass_storage


async def test_warns_once_when_pricing_past_the_newest_schedule(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A day in a year with no rate schedule logs one warning per HA run."""
    year = tariff.newest_schedule_year() + 1
    freezer.move_to(f"{year}-01-20 12:00:00")
    await _write_cost(hass, _window(f"{year}-01-05", 4))
    await _write_cost(hass, _window(f"{year}-01-05", 6))
    warnings = [r for r in caplog.records if f"LVVWD rates for {year}" in r.message]
    assert len(warnings) == 1


async def test_no_statistics_when_empty(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """Empty scrape data writes nothing (no crash, no rows)."""
    await async_write_statistics(hass, UNIQUE_ID, [], {}, enable_cost=True)
    await async_wait_recording_done(hass)
    assert await _series(hass, "water") == []
    assert await _series(hass, "water_daily") == []
    assert await _series(hass, "water_cost") == []


async def test_cost_repeat_poll_and_malformed_state(
    recorder_mock: Recorder, hass: HomeAssistant, hass_storage: dict
) -> None:
    """A poll with no new day writes nothing; an unreadable state is ignored."""
    await _write_cost(hass, _window("2026-01-05", 3))
    first = await _series(hass, "water_cost")
    await _write_cost(hass, _window("2026-01-05", 3))
    assert await _series(hass, "water_cost") == first

    hass_storage[f"lvvwd.cost_{UNIQUE_ID}"]["data"] = {"last_day": "garbage"}
    await _write_cost(hass, _window("2026-01-05", 4))
    after = await _series(hass, "water_cost")
    assert after[:3] == first
    assert len(after) == 4
    assert after[3]["sum"] > first[-1]["sum"]
