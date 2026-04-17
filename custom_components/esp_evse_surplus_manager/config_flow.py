"""Config flow for ESP EVSE Surplus Manager."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import device_registry as dr, entity_registry as er, selector
from homeassistant.util import slugify

from .const import (
    CONF_ADD_ANOTHER_CHARGER,
    CONF_CHARGERS,
    CONF_CHARGING_SENSOR,
    CONF_CONNECTED_SENSOR,
    CONF_DEVICE_ID,
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
    CONF_STATE_CHANGE_GUARD_SECONDS,
    CONF_VOLTAGE_NUMBER,
    DEFAULT_DEBUG,
    DEFAULT_HYSTERESIS_SECONDS,
    DEFAULT_NAME,
    DEFAULT_PLANNER_PERIOD_SECONDS,
    DEFAULT_STATE_CHANGE_GUARD_SECONDS,
    DOMAIN,
)
from .discovery import DeviceEntityDescription, infer_esp_evse_entities


def _text_selector() -> selector.TextSelector:
    return selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
    )


def _sensor_selector(
    device_class: SensorDeviceClass | None = None,
) -> selector.EntitySelector:
    config: dict[str, Any] = {"domain": ["sensor"]}
    if device_class is not None:
        config["device_class"] = [device_class]
    return selector.EntitySelector(selector.EntitySelectorConfig(**config))


def _number_value_selector(
    *,
    minimum: float,
    maximum: float,
    step: float,
    unit: str | None = None,
) -> selector.NumberSelector:
    config: dict[str, Any] = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": selector.NumberSelectorMode.BOX,
    }
    if unit is not None:
        config["unit_of_measurement"] = unit

    return selector.NumberSelector(
        selector.NumberSelectorConfig(**config)
    )


class EspEvseSurplusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the ESP EVSE Surplus Manager config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow state."""
        self._site_data: dict[str, Any] = {}
        self._chargers: list[dict[str, Any]] = []
        self._reconfigure_entry: config_entries.ConfigEntry | None = None
        self._selected_charger_slug: str | None = None

    def _finish_charger_flow(self) -> FlowResult:
        """Finish charger collection for initial setup or reconfigure."""
        if self._reconfigure_entry is not None:
            updated_data = dict(self._reconfigure_entry.data)
            updated_data[CONF_CHARGERS] = self._chargers
            return self.async_update_reload_and_abort(
                self._reconfigure_entry,
                data_updates=updated_data,
            )

        payload = dict(self._site_data)
        payload[CONF_CHARGERS] = self._chargers
        return self.async_create_entry(
            title=str(self._site_data[CONF_NAME]),
            data=payload,
        )

    def _esp_evse_device_options(self) -> list[selector.SelectOptionDict]:
        """Return only devices that expose the expected ESP EVSE entities."""
        device_registry = dr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        configured_ids = {
            str(charger.get(CONF_DEVICE_ID))
            for charger in self._chargers
            if charger.get(CONF_DEVICE_ID)
        }

        options: list[selector.SelectOptionDict] = []
        for device in device_registry.devices.values():
            if device.id in configured_ids:
                continue
            device_name = (
                device.name_by_user
                or device.name
                or device.model
                or device.id
            )
            entities = tuple(
                DeviceEntityDescription(
                    entity_id=entry.entity_id,
                    unique_id=entry.unique_id,
                    original_name=entry.original_name,
                )
                for entry in er.async_entries_for_device(
                    entity_registry,
                    device.id,
                    include_disabled_entities=False,
                )
            )
            try:
                infer_esp_evse_entities(entities)
            except ValueError:
                continue
            options.append({"value": device.id, "label": str(device_name)})

        options.sort(key=lambda option: option["label"].lower())
        return options

    def _configured_charger_options(self) -> list[selector.SelectOptionDict]:
        """Return configured chargers as selectable options."""
        options = [
            {
                "value": str(charger[CONF_SLUG]),
                "label": f"{charger[CONF_NAME]} (priority {int(charger[CONF_PRIORITY])})",
            }
            for charger in self._chargers
        ]
        options.sort(key=lambda option: option["label"].lower())
        return options

    def _charger_by_slug(self, charger_slug: str) -> dict[str, Any]:
        """Return a configured charger by slug."""
        for charger in self._chargers:
            if charger.get(CONF_SLUG) == charger_slug:
                return charger
        raise ValueError("charger_not_found")

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Collect site-wide configuration."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            self._site_data = dict(user_input)
            return await self.async_step_charger()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=DEFAULT_NAME): _text_selector(),
                    vol.Required(CONF_GRID_POWER_SENSOR): _sensor_selector(
                        SensorDeviceClass.POWER
                    ),
                    vol.Required(CONF_GRID_VOLTAGE_SENSOR): _sensor_selector(
                        SensorDeviceClass.VOLTAGE
                    ),
                    vol.Required(
                        CONF_PLANNER_PERIOD_SECONDS,
                        default=DEFAULT_PLANNER_PERIOD_SECONDS,
                    ): _number_value_selector(
                        minimum=10,
                        maximum=300,
                        step=1,
                        unit="s",
                    ),
                    vol.Required(
                        CONF_HYSTERESIS_SECONDS,
                        default=DEFAULT_HYSTERESIS_SECONDS,
                    ): _number_value_selector(
                        minimum=60,
                        maximum=1800,
                        step=10,
                        unit="s",
                    ),
                    vol.Required(
                        CONF_STATE_CHANGE_GUARD_SECONDS,
                        default=DEFAULT_STATE_CHANGE_GUARD_SECONDS,
                    ): _number_value_selector(
                        minimum=60,
                        maximum=1800,
                        step=10,
                        unit="s",
                    ),
                    vol.Required(CONF_DEBUG, default=DEFAULT_DEBUG): selector.BooleanSelector(),
                }
            ),
        )

    async def async_step_charger(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Collect one charger definition, repeating until the user finishes."""
        errors: dict[str, str] = {}
        device_options = self._esp_evse_device_options()

        if not device_options:
            if self._chargers:
                return self._finish_charger_flow()
            return self.async_abort(reason="no_esp_evse_devices")

        if user_input is not None:
            charger = dict(user_input)
            device_id = str(charger[CONF_DEVICE_ID])
            if any(existing.get(CONF_DEVICE_ID) == device_id for existing in self._chargers):
                errors["base"] = "duplicate_charger_device"
            else:
                try:
                    charger = self._infer_charger_from_device(
                        device_id=device_id,
                        priority=int(charger[CONF_PRIORITY]),
                    )
                except ValueError:
                    errors["base"] = "device_inference_failed"
                else:
                    charger_slug = slugify(str(charger[CONF_NAME]))
                    if not charger_slug or any(
                        existing[CONF_SLUG] == charger_slug for existing in self._chargers
                    ):
                        errors["base"] = "duplicate_charger_name"
                    else:
                        add_another = bool(user_input[CONF_ADD_ANOTHER_CHARGER])
                        charger[CONF_SLUG] = charger_slug
                        self._chargers.append(charger)
                        if add_another:
                            return await self.async_step_charger()

                        return self._finish_charger_flow()

        suggested_priority = len(self._chargers) + 1
        return self.async_show_form(
            step_id="charger",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=device_options)
                    ),
                    vol.Required(CONF_PRIORITY, default=suggested_priority): _number_value_selector(
                        minimum=1,
                        maximum=100,
                        step=1,
                    ),
                    vol.Required(
                        CONF_ADD_ANOTHER_CHARGER,
                        default=len(device_options) > 1,
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={"charger_count": str(len(self._chargers))},
        )

    def _infer_charger_from_device(
        self,
        *,
        device_id: str,
        priority: int,
    ) -> dict[str, Any]:
        """Build one charger config by inferring standard ESP EVSE entities."""
        device_registry = dr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)

        device = device_registry.async_get(device_id)
        if device is None:
            raise ValueError("device_not_found")

        device_name = (
            device.name_by_user
            or device.name
            or device.model
            or f"charger_{len(self._chargers) + 1}"
        )
        entities = tuple(
            DeviceEntityDescription(
                entity_id=entry.entity_id,
                unique_id=entry.unique_id,
                original_name=entry.original_name,
            )
            for entry in er.async_entries_for_device(
                entity_registry,
                device_id,
                include_disabled_entities=False,
            )
        )
        inferred = infer_esp_evse_entities(entities)
        return {
            CONF_DEVICE_ID: device_id,
            CONF_NAME: str(device_name),
            CONF_PRIORITY: priority,
            CONF_CHARGING_SENSOR: inferred[CONF_CHARGING_SENSOR],
            CONF_MIN_AMPS: 6,
            CONF_MAX_AMPS: 32,
            **inferred,
        }

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Allow editing charger priorities or adding chargers later."""
        entry = self._get_reconfigure_entry()
        self._reconfigure_entry = entry
        self._site_data = dict(entry.data)
        self._chargers = [dict(charger) for charger in entry.data.get(CONF_CHARGERS, [])]
        device_options = self._esp_evse_device_options()
        charger_options = self._configured_charger_options()

        if not charger_options and not device_options:
            return self.async_abort(reason="no_esp_evse_devices")

        menu_options: list[str] = []
        if charger_options:
            menu_options.append("edit_charger")
        if device_options:
            menu_options.append("charger")

        if len(menu_options) == 1:
            only_action = menu_options[0]
            if only_action == "edit_charger":
                return await self.async_step_edit_charger()
            return await self.async_step_charger()

        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=menu_options,
        )

    async def async_step_edit_charger(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Choose which configured charger to edit."""
        charger_options = self._configured_charger_options()
        if not charger_options:
            return await self.async_step_reconfigure()

        if user_input is not None:
            self._selected_charger_slug = str(user_input[CONF_SLUG])
            return await self.async_step_edit_priority()

        return self.async_show_form(
            step_id="edit_charger",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SLUG): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=charger_options)
                    ),
                }
            ),
        )

    async def async_step_edit_priority(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Edit one configured charger's priority."""
        if self._selected_charger_slug is None:
            return await self.async_step_edit_charger()

        charger = self._charger_by_slug(self._selected_charger_slug)
        if user_input is not None:
            charger[CONF_PRIORITY] = int(user_input[CONF_PRIORITY])
            if bool(user_input[CONF_ADD_ANOTHER_CHARGER]):
                self._selected_charger_slug = None
                return await self.async_step_edit_charger()
            return self._finish_charger_flow()

        return self.async_show_form(
            step_id="edit_priority",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PRIORITY, default=int(charger[CONF_PRIORITY])): _number_value_selector(
                        minimum=1,
                        maximum=100,
                        step=1,
                    ),
                    vol.Required(
                        CONF_ADD_ANOTHER_CHARGER,
                        default=False,
                    ): selector.BooleanSelector(),
                }
            ),
            description_placeholders={CONF_NAME: str(charger[CONF_NAME])},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "EspEvseSurplusOptionsFlow":
        """Return the options flow handler."""
        return EspEvseSurplusOptionsFlow(config_entry)


