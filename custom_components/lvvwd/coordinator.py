"""DataUpdateCoordinator for the Las Vegas Valley Water District integration."""

from __future__ import annotations

import logging
from datetime import date
from typing import TypedDict

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    LvvwdAuthError,
    LvvwdClient,
    LvvwdConnectionError,
    LvvwdError,
)
from .const import (
    CONF_ENABLE_COST,
    CONF_METER_SIZE,
    DEFAULT_ENABLE_COST,
    DEFAULT_METER_SIZE,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .statistics import async_write_statistics

_LOGGER = logging.getLogger(__name__)

type LvvwdConfigEntry = ConfigEntry[LvvwdDataUpdateCoordinator]


class LvvwdData(TypedDict):
    """Shape of the data the coordinator publishes each refresh."""

    monthly: list[tuple[int, int, int]]
    daily: dict[str, int | None]
    data_through: date | None


class LvvwdDataUpdateCoordinator(DataUpdateCoordinator[LvvwdData]):
    """Scrape the LVVWD account portal once per refresh and write statistics."""

    config_entry: LvvwdConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: LvvwdConfigEntry,
        client: LvvwdClient,
    ) -> None:
        """Initialize."""
        self.client = client
        self.username: str = config_entry.data[CONF_USERNAME]
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {self.username}",
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> LvvwdData:
        """Log in, scrape monthly + daily usage, and write statistics.

        The synchronous ``requests``-based client runs on the executor so it
        never blocks the event loop. An auth bounce raises
        ``ConfigEntryAuthFailed`` (HA starts the reauth flow); any other
        scrape failure raises ``UpdateFailed`` and the last good data is kept.
        """
        try:
            monthly, daily = await self.hass.async_add_executor_job(
                self.client.fetch_all
            )
        except LvvwdAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except (LvvwdConnectionError, LvvwdError, TimeoutError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="scrape_failed",
                translation_placeholders={"error": str(err) or type(err).__name__},
            ) from err

        data_through = _latest_complete_day(daily)
        data: LvvwdData = {
            "monthly": monthly,
            "daily": daily,
            "data_through": data_through,
        }
        _LOGGER.debug(
            "Scraped %d month(s) and %d day(s); data through %s",
            len(monthly),
            len(daily),
            data_through,
        )

        options = self.config_entry.options
        await async_write_statistics(
            self.hass,
            self.config_entry.unique_id or self.username.strip().lower(),
            monthly,
            daily,
            enable_cost=bool(options.get(CONF_ENABLE_COST, DEFAULT_ENABLE_COST)),
            meter_size=str(options.get(CONF_METER_SIZE, DEFAULT_METER_SIZE)),
        )
        return data


def _latest_complete_day(daily: dict[str, int | None]) -> date | None:
    """Return the most recent day that actually has a posted reading."""
    posted = [iso for iso, gal in daily.items() if gal is not None]
    if not posted:
        return None
    return date.fromisoformat(max(posted))
