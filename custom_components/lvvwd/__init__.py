"""The Las Vegas Valley Water District integration."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .api import LvvwdClient
from .const import DOMAIN
from .coordinator import (
    LvvwdConfigEntry,
    LvvwdDataUpdateCoordinator,
    portal_changed_issue_id,
)
from .statistics import async_remove_cost_state

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: LvvwdConfigEntry) -> bool:
    """Set up Las Vegas Valley Water District from a config entry."""
    client = LvvwdClient(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    coordinator = LvvwdDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload when options (meter size / cost toggle) change so the next
    # scrape writes the right statistics.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: LvvwdConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: LvvwdConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: LvvwdConfigEntry) -> None:
    """Delete the cost accrual's saved state and any open Repairs issue.

    The statistics themselves stay (long-term history persists by design).
    """
    await async_remove_cost_state(
        hass, entry.unique_id or entry.data[CONF_USERNAME].strip().lower()
    )
    ir.async_delete_issue(hass, DOMAIN, portal_changed_issue_id(entry.entry_id))
