"""Reusable offline controller scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from custom_components.esp_evse_surplus_manager.controller import (
    ControllerChargerInput,
    SurplusController,
)
from custom_components.esp_evse_surplus_manager.simulation import SimulationSample


@dataclass(slots=True, frozen=True)
class ScenarioFixture:
    """A named controller simulation fixture."""

    controller: SurplusController
    start: datetime
    tick_seconds: int
    hysteresis_seconds: int
    samples: list[SimulationSample]


def charger_input(
    charger_id: str,
    *,
    priority: int,
    connected: bool = True,
    charging: bool | None = None,
    enabled: bool = False,
    manual_override: bool = False,
    measured_actual_amps: float = 0.0,
    pilot_setpoint_amps: int = 0,
) -> ControllerChargerInput:
    """Build a compact charger input for scenario fixtures."""
    return ControllerChargerInput(
        charger_id=charger_id,
        name=charger_id,
        priority=priority,
        min_amps=6,
        max_amps=32,
        connected=connected,
        charging=charging,
        enabled=enabled,
        manual_override=manual_override,
        measured_actual_amps=measured_actual_amps,
        pilot_setpoint_amps=pilot_setpoint_amps,
    )


def single_ev_surplus_wakeup_fixture() -> ScenarioFixture:
    """One EV sees sustained surplus long enough to wake after hysteresis."""
    return ScenarioFixture(
        controller=SurplusController(["ev1"]),
        start=datetime(2026, 1, 1, tzinfo=UTC),
        tick_seconds=20,
        hysteresis_seconds=300,
        samples=[
            SimulationSample(
                repeat=16,
                grid_power_watts=-8 * 230,
                grid_voltage_volts=230,
                max_grid_import_watts=0,
                chargers=(charger_input("ev1", priority=1),),
            )
        ],
    )


def two_ev_threshold_transition_fixture() -> ScenarioFixture:
    """Two idle EVs move from only-top-priority to both-enabled as surplus rises."""
    return ScenarioFixture(
        controller=SurplusController(["ev1", "ev2"]),
        start=datetime(2026, 1, 1, tzinfo=UTC),
        tick_seconds=20,
        hysteresis_seconds=300,
        samples=[
            SimulationSample(
                repeat=16,
                grid_power_watts=-8 * 230,
                grid_voltage_volts=230,
                max_grid_import_watts=0,
                chargers=(
                    charger_input("ev1", priority=1),
                    charger_input("ev2", priority=2),
                ),
            ),
            SimulationSample(
                repeat=16,
                grid_power_watts=-13 * 230,
                grid_voltage_volts=230,
                max_grid_import_watts=0,
                chargers=(
                    charger_input("ev1", priority=1, enabled=True, measured_actual_amps=0),
                    charger_input("ev2", priority=2),
                ),
            ),
        ],
    )
