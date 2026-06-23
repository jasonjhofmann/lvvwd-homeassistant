"""Sensor tests for the LVVWD integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from .conftest import make_entry

pytestmark = pytest.mark.usefixtures("mock_client")


async def _setup(hass: HomeAssistant) -> None:
    # Keep gallons as gallons so the assertions read the native value (HA would
    # otherwise auto-convert the volume to the metric default).
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.lvvwd.coordinator.async_write_statistics",
        new=AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_sensors_created_with_values(hass: HomeAssistant) -> None:
    """Both account sensors are created and report the latest scrape."""
    await _setup(hass)

    latest = hass.states.get("sensor.lvvwd_latest_month_usage")
    through = hass.states.get("sensor.lvvwd_data_through")
    assert latest is not None
    assert through is not None

    # Latest month = gallons of the last monthly tuple (SAMPLE_MONTHLY[-1]).
    assert latest.state == "58000"
    # Data-through is the latest posted day (2026-06-16) at Pacific midnight.
    assert through.state.startswith("2026-06-16")


async def test_latest_month_none_when_no_monthly(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """With no monthly data the latest-month sensor reports unknown."""
    mock_client.fetch_all.return_value = ([], {"2026-06-16": 1000})
    await _setup(hass)
    latest = hass.states.get("sensor.lvvwd_latest_month_usage")
    assert latest is not None
    assert latest.state == "unknown"


async def test_data_through_none_when_no_posted_days(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """All-None daily data leaves data-through unknown."""
    mock_client.fetch_all.return_value = ([(2026, 6, 58000)], {"2026-06-16": None})
    await _setup(hass)
    through = hass.states.get("sensor.lvvwd_data_through")
    assert through is not None
    assert through.state == "unknown"
