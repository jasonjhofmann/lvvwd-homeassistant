"""Setup / unload tests for the LVVWD integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.lvvwd.api import (
    LvvwdAuthError,
    LvvwdConnectionError,
    LvvwdParseError,
)
from custom_components.lvvwd.const import (
    CONF_ENABLE_COST,
    CONF_METER_SIZE,
    DOMAIN,
)
from custom_components.lvvwd.coordinator import portal_changed_issue_id

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


async def test_repeated_parse_failures_raise_a_repair_until_a_good_scrape(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Two unreadable scrapes in a row open a Repairs issue; success clears it."""
    entry = make_entry()
    entry.add_to_hass(hass)
    registry = ir.async_get(hass)
    issue_id = portal_changed_issue_id(entry.entry_id)

    with _patch_statistics():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data

        mock_client.fetch_all.side_effect = LvvwdConnectionError("network down")
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        assert registry.async_get_issue(DOMAIN, issue_id) is None

        mock_client.fetch_all.side_effect = LvvwdParseError("data-series not found")
        await coordinator.async_refresh()
        assert registry.async_get_issue(DOMAIN, issue_id) is None
        await coordinator.async_refresh()
        issue = registry.async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.translation_key == "portal_changed"
        assert issue.translation_placeholders == {"error": "data-series not found"}

        mock_client.fetch_all.side_effect = None
        await coordinator.async_refresh()
        assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_removing_the_entry_cleans_up_state_and_issue(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """Removal deletes the cost-accrual state file and any open issue."""
    entry = make_entry()
    entry.add_to_hass(hass)
    hass_storage[f"lvvwd.cost_{entry.unique_id}"] = {
        "version": 1,
        "key": f"lvvwd.cost_{entry.unique_id}",
        "data": {},
    }

    with _patch_statistics():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        ir.async_create_issue(
            hass,
            DOMAIN,
            portal_changed_issue_id(entry.entry_id),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="portal_changed",
            translation_placeholders={"error": "x"},
        )
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    assert f"lvvwd.cost_{entry.unique_id}" not in hass_storage
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, portal_changed_issue_id(entry.entry_id)
        )
        is None
    )
