"""Sensors for the Las Vegas Valley Water District integration."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import LvvwdConfigEntry, LvvwdDataUpdateCoordinator
from .entity import LvvwdEntity

PARALLEL_UPDATES = 0

_PACIFIC = ZoneInfo("America/Los_Angeles")

LATEST_MONTH_DESCRIPTION = SensorEntityDescription(
    key="latest_month",
    translation_key="latest_month",
    device_class=SensorDeviceClass.WATER,
    native_unit_of_measurement=UnitOfVolume.GALLONS,
    state_class=SensorStateClass.TOTAL,
)

DATA_THROUGH_DESCRIPTION = SensorEntityDescription(
    key="data_through",
    translation_key="data_through",
    device_class=SensorDeviceClass.TIMESTAMP,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: LvvwdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the LVVWD account sensors."""
    coordinator = config_entry.runtime_data
    async_add_entities(
        [
            LvvwdLatestMonthSensor(coordinator, config_entry),
            LvvwdDataThroughSensor(coordinator, config_entry),
        ]
    )


class LvvwdLatestMonthSensor(LvvwdEntity, SensorEntity):
    """Most recent completed-month consumption (gallons)."""

    entity_description = LATEST_MONTH_DESCRIPTION

    def __init__(
        self,
        coordinator: LvvwdDataUpdateCoordinator,
        entry: LvvwdConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}-latest_month"

    @property
    def native_value(self) -> StateType:
        """Return the latest month's gallons."""
        monthly = self.coordinator.data["monthly"]
        if not monthly:
            return None
        return monthly[-1][2]


class LvvwdDataThroughSensor(LvvwdEntity, SensorEntity):
    """Timestamp of the most recent day with a posted reading."""

    entity_description = DATA_THROUGH_DESCRIPTION

    def __init__(
        self,
        coordinator: LvvwdDataUpdateCoordinator,
        entry: LvvwdConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}-data_through"

    @property
    def native_value(self) -> datetime | None:
        """Return the data-through day as a TZ-aware timestamp.

        The day is anchored at local-Pacific midnight (then made TZ-aware) so
        the timestamp device class renders a stable instant.
        """
        through: date | None = self.coordinator.data["data_through"]
        if through is None:
            return None
        return datetime.combine(through, time.min, tzinfo=_PACIFIC).astimezone(UTC)
