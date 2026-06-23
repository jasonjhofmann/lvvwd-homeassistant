"""Diagnostics support for the Las Vegas Valley Water District integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import LvvwdConfigEntry

# Keys redacted at any depth. The username and password are credentials; the
# account number and meter serial are personally identifying. "account" and
# "meter" are pre-listed so a future revision that surfaces them anywhere in
# the dump scrubs automatically — unused keys cost nothing.
TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, "account", "meter"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LvvwdConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the account entry."""
    coordinator = entry.runtime_data
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "update_interval": str(coordinator.update_interval),
        "last_update_success": coordinator.last_update_success,
        "last_exception": (
            str(coordinator.last_exception) if coordinator.last_exception else None
        ),
        "data": async_redact_data(
            {
                "monthly": coordinator.data["monthly"],
                "daily": coordinator.data["daily"],
                "data_through": (
                    coordinator.data["data_through"].isoformat()
                    if coordinator.data["data_through"]
                    else None
                ),
            }
            if coordinator.data
            else {},
            TO_REDACT,
        ),
    }
