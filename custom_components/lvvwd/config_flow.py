"""Config flow for the Las Vegas Valley Water District integration.

One config entry per portal account: the username is the unique id (lower
cased) and the password is validated by a real login before the entry is
created. An options flow exposes the meter size (used only when cost modeling
is enabled) and the cost-modeling toggle.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import LvvwdAuthError, LvvwdClient, LvvwdConnectionError, LvvwdError
from .const import (
    CONF_ENABLE_COST,
    CONF_METER_SIZE,
    DEFAULT_ENABLE_COST,
    DEFAULT_METER_SIZE,
    DOMAIN,
    SUPPORTED_METER_SIZES,
)

_LOGGER = logging.getLogger(__name__)


async def _async_validate_login(
    flow: ConfigFlow, username: str, password: str
) -> dict[str, str]:
    """Validate credentials by logging in; return a base-error mapping.

    An empty mapping means the login succeeded. The synchronous client runs on
    the executor so it never blocks the event loop.
    """
    errors: dict[str, str] = {}
    client = LvvwdClient(username, password)
    try:
        await flow.hass.async_add_executor_job(client.login)
    except LvvwdAuthError:
        errors["base"] = "invalid_auth"
    except (LvvwdConnectionError, LvvwdError):
        errors["base"] = "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected exception")
        errors["base"] = "unknown"
    return errors


class LvvwdConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the account config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step: portal credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            self._async_abort_entries_match({CONF_USERNAME: username})
            errors = await _async_validate_login(self, username, password)
            if not errors:
                await self.async_set_unique_id(username.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"LVVWD ({username})",
                    data={CONF_USERNAME: username, CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauthentication on a rejected password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password and validate it.

        The username is fixed (it is the unique id); only the password changes.
        """
        entry = self._get_reauth_entry()
        username = entry.data[CONF_USERNAME]
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            errors = await _async_validate_login(self, username, password)
            if not errors:
                return self._async_update_credentials_and_abort(
                    entry, username, password
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": username},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow changing the password proactively (username is the unique id)."""
        entry = self._get_reconfigure_entry()
        username = entry.data[CONF_USERNAME]
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            errors = await _async_validate_login(self, username, password)
            if not errors:
                return self._async_update_credentials_and_abort(
                    entry, username, password
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": username},
            errors=errors,
        )

    @callback
    def _async_update_credentials_and_abort(
        self, entry: ConfigEntry, username: str, password: str
    ) -> ConfigFlowResult:
        """Save validated credentials and reload the entry exactly once.

        The entry's options update-listener reloads on data changes while the
        entry is loaded, so this only schedules its own reload when that
        listener cannot fire (entry not loaded, e.g. reauth after a failed
        setup, or no change) — mirroring the airnow donor and avoiding the
        double-setup that ``async_update_reload_and_abort`` caused.
        """
        changed = self.hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_USERNAME: username, CONF_PASSWORD: password},
        )
        if not (changed and entry.state is ConfigEntryState.LOADED):
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        reason = "reauth_successful"
        if self.source == SOURCE_RECONFIGURE:
            reason = "reconfigure_successful"
        return self.async_abort(reason=reason)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> LvvwdOptionsFlow:
        """Return the options flow handler."""
        return LvvwdOptionsFlow()


class LvvwdOptionsFlow(OptionsFlow):
    """Handle meter size and cost-modeling options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        meter_options = [
            SelectOptionDict(value=size, label=f'{size}"')
            for size in SUPPORTED_METER_SIZES
        ]
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_METER_SIZE,
                        default=options.get(CONF_METER_SIZE, DEFAULT_METER_SIZE),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=meter_options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_ENABLE_COST,
                        default=options.get(CONF_ENABLE_COST, DEFAULT_ENABLE_COST),
                    ): BooleanSelector(),
                }
            ),
        )
