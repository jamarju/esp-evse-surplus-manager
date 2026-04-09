"""Sensor entities for ESP EVSE Surplus."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EspEvseSurplusConfigEntry
from .entity import EspEvseSurplusEntity
from .models import ChargerSnapshot, IntegrationSnapshot


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EspEvseSurplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    entities: list[SensorEntity] = []
    for charger_id in entry.runtime_data.charger_by_id:
        entities.extend(
            [
                EspEvseChargerSensor(
                    entry,
                    charger_id,
                    "desired_actual_amps",
                    "Desired actual current",
                    lambda snapshot: snapshot.desired_actual_amps,
                    device_class=SensorDeviceClass.CURRENT,
                    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
                ),
                EspEvseChargerSensor(
                    entry,
                    charger_id,
                    "pilot_request_amps",
                    "Pilot request",
                    lambda snapshot: snapshot.pilot_request_amps,
                    device_class=SensorDeviceClass.CURRENT,
                    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
                ),
                EspEvseChargerSensor(
                    entry,
                    charger_id,
                    "measured_actual_amps",
                    "Measured actual current",
                    lambda snapshot: round(snapshot.measured_actual_amps, 2),
                    device_class=SensorDeviceClass.CURRENT,
                    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
                ),
            ]
        )

    if entry.runtime_data.debug_enabled:
        entities.extend(
            [
                EspEvseSiteSensor(
                    entry,
                    "available_actual_amps",
                    "Available actual current",
                    lambda snapshot: snapshot.available_actual_amps,
                    device_class=SensorDeviceClass.CURRENT,
                    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
                    entity_category=EntityCategory.DIAGNOSTIC,
                ),
                EspEvseSiteSensor(
                    entry,
                    "managed_actual_current_amps",
                    "Managed actual current",
                    lambda snapshot: round(snapshot.managed_actual_current_amps, 2),
                    device_class=SensorDeviceClass.CURRENT,
                    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
                    entity_category=EntityCategory.DIAGNOSTIC,
                ),
                EspEvseSiteSensor(
                    entry,
                    "managed_planned_current_amps",
                    "Managed planned current",
                    lambda snapshot: snapshot.managed_planned_current_amps,
                    device_class=SensorDeviceClass.CURRENT,
                    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
                    entity_category=EntityCategory.DIAGNOSTIC,
                ),
                EspEvseSiteSensor(
                    entry,
                    "active_managed_charger_count",
                    "Active managed chargers",
                    lambda snapshot: snapshot.active_managed_charger_count,
                    entity_category=EntityCategory.DIAGNOSTIC,
                ),
                EspEvseSiteSensor(
                    entry,
                    "allocator_state",
                    "Allocator state",
                    lambda snapshot: snapshot.allocator_state,
                    lambda snapshot: snapshot.allocator_attributes,
                    entity_category=EntityCategory.DIAGNOSTIC,
                ),
            ]
        )

    async_add_entities(entities)


class EspEvseSiteSensor(EspEvseSurplusEntity, SensorEntity):
    """Site-level snapshot sensor."""

    def __init__(
        self,
        entry: EspEvseSurplusConfigEntry,
        object_id: str,
        name: str,
        extractor: Callable[[IntegrationSnapshot], str | int | float | None],
        attrs_extractor: Callable[[IntegrationSnapshot], dict[str, Any] | None] | None = None,
        *,
        device_class: SensorDeviceClass | None = None,
        native_unit_of_measurement: str | None = None,
        entity_category: EntityCategory | None = None,
    ) -> None:
        super().__init__(entry, object_id, name)
        self._extractor = extractor
        self._attrs_extractor = attrs_extractor
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = native_unit_of_measurement
        self._attr_entity_category = entity_category

    @property
    def native_value(self) -> str | int | float | None:
        if self.coordinator.data is None:
            return None
        return self._extractor(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None or self._attrs_extractor is None:
            return None
        return self._attrs_extractor(self.coordinator.data)


class EspEvseChargerSensor(EspEvseSurplusEntity, SensorEntity):
    """Per-charger snapshot sensor."""

    def __init__(
        self,
        entry: EspEvseSurplusConfigEntry,
        charger_id: str,
        object_id: str,
        name: str,
        extractor: Callable[[ChargerSnapshot], str | int | float | None],
        *,
        device_class: SensorDeviceClass | None = None,
        native_unit_of_measurement: str | None = None,
        entity_category: EntityCategory | None = None,
    ) -> None:
        super().__init__(entry, object_id, name, charger_id)
        self._extractor = extractor
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = native_unit_of_measurement
        self._attr_entity_category = entity_category

    @property
    def native_value(self) -> str | int | float | None:
        if self.coordinator.data is None:
            return None
        return self._extractor(self.coordinator.data.charger(self._charger_id))
