"""Pure planner and controller tests."""

from __future__ import annotations

from dataclasses import replace
import unittest
from datetime import UTC, datetime, timedelta

from custom_components.esp_evse_surplus_manager.const import (
    DEFAULT_STATE_CHANGE_GUARD_SECONDS,
)
from custom_components.esp_evse_surplus_manager.controller import SurplusController
from custom_components.esp_evse_surplus_manager.models import PlannerCharger, PlannerInputs
from custom_components.esp_evse_surplus_manager.planner import plan_surplus
from custom_components.esp_evse_surplus_manager.simulation import run_timeline
from tests.scenario_fixtures import (
    charger_input,
    single_ev_surplus_wakeup_fixture,
    two_ev_threshold_transition_fixture,
)


TEST_TICK_SECONDS = 20
TEST_VOLTAGE_VOLTS = 230
TEST_HYSTERESIS_SECONDS = 5 * 60
TEST_STATE_CHANGE_GUARD_SECONDS = DEFAULT_STATE_CHANGE_GUARD_SECONDS
LOCKOUT_RELEASE_STEP = TEST_HYSTERESIS_SECONDS // TEST_TICK_SECONDS
LOCKOUT_LAST_BLOCKED_STEP = LOCKOUT_RELEASE_STEP - 1
LOCKOUT_OBSERVATION_COUNT = LOCKOUT_RELEASE_STEP + 1
STATE_CHANGE_RELEASE_STEP = TEST_STATE_CHANGE_GUARD_SECONDS // TEST_TICK_SECONDS


def _grid_power_for_available_amps(
    available_actual_amps: float,
    chargers: tuple,
    *,
    voltage_volts: float = TEST_VOLTAGE_VOLTS,
    max_grid_import_watts: float = 0.0,
) -> float:
    """Back-compute a grid reading that yields the requested site budget."""
    managed_actual_amps = sum(
        charger.measured_actual_amps
        for charger in chargers
        if not charger.manual_override
    )
    return (
        managed_actual_amps * voltage_volts
        - available_actual_amps * voltage_volts
        + max_grid_import_watts
    )


