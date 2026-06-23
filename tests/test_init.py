"""Setup / unload tests for the LVVWD integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.lvvwd.api import LvvwdAuthError, LvvwdConnectionError
from custom_components.lvvwd.const import (
    CONF_ENABLE_COST,
    CONF_METER_SIZE,
)

from .conftest import SAMPLE_DAILY, SAMPLE_MONTHLY, make_entry

pytestmark = pytest.mark.usefixtures("mock_client")


def _patch_statistics() -> AsyncMock:
    """Patch the statistics writer the coordinator calls (no recorder needed)."""
    return patch(
        "custom_components.lvvwd.coordinator.async_write_statistics",
        new=AsyncMock(),
    )


async def test_setup_and_unload(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A valid scrape loads the entry, publishes data, then unloads cleanly."""
    entry = make_entry()
    entry.add_to_hass(hass)

    with _patch_statistics() as write_stats:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    assert coordinator.data["monthly"] == SAMPLE_MONTHLY
    assert coordinator.data["daily"] == SAMPLE_DAILY
    # data_through is the latest day with a posted (non-None) reading.
    assert coordinator.data["data_through"].isoformat() == "2026-06-16"
    write_stats.assert_awaited()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_passes_options_to_statistics(
    hass: HomeAssistant,
) -> None:
    """Meter size + cost opt-in flow through to the statistics writer."""
    entry = make_entry(meter_size="1", enable_cost=True)
    entry.add_to_hass(hass)

    with _patch_statistics() as write_stats:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    write_stats.assert_awaited_once()
    kwargs = write_stats.await_args.kwargs
    assert kwargs["enable_cost"] is True
    assert kwargs["meter_size"] == "1"


async def test_setup_auth_error_triggers_reauth(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """An auth bounce on first refresh sets the entry to SETUP_ERROR."""
    mock_client.fetch_all.side_effect = LvvwdAuthError("bad creds")
    entry = make_entry()
    entry.add_to_hass(hass)

    with _patch_statistics():
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"].get("source") == "reauth" for f in flows)


async def test_setup_connection_error_retries(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A transport failure on first refresh defers setup (retry)."""
    mock_client.fetch_all.side_effect = LvvwdConnectionError("network down")
    entry = make_entry()
    entry.add_to_hass(hass)

    with _patch_statistics():
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_options_change_reloads_entry(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Updating options reloads the entry (so the next scrape re-stats)."""
    entry = make_entry()
    entry.add_to_hass(hass)

    with _patch_statistics():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        fetch_calls = mock_client.fetch_all.call_count

        hass.config_entries.async_update_entry(
            entry, options={CONF_METER_SIZE: "1", CONF_ENABLE_COST: True}
        )
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    # The reload performed another scrape.
    assert mock_client.fetch_all.call_count > fetch_calls
