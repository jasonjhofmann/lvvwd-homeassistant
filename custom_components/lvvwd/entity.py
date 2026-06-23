"""Base entity for the Las Vegas Valley Water District integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONFIGURATION_URL, DOMAIN
from .coordinator import LvvwdConfigEntry, LvvwdDataUpdateCoordinator


class LvvwdEntity(CoordinatorEntity[LvvwdDataUpdateCoordinator]):
    """Base entity tied to one LVVWD account."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LvvwdDataUpdateCoordinator,
        entry: LvvwdConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, entry.unique_id or coordinator.username)},
            manufacturer="Las Vegas Valley Water District",
            name="LVVWD",
            configuration_url=CONFIGURATION_URL,
        )
