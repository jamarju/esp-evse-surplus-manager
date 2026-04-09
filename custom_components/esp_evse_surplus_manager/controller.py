"""Pure controller logic shared by Home Assistant and offline simulation tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any

from .const import DEFAULT_GRID_VOLTAGE, DEFAULT_STATE_CHANGE_GUARD_SECONDS
from .models import (
    ChargerSnapshot,
    IntegrationSnapshot,
    PlannerCharger,
    PlannerInputs,
)
from .planner import plan_surplus


def _pilot_request_for_target(
    desired_actual_amps: int,
    min_amps: int,
    max_amps: int,
) -> int:
    """Translate an actual-current target into a whole-amp pilot request."""
    if desired_actual_amps <= 0:
        return min_amps
    return max(min_amps, min(max_amps, desired_actual_amps))


def _power_flow_text(power_watts: float) -> str:
    """Render a signed grid power reading as import/export text."""
    if math.isclose(power_watts, 0.0, abs_tol=0.5):
        return "balanced at 0 W"
    if power_watts > 0:
        return f"importing {power_watts:.0f} W"
    return f"exporting {abs(power_watts):.0f} W"


def _allocation_bucket_text(bucket: str) -> str:
    """Explain the planner bucket in plain language."""
    return {
        "active": "actively allocated current",
        "bootstrap": "6 A bootstrap offer while waiting for real charging draw",
        "candidate": "connected but not selected by priority/budget on this tick",
        "excluded": "not currently allocatable",
        "manual_override": "manual override; integration leaves it alone",
    }.get(bucket, bucket)


def _build_allocator_diagnostics(
    *,
    grid_power_watts: float,
    grid_voltage_volts: float,
    max_grid_import_watts: float,
    planner_result,
    charger_snapshots: list[ChargerSnapshot],
    planning_eligibility: dict[str, bool],
    observed_pilot_setpoints: dict[str, int],
    guarded_shutdown_ids: tuple[str, ...],
    raw_allocator_state: str,
) -> tuple[str, str, dict[str, Any]]:
    """Render human-readable planner reasoning plus structured attributes."""
    voltage = grid_voltage_volts if grid_voltage_volts > 0 else DEFAULT_GRID_VOLTAGE
    grid_delta_watts = max_grid_import_watts - grid_power_watts
    grid_delta_exact_amps = grid_delta_watts / voltage
    grid_delta_whole_amps = math.floor(grid_delta_watts / voltage)
    exact_available_actual_amps = (
        planner_result.managed_planned_current_amps + grid_delta_exact_amps
    )
    eligible_ids = planner_result.planning_eligible_ids

    charger_decisions: list[dict[str, Any]] = []
    charger_lines: list[str] = []
    for snapshot in charger_snapshots:
        observed_setpoint = observed_pilot_setpoints.get(snapshot.charger_id, 0)
        planning_ok = planning_eligibility.get(snapshot.charger_id, False)
        decision = {
            "charger_id": snapshot.charger_id,
            "name": snapshot.name,
            "connected": snapshot.connected,
            "enabled": snapshot.enabled,
            "manual_override": snapshot.manual_override,
            "observed_setpoint_amps": observed_setpoint,
            "measured_actual_amps": round(snapshot.measured_actual_amps, 2),
            "desired_actual_amps": snapshot.desired_actual_amps,
            "pilot_request_amps": snapshot.pilot_request_amps,
            "should_enable": snapshot.should_enable,
            "allocator_bucket": snapshot.allocator_bucket,
            "bucket_reason": _allocation_bucket_text(snapshot.allocator_bucket),
            "planning_eligible": planning_ok,
        }
        charger_decisions.append(decision)

        if snapshot.manual_override:
            charger_lines.append(
                f"{snapshot.name}: manual override is on, measured "
                f"{snapshot.measured_actual_amps:.2f} A, and the integration leaves "
                "enable/current untouched."
            )
            continue

        enable_text = "on" if snapshot.should_enable else "off"
        line = (
            f"{snapshot.name}: observed setpoint {observed_setpoint} A, measured "
            f"{snapshot.measured_actual_amps:.2f} A, "
            f"desired actual {snapshot.desired_actual_amps} A, pilot "
            f"{snapshot.pilot_request_amps} A, switch should be {enable_text}; "
            f"reason: {_allocation_bucket_text(snapshot.allocator_bucket)}."
        )
        line += (
            " This charger is plannable on this tick."
            if planning_ok
            else " This charger is not plannable on this tick, so measurement is only"
            " used as a sanity check and the planner falls back to bootstrap-style"
            " behavior."
        )
        charger_lines.append(line)

    if eligible_ids:
        planning_line = (
            "Plannable chargers on this tick are "
            f"{', '.join(eligible_ids)}. Their observed setpoints sum to "
            f"{planner_result.managed_planned_current_amps} A, and the planner adds "
            f"the conservative grid delta of {grid_delta_whole_amps:+d} A to reach "
            f"{planner_result.available_actual_amps} A of allocatable charging."
        )
    else:
        planning_line = (
            "No charger is plannable on this tick, so no charger setpoint is used as "
            "the budget baseline; connected chargers can still be kept at bootstrap "
            "6 A offers by the enable/pilot policy."
        )

    guarded_shutdown_line = (
        "Contactor guards are still holding "
        f"{', '.join(guarded_shutdown_ids)} on, so every currently enabled charger "
        "stays at 6 A until that shutdown can happen."
        if guarded_shutdown_ids
        else "No guarded shutdown is active on this tick."
    )

    explanation = " ".join(
        [
            (
                f"Power meter says grid is {_power_flow_text(grid_power_watts)}. "
                f"Target is {_power_flow_text(max_grid_import_watts)}, so delta is "
                f"{max_grid_import_watts:.0f} - ({grid_power_watts:.0f}) = "
                f"{grid_delta_watts:+.0f} W. At {voltage:.1f} V that is "
                f"{grid_delta_exact_amps:+.2f} A of raw site headroom, "
                f"conservatively {grid_delta_whole_amps:+d} A this tick."
            ),
            (
                f"Plannable charger setpoints sum to "
                f"{planner_result.managed_planned_current_amps} A while measured "
                f"managed current is {planner_result.managed_actual_current_amps:.2f} A, "
                f"so the planner "
                f"budgets floor({exact_available_actual_amps:.2f}) = "
                f"{planner_result.available_actual_amps} A of actual charging for "
                "managed EVs on this tick."
            ),
            planning_line,
            " ".join(charger_lines),
            guarded_shutdown_line,
        ]
    )

    summary = (
        f"grid {grid_delta_whole_amps:+d} A; "
        + ", ".join(
            (
                f"{snapshot.charger_id} manual"
                if snapshot.manual_override
                else (
                    f"{snapshot.charger_id} "
                    f"{snapshot.desired_actual_amps}->{snapshot.pilot_request_amps} A "
                    f"{'on' if snapshot.should_enable else 'off'}"
                )
            )
            for snapshot in charger_snapshots
        )
    )
    if len(summary) > 255:
        summary = (
            f"grid {grid_delta_whole_amps:+d} A; "
            + ", ".join(
                f"{snapshot.charger_id} {'on' if snapshot.should_enable else 'off'}"
                for snapshot in charger_snapshots
            )
        )

    attributes = {
        "summary": summary,
        "explanation": explanation,
        "grid_power_watts": round(grid_power_watts, 2),
        "grid_voltage_volts": round(voltage, 2),
        "max_grid_import_watts": round(max_grid_import_watts, 2),
        "grid_delta_watts": round(grid_delta_watts, 2),
        "grid_delta_amps_exact": round(grid_delta_exact_amps, 2),
        "grid_delta_amps_whole": grid_delta_whole_amps,
        "managed_actual_current_amps": round(
            planner_result.managed_actual_current_amps,
            2,
        ),
        "managed_planned_current_amps": planner_result.managed_planned_current_amps,
        "available_actual_amps": planner_result.available_actual_amps,
        "active_managed_charger_count": planner_result.active_managed_charger_count,
        "planning_eligible_ids": list(planner_result.planning_eligible_ids),
        "preferred_enabled_ids": list(planner_result.preferred_enabled_ids),
        "wakeup_candidate_ids": list(planner_result.wakeup_candidate_ids),
        "guarded_shutdown_ids": list(guarded_shutdown_ids),
        "charger_decisions": charger_decisions,
        "raw_allocator_state": raw_allocator_state,
    }
    return summary, explanation, attributes


@dataclass(slots=True, frozen=True)
class ControllerChargerInput:
    """Controller-facing charger state."""

    charger_id: str
    name: str
    priority: int
    min_amps: int
    max_amps: int
    connected: bool
    enabled: bool
    manual_override: bool
    measured_actual_amps: float
    charging: bool | None = None
    pilot_setpoint_amps: int = 0


@dataclass(slots=True)
class HysteresisTracker:
    """Track the two state-change guards for one charger."""

    observed_contactor_closed: bool | None = None
    observed_contactor_changed_at: datetime | None = None
    enable_condition_met: bool | None = None
    enable_condition_since: datetime | None = None
    disable_condition_met: bool | None = None
    disable_condition_since: datetime | None = None

    def update(
        self,
        *,
        now: datetime,
        enable_condition_met: bool,
        disable_condition_met: bool,
        desired_enabled: bool,
        observed_enabled: bool,
        observed_contactor_closed: bool,
        lockout_delay: timedelta,
        settle_delay: timedelta,
        allow_change: bool = True,
    ) -> tuple[bool, bool]:
        """Return the guarded desired enabled state plus whether change is blocked."""
        self.observe(now=now, observed_contactor_closed=observed_contactor_closed)
        self._observe_enable_condition(now=now, condition_met=enable_condition_met)
        self._observe_disable_condition(now=now, condition_met=disable_condition_met)

        if not allow_change or desired_enabled == observed_enabled:
            return observed_enabled, False

        if desired_enabled:
            if not enable_condition_met:
                return observed_enabled, True
            if (
                self.enable_condition_since is None
                or now - self.enable_condition_since < settle_delay
            ):
                return observed_enabled, True
        elif not disable_condition_met:
            if (
                self.disable_condition_since is None
                or now - self.disable_condition_since < settle_delay
            ):
                return observed_enabled, True

        if (
            self.observed_contactor_changed_at is not None
            and now - self.observed_contactor_changed_at < lockout_delay
        ):
            return observed_enabled, True

        return desired_enabled, False

    def observe(self, *, now: datetime, observed_contactor_closed: bool) -> None:
        """Record the latest observed contactor state."""
        if self.observed_contactor_closed != observed_contactor_closed:
            self.observed_contactor_closed = observed_contactor_closed
            self.observed_contactor_changed_at = now

    def _observe_enable_condition(
        self,
        *,
        now: datetime,
        condition_met: bool,
    ) -> None:
        """Track how long enable conditions have stayed favorable/unfavorable."""
        if self.enable_condition_met != condition_met:
            self.enable_condition_met = condition_met
            self.enable_condition_since = now

    def _observe_disable_condition(
        self,
        *,
        now: datetime,
        condition_met: bool,
    ) -> None:
        """Track how long keep-enabled conditions have stayed favorable/unfavorable."""
        if self.disable_condition_met != condition_met:
            self.disable_condition_met = condition_met
            self.disable_condition_since = now


class SurplusController:
    """Stateful pure controller for planner + hysteresis behavior."""

    def __init__(self, charger_ids: tuple[str, ...] | list[str]) -> None:
        """Initialize controller state."""
        self._trackers = {
            charger_id: HysteresisTracker()
            for charger_id in charger_ids
        }
        self._last_pilot_requests = {
            charger_id: None
            for charger_id in charger_ids
        }

    def _resolved_pilot_setpoint(self, charger: ControllerChargerInput) -> int:
        """Return the best available current setpoint basis for a charger."""
        observed = int(round(charger.pilot_setpoint_amps))
        if observed > 0:
            return observed

        remembered = self._last_pilot_requests.get(charger.charger_id)
        if remembered is not None and remembered > 0:
            return remembered

        if charger.enabled and charger.measured_actual_amps > 0.1:
            return max(
                charger.min_amps,
                min(charger.max_amps, math.floor(charger.measured_actual_amps)),
            )

        if charger.enabled and charger.connected:
            return charger.min_amps

        return 0

    def _is_charging(self, charger: ControllerChargerInput) -> bool:
        """Return the best available charging-state signal for a charger."""
        if charger.charging is not None:
            return charger.charging
        return charger.enabled and charger.measured_actual_amps > 0.5

    def _planning_eligible(
        self,
        charger: ControllerChargerInput,
        pilot_setpoint_amps: int,
        *,
        manual_override: bool | None = None,
    ) -> bool:
        """Return whether a charger is behaving close enough to plan around."""
        override = charger.manual_override if manual_override is None else manual_override
        if (
            not charger.connected
            or not charger.enabled
            or not self._is_charging(charger)
            or override
            or pilot_setpoint_amps <= 0
        ):
            return False
        return True

    def _plan(
        self,
        *,
        grid_power_watts: float,
        grid_voltage_volts: float,
        max_grid_import_watts: float,
        chargers: tuple[ControllerChargerInput, ...],
        observed_pilot_setpoints: dict[str, int],
        auto_override_ids: set[str] | None = None,
        connected_override_ids: set[str] | None = None,
        disabled_override_ids: set[str] | None = None,
    ):
        """Build planner inputs, optionally forcing selected chargers into auto mode."""
        override_ids = auto_override_ids or set()
        connected_ids = connected_override_ids or set()
        disabled_ids = disabled_override_ids or set()
        planning_eligibility: dict[str, bool] = {}
        planner_chargers: list[PlannerCharger] = []

        for charger in chargers:
            manual_override = (
                charger.manual_override and charger.charger_id not in override_ids
            )
            connected = charger.connected or charger.charger_id in connected_ids
            enabled = charger.enabled and charger.charger_id not in disabled_ids
            charging = False if charger.charger_id in disabled_ids else self._is_charging(charger)
            planning_ok = self._planning_eligible(
                ControllerChargerInput(
                    charger_id=charger.charger_id,
                    name=charger.name,
                    priority=charger.priority,
                    min_amps=charger.min_amps,
                    max_amps=charger.max_amps,
                    connected=connected,
                    charging=charging,
                    enabled=enabled,
                    manual_override=manual_override,
                    measured_actual_amps=charger.measured_actual_amps,
                    pilot_setpoint_amps=charger.pilot_setpoint_amps,
                ),
                observed_pilot_setpoints[charger.charger_id],
                manual_override=manual_override,
            )
            planning_eligibility[charger.charger_id] = planning_ok
            planner_chargers.append(
                PlannerCharger(
                    charger_id=charger.charger_id,
                    priority=charger.priority,
                    min_amps=charger.min_amps,
                    max_amps=charger.max_amps,
                    connected=connected,
                    charging=charging,
                    enabled=enabled,
                    manual_override=manual_override,
                    pilot_setpoint_amps=observed_pilot_setpoints[charger.charger_id],
                    measured_actual_amps=charger.measured_actual_amps,
                    planning_eligible=planning_ok,
                )
            )

        return (
            plan_surplus(
                PlannerInputs(
                    grid_power_watts=grid_power_watts,
                    grid_voltage_volts=grid_voltage_volts,
                    max_grid_import_watts=max_grid_import_watts,
                    chargers=tuple(planner_chargers),
                )
            ),
            planning_eligibility,
        )

    def step(
        self,
        *,
        now: datetime,
        grid_power_watts: float,
        grid_voltage_volts: float,
        max_grid_import_watts: float,
        hysteresis_seconds: int,
        chargers: tuple[ControllerChargerInput, ...],
    ) -> IntegrationSnapshot:
        """Compute a full control snapshot for one planner tick."""
        observed_pilot_setpoints = {
            charger.charger_id: self._resolved_pilot_setpoint(charger)
            for charger in chargers
        }
        charging_state = {
            charger.charger_id: self._is_charging(charger)
            for charger in chargers
        }
        planner_result, planning_eligibility = self._plan(
            grid_power_watts=grid_power_watts,
            grid_voltage_volts=grid_voltage_volts,
            max_grid_import_watts=max_grid_import_watts,
            chargers=chargers,
            observed_pilot_setpoints=observed_pilot_setpoints,
        )
        desired_enabled = {
            charger.charger_id: (
                charger.charger_id in planner_result.preferred_enabled_ids
                or (
                    charger.connected
                    and charger.enabled
                    and not charger.manual_override
                    and not charging_state[charger.charger_id]
                )
            )
            for charger in chargers
        }
        for charger in chargers:
            if not charger.manual_override:
                continue
            manual_result, _ = self._plan(
                grid_power_watts=grid_power_watts,
                grid_voltage_volts=grid_voltage_volts,
                max_grid_import_watts=max_grid_import_watts,
                chargers=chargers,
                observed_pilot_setpoints=observed_pilot_setpoints,
                auto_override_ids={charger.charger_id},
            )
            desired_enabled[charger.charger_id] = (
                charger.charger_id in manual_result.preferred_enabled_ids
            )
        enable_condition_met = {}
        disable_condition_met = {
            charger.charger_id: (
                charger.charger_id in planner_result.preferred_enabled_ids
            )
            for charger in chargers
        }
        for charger in chargers:
            condition_result, _ = self._plan(
                grid_power_watts=grid_power_watts,
                grid_voltage_volts=grid_voltage_volts,
                max_grid_import_watts=max_grid_import_watts,
                chargers=chargers,
                observed_pilot_setpoints=observed_pilot_setpoints,
                auto_override_ids={charger.charger_id},
                connected_override_ids={charger.charger_id},
                disabled_override_ids={charger.charger_id},
            )
            enable_condition_met[charger.charger_id] = (
                charger.charger_id in condition_result.preferred_enabled_ids
            )

        lockout_delay = timedelta(seconds=hysteresis_seconds)
        settle_delay = timedelta(seconds=DEFAULT_STATE_CHANGE_GUARD_SECONDS)
        decisions: list[tuple[ControllerChargerInput, int, bool, bool]] = []
        guarded_shutdown_ids: list[str] = []

        for charger in chargers:
            desired_actual = planner_result.desired_actual_amps[charger.charger_id]
            wakeup_candidate = (
                charger.charger_id in planner_result.wakeup_candidate_ids
            )
            should_enable, blocked_change = self._tracker_for(charger.charger_id).update(
                now=now,
                enable_condition_met=enable_condition_met[charger.charger_id],
                disable_condition_met=disable_condition_met[charger.charger_id],
                desired_enabled=desired_enabled[charger.charger_id],
                observed_enabled=charger.enabled,
                observed_contactor_closed=charging_state[charger.charger_id],
                lockout_delay=lockout_delay,
                settle_delay=settle_delay,
                allow_change=not charger.manual_override,
            )
            if (
                blocked_change
                and charger.enabled
                and charger.connected
                and not desired_enabled[charger.charger_id]
            ):
                guarded_shutdown_ids.append(charger.charger_id)
            decisions.append(
                (charger, desired_actual, wakeup_candidate, should_enable)
            )

        guarded_shutdown = bool(guarded_shutdown_ids)
        charger_snapshots: list[ChargerSnapshot] = []

        for charger, desired_actual, wakeup_candidate, should_enable in decisions:
            if charger.manual_override:
                charger_snapshots.append(
                    ChargerSnapshot(
                        charger_id=charger.charger_id,
                        name=charger.name,
                        connected=charger.connected,
                        enabled=charger.enabled,
                        manual_override=True,
                        measured_actual_amps=charger.measured_actual_amps,
                        desired_actual_amps=0,
                        pilot_request_amps=0,
                        should_enable=charger.enabled,
                        allocator_bucket="manual_override",
                    )
                )
                continue

            if guarded_shutdown and charger.enabled and charger.connected:
                pilot_request = charger.min_amps
            elif (wakeup_candidate or desired_actual > 0) and not charger.enabled:
                pilot_request = charger.min_amps
            else:
                pilot_request = _pilot_request_for_target(
                    desired_actual,
                    charger.min_amps,
                    charger.max_amps,
                )
            allocator_bucket = "excluded"
            if charger.charger_id in planner_result.active_ids:
                allocator_bucket = "active"
            elif wakeup_candidate or (
                charger.connected
                and charger.enabled
                and not charging_state[charger.charger_id]
            ):
                allocator_bucket = "bootstrap"
            elif charger.charger_id in planner_result.ordered_candidate_ids:
                allocator_bucket = "candidate"
            self._remember_pilot_request(charger.charger_id, pilot_request)

            charger_snapshots.append(
                ChargerSnapshot(
                    charger_id=charger.charger_id,
                    name=charger.name,
                    connected=charger.connected,
                    enabled=charger.enabled,
                    manual_override=False,
                    measured_actual_amps=charger.measured_actual_amps,
                    desired_actual_amps=desired_actual,
                    pilot_request_amps=pilot_request,
                    should_enable=should_enable,
                    allocator_bucket=allocator_bucket,
                )
            )

        raw_allocator_state = (
            f"{planner_result.allocator_state} "
            f"guarded_off={','.join(guarded_shutdown_ids) or 'none'} "
            f"planning={','.join(planner_result.planning_eligible_ids) or 'none'}"
        )
        allocator_state, allocator_explanation, allocator_attributes = (
            _build_allocator_diagnostics(
                grid_power_watts=grid_power_watts,
                grid_voltage_volts=grid_voltage_volts,
                max_grid_import_watts=max_grid_import_watts,
                planner_result=planner_result,
                charger_snapshots=charger_snapshots,
                planning_eligibility=planning_eligibility,
                observed_pilot_setpoints=observed_pilot_setpoints,
                guarded_shutdown_ids=tuple(guarded_shutdown_ids),
                raw_allocator_state=raw_allocator_state,
            )
        )

        return IntegrationSnapshot(
            grid_power_watts=grid_power_watts,
            grid_voltage_volts=grid_voltage_volts,
            max_grid_import_watts=max_grid_import_watts,
            available_actual_amps=planner_result.available_actual_amps,
            managed_actual_current_amps=planner_result.managed_actual_current_amps,
            managed_planned_current_amps=planner_result.managed_planned_current_amps,
            active_managed_charger_count=planner_result.active_managed_charger_count,
            allocator_state=allocator_state,
            allocator_explanation=allocator_explanation,
            allocator_attributes=allocator_attributes,
            chargers=tuple(charger_snapshots),
        )

    def _tracker_for(self, charger_id: str) -> HysteresisTracker:
        """Get or create a tracker for a charger."""
        if charger_id not in self._trackers:
            self._trackers[charger_id] = HysteresisTracker()
        return self._trackers[charger_id]

    def _remember_pilot_request(self, charger_id: str, pilot_request: int) -> None:
        """Remember the last pilot request written for a charger."""
        self._last_pilot_requests[charger_id] = pilot_request
