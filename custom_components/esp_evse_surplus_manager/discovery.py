"""Helpers to infer ESP EVSE entities from a device."""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
    CONF_CHARGING_SENSOR,
    CONF_CONNECTED_SENSOR,
    CONF_CURRENT_NUMBER,
    CONF_CURRENT_SENSOR,
    CONF_ENABLE_SWITCH,
    CONF_VOLTAGE_NUMBER,
)


@dataclass(slots=True, frozen=True)
class DeviceEntityDescription:
    """Minimal entity information needed for ESP EVSE inference."""

    entity_id: str
    unique_id: str | None = None
    original_name: str | None = None


def _matches(
    entity: DeviceEntityDescription,
    *,
    domain: str,
    unique_suffixes: tuple[str, ...],
    entity_suffixes: tuple[str, ...] = (),
    original_names: tuple[str, ...] = (),
) -> bool:
    """Return whether the entity looks like the requested ESP EVSE endpoint."""
    if not entity.entity_id.startswith(f"{domain}."):
        return False

    unique_id = entity.unique_id or ""
    if any(unique_id.endswith(suffix) for suffix in unique_suffixes):
        return True

    if any(entity.entity_id.endswith(suffix) for suffix in entity_suffixes):
        return True

    original_name = (entity.original_name or "").strip().lower()
    return any(original_name == name.lower() for name in original_names)


def _pick_entity(
    entities: tuple[DeviceEntityDescription, ...],
    *,
    domain: str,
    unique_suffixes: tuple[str, ...],
    entity_suffixes: tuple[str, ...] = (),
    original_names: tuple[str, ...] = (),
) -> str | None:
    """Pick the first matching entity id."""
    return next(
        (
            entity.entity_id
            for entity in entities
            if _matches(
                entity,
                domain=domain,
                unique_suffixes=unique_suffixes,
                entity_suffixes=entity_suffixes,
                original_names=original_names,
            )
        ),
        None,
    )


def infer_esp_evse_entities(
    entities: tuple[DeviceEntityDescription, ...],
) -> dict[str, str | None]:
    """Infer the standard ESP EVSE entities exposed by a device."""
    inferred = {
        CONF_CONNECTED_SENSOR: _pick_entity(
            entities,
            domain="binary_sensor",
            unique_suffixes=("-binary_sensor-vehicle_connected",),
            entity_suffixes=("_vehicle_connected",),
            original_names=("Vehicle Connected",),
        ),
        CONF_CHARGING_SENSOR: _pick_entity(
            entities,
            domain="binary_sensor",
            unique_suffixes=("-binary_sensor-vehicle_charging",),
            entity_suffixes=("_vehicle_charging",),
            original_names=("Vehicle Charging",),
        ),
        CONF_CURRENT_SENSOR: _pick_entity(
            entities,
            domain="sensor",
            unique_suffixes=("-sensor-ev_charging_current",),
            entity_suffixes=("_ev_charging_current",),
            original_names=("EV Charging Current",),
        ),
        CONF_ENABLE_SWITCH: _pick_entity(
            entities,
            domain="switch",
            unique_suffixes=("-switch-evse_enable",),
            entity_suffixes=("_evse_enable",),
            original_names=("EVSE Enable",),
        ),
        CONF_CURRENT_NUMBER: _pick_entity(
            entities,
            domain="number",
            unique_suffixes=("-number-set_charging_current",),
            entity_suffixes=("_set_charging_current",),
            original_names=("Set Charging Current",),
        ),
        CONF_VOLTAGE_NUMBER: _pick_entity(
            entities,
            domain="number",
            unique_suffixes=("-number-set_voltage",),
            entity_suffixes=("_set_voltage",),
            original_names=("Set Voltage",),
        ),
    }
    missing = [
        key
        for key in (
            CONF_CONNECTED_SENSOR,
            CONF_CURRENT_SENSOR,
            CONF_ENABLE_SWITCH,
            CONF_CURRENT_NUMBER,
        )
        if not inferred[key]
    ]
    if missing:
        raise ValueError(", ".join(missing))

    return inferred