def _run_available_profile(
    controller: SurplusController,
    *,
    available_actual_amps: list[float],
    chargers: tuple,
    hysteresis_seconds: int = TEST_HYSTERESIS_SECONDS,
    measurement_fn,
) -> list:
    """Run a controller profile while feeding measured-current feedback."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    current_chargers = chargers
    snapshots = []

    for available_amps in available_actual_amps:
        snapshot = controller.step(
            now=now,
            grid_power_watts=_grid_power_for_available_amps(
                available_amps,
                current_chargers,
            ),
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=hysteresis_seconds,
            chargers=current_chargers,
        )
        snapshots.append(snapshot)

        current_chargers = tuple(
            replace(
                charger,
                enabled=(
                    charger.enabled
                    if charger.manual_override
                    else snapshot.charger(charger.charger_id).should_enable
                ),
                pilot_setpoint_amps=snapshot.charger(
                    charger.charger_id
                ).pilot_request_amps,
                measured_actual_amps=measurement_fn(
                    charger,
                    snapshot.charger(charger.charger_id),
                    (
                        charger.enabled
                        if charger.manual_override
                        else snapshot.charger(charger.charger_id).should_enable
                    ),
                ),
            )
            for charger in current_chargers
        )
        now += timedelta(seconds=TEST_TICK_SECONDS)

    return snapshots


class PlannerTests(unittest.TestCase):
    def test_manual_override_is_removed_from_managed_pool(self) -> None:
        result = plan_surplus(
            PlannerInputs(
                grid_power_watts=0,
                grid_voltage_volts=230,
                max_grid_import_watts=0,
                chargers=(
                    PlannerCharger(
                        charger_id="auto",
                        priority=1,
                        min_amps=6,
                        max_amps=32,
                        connected=True,
                        enabled=True,
                        manual_override=False,
                        pilot_setpoint_amps=6,
                        measured_actual_amps=6,
                        planning_eligible=True,
                    ),
                    PlannerCharger(
                        charger_id="manual",
                        priority=2,
                        min_amps=6,
                        max_amps=32,
                        connected=True,
                        enabled=True,
                        manual_override=True,
                        pilot_setpoint_amps=16,
                        measured_actual_amps=16,
                    ),
                ),
            )
        )

        self.assertEqual(result.managed_actual_current_amps, 6)
        self.assertEqual(result.desired_actual_amps["auto"], 6)
        self.assertEqual(result.desired_actual_amps["manual"], 0)

    def test_active_chargers_seed_before_idle(self) -> None:
        result = plan_surplus(
            PlannerInputs(
                grid_power_watts=0,
                grid_voltage_volts=230,
                max_grid_import_watts=3 * 230,
                chargers=(
                    PlannerCharger(
                        charger_id="active",
                        priority=10,
                        min_amps=6,
                        max_amps=32,
                        connected=True,
                        enabled=True,
                        manual_override=False,
                        pilot_setpoint_amps=10,
                        measured_actual_amps=10,
                        planning_eligible=True,
                    ),
                    PlannerCharger(
                        charger_id="idle",
                        priority=1,
                        min_amps=6,
                        max_amps=32,
                        connected=True,
                        enabled=False,
                        manual_override=False,
                        measured_actual_amps=0,
                    ),
                ),
            )
        )

        self.assertEqual(result.desired_actual_amps["active"], 13)
        self.assertEqual(result.desired_actual_amps["idle"], 0)
        self.assertEqual(result.wakeup_candidate_ids, ("idle",))

    def test_priority_remainder_distribution_is_balanced(self) -> None:
        result = plan_surplus(
            PlannerInputs(
                grid_power_watts=0,
                grid_voltage_volts=230,
                max_grid_import_watts=6 * 230,
                chargers=(
                    PlannerCharger(
                        charger_id="ev1",
                        priority=1,
                        min_amps=6,
                        max_amps=32,
                        connected=True,
                        enabled=True,
                        manual_override=False,
                        pilot_setpoint_amps=7,
                        measured_actual_amps=7,
                        planning_eligible=True,
                    ),
                    PlannerCharger(
                        charger_id="ev2",
                        priority=2,
                        min_amps=6,
                        max_amps=32,
                        connected=True,
                        enabled=True,
                        manual_override=False,
                        pilot_setpoint_amps=7,
                        measured_actual_amps=7,
                        planning_eligible=True,
                    ),
                ),
            )
        )

        self.assertEqual(result.desired_actual_amps["ev1"], 10)
        self.assertEqual(result.desired_actual_amps["ev2"], 10)

    def test_idle_bootstrap_car_does_not_consume_actual_budget(self) -> None:
        result = plan_surplus(
            PlannerInputs(
                grid_power_watts=0,
                grid_voltage_volts=230,
                max_grid_import_watts=20 * 230,
                chargers=(
                    PlannerCharger(
                        charger_id="active",
                        priority=1,
                        min_amps=6,
                        max_amps=32,
                        connected=True,
                        enabled=True,
                        manual_override=False,
                        pilot_setpoint_amps=12,
                        measured_actual_amps=12,
                        planning_eligible=True,
                    ),
                    PlannerCharger(
                        charger_id="idle",
                        priority=2,
                        min_amps=6,
                        max_amps=32,
                        connected=True,
                        enabled=False,
                        manual_override=False,
                        measured_actual_amps=0,
                    ),
                ),
            )
        )

        self.assertEqual(result.desired_actual_amps["active"], 32)
        self.assertEqual(result.desired_actual_amps["idle"], 0)
        self.assertEqual(result.wakeup_candidate_ids, ("idle",))

    def test_idle_charger_does_not_wake_without_spare_budget_after_active_allocations(self) -> None:
        result = plan_surplus(
            PlannerInputs(
                grid_power_watts=0,
                grid_voltage_volts=230,
                max_grid_import_watts=0,
                chargers=(
                    PlannerCharger(
                        charger_id="ev1",
                        priority=1,
                        min_amps=6,
                        max_amps=32,
                        connected=True,
                        enabled=True,
                        manual_override=False,
                        pilot_setpoint_amps=7,
                        measured_actual_amps=7,
                        planning_eligible=True,
                    ),
                    PlannerCharger(
                        charger_id="ev2",
                        priority=2,
                        min_amps=6,
                        max_amps=32,
                        connected=True,
                        enabled=False,
                        manual_override=False,
                        measured_actual_amps=0,
                    ),
                ),
            )
        )

        self.assertEqual(result.available_actual_amps, 7)
        self.assertEqual(result.desired_actual_amps["ev1"], 7)
        self.assertEqual(result.desired_actual_amps["ev2"], 0)
        self.assertEqual(result.wakeup_candidate_ids, ())


class SimulationTests(unittest.TestCase):
    def test_ramping_surplus_wakes_first_ev_then_second_and_rebalances(self) -> None:
        controller = SurplusController(["ev1", "ev2"])
        available_profile = [
            amps
            for amps in range(1, 14)
            for _ in range(LOCKOUT_OBSERVATION_COUNT)
        ]
        available_profile.extend([13, 32])

        snapshots = _run_available_profile(
            controller,
            available_actual_amps=available_profile,
            chargers=(
                charger_input("ev1", priority=1),
                charger_input("ev2", priority=2),
            ),
            measurement_fn=lambda charger, snapshot, enabled_next: (
                float(snapshot.desired_actual_amps)
                if enabled_next and snapshot.desired_actual_amps > 0
                else float(snapshot.pilot_request_amps)
                if enabled_next
                else 0.0
            ),
        )

        # Each plateau lasts a full hysteresis window before surplus increases again.
        self.assertFalse(snapshots[79].charger("ev1").should_enable)
        self.assertFalse(snapshots[79].charger("ev2").should_enable)
        self.assertTrue(snapshots[95].charger("ev1").should_enable)
        self.assertFalse(snapshots[95].charger("ev2").should_enable)
        self.assertFalse(snapshots[175].charger("ev2").should_enable)
        self.assertTrue(snapshots[179].charger("ev2").should_enable)
        self.assertEqual(snapshots[191].charger("ev1").desired_actual_amps, 6)
        self.assertTrue(snapshots[191].charger("ev2").should_enable)
        self.assertEqual(snapshots[192].charger("ev1").desired_actual_amps, 7)
        self.assertEqual(snapshots[192].charger("ev2").desired_actual_amps, 6)
        self.assertEqual(snapshots[209].charger("ev1").desired_actual_amps, 16)
        self.assertEqual(snapshots[209].charger("ev2").desired_actual_amps, 16)

    def test_single_ev_enables_after_five_minutes_of_surplus(self) -> None:
        fixture = single_ev_surplus_wakeup_fixture()
        timeline = run_timeline(
            fixture.controller,
            start=fixture.start,
            tick_seconds=fixture.tick_seconds,
            hysteresis_seconds=fixture.hysteresis_seconds,
            samples=fixture.samples,
        )

        self.assertEqual(
            timeline[LOCKOUT_LAST_BLOCKED_STEP].snapshot.charger("ev1").desired_actual_amps,
            0,
        )
        self.assertEqual(
            timeline[LOCKOUT_LAST_BLOCKED_STEP].snapshot.charger("ev1").pilot_request_amps,
            6,
        )
        self.assertFalse(
            timeline[LOCKOUT_LAST_BLOCKED_STEP].snapshot.charger("ev1").should_enable
        )
        self.assertEqual(
            timeline[LOCKOUT_RELEASE_STEP].snapshot.charger("ev1").desired_actual_amps,
            0,
        )
        self.assertEqual(
            timeline[LOCKOUT_RELEASE_STEP].snapshot.charger("ev1").pilot_request_amps,
            6,
        )
        self.assertTrue(timeline[LOCKOUT_RELEASE_STEP].snapshot.charger("ev1").should_enable)

    def test_sleeping_charger_holds_bootstrap_pilot_until_switch_is_observed_on(self) -> None:
        controller = SurplusController(["ev1"])
        start = datetime(2026, 1, 1, tzinfo=UTC)

        asleep_snapshot = controller.step(
            now=start,
            grid_power_watts=-10 * 230,
            grid_voltage_volts=230,
            max_grid_import_watts=0,
            hysteresis_seconds=0,
            chargers=(charger_input("ev1", priority=1, enabled=False),),
        )
        awake_snapshot = controller.step(
            now=start,
            grid_power_watts=-10 * 230,
            grid_voltage_volts=230,
            max_grid_import_watts=0,
            hysteresis_seconds=0,
            chargers=(charger_input("ev1", priority=1, enabled=True),),
        )
        active_snapshot = controller.step(
            now=datetime(2026, 1, 1, 0, 0, 20, tzinfo=UTC),
            grid_power_watts=0,
            grid_voltage_volts=230,
            max_grid_import_watts=0,
            hysteresis_seconds=0,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    measured_actual_amps=10,
                    pilot_setpoint_amps=10,
                ),
            ),
        )

        self.assertEqual(asleep_snapshot.charger("ev1").desired_actual_amps, 0)
        self.assertEqual(asleep_snapshot.charger("ev1").pilot_request_amps, 6)
        self.assertEqual(awake_snapshot.charger("ev1").pilot_request_amps, 6)
        self.assertEqual(active_snapshot.charger("ev1").pilot_request_amps, 10)

    def test_setpoint_sum_budget_is_shared_evenly_across_active_chargers(self) -> None:
        controller = SurplusController(["ev1", "ev2"])
        first_snapshot = controller.step(
            now=datetime(2026, 1, 1, tzinfo=UTC),
            grid_power_watts=0,
            grid_voltage_volts=230,
            max_grid_import_watts=4 * 230,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                charger_input("ev1", priority=1, enabled=True, measured_actual_amps=7),
                charger_input("ev2", priority=2, enabled=True, measured_actual_amps=6),
            ),
        )
        second_snapshot = controller.step(
            now=datetime(2026, 1, 1, 0, 0, 20, tzinfo=UTC),
            grid_power_watts=0,
            grid_voltage_volts=230,
            max_grid_import_watts=4 * 230,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    measured_actual_amps=7.5,
                    pilot_setpoint_amps=first_snapshot.charger("ev1").pilot_request_amps,
                ),
                charger_input(
                    "ev2",
                    priority=2,
                    enabled=True,
                    measured_actual_amps=6.5,
                    pilot_setpoint_amps=first_snapshot.charger("ev2").pilot_request_amps,
                ),
            ),
        )

        self.assertEqual(first_snapshot.charger("ev1").pilot_request_amps, 9)
        self.assertEqual(first_snapshot.charger("ev2").pilot_request_amps, 8)
        self.assertEqual(second_snapshot.charger("ev1").pilot_request_amps, 11)
        self.assertEqual(second_snapshot.charger("ev2").pilot_request_amps, 10)

    def test_single_ev_underdelivery_is_recovered_by_setpoint_sum_budget(self) -> None:
        controller = SurplusController(["ev1"])
        chargers = (
            charger_input(
                "ev1",
                priority=1,
                enabled=True,
                measured_actual_amps=6.5,
            ),
        )
        first_snapshot = controller.step(
            now=datetime(2026, 1, 1, tzinfo=UTC),
            grid_power_watts=_grid_power_for_available_amps(7.5, chargers),
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=chargers,
        )
        second_snapshot = controller.step(
            now=datetime(2026, 1, 1, 0, 0, TEST_TICK_SECONDS, tzinfo=UTC),
            grid_power_watts=_grid_power_for_available_amps(
                7.5,
                (
                    replace(
                        chargers[0],
                        enabled=first_snapshot.charger("ev1").should_enable,
                        measured_actual_amps=6.5,
                    ),
                ),
            ),
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                replace(
                    chargers[0],
                    enabled=first_snapshot.charger("ev1").should_enable,
                    measured_actual_amps=6.5,
                    pilot_setpoint_amps=first_snapshot.charger("ev1").pilot_request_amps,
                ),
            ),
        )

        self.assertEqual(first_snapshot.charger("ev1").desired_actual_amps, 7)
        self.assertEqual(first_snapshot.charger("ev1").pilot_request_amps, 7)
        self.assertEqual(second_snapshot.charger("ev1").desired_actual_amps, 8)
        self.assertEqual(second_snapshot.charger("ev1").pilot_request_amps, 8)

    def test_budget_tracks_previous_setpoint_sum_for_nominal_chargers_not_live_consumption(
        self,
    ) -> None:
        controller = SurplusController(["ev1"])
        steady_chargers = (
            charger_input(
                "ev1",
                priority=1,
                enabled=True,
                measured_actual_amps=16,
            ),
        )
        controller.step(
            now=datetime(2026, 1, 1, tzinfo=UTC),
            grid_power_watts=0,
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=steady_chargers,
        )

        slightly_underdelivering = (
            charger_input(
                "ev1",
                priority=1,
                enabled=True,
                measured_actual_amps=15.2,
            ),
        )
        snapshot = controller.step(
            now=datetime(2026, 1, 1, 0, 0, TEST_TICK_SECONDS, tzinfo=UTC),
            grid_power_watts=-TEST_VOLTAGE_VOLTS,
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=slightly_underdelivering,
        )

        self.assertEqual(snapshot.charger("ev1").desired_actual_amps, 17)
        self.assertEqual(snapshot.charger("ev1").pilot_request_amps, 17)

    def test_two_ev_threshold_transition_fixture(self) -> None:
        fixture = two_ev_threshold_transition_fixture()
        timeline = run_timeline(
            fixture.controller,
            start=fixture.start,
            tick_seconds=fixture.tick_seconds,
            hysteresis_seconds=fixture.hysteresis_seconds,
            samples=fixture.samples,
        )

        phase_one = timeline[LOCKOUT_RELEASE_STEP].snapshot
        phase_two = timeline[-1].snapshot

        self.assertEqual(phase_one.charger("ev1").desired_actual_amps, 0)
        self.assertEqual(phase_one.charger("ev1").pilot_request_amps, 6)
        self.assertTrue(phase_one.charger("ev1").should_enable)
        self.assertFalse(phase_one.charger("ev2").should_enable)
        self.assertEqual(phase_one.charger("ev2").desired_actual_amps, 0)

        self.assertEqual(phase_two.charger("ev1").desired_actual_amps, 0)
        self.assertEqual(phase_two.charger("ev1").pilot_request_amps, 6)
        self.assertTrue(phase_two.charger("ev1").should_enable)
        self.assertTrue(phase_two.charger("ev2").should_enable)
        self.assertEqual(phase_two.charger("ev2").desired_actual_amps, 0)
        self.assertEqual(phase_two.charger("ev2").pilot_request_amps, 6)

    def test_second_charger_does_not_enable_when_only_first_charger_fits_budget(self) -> None:
        controller = SurplusController(["ev1", "ev2"])
        start = datetime(2026, 1, 1, tzinfo=UTC)

        timeline = []
        for step in range(LOCKOUT_OBSERVATION_COUNT):
            timeline.append(
                controller.step(
                    now=start + timedelta(seconds=step * TEST_TICK_SECONDS),
                    grid_power_watts=0,
                    grid_voltage_volts=230,
                    max_grid_import_watts=0,
                    hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
                    chargers=(
                        charger_input(
                            "ev1",
                            priority=1,
                            enabled=True,
                            measured_actual_amps=7,
                        ),
                        charger_input("ev2", priority=2, enabled=False),
                    ),
                )
            )

        self.assertTrue(timeline[-1].charger("ev1").should_enable)
        self.assertFalse(timeline[-1].charger("ev2").should_enable)
        self.assertEqual(timeline[-1].charger("ev2").pilot_request_amps, 6)

    def test_enable_requires_one_minute_of_continuous_favorable_condition(
        self,
    ) -> None:
        controller = SurplusController(["ev1"])
        snapshots = _run_available_profile(
            controller,
            available_actual_amps=[6] * 12 + [5] + [6] * 12,
            chargers=(charger_input("ev1", priority=1),),
            measurement_fn=lambda charger, snapshot, enabled_next: 0.0,
        )

        self.assertTrue(
            all(
                not snapshot.charger("ev1").should_enable
                for snapshot in snapshots[: LOCKOUT_RELEASE_STEP + 1]
            )
        )
        self.assertTrue(
            snapshots[LOCKOUT_RELEASE_STEP + 1].charger("ev1").should_enable
        )

    def test_disable_hysteresis_ignores_single_bad_tick(self) -> None:
        controller = SurplusController(["ev1"])
        snapshots = _run_available_profile(
            controller,
            available_actual_amps=[6] * 12 + [5] + [6] * 12,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    measured_actual_amps=6,
                ),
            ),
            measurement_fn=lambda charger, snapshot, enabled_next: (
                6.0 if enabled_next else 0.0
            ),
        )

        self.assertTrue(all(snapshot.charger("ev1").should_enable for snapshot in snapshots))

    def test_manual_override_stationary_regime_keeps_other_ev_managed(self) -> None:
        controller = SurplusController(["ev1", "ev2"])
        snapshot = controller.step(
            now=datetime(2026, 1, 1, tzinfo=UTC),
            grid_power_watts=_grid_power_for_available_amps(
                7,
                (
                    charger_input(
                        "ev1",
                        priority=1,
                        enabled=True,
                        manual_override=True,
                        measured_actual_amps=16,
                    ),
                    charger_input(
                        "ev2",
                        priority=2,
                        enabled=True,
                        measured_actual_amps=7,
                    ),
                ),
            ),
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    manual_override=True,
                    measured_actual_amps=16,
                ),
                charger_input(
                    "ev2",
                    priority=2,
                    enabled=True,
                    measured_actual_amps=7,
                ),
            ),
        )

        self.assertEqual(snapshot.managed_actual_current_amps, 7)
        self.assertTrue(snapshot.charger("ev1").manual_override)
        self.assertEqual(snapshot.charger("ev1").desired_actual_amps, 0)
        self.assertEqual(snapshot.charger("ev1").pilot_request_amps, 0)
        self.assertEqual(snapshot.charger("ev2").desired_actual_amps, 7)

    def test_enabling_manual_override_while_charging_stops_management_and_treats_load_as_ordinary_house_load(
        self,
    ) -> None:
        controller = SurplusController(["ev1", "ev2"])
        before_override = controller.step(
            now=datetime(2026, 1, 1, tzinfo=UTC),
            grid_power_watts=-8 * TEST_VOLTAGE_VOLTS,
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    measured_actual_amps=18,
                ),
                charger_input(
                    "ev2",
                    priority=2,
                    enabled=True,
                    measured_actual_amps=6,
                ),
            ),
        )
        after_override = controller.step(
            now=datetime(2026, 1, 1, 0, 0, TEST_TICK_SECONDS, tzinfo=UTC),
            grid_power_watts=-8 * TEST_VOLTAGE_VOLTS,
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    manual_override=True,
                    measured_actual_amps=18,
                ),
                charger_input(
                    "ev2",
                    priority=2,
                    enabled=True,
                    measured_actual_amps=6,
                ),
            ),
        )

        self.assertEqual(before_override.available_actual_amps, 32)
        self.assertEqual(before_override.charger("ev2").desired_actual_amps, 16)

        self.assertEqual(after_override.available_actual_amps, 24)
        self.assertEqual(after_override.managed_actual_current_amps, 6)
        self.assertTrue(after_override.charger("ev1").manual_override)
        self.assertEqual(after_override.charger("ev1").desired_actual_amps, 0)
        self.assertEqual(after_override.charger("ev1").pilot_request_amps, 0)
        self.assertTrue(after_override.charger("ev1").should_enable)
        self.assertEqual(after_override.charger("ev2").desired_actual_amps, 24)
        self.assertEqual(after_override.charger("ev2").pilot_request_amps, 24)

    def test_disabling_manual_override_rebalances_immediately_if_charger_is_already_active(self) -> None:
        controller = SurplusController(["ev1", "ev2"])
        first_snapshot = controller.step(
            now=datetime(2026, 1, 1, tzinfo=UTC),
            grid_power_watts=_grid_power_for_available_amps(
                20,
                (
                    charger_input(
                        "ev1",
                        priority=1,
                        enabled=True,
                        manual_override=True,
                        measured_actual_amps=16,
                    ),
                    charger_input(
                        "ev2",
                        priority=2,
                        enabled=True,
                        measured_actual_amps=7,
                    ),
                ),
            ),
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    manual_override=True,
                    measured_actual_amps=16,
                ),
                charger_input(
                    "ev2",
                    priority=2,
                    enabled=True,
                    measured_actual_amps=7,
                ),
            ),
        )
        second_snapshot = controller.step(
            now=datetime(2026, 1, 1, 0, 0, TEST_TICK_SECONDS, tzinfo=UTC),
            grid_power_watts=_grid_power_for_available_amps(
                20,
                (
                    charger_input(
                        "ev1",
                        priority=1,
                        enabled=True,
                        manual_override=False,
                        measured_actual_amps=16,
                    ),
                    charger_input(
                        "ev2",
                        priority=2,
                        enabled=True,
                        measured_actual_amps=7,
                    ),
                ),
            ),
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=first_snapshot.charger("ev1").should_enable,
                    manual_override=False,
                    measured_actual_amps=16,
                ),
                charger_input(
                    "ev2",
                    priority=2,
                    enabled=first_snapshot.charger("ev2").should_enable,
                    measured_actual_amps=7,
                ),
            ),
        )

        self.assertFalse(second_snapshot.charger("ev1").manual_override)
        self.assertTrue(second_snapshot.charger("ev1").should_enable)
        self.assertEqual(second_snapshot.charger("ev1").desired_actual_amps, 17)
        self.assertEqual(second_snapshot.charger("ev1").pilot_request_amps, 17)
        self.assertEqual(second_snapshot.charger("ev2").desired_actual_amps, 16)
        self.assertEqual(second_snapshot.charger("ev2").pilot_request_amps, 16)

    def test_manual_override_while_bootstrapping_fully_backs_off_and_never_auto_enables(self) -> None:
        controller = SurplusController(["ev2"])
        first_snapshot = controller.step(
            now=datetime(2026, 1, 1, tzinfo=UTC),
            grid_power_watts=-6 * TEST_VOLTAGE_VOLTS,
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(charger_input("ev2", priority=1),),
        )

        snapshots = [first_snapshot]
        start = datetime(2026, 1, 1, 0, 0, TEST_TICK_SECONDS, tzinfo=UTC)
        for step in range(LOCKOUT_OBSERVATION_COUNT):
            snapshots.append(
                controller.step(
                    now=start + timedelta(seconds=step * TEST_TICK_SECONDS),
                    grid_power_watts=-6 * TEST_VOLTAGE_VOLTS,
                    grid_voltage_volts=TEST_VOLTAGE_VOLTS,
                    max_grid_import_watts=0,
                    hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
                    chargers=(
                        charger_input(
                            "ev2",
                            priority=1,
                            manual_override=True,
                        ),
                    ),
                )
            )

        self.assertEqual(first_snapshot.charger("ev2").pilot_request_amps, 6)
        self.assertFalse(first_snapshot.charger("ev2").should_enable)
        self.assertTrue(all(snapshot.charger("ev2").manual_override for snapshot in snapshots[1:]))
        self.assertTrue(all(snapshot.charger("ev2").pilot_request_amps == 0 for snapshot in snapshots[1:]))
        self.assertTrue(all(not snapshot.charger("ev2").should_enable for snapshot in snapshots[1:]))

    def test_disabling_manual_override_enables_immediately_if_surplus_has_already_been_stable_for_five_minutes(
        self,
    ) -> None:
        controller = SurplusController(["ev2"])
        start = datetime(2026, 1, 1, tzinfo=UTC)

        for step in range(LOCKOUT_OBSERVATION_COUNT):
            snapshot = controller.step(
                now=start + timedelta(seconds=step * TEST_TICK_SECONDS),
                grid_power_watts=-10 * TEST_VOLTAGE_VOLTS,
                grid_voltage_volts=TEST_VOLTAGE_VOLTS,
                max_grid_import_watts=0,
                hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
                chargers=(
                    charger_input(
                        "ev2",
                        priority=1,
                        enabled=False,
                        manual_override=True,
                    ),
                ),
            )
            self.assertFalse(snapshot.charger("ev2").should_enable)

        released_snapshot = controller.step(
            now=start + timedelta(seconds=LOCKOUT_OBSERVATION_COUNT * TEST_TICK_SECONDS),
            grid_power_watts=-10 * TEST_VOLTAGE_VOLTS,
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(charger_input("ev2", priority=1, enabled=False),),
        )

        self.assertFalse(released_snapshot.charger("ev2").manual_override)
        self.assertEqual(released_snapshot.charger("ev2").pilot_request_amps, 6)
        self.assertTrue(released_snapshot.charger("ev2").should_enable)

    def test_plugging_in_enables_immediately_when_surplus_has_already_been_stable(
        self,
    ) -> None:
        controller = SurplusController(["ev1"])
        start = datetime(2026, 1, 1, tzinfo=UTC)

        for step in range(LOCKOUT_OBSERVATION_COUNT):
            snapshot = controller.step(
                now=start + timedelta(seconds=step * TEST_TICK_SECONDS),
                grid_power_watts=-10 * TEST_VOLTAGE_VOLTS,
                grid_voltage_volts=TEST_VOLTAGE_VOLTS,
                max_grid_import_watts=0,
                hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
                chargers=(
                    charger_input(
                        "ev1",
                        priority=1,
                        connected=False,
                        enabled=False,
                    ),
                ),
            )
            self.assertFalse(snapshot.charger("ev1").should_enable)

        plugged_snapshot = controller.step(
            now=start + timedelta(seconds=LOCKOUT_OBSERVATION_COUNT * TEST_TICK_SECONDS),
            grid_power_watts=-10 * TEST_VOLTAGE_VOLTS,
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    connected=True,
                    enabled=False,
                ),
            ),
        )

        self.assertEqual(plugged_snapshot.charger("ev1").pilot_request_amps, 6)
        self.assertTrue(plugged_snapshot.charger("ev1").should_enable)

    def test_manual_switch_off_waits_five_minutes_before_reenable_when_surplus_stays_favorable(
        self,
    ) -> None:
        controller = SurplusController(["ev1"])
        start = datetime(2026, 1, 1, tzinfo=UTC)

        initial_snapshot = controller.step(
            now=start,
            grid_power_watts=0,
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    measured_actual_amps=10,
                ),
            ),
        )

        snapshots = []
        for step in range(1, LOCKOUT_OBSERVATION_COUNT + 1):
            snapshots.append(
                controller.step(
                    now=start + timedelta(seconds=step * TEST_TICK_SECONDS),
                    grid_power_watts=-10 * TEST_VOLTAGE_VOLTS,
                    grid_voltage_volts=TEST_VOLTAGE_VOLTS,
                    max_grid_import_watts=0,
                    hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
                    chargers=(charger_input("ev1", priority=1, enabled=False),),
                )
            )

        self.assertTrue(initial_snapshot.charger("ev1").should_enable)
        self.assertTrue(all(not snapshot.charger("ev1").should_enable for snapshot in snapshots[:-1]))
        self.assertTrue(snapshots[-1].charger("ev1").should_enable)

    def test_two_active_chargers_reallocate_after_one_minute_of_persistent_shortfall_when_lockout_has_expired(
        self,
    ) -> None:
        controller = SurplusController(["ev1", "ev2"])
        snapshots = _run_available_profile(
            controller,
            available_actual_amps=[12] * LOCKOUT_OBSERVATION_COUNT
            + [11] * (STATE_CHANGE_RELEASE_STEP + 1),
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    measured_actual_amps=6,
                ),
                charger_input(
                    "ev2",
                    priority=2,
                    enabled=True,
                    measured_actual_amps=6,
                ),
            ),
            measurement_fn=lambda charger, snapshot, enabled_next: (
                float(snapshot.pilot_request_amps) if enabled_next else 0.0
            ),
        )

        before_cloud = snapshots[LOCKOUT_OBSERVATION_COUNT - 1]
        first_cloud_tick = snapshots[LOCKOUT_OBSERVATION_COUNT]
        shutdown_tick = snapshots[-1]

        self.assertTrue(before_cloud.charger("ev1").should_enable)
        self.assertTrue(before_cloud.charger("ev2").should_enable)
        self.assertEqual(before_cloud.charger("ev1").pilot_request_amps, 6)
        self.assertEqual(before_cloud.charger("ev2").pilot_request_amps, 6)

        self.assertTrue(first_cloud_tick.charger("ev1").should_enable)
        self.assertTrue(first_cloud_tick.charger("ev2").should_enable)
        self.assertEqual(first_cloud_tick.charger("ev1").pilot_request_amps, 6)
        self.assertEqual(first_cloud_tick.charger("ev2").pilot_request_amps, 6)

        self.assertTrue(shutdown_tick.charger("ev1").should_enable)
        self.assertFalse(shutdown_tick.charger("ev2").should_enable)
        self.assertEqual(shutdown_tick.charger("ev1").desired_actual_amps, 11)
        self.assertEqual(shutdown_tick.charger("ev1").pilot_request_amps, 11)
        self.assertEqual(shutdown_tick.charger("ev2").desired_actual_amps, 0)

    def test_recently_enabled_low_priority_charger_stays_on_if_cloud_passes_before_lockout_expires(
        self,
    ) -> None:
        controller = SurplusController(["ev1", "ev2"])
        snapshots = _run_available_profile(
            controller,
            available_actual_amps=[11] * LOCKOUT_LAST_BLOCKED_STEP + [12] * 3,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    measured_actual_amps=6,
                ),
                charger_input(
                    "ev2",
                    priority=2,
                    enabled=True,
                    measured_actual_amps=6,
                ),
            ),
            measurement_fn=lambda charger, snapshot, enabled_next: (
                float(snapshot.pilot_request_amps) if enabled_next else 0.0
            ),
        )

        last_cloudy_tick = snapshots[LOCKOUT_LAST_BLOCKED_STEP - 1]
        first_clear_tick = snapshots[LOCKOUT_LAST_BLOCKED_STEP]
        final_tick = snapshots[-1]

        self.assertTrue(
            all(snapshot.charger("ev1").should_enable for snapshot in snapshots)
        )
        self.assertTrue(
            all(snapshot.charger("ev2").should_enable for snapshot in snapshots)
        )
        self.assertEqual(last_cloudy_tick.charger("ev1").pilot_request_amps, 6)
        self.assertEqual(last_cloudy_tick.charger("ev2").pilot_request_amps, 6)
        self.assertEqual(first_clear_tick.charger("ev1").desired_actual_amps, 6)
        self.assertEqual(first_clear_tick.charger("ev2").desired_actual_amps, 6)
        self.assertEqual(final_tick.charger("ev1").pilot_request_amps, 6)
        self.assertEqual(final_tick.charger("ev2").pilot_request_amps, 6)

    def test_recently_enabled_low_priority_charger_drops_and_reallocates_on_lockout_expiry_if_cloud_persists(
        self,
    ) -> None:
        controller = SurplusController(["ev1", "ev2"])
        snapshots = _run_available_profile(
            controller,
            available_actual_amps=[11] * (LOCKOUT_RELEASE_STEP + 1),
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    measured_actual_amps=6,
                ),
                charger_input(
                    "ev2",
                    priority=2,
                    enabled=True,
                    measured_actual_amps=6,
                ),
            ),
            measurement_fn=lambda charger, snapshot, enabled_next: (
                float(snapshot.pilot_request_amps) if enabled_next else 0.0
            ),
        )

        still_locked = snapshots[LOCKOUT_LAST_BLOCKED_STEP]
        release_tick = snapshots[LOCKOUT_RELEASE_STEP]

        self.assertTrue(still_locked.charger("ev1").should_enable)
        self.assertTrue(still_locked.charger("ev2").should_enable)
        self.assertEqual(still_locked.charger("ev1").pilot_request_amps, 6)
        self.assertEqual(still_locked.charger("ev2").pilot_request_amps, 6)

        self.assertTrue(release_tick.charger("ev1").should_enable)
        self.assertFalse(release_tick.charger("ev2").should_enable)
        self.assertEqual(release_tick.charger("ev1").desired_actual_amps, 11)
        self.assertEqual(release_tick.charger("ev1").pilot_request_amps, 11)
        self.assertEqual(release_tick.charger("ev2").desired_actual_amps, 0)

    def test_higher_priority_charger_enables_immediately_if_surplus_was_already_stable(
        self,
    ) -> None:
        controller = SurplusController(["high", "low"])
        start = datetime(2026, 1, 1, tzinfo=UTC)
        current_chargers = (
            charger_input(
                "high",
                priority=1,
                connected=False,
                enabled=False,
            ),
            charger_input(
                "low",
                priority=2,
                enabled=True,
                measured_actual_amps=10,
            ),
        )

        # Keep the low-priority charger on long enough for its disable lockout to expire.
        for step in range(LOCKOUT_OBSERVATION_COUNT):
            snapshot = controller.step(
                now=start + timedelta(seconds=step * TEST_TICK_SECONDS),
                grid_power_watts=_grid_power_for_available_amps(10, current_chargers),
                grid_voltage_volts=TEST_VOLTAGE_VOLTS,
                max_grid_import_watts=0,
                hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
                chargers=current_chargers,
            )
            self.assertTrue(snapshot.charger("low").should_enable)
            self.assertFalse(snapshot.charger("high").should_enable)
            current_chargers = tuple(
                replace(
                    charger,
                    enabled=snapshot.charger(charger.charger_id).should_enable,
                    pilot_setpoint_amps=snapshot.charger(
                        charger.charger_id
                    ).pilot_request_amps,
                    measured_actual_amps=(
                        float(snapshot.charger(charger.charger_id).desired_actual_amps)
                        if snapshot.charger(charger.charger_id).should_enable
                        and snapshot.charger(charger.charger_id).desired_actual_amps > 0
                        else float(
                            snapshot.charger(charger.charger_id).pilot_request_amps
                        )
                        if snapshot.charger(charger.charger_id).should_enable
                        else 0.0
                    ),
                )
                for charger in current_chargers
            )

        takeover_snapshots = []
        current_chargers = (
            replace(current_chargers[0], connected=True),
            current_chargers[1],
        )
        for step in range((STATE_CHANGE_RELEASE_STEP * 2) + 2):
            takeover_snapshots.append(
                controller.step(
                    now=start
                    + timedelta(
                        seconds=(LOCKOUT_OBSERVATION_COUNT + step) * TEST_TICK_SECONDS
                    ),
                    grid_power_watts=_grid_power_for_available_amps(10, current_chargers),
                    grid_voltage_volts=TEST_VOLTAGE_VOLTS,
                    max_grid_import_watts=0,
                    hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
                    chargers=current_chargers,
                )
            )
            current_chargers = tuple(
                replace(
                    charger,
                    enabled=takeover_snapshots[-1].charger(charger.charger_id).should_enable,
                    pilot_setpoint_amps=takeover_snapshots[-1].charger(
                        charger.charger_id
                    ).pilot_request_amps,
                    measured_actual_amps=(
                        float(
                            takeover_snapshots[-1].charger(
                                charger.charger_id
                            ).desired_actual_amps
                        )
                        if takeover_snapshots[-1].charger(
                            charger.charger_id
                        ).should_enable
                        and takeover_snapshots[-1].charger(
                            charger.charger_id
                        ).desired_actual_amps > 0
                        else float(
                            takeover_snapshots[-1].charger(
                                charger.charger_id
                            ).pilot_request_amps
                        )
                        if takeover_snapshots[-1].charger(
                            charger.charger_id
                        ).should_enable
                        else 0.0
                    ),
                )
                for charger in current_chargers
            )

        self.assertTrue(takeover_snapshots[0].charger("high").should_enable)
        self.assertEqual(takeover_snapshots[0].charger("high").pilot_request_amps, 6)
        self.assertTrue(takeover_snapshots[-1].charger("high").should_enable)
        self.assertEqual(takeover_snapshots[-1].charger("high").pilot_request_amps, 10)
        self.assertFalse(takeover_snapshots[-1].charger("low").should_enable)

    def test_unplugged_higher_priority_charger_is_skipped_when_lower_priority_one_becomes_viable(
        self,
    ) -> None:
        controller = SurplusController(["ev1", "ev2", "ev3"])
        start = datetime(2026, 1, 1, tzinfo=UTC)

        current_chargers = (
            charger_input(
                "ev1",
                priority=1,
                connected=True,
                enabled=True,
                measured_actual_amps=11,
                pilot_setpoint_amps=11,
            ),
            charger_input(
                "ev2",
                priority=2,
                connected=False,
                enabled=False,
            ),
            charger_input(
                "ev3",
                priority=3,
                connected=True,
                enabled=False,
            ),
        )

        for step in range(LOCKOUT_OBSERVATION_COUNT):
            snapshot = controller.step(
                now=start + timedelta(seconds=step * TEST_TICK_SECONDS),
                grid_power_watts=_grid_power_for_available_amps(11, current_chargers),
                grid_voltage_volts=TEST_VOLTAGE_VOLTS,
                max_grid_import_watts=0,
                hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
                chargers=current_chargers,
            )
            self.assertTrue(snapshot.charger("ev1").should_enable)
            self.assertFalse(snapshot.charger("ev2").should_enable)
            self.assertFalse(snapshot.charger("ev3").should_enable)

            current_chargers = tuple(
                replace(
                    charger,
                    enabled=snapshot.charger(charger.charger_id).should_enable,
                    pilot_setpoint_amps=snapshot.charger(
                        charger.charger_id
                    ).pilot_request_amps,
                    measured_actual_amps=(
                        float(snapshot.charger(charger.charger_id).pilot_request_amps)
                        if snapshot.charger(charger.charger_id).should_enable
                        and charger.connected
                        else 0.0
                    ),
                )
                for charger in current_chargers
            )

        snapshots = []
        for step in range(STATE_CHANGE_RELEASE_STEP + 2):
            snapshots.append(
                controller.step(
                    now=start
                    + timedelta(
                        seconds=(LOCKOUT_OBSERVATION_COUNT + step) * TEST_TICK_SECONDS
                    ),
                    grid_power_watts=_grid_power_for_available_amps(12, current_chargers),
                    grid_voltage_volts=TEST_VOLTAGE_VOLTS,
                    max_grid_import_watts=0,
                    hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
                    chargers=current_chargers,
                )
            )
            current_chargers = tuple(
                replace(
                    charger,
                    enabled=snapshots[-1].charger(charger.charger_id).should_enable,
                    pilot_setpoint_amps=snapshots[-1].charger(
                        charger.charger_id
                    ).pilot_request_amps,
                    measured_actual_amps=(
                        float(snapshots[-1].charger(charger.charger_id).pilot_request_amps)
                        if snapshots[-1].charger(charger.charger_id).should_enable
                        and charger.connected
                        else 0.0
                    ),
                )
                for charger in current_chargers
            )

        first_12a_tick = snapshots[0]
        enable_tick = snapshots[STATE_CHANGE_RELEASE_STEP]
        rebalance_tick = snapshots[STATE_CHANGE_RELEASE_STEP + 1]

        self.assertEqual(
            first_12a_tick.allocator_attributes["preferred_enabled_ids"],
            ["ev1", "ev3"],
        )
        self.assertEqual(
            first_12a_tick.allocator_attributes["wakeup_candidate_ids"],
            ["ev3"],
        )
        self.assertTrue(first_12a_tick.charger("ev1").should_enable)
        self.assertFalse(first_12a_tick.charger("ev2").should_enable)
        self.assertFalse(first_12a_tick.charger("ev3").should_enable)
        self.assertEqual(first_12a_tick.charger("ev3").pilot_request_amps, 6)

        self.assertTrue(enable_tick.charger("ev1").should_enable)
        self.assertFalse(enable_tick.charger("ev2").should_enable)
        self.assertTrue(enable_tick.charger("ev3").should_enable)
        self.assertEqual(enable_tick.charger("ev3").pilot_request_amps, 6)
        self.assertEqual(enable_tick.charger("ev3").desired_actual_amps, 0)

        self.assertTrue(rebalance_tick.charger("ev1").should_enable)
        self.assertFalse(rebalance_tick.charger("ev2").should_enable)
        self.assertTrue(rebalance_tick.charger("ev3").should_enable)
        self.assertEqual(rebalance_tick.charger("ev1").desired_actual_amps, 6)
        self.assertEqual(rebalance_tick.charger("ev3").desired_actual_amps, 6)

    def test_connected_but_full_higher_priority_charger_is_tried_first_then_skipped_for_future_slots(
        self,
    ) -> None:
        controller = SurplusController(["ev1", "ev2", "ev3"])
        start = datetime(2026, 1, 1, tzinfo=UTC)

        current_chargers = (
            charger_input(
                "ev1",
                priority=1,
                connected=True,
                enabled=True,
                measured_actual_amps=11,
                pilot_setpoint_amps=11,
            ),
            charger_input(
                "ev2",
                priority=2,
                connected=True,
                charging=False,
                enabled=False,
                measured_actual_amps=0,
            ),
            charger_input(
                "ev3",
                priority=3,
                connected=True,
                enabled=False,
                measured_actual_amps=0,
            ),
        )

        for step in range(LOCKOUT_OBSERVATION_COUNT):
            snapshot = controller.step(
                now=start + timedelta(seconds=step * TEST_TICK_SECONDS),
                grid_power_watts=_grid_power_for_available_amps(11, current_chargers),
                grid_voltage_volts=TEST_VOLTAGE_VOLTS,
                max_grid_import_watts=0,
                hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
                chargers=current_chargers,
            )
            current_chargers = tuple(
                replace(
                    charger,
                    enabled=snapshot.charger(charger.charger_id).should_enable,
                    pilot_setpoint_amps=snapshot.charger(
                        charger.charger_id
                    ).pilot_request_amps,
                    measured_actual_amps=(
                        0.0
                        if not snapshot.charger(charger.charger_id).should_enable
                        else 0.0
                        if charger.charger_id == "ev2"
                        else float(snapshot.charger(charger.charger_id).desired_actual_amps)
                        if snapshot.charger(charger.charger_id).desired_actual_amps > 0
                        else float(snapshot.charger(charger.charger_id).pilot_request_amps)
                    ),
                    charging=False if charger.charger_id == "ev2" else None,
                )
                for charger in current_chargers
            )

        snapshots = []
        for step in range((STATE_CHANGE_RELEASE_STEP * 2) + 3):
            snapshots.append(
                controller.step(
                    now=start
                    + timedelta(
                        seconds=(LOCKOUT_OBSERVATION_COUNT + step) * TEST_TICK_SECONDS
                    ),
                    grid_power_watts=_grid_power_for_available_amps(12, current_chargers),
                    grid_voltage_volts=TEST_VOLTAGE_VOLTS,
                    max_grid_import_watts=0,
                    hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
                    chargers=current_chargers,
                )
            )
            current_chargers = tuple(
                replace(
                    charger,
                    enabled=snapshots[-1].charger(charger.charger_id).should_enable,
                    pilot_setpoint_amps=snapshots[-1].charger(
                        charger.charger_id
                    ).pilot_request_amps,
                    measured_actual_amps=(
                        0.0
                        if not snapshots[-1].charger(charger.charger_id).should_enable
                        else 0.0
                        if charger.charger_id == "ev2"
                        else float(
                            snapshots[-1].charger(charger.charger_id).desired_actual_amps
                        )
                        if snapshots[-1].charger(charger.charger_id).desired_actual_amps > 0
                        else float(
                            snapshots[-1].charger(charger.charger_id).pilot_request_amps
                        )
                    ),
                    charging=False if charger.charger_id == "ev2" else None,
                )
                for charger in current_chargers
            )

        first_enabled_tick = snapshots[STATE_CHANGE_RELEASE_STEP]
        next_candidate_tick = snapshots[STATE_CHANGE_RELEASE_STEP + 1]
        lower_priority_enable_tick = snapshots[(STATE_CHANGE_RELEASE_STEP * 2) + 1]
        rebalance_tick = snapshots[(STATE_CHANGE_RELEASE_STEP * 2) + 2]

        self.assertEqual(
            first_enabled_tick.allocator_attributes["preferred_enabled_ids"],
            ["ev1", "ev2"],
        )
        self.assertTrue(first_enabled_tick.charger("ev2").should_enable)
        self.assertFalse(first_enabled_tick.charger("ev3").should_enable)

        self.assertEqual(
            next_candidate_tick.allocator_attributes["planning_eligible_ids"],
            ["ev1"],
        )
        self.assertEqual(
            next_candidate_tick.allocator_attributes["preferred_enabled_ids"],
            ["ev1", "ev3"],
        )
        self.assertTrue(next_candidate_tick.charger("ev2").should_enable)
        self.assertFalse(next_candidate_tick.charger("ev3").should_enable)

        self.assertTrue(lower_priority_enable_tick.charger("ev2").should_enable)
        self.assertTrue(lower_priority_enable_tick.charger("ev3").should_enable)
        self.assertEqual(lower_priority_enable_tick.charger("ev3").desired_actual_amps, 0)
        self.assertEqual(lower_priority_enable_tick.charger("ev3").pilot_request_amps, 6)

        self.assertEqual(
            rebalance_tick.allocator_attributes["planning_eligible_ids"],
            ["ev1", "ev3"],
        )
        self.assertEqual(
            rebalance_tick.allocator_attributes["preferred_enabled_ids"],
            ["ev1", "ev3"],
        )
        self.assertTrue(rebalance_tick.charger("ev2").should_enable)
        self.assertEqual(rebalance_tick.charger("ev2").desired_actual_amps, 0)
        self.assertEqual(rebalance_tick.charger("ev2").pilot_request_amps, 6)
        self.assertTrue(rebalance_tick.charger("ev3").should_enable)
        self.assertEqual(rebalance_tick.charger("ev1").desired_actual_amps, 6)
        self.assertEqual(rebalance_tick.charger("ev3").desired_actual_amps, 6)

    def test_paused_car_keeps_bootstrap_pilot_while_active_car_gets_rest(self) -> None:
        controller = SurplusController(["ev1", "ev2"])
        snapshot = controller.step(
            now=datetime(2026, 1, 1, tzinfo=UTC),
            grid_power_watts=0,
            grid_voltage_volts=230,
            max_grid_import_watts=6 * 230,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                charger_input("ev1", priority=1, enabled=True, measured_actual_amps=0),
                charger_input("ev2", priority=2, enabled=True, measured_actual_amps=10),
            ),
        )

        self.assertEqual(snapshot.charger("ev1").pilot_request_amps, 6)
        self.assertEqual(snapshot.charger("ev1").desired_actual_amps, 0)
        self.assertEqual(snapshot.charger("ev2").desired_actual_amps, 16)

    def test_single_ev_pause_falls_back_to_bootstrap_and_then_recovers_cleanly(self) -> None:
        controller = SurplusController(["ev1"])
        steady_chargers = (
            charger_input(
                "ev1",
                priority=1,
                enabled=True,
                measured_actual_amps=16,
                pilot_setpoint_amps=16,
            ),
        )
        paused_chargers = (
            charger_input(
                "ev1",
                priority=1,
                enabled=True,
                charging=False,
                measured_actual_amps=2,
                pilot_setpoint_amps=16,
            ),
        )

        # Steady charging, then a paused EV with a stale 16 A pilot, then the
        # observed 6 A bootstrap state, then normal charging again.
        steady_snapshot = controller.step(
            now=datetime(2026, 1, 1, tzinfo=UTC),
            grid_power_watts=_grid_power_for_available_amps(16, steady_chargers),
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=steady_chargers,
        )
        paused_snapshot = controller.step(
            now=datetime(2026, 1, 1, 0, 0, TEST_TICK_SECONDS, tzinfo=UTC),
            grid_power_watts=_grid_power_for_available_amps(16, paused_chargers),
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=paused_chargers,
        )
        still_paused_snapshot = controller.step(
            now=datetime(2026, 1, 1, 0, 0, TEST_TICK_SECONDS * 2, tzinfo=UTC),
            grid_power_watts=_grid_power_for_available_amps(
                16,
                (
                    charger_input(
                        "ev1",
                        priority=1,
                        enabled=True,
                        charging=False,
                        measured_actual_amps=2,
                        pilot_setpoint_amps=6,
                    ),
                ),
            ),
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    charging=False,
                    measured_actual_amps=2,
                    pilot_setpoint_amps=6,
                ),
            ),
        )
        recovery_snapshot = controller.step(
            now=datetime(2026, 1, 1, tzinfo=UTC)
            + timedelta(seconds=TEST_TICK_SECONDS * 3),
            grid_power_watts=_grid_power_for_available_amps(
                16,
                (
                    charger_input(
                        "ev1",
                        priority=1,
                        enabled=True,
                        measured_actual_amps=6,
                        pilot_setpoint_amps=6,
                    ),
                ),
            ),
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=(
                charger_input(
                    "ev1",
                    priority=1,
                    enabled=True,
                    measured_actual_amps=6,
                    pilot_setpoint_amps=6,
                ),
            ),
        )

        self.assertEqual(steady_snapshot.charger("ev1").pilot_request_amps, 16)
        self.assertEqual(paused_snapshot.charger("ev1").desired_actual_amps, 0)
        self.assertEqual(paused_snapshot.charger("ev1").pilot_request_amps, 6)
        self.assertEqual(still_paused_snapshot.charger("ev1").pilot_request_amps, 6)
        self.assertTrue(paused_snapshot.charger("ev1").should_enable)
        self.assertTrue(still_paused_snapshot.charger("ev1").should_enable)
        self.assertEqual(recovery_snapshot.charger("ev1").pilot_request_amps, 16)
        self.assertTrue(recovery_snapshot.charger("ev1").should_enable)

    def test_allocator_state_exposes_readable_explanation_and_structured_details(self) -> None:
        controller = SurplusController(["ev1"])
        chargers = (
            charger_input(
                "ev1",
                priority=1,
                enabled=True,
                measured_actual_amps=14,
            ),
        )

        controller.step(
            now=datetime(2026, 1, 1, tzinfo=UTC),
            grid_power_watts=_grid_power_for_available_amps(15, chargers),
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=chargers,
        )
        snapshot = controller.step(
            now=datetime(2026, 1, 1, 0, 0, TEST_TICK_SECONDS, tzinfo=UTC),
            grid_power_watts=_grid_power_for_available_amps(15, chargers),
            grid_voltage_volts=TEST_VOLTAGE_VOLTS,
            max_grid_import_watts=0,
            hysteresis_seconds=TEST_HYSTERESIS_SECONDS,
            chargers=chargers,
        )

        self.assertIn("grid +1 A", snapshot.allocator_state)
        self.assertIn("ev1 16->16 A on", snapshot.allocator_state)
        self.assertIn("Power meter says grid is exporting 230 W.", snapshot.allocator_explanation)
        self.assertIn("delta is 0 - (-230) = +230 W", snapshot.allocator_explanation)
        self.assertIn(
            "Plannable chargers on this tick are ev1.",
            snapshot.allocator_explanation,
        )
        self.assertEqual(snapshot.allocator_attributes["grid_delta_amps_whole"], 1)
        self.assertEqual(snapshot.allocator_attributes["managed_planned_current_amps"], 15)
        self.assertEqual(
            snapshot.allocator_attributes["planning_eligible_ids"],
            ["ev1"],
        )
        self.assertEqual(
            snapshot.allocator_attributes["charger_decisions"][0]["observed_setpoint_amps"],
            15,
        )
        self.assertIn(
            "available=16A",
            snapshot.allocator_attributes["raw_allocator_state"],
        )


if __name__ == "__main__":
    unittest.main()
