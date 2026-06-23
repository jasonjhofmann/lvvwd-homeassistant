"""Sensor tests for the LVVWD integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.lvvwd.const import DOMAIN

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

    latest = hass.states.get("sensor.lvvwd_testuser_latest_month_usage")
    through = hass.states.get("sensor.lvvwd_testuser_data_through")
    assert latest is not None
    assert through is not None

    # Latest month = gallons of the last monthly tuple (SAMPLE_MONTHLY[-1]).
    assert latest.state == "58000"
    # Data-through is the latest posted day (2026-06-16) at Pacific midnight.
    assert through.state.startswith("2026-06-16")


async def test_two_accounts_get_distinct_entities(hass: HomeAssistant) -> None:
    """A second account becomes its own device with its own entity_ids.

    Without the per-account device name the two entries would both want
    ``sensor.lvvwd_latest_month_usage`` and the second would be disambiguated to
    ``...-_2``. Naming the device after the entry keeps them distinct and stable.
    """
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry_a = make_entry(username="accountone")
    entry_b = make_entry(username="accounttwo")
    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)
    with patch(
        "custom_components.lvvwd.coordinator.async_write_statistics",
        new=AsyncMock(),
    ):
        # Loading the component for the first entry also schedules the second,
        # so set up each only if it isn't already loaded.
        for entry in (entry_a, entry_b):
            if entry.state is not ConfigEntryState.LOADED:
                assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
    assert entry_a.state is ConfigEntryState.LOADED
    assert entry_b.state is ConfigEntryState.LOADED

    # Each account owns a distinct, suffix-free slug...
    assert hass.states.get("sensor.lvvwd_accountone_latest_month_usage") is not None
    assert hass.states.get("sensor.lvvwd_accounttwo_latest_month_usage") is not None
    # ...and the collision fallback never appears.
    assert hass.states.get("sensor.lvvwd_latest_month_usage") is None
    assert hass.states.get("sensor.lvvwd_latest_month_usage_2") is None

    # Two separate service devices, named after their entries.
    devices = dr.async_get(hass)
    device_a = devices.async_get_device(identifiers={(DOMAIN, "accountone")})
    device_b = devices.async_get_device(identifiers={(DOMAIN, "accounttwo")})
    assert device_a is not None and device_b is not None
    assert device_a.id != device_b.id
    assert device_a.name == "LVVWD (accountone)"
    assert device_b.name == "LVVWD (accounttwo)"


async def test_latest_month_none_when_no_monthly(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """With no monthly data the latest-month sensor reports unknown."""
    mock_client.fetch_all.return_value = ([], {"2026-06-16": 1000})
    await _setup(hass)
    latest = hass.states.get("sensor.lvvwd_testuser_latest_month_usage")
    assert latest is not None
    assert latest.state == "unknown"


async def test_data_through_none_when_no_posted_days(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """All-None daily data leaves data-through unknown."""
    mock_client.fetch_all.return_value = ([(2026, 6, 58000)], {"2026-06-16": None})
    await _setup(hass)
    through = hass.states.get("sensor.lvvwd_testuser_data_through")
    assert through is not None
    assert through.state == "unknown"
