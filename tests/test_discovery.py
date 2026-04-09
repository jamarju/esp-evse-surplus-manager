"""Tests for ESP EVSE entity inference."""

from __future__ import annotations

import unittest

from custom_components.esp_evse_surplus_manager.discovery import (
    DeviceEntityDescription,
    infer_esp_evse_entities,
)


class DiscoveryTests(unittest.TestCase):
    def test_infers_standard_esp_evse_entities_from_unique_ids(self) -> None:
        fake_device_id = "test-esp-evse-node"
        inferred = infer_esp_evse_entities(
            (
                DeviceEntityDescription(
                    entity_id="binary_sensor.ev1_vehicle_connected",
                    unique_id=f"{fake_device_id}-binary_sensor-vehicle_connected",
                ),
                DeviceEntityDescription(
                    entity_id="binary_sensor.ev1_vehicle_charging",
                    unique_id=f"{fake_device_id}-binary_sensor-vehicle_charging",
                ),
                DeviceEntityDescription(
                    entity_id="sensor.ev1_ev_charging_current",
                    unique_id=f"{fake_device_id}-sensor-ev_charging_current",
                ),
                DeviceEntityDescription(
                    entity_id="switch.ev1_evse_enable",
                    unique_id=f"{fake_device_id}-switch-evse_enable",
                ),
                DeviceEntityDescription(
                    entity_id="number.ev1_set_charging_current",
                    unique_id=f"{fake_device_id}-number-set_charging_current",
                ),
                DeviceEntityDescription(
                    entity_id="number.ev1_set_voltage",
                    unique_id=f"{fake_device_id}-number-set_voltage",
                ),
            )
        )

        self.assertEqual(
            inferred["connected_sensor"],
            "binary_sensor.ev1_vehicle_connected",
        )
        self.assertEqual(
            inferred["charging_sensor"],
            "binary_sensor.ev1_vehicle_charging",
        )
        self.assertEqual(
            inferred["current_sensor"],
            "sensor.ev1_ev_charging_current",
        )
        self.assertEqual(inferred["enable_switch"], "switch.ev1_evse_enable")
        self.assertEqual(
            inferred["current_number"],
            "number.ev1_set_charging_current",
        )
        self.assertEqual(inferred["voltage_number"], "number.ev1_set_voltage")

    def test_infers_standard_esp_evse_entities_from_entity_suffixes_and_original_name(self) -> None:
        inferred = infer_esp_evse_entities(
            (
                DeviceEntityDescription(entity_id="binary_sensor.ev1_vehicle_connected"),
                DeviceEntityDescription(entity_id="binary_sensor.ev1_vehicle_charging"),
                DeviceEntityDescription(entity_id="sensor.ev1_ev_charging_current"),
                DeviceEntityDescription(entity_id="switch.ev1_evse_enable"),
                DeviceEntityDescription(entity_id="number.ev1_set_charging_current"),
                DeviceEntityDescription(
                    entity_id="number.ev1_line_voltage",
                    original_name="Set Voltage",
                ),
            )
        )

        self.assertEqual(inferred["connected_sensor"], "binary_sensor.ev1_vehicle_connected")
        self.assertEqual(inferred["charging_sensor"], "binary_sensor.ev1_vehicle_charging")
        self.assertEqual(inferred["current_sensor"], "sensor.ev1_ev_charging_current")
        self.assertEqual(inferred["enable_switch"], "switch.ev1_evse_enable")
        self.assertEqual(inferred["current_number"], "number.ev1_set_charging_current")
        self.assertEqual(inferred["voltage_number"], "number.ev1_line_voltage")

    def test_raises_when_required_entities_are_missing(self) -> None:
        fake_device_id = "test-esp-evse-node"
        with self.assertRaisesRegex(
            ValueError,
            "connected_sensor, current_sensor, enable_switch, current_number",
        ):
            infer_esp_evse_entities(
                (
                    DeviceEntityDescription(
                        entity_id="sensor.ev1_internal_temperature",
                        unique_id=f"{fake_device_id}-sensor-internal_temperature",
                    ),
                )
            )


if __name__ == "__main__":
    unittest.main()
