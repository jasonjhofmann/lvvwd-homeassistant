"""Diagnostics tests — credentials must be redacted."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from custom_components.lvvwd.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import TEST_PASSWORD, TEST_USERNAME, make_entry

pytestmark = pytest.mark.usefixtures("mock_client")


async def test_diagnostics_redacts_credentials(hass: HomeAssistant) -> None:
    """The username and password are scrubbed from the diagnostics dump."""
    entry = make_entry(meter_size="1", enable_cost=True)
    entry.add_to_hass(hass)

    with patch(
        "custom_components.lvvwd.coordinator.async_write_statistics",
        new=AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    # Credentials redacted.
    assert diagnostics["entry_data"][CONF_USERNAME] == "**REDACTED**"
    assert diagnostics["entry_data"][CONF_PASSWORD] == "**REDACTED**"
    assert TEST_USERNAME not in str(diagnostics)
    assert TEST_PASSWORD not in str(diagnostics)

    # Non-sensitive coordinator data is present.
    assert diagnostics["last_update_success"] is True
    assert diagnostics["data"]["monthly"]
    assert diagnostics["data"]["data_through"] == "2026-06-16"
    assert diagnostics["options"] == {"meter_size": "1", "enable_cost": True}
