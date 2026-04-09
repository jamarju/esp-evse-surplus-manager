"""Coordinator for ESP EVSE surplus planning and actuation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
import math
from typing import Any

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_CHARGERS,
    DEFAULT_GRID_VOLTAGE,
    DEFAULT_MAX_GRID_IMPORT_WATTS,
    DOMAIN,
    SERVICE_SET_VALUE,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .controller import ControllerChargerInput, SurplusController
from .models import (
    ChargerConfig,
    GlobalConfig,
    IntegrationSnapshot,
    SiteRuntimeSettings,
)

LOGGER = logging.getLogger(__name__)


class EspEvseSurplusCoordinator(DataUpdateCoordinator[IntegrationSnapshot]):
    """Owns planning, hysteresis, persistence, and actuation."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        global_config: GlobalConfig,
        charger_configs: tuple[ChargerConfig, ...],
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=global_config.planner_period_seconds),
        )
        self.config_entry = entry
        self.global_config = global_config
        self.charger_configs = charger_configs
        self.charger_by_id = {charger.charger_id: charger for charger in charger_configs}
        self.runtime_settings = SiteRuntimeSettings(
            max_grid_import_watts=DEFAULT_MAX_GRID_IMPORT_WATTS,
            chargers={},
        )
        for charger in charger_configs:
            self.runtime_settings.ensure_charger(charger.charger_id)

        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{entry.entry_id}",
        )
        self._control_lock = asyncio.Lock()
        self._last_forwarded_voltage: float | None = None
        self._controller = SurplusController(
            tuple(charger.charger_id for charger in charger_configs)
        )

    @property
    def debug_enabled(self) -> bool:
        """Return whether debug sensors should be exposed."""
        return self.global_config.debug

    async def async_initialize(self) -> None:
        """Load persisted settings and perform the first refresh."""
        stored = await self._store.async_load()
        self.runtime_settings = SiteRuntimeSettings.from_mapping(
            stored,
            tuple(charger.charger_id for charger in self.charger_configs),
        )
        if self._stored_runtime_has_legacy_trim_keys(stored):
            self._async_schedule_save()
        await self.async_config_entry_first_refresh()

    async def async_set_max_grid_import_watts(self, value: float) -> None:
        """Update the runtime max-grid-import budget."""
        self.runtime_settings.max_grid_import_watts = float(value)
        self._async_schedule_save()
        await self.async_request_refresh()

    async def async_set_manual_override(self, charger_id: str, enabled: bool) -> None:
        """Update manual override for a charger."""
        settings = self.runtime_settings.ensure_charger(charger_id)
        settings.manual_override = enabled
        self._async_schedule_save()
        await self.async_request_refresh()

    def runtime_for(self, charger_id: str):
        """Return runtime settings for a charger."""
        return self.runtime_settings.ensure_charger(charger_id)

    async def async_forward_voltage(self, voltage: float) -> None:
        """Forward measured grid voltage to configured EVSE voltage numbers."""
        if voltage <= 0:
            return

        if self._last_forwarded_voltage is not None and math.isclose(
            self._last_forwarded_voltage,
            voltage,
            abs_tol=0.05,
        ):
            return

        async with self._control_lock:
            await self._async_forward_voltage_locked(voltage)

    async def _async_update_data(self) -> IntegrationSnapshot:
        """Poll Home Assistant state, compute targets, and actuate chargers."""
        now = datetime.now(tz=UTC)
        grid_power = self._state_float(self.global_config.grid_power_sensor, 0.0)
        grid_voltage = self._state_float(
            self.global_config.grid_voltage_sensor,
            DEFAULT_GRID_VOLTAGE,
        )

        snapshot = self._controller.step(
            now=now,
            grid_power_watts=grid_power,
            grid_voltage_volts=grid_voltage,
            max_grid_import_watts=self.runtime_settings.max_grid_import_watts,
            hysteresis_seconds=self.global_config.hysteresis_seconds,
            chargers=tuple(
                self._controller_input(charger) for charger in self.charger_configs
            ),
        )
        if self.debug_enabled and LOGGER.isEnabledFor(logging.DEBUG):
            LOGGER.debug("Planner state: %s", snapshot.allocator_state)
            LOGGER.debug("Planner explanation: %s", snapshot.allocator_explanation)
        await self._async_apply_control(snapshot)
        return snapshot

    def _stored_runtime_has_legacy_trim_keys(
        self,
        stored: dict[str, Any] | None,
    ) -> bool:
        """Detect stale trim keys so they can be rewritten away."""
        raw_chargers = (stored or {}).get(CONF_CHARGERS, {})
        for settings in raw_chargers.values():
            if not isinstance(settings, dict):
                continue
            if any(
                key in settings
                for key in (
                    "static_trim_amps",
                    "adaptive_trim_enabled",
                    "adaptive_trim_amps",
                )
            ):
                return True
        return False

    def _controller_input(self, charger: ChargerConfig) -> ControllerChargerInput:
        """Read Home Assistant state into the controller-facing input model."""
        return ControllerChargerInput(
            charger_id=charger.charger_id,
            name=charger.name,
            priority=charger.priority,
            min_amps=charger.min_amps,
            max_amps=charger.max_amps,
            connected=self._state_is_on(charger.connected_sensor),
            charging=(
                self._state_is_on(charger.charging_sensor)
                if charger.charging_sensor
                else self._state_float(charger.current_sensor, 0.0) > 0.5
            ),
            enabled=self._state_is_on(charger.enable_switch),
            manual_override=self.runtime_settings.ensure_charger(
                charger.charger_id
            ).manual_override,
            pilot_setpoint_amps=int(
                round(self._state_float(charger.current_number, 0.0) or 0.0)
            ),
            measured_actual_amps=self._state_float(charger.current_sensor, 0.0),
        )

    async def _async_apply_control(self, snapshot: IntegrationSnapshot) -> None:
        """Write desired pilot / enable / voltage values back to Home Assistant."""
        async with self._control_lock:
            if snapshot.grid_voltage_volts > 0:
                await self._async_forward_voltage_locked(snapshot.grid_voltage_volts)

            managed_snapshots = [
                charger_snapshot
                for charger_snapshot in snapshot.chargers
                if not charger_snapshot.manual_override
            ]

            for charger_snapshot in managed_snapshots:
                if charger_snapshot.should_enable:
                    continue
                charger = self.charger_by_id[charger_snapshot.charger_id]
                await self._async_turn_off_if_needed(charger.enable_switch)

            for charger_snapshot in managed_snapshots:
                charger = self.charger_by_id[charger_snapshot.charger_id]
                await self._async_set_number_if_needed(
                    charger.current_number,
                    float(charger_snapshot.pilot_request_amps),
                )

            for charger_snapshot in managed_snapshots:
                if not charger_snapshot.should_enable:
                    continue
                charger = self.charger_by_id[charger_snapshot.charger_id]
                await self._async_turn_on_if_needed(charger.enable_switch)

    async def _async_forward_voltage_locked(self, voltage: float) -> None:
        """Forward voltage while the control lock is already held."""
        for charger in self.charger_configs:
            if charger.voltage_number is None:
                continue
            await self._async_set_number_if_needed(charger.voltage_number, voltage)
        self._last_forwarded_voltage = voltage

    async def _async_set_number_if_needed(self, entity_id: str, value: float) -> None:
        """Avoid redundant number.set_value service calls."""
        current = self._state_float(entity_id, None)
        if current is not None and math.isclose(current, value, abs_tol=0.05):
            return
        if not self.hass.services.has_service(NUMBER_DOMAIN, SERVICE_SET_VALUE):
            return
        await self.hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {
                ATTR_ENTITY_ID: entity_id,
                "value": value,
            },
            blocking=True,
        )

    async def _async_turn_on_if_needed(self, entity_id: str) -> None:
        """Turn on an HA switch if needed."""
        if self._state_is_on(entity_id):
            return
        if not self.hass.services.has_service(SWITCH_DOMAIN, "turn_on"):
            return
        await self.hass.services.async_call(
            SWITCH_DOMAIN,
            "turn_on",
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    async def _async_turn_off_if_needed(self, entity_id: str) -> None:
        """Turn off an HA switch if needed."""
        if not self._state_is_on(entity_id):
            return
        if not self.hass.services.has_service(SWITCH_DOMAIN, "turn_off"):
            return
        await self.hass.services.async_call(
            SWITCH_DOMAIN,
            "turn_off",
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    def _async_schedule_save(self) -> None:
        """Persist runtime-owned settings."""
        self._store.async_delay_save(self.runtime_settings.as_mapping, 1.0)

    def _state_float(self, entity_id: str | None, default: float | None) -> float | None:
        """Read a numeric state."""
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return default
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return default

    def _state_is_on(self, entity_id: str | None) -> bool:
        """Read an on/off state."""
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == STATE_ON
