"""Constants for the ESP EVSE Surplus integration."""

from __future__ import annotations

DOMAIN = "esp_evse_surplus_manager"

PLATFORMS = ("number", "sensor", "switch")

MANUFACTURER = "ESP EVSE / Home Assistant"
DEFAULT_NAME = "esp-evse-surplus-manager"
DEFAULT_PLANNER_PERIOD_SECONDS = 20
DEFAULT_HYSTERESIS_SECONDS = 5 * 60
DEFAULT_STATE_CHANGE_GUARD_SECONDS = 3 * 60
DEFAULT_GRID_VOLTAGE = 230.0
DEFAULT_MAX_GRID_IMPORT_WATTS = 0.0
DEFAULT_DEBUG = False

CONF_CHARGERS = "chargers"
CONF_GRID_POWER_SENSOR = "grid_power_sensor"
CONF_GRID_VOLTAGE_SENSOR = "grid_voltage_sensor"
CONF_PLANNER_PERIOD_SECONDS = "planner_period_seconds"
CONF_HYSTERESIS_SECONDS = "hysteresis_seconds"
CONF_STATE_CHANGE_GUARD_SECONDS = "state_change_guard_seconds"
CONF_DEBUG = "debug"
CONF_PRIORITY = "priority"
CONF_CONNECTED_SENSOR = "connected_sensor"
CONF_CHARGING_SENSOR = "charging_sensor"
CONF_CURRENT_SENSOR = "current_sensor"
CONF_ENABLE_SWITCH = "enable_switch"
CONF_CURRENT_NUMBER = "current_number"
CONF_VOLTAGE_NUMBER = "voltage_number"
CONF_MIN_AMPS = "min_amps"
CONF_MAX_AMPS = "max_amps"
CONF_ADD_ANOTHER_CHARGER = "add_another_charger"
CONF_SLUG = "slug"
CONF_DEVICE_ID = "device_id"

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}_settings"

SERVICE_SET_VALUE = "set_value"
