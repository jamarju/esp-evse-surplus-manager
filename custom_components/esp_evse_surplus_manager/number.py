"""Number entities for ESP EVSE Surplus."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfPower
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.core import HomeAssistant

from . import EspEvseSurplusConfigEntry
from .entity import EspEvseSurplusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EspEvseSurplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up number entities."""
    async_add_entities([EspEvseMaxGridImportNumber(entry)])


class EspEvseMaxGridImportNumber(EspEvseSurplusEntity, NumberEntity):
    """Site-wide max grid import budget."""

    _attr_native_min_value = -10000
    _attr_native_max_value = 30000
    _attr_native_step = 100
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: EspEvseSurplusConfigEntry) -> None:
        super().__init__(entry, "max_grid_import_watts", "Max grid import")

    @property
    def native_value(self) -> float:
        return self.coordinator.runtime_settings.max_grid_import_watts

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_max_grid_import_watts(value)
