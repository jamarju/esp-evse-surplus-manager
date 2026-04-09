"""Pure data models for ESP EVSE surplus planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import (
    CONF_CHARGERS,
    CONF_CHARGING_SENSOR,
    CONF_CONNECTED_SENSOR,
    CONF_CURRENT_NUMBER,
    CONF_CURRENT_SENSOR,
    CONF_DEBUG,
    CONF_ENABLE_SWITCH,
    CONF_GRID_POWER_SENSOR,
    CONF_GRID_VOLTAGE_SENSOR,
    CONF_HYSTERESIS_SECONDS,
    CONF_MAX_AMPS,
    CONF_MIN_AMPS,
    CONF_PLANNER_PERIOD_SECONDS,
    CONF_PRIORITY,
    CONF_SLUG,
    CONF_VOLTAGE_NUMBER,
    DEFAULT_DEBUG,
    DEFAULT_HYSTERESIS_SECONDS,
    DEFAULT_NAME,
    DEFAULT_PLANNER_PERIOD_SECONDS,
)


@dataclass(slots=True, frozen=True)
class GlobalConfig:
    """Static global configuration loaded from a config entry."""

    name: str
    grid_power_sensor: str
    grid_voltage_sensor: str
    planner_period_seconds: int = DEFAULT_PLANNER_PERIOD_SECONDS
    hysteresis_seconds: int = DEFAULT_HYSTERESIS_SECONDS
    debug: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "GlobalConfig":
        """Create a global configuration from config-entry data."""
        return cls(
            name=str(data.get("name", DEFAULT_NAME)),
            grid_power_sensor=str(data[CONF_GRID_POWER_SENSOR]),
            grid_voltage_sensor=str(data[CONF_GRID_VOLTAGE_SENSOR]),
            planner_period_seconds=int(
                data.get(CONF_PLANNER_PERIOD_SECONDS, DEFAULT_PLANNER_PERIOD_SECONDS)
            ),
            hysteresis_seconds=int(
                data.get(CONF_HYSTERESIS_SECONDS, DEFAULT_HYSTERESIS_SECONDS)
            ),
            debug=bool(data.get(CONF_DEBUG, DEFAULT_DEBUG)),
        )


@dataclass(slots=True, frozen=True)
class ChargerConfig:
    """Static charger configuration loaded from a config entry."""

    charger_id: str
    name: str
    priority: int
    charging_sensor: str | None
    connected_sensor: str
    current_sensor: str
    enable_switch: str
    current_number: str
    voltage_number: str | None = None
    min_amps: int = 6
    max_amps: int = 32

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ChargerConfig":
        """Create a charger configuration from config-entry data."""
        return cls(
            charger_id=str(data[CONF_SLUG]),
            name=str(data["name"]),
            priority=int(data[CONF_PRIORITY]),
            charging_sensor=data.get(CONF_CHARGING_SENSOR) or None,
            connected_sensor=str(data[CONF_CONNECTED_SENSOR]),
            current_sensor=str(data[CONF_CURRENT_SENSOR]),
            enable_switch=str(data[CONF_ENABLE_SWITCH]),
            current_number=str(data[CONF_CURRENT_NUMBER]),
            voltage_number=data.get(CONF_VOLTAGE_NUMBER) or None,
            min_amps=int(data.get(CONF_MIN_AMPS, 6)),
            max_amps=int(data.get(CONF_MAX_AMPS, 32)),
        )


@dataclass(slots=True)
class ChargerRuntimeSettings:
    """Mutable per-charger settings owned by the integration."""

    manual_override: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "ChargerRuntimeSettings":
        """Create runtime settings from storage data."""
        payload = data or {}
        return cls(
            manual_override=bool(payload.get("manual_override", False)),
        )

    def as_mapping(self) -> dict[str, Any]:
        """Serialize runtime settings."""
        return {
            "manual_override": self.manual_override,
        }


@dataclass(slots=True)
class SiteRuntimeSettings:
    """Mutable site-wide settings owned by the integration."""

    max_grid_import_watts: float = 0.0
    chargers: dict[str, ChargerRuntimeSettings] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        data: dict[str, Any] | None,
        charger_ids: tuple[str, ...],
    ) -> "SiteRuntimeSettings":
        """Create site settings from storage data."""
        payload = data or {}
        raw_chargers = payload.get(CONF_CHARGERS, {})
        return cls(
            max_grid_import_watts=float(payload.get("max_grid_import_watts", 0.0)),
            chargers={
                charger_id: ChargerRuntimeSettings.from_mapping(raw_chargers.get(charger_id))
                for charger_id in charger_ids
            },
        )

    def ensure_charger(self, charger_id: str) -> ChargerRuntimeSettings:
        """Get runtime settings for a charger, creating defaults when needed."""
        if charger_id not in self.chargers:
            self.chargers[charger_id] = ChargerRuntimeSettings()
        return self.chargers[charger_id]

    def as_mapping(self) -> dict[str, Any]:
        """Serialize site runtime settings."""
        return {
            "max_grid_import_watts": self.max_grid_import_watts,
            CONF_CHARGERS: {
                charger_id: settings.as_mapping()
                for charger_id, settings in self.chargers.items()
            },
        }


@dataclass(slots=True, frozen=True)
class PlannerCharger:
    """Planner-facing charger input."""

    charger_id: str
    priority: int
    min_amps: int
    max_amps: int
    connected: bool
    enabled: bool = False
    manual_override: bool = False
    measured_actual_amps: float = 0.0
    charging: bool = False
    pilot_setpoint_amps: int = 0
    planning_eligible: bool = False

    @property
    def active(self) -> bool:
        """Return whether the charger is actively drawing current."""
        return (
            self.connected
            and self.enabled
            and self.planning_eligible
            and self.pilot_setpoint_amps > 0
        )


@dataclass(slots=True, frozen=True)
class PlannerInputs:
    """Inputs required for the pure surplus planner."""

    grid_power_watts: float
    grid_voltage_volts: float
    max_grid_import_watts: float
    chargers: tuple[PlannerCharger, ...]


@dataclass(slots=True, frozen=True)
class PlannerResult:
    """Pure planner outputs."""

    desired_actual_amps: dict[str, int]
    available_actual_amps: int
    managed_actual_current_amps: float
    managed_planned_current_amps: int
    active_managed_charger_count: int
    ordered_candidate_ids: tuple[str, ...]
    active_ids: tuple[str, ...]
    planning_eligible_ids: tuple[str, ...]
    preferred_enabled_ids: tuple[str, ...]
    wakeup_candidate_ids: tuple[str, ...]
    allocator_state: str


@dataclass(slots=True, frozen=True)
class ChargerSnapshot:
    """Full per-charger runtime state published by the coordinator."""

    charger_id: str
    name: str
    connected: bool
    enabled: bool
    manual_override: bool
    measured_actual_amps: float
    desired_actual_amps: int
    pilot_request_amps: int
    should_enable: bool
    allocator_bucket: str


@dataclass(slots=True, frozen=True)
class IntegrationSnapshot:
    """Runtime snapshot published by the coordinator."""

    grid_power_watts: float
    grid_voltage_volts: float
    max_grid_import_watts: float
    available_actual_amps: int
    managed_actual_current_amps: float
    managed_planned_current_amps: int
    active_managed_charger_count: int
    allocator_state: str
    allocator_explanation: str
    allocator_attributes: dict[str, Any]
    chargers: tuple[ChargerSnapshot, ...]

    def charger(self, charger_id: str) -> ChargerSnapshot:
        """Return a charger snapshot by id."""
        for charger in self.chargers:
            if charger.charger_id == charger_id:
                return charger
        raise KeyError(charger_id)
