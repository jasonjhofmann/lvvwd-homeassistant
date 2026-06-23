"""Config-flow tests for the LVVWD integration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.lvvwd.api import LvvwdAuthError, LvvwdConnectionError
from custom_components.lvvwd.const import (
    CONF_ENABLE_COST,
    CONF_METER_SIZE,
    DOMAIN,
)

from .conftest import TEST_PASSWORD, TEST_USERNAME, make_entry

pytestmark = pytest.mark.usefixtures("mock_client")


async def _start_user_flow(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow_happy_path(hass: HomeAssistant) -> None:
    """A valid login creates the account entry with the right title/unique_id."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_USERNAME: "TestUser", CONF_PASSWORD: TEST_PASSWORD},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "LVVWD (TestUser)"
    assert result["data"] == {
        CONF_USERNAME: "TestUser",
        CONF_PASSWORD: TEST_PASSWORD,
    }
    entry = result["result"]
    # unique_id is the lower-cased, stripped username.
    assert entry.unique_id == "testuser"


async def test_user_flow_strips_username(hass: HomeAssistant) -> None:
    """Surrounding whitespace is trimmed from the username before use."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_USERNAME: "  Spaced  ", CONF_PASSWORD: TEST_PASSWORD},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "LVVWD (Spaced)"
    assert result["result"].unique_id == "spaced"


async def test_user_flow_invalid_auth_recovers(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A rejected login shows invalid_auth; a corrected retry then succeeds."""
    mock_client.login.side_effect = LvvwdAuthError("bad creds")
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: "wrong"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    # Fix the password; the same flow now completes.
    mock_client.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A transport error surfaces as cannot_connect."""
    mock_client.login.side_effect = LvvwdConnectionError("network down")
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_unknown_error(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """An unexpected exception surfaces as the generic unknown error."""
    mock_client.login.side_effect = RuntimeError("boom")
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_duplicate_account_aborts(hass: HomeAssistant) -> None:
    """A second entry for the same username aborts as already_configured."""
    existing = make_entry(username=TEST_USERNAME)
    existing.add_to_hass(hass)

    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow_updates_password(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Reauth validates and saves a new password without changing the entry."""
    entry = make_entry(username=TEST_USERNAME, password="old")
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    # Wrong password first -> error, then correct password -> success.
    mock_client.login.side_effect = LvvwdAuthError("still wrong")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "still-wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"
    # Username (the unique id) is untouched.
    assert entry.data[CONF_USERNAME] == TEST_USERNAME


async def test_options_flow_sets_meter_and_cost(hass: HomeAssistant) -> None:
    """The options flow persists meter size and the cost opt-in."""
    entry = make_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_METER_SIZE: "1", CONF_ENABLE_COST: True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_METER_SIZE: "1", CONF_ENABLE_COST: True}