class EspEvseSurplusOptionsFlow(config_entries.OptionsFlow):
    """Edit runtime planner options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Adjust planner cadence and debug mode."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_period = self._config_entry.options.get(
            CONF_PLANNER_PERIOD_SECONDS,
            self._config_entry.data[CONF_PLANNER_PERIOD_SECONDS],
        )
        current_hysteresis = self._config_entry.options.get(
            CONF_HYSTERESIS_SECONDS,
            self._config_entry.data[CONF_HYSTERESIS_SECONDS],
        )
        current_state_change_guard = self._config_entry.options.get(
            CONF_STATE_CHANGE_GUARD_SECONDS,
            self._config_entry.data.get(
                CONF_STATE_CHANGE_GUARD_SECONDS,
                DEFAULT_STATE_CHANGE_GUARD_SECONDS,
            ),
        )
        current_debug = self._config_entry.options.get(
            CONF_DEBUG,
            self._config_entry.data[CONF_DEBUG],
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PLANNER_PERIOD_SECONDS,
                        default=current_period,
                    ): _number_value_selector(
                        minimum=10,
                        maximum=300,
                        step=1,
                        unit="s",
                    ),
                    vol.Required(
                        CONF_HYSTERESIS_SECONDS,
                        default=current_hysteresis,
                    ): _number_value_selector(
                        minimum=60,
                        maximum=1800,
                        step=10,
                        unit="s",
                    ),
                    vol.Required(
                        CONF_STATE_CHANGE_GUARD_SECONDS,
                        default=current_state_change_guard,
                    ): _number_value_selector(
                        minimum=60,
                        maximum=1800,
                        step=10,
                        unit="s",
                    ),
                    vol.Required(CONF_DEBUG, default=current_debug): selector.BooleanSelector(),
                }
            ),
        )
