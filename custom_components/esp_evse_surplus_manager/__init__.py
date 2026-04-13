"""The ESP EVSE Surplus integration."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .const import (
    CONF_CHARGERS,
    CONF_DEBUG,
    CONF_HYSTERESIS_SECONDS,
    CONF_PLANNER_PERIOD_SECONDS,
    CONF_STATE_CHANGE_GUARD_SECONDS,
    DOMAIN,
    PLATFORMS,
)
from .models import ChargerConfig, GlobalConfig

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from .coordinator import EspEvseSurplusCoordinator

    type EspEvseSurplusConfigEntry = ConfigEntry[EspEvseSurplusCoordinator]
else:
    EspEvseSurplusConfigEntry = Any

_LEGACY_TRIM_SUFFIXES = ("static_trim_amps", "adaptive_trim_enabled", "adaptive_trim_amps")

async def _async_remove_legacy_trim_entities(
    hass: Any,
    entry: Any,
    charger_configs: tuple[ChargerConfig, ...],
) -> None:
    """Delete obsolete trim entities from the entity registry."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    stale_unique_ids = {
        f"{entry.entry_id}_{charger.charger_id}_{suffix}"
        for charger in charger_configs
        for suffix in _LEGACY_TRIM_SUFFIXES
    }
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.unique_id in stale_unique_ids:
            registry.async_remove(entity_entry.entity_id)


async def async_setup_entry(hass: Any, entry: Any) -> bool:
    """Set up ESP EVSE Surplus from a config entry."""
    from .coordinator import EspEvseSurplusCoordinator

    base_config = GlobalConfig.from_mapping(entry.data)
    global_config = replace(
        base_config,
        planner_period_seconds=int(
            entry.options.get(
                CONF_PLANNER_PERIOD_SECONDS,
                base_config.planner_period_seconds,
            )
        ),
        hysteresis_seconds=int(
            entry.options.get(CONF_HYSTERESIS_SECONDS, base_config.hysteresis_seconds)
        ),
        state_change_guard_seconds=int(
            entry.options.get(
                CONF_STATE_CHANGE_GUARD_SECONDS,
                base_config.state_change_guard_seconds,
            )
        ),
        debug=bool(entry.options.get(CONF_DEBUG, base_config.debug)),
    )
    charger_configs = tuple(
        ChargerConfig.from_mapping(raw)
        for raw in entry.data.get(CONF_CHARGERS, [])
    )

    coordinator = EspEvseSurplusCoordinator(hass, entry, global_config, charger_configs)
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await coordinator.async_initialize()
    await _async_remove_legacy_trim_entities(hass, entry, charger_configs)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: Any, entry: Any) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: Any, entry: Any) -> None:
    """Reload the config entry after options updates."""
    await hass.config_entries.async_reload(entry.entry_id)
