"""Switch entities for ESP EVSE Surplus."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EspEvseSurplusConfigEntry
from .entity import EspEvseSurplusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EspEvseSurplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switch entities."""
    async_add_entities(
        [
            EspEvseManualOverrideSwitch(entry, charger_id)
            for charger_id in entry.runtime_data.charger_by_id
        ]
    )


class EspEvseManualOverrideSwitch(EspEvseSurplusEntity, SwitchEntity):
    """Take one charger out of the managed pool."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: EspEvseSurplusConfigEntry, charger_id: str) -> None:
        super().__init__(entry, "manual_override", "Manual override", charger_id)

    @property
    def is_on(self) -> bool:
        return self.coordinator.runtime_for(self._charger_id).manual_override

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_manual_override(self._charger_id, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_manual_override(self._charger_id, False)
