"""Offline scenario helpers for timing-sensitive controller tests."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .controller import ControllerChargerInput, SurplusController
from .models import IntegrationSnapshot


@dataclass(slots=True, frozen=True)
class SimulationSample:
    """One repeated controller input for a timeline segment."""

    repeat: int
    grid_power_watts: float
    grid_voltage_volts: float
    max_grid_import_watts: float
    chargers: tuple[ControllerChargerInput, ...]


@dataclass(slots=True, frozen=True)
class SimulationTick:
    """One evaluated point on the simulation timeline."""

    at: datetime
    snapshot: IntegrationSnapshot


def run_timeline(
    controller: SurplusController,
    *,
    start: datetime,
    tick_seconds: int,
    hysteresis_seconds: int,
    samples: list[SimulationSample],
    apply_control: bool = True,
) -> list[SimulationTick]:
    """Run a discrete-time simulation over repeated controller samples.

    When `apply_control` is true, the previous tick's `should_enable` output is fed
    into the next repeated sample as the observed charger-enabled state.
    """
    now = start
    timeline: list[SimulationTick] = []

    for sample in samples:
        current_chargers = list(sample.chargers)
        for _ in range(sample.repeat):
            snapshot = controller.step(
                now=now,
                grid_power_watts=sample.grid_power_watts,
                grid_voltage_volts=sample.grid_voltage_volts,
                max_grid_import_watts=sample.max_grid_import_watts,
                hysteresis_seconds=hysteresis_seconds,
                chargers=tuple(current_chargers),
            )
            timeline.append(SimulationTick(at=now, snapshot=snapshot))

            if apply_control:
                current_chargers = [
                    replace(
                        charger,
                        enabled=snapshot.charger(charger.charger_id).should_enable,
                        pilot_setpoint_amps=snapshot.charger(
                            charger.charger_id
                        ).pilot_request_amps,
                    )
                    for charger in current_chargers
                ]

            now += timedelta(seconds=tick_seconds)

    return timeline
