"""Base entities for ESP EVSE Surplus."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EspEvseSurplusConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import EspEvseSurplusCoordinator


class EspEvseSurplusEntity(CoordinatorEntity[EspEvseSurplusCoordinator]):
    """Common entity behavior for the integration."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: EspEvseSurplusConfigEntry,
        object_id: str,
        name: str,
        charger_id: str | None = None,
    ) -> None:
        """Initialize the base entity."""
        super().__init__(entry.runtime_data)
        self._entry = entry
        self._charger_id = charger_id
        self._attr_name = name
        if charger_id is None:
            self._attr_unique_id = f"{entry.entry_id}_{object_id}"
        else:
            self._attr_unique_id = f"{entry.entry_id}_{charger_id}_{object_id}"

    @property
    def device_info(self) -> DeviceInfo:
        """Describe the integration device hierarchy."""
        if self._charger_id is None:
            return DeviceInfo(
                identifiers={(DOMAIN, self._entry.entry_id)},
                manufacturer=MANUFACTURER,
                name=self.coordinator.global_config.name,
                model="Surplus controller",
            )

        charger = self.coordinator.charger_by_id[self._charger_id]
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}:{self._charger_id}")},
            manufacturer=MANUFACTURER,
            name=charger.name,
            model="Managed charger",
            via_device=(DOMAIN, self._entry.entry_id),
        )
