"""The Las Vegas Valley Water District integration."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from .api import LvvwdClient
from .coordinator import LvvwdConfigEntry, LvvwdDataUpdateCoordinator

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
