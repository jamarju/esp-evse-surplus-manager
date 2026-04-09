"""Pure surplus planning logic."""

from __future__ import annotations

import math

from .const import DEFAULT_GRID_VOLTAGE
from .models import PlannerCharger, PlannerInputs, PlannerResult


def _distribute_evenly(
    amps: int,
    chargers: list[PlannerCharger],
    capacities: dict[str, int],
) -> dict[str, int]:
    """Distribute whole amps evenly, giving remainders to earlier chargers."""
    grants = {charger.charger_id: 0 for charger in chargers}
    remaining = [
        charger
        for charger in chargers
        if capacities.get(charger.charger_id, 0) > 0
    ]
    amps_left = amps

    while amps_left > 0 and remaining:
        share, remainder = divmod(amps_left, len(remaining))
        spent = 0
        next_remaining: list[PlannerCharger] = []

        for index, charger in enumerate(remaining):
            grant = share + (1 if index < remainder else 0)
            capacity_left = capacities[charger.charger_id] - grants[charger.charger_id]
            grant = min(grant, capacity_left)
            if grant > 0:
                grants[charger.charger_id] += grant
                spent += grant
            if grants[charger.charger_id] < capacities[charger.charger_id]:
                next_remaining.append(charger)

        if spent == 0:
            break

        amps_left -= spent
        remaining = next_remaining

    return grants


def plan_surplus(inputs: PlannerInputs) -> PlannerResult:
    """Allocate available current across any number of managed chargers."""
    grid_voltage = inputs.grid_voltage_volts
    if grid_voltage <= 0:
        grid_voltage = DEFAULT_GRID_VOLTAGE

    managed_actual_current = sum(
        charger.measured_actual_amps
        for charger in inputs.chargers
        if not charger.manual_override
    )
    managed_planned_current = sum(
        charger.pilot_setpoint_amps
        for charger in inputs.chargers
        if not charger.manual_override and charger.planning_eligible
    )
    available_power = (
        managed_planned_current * grid_voltage
        - inputs.grid_power_watts
        + inputs.max_grid_import_watts
    )
    available_actual_amps = math.floor(available_power / grid_voltage)

    candidates = [
        charger
        for charger in inputs.chargers
        if charger.connected and not charger.manual_override
    ]
    preferred_order = sorted(
        candidates,
        key=lambda charger: (charger.priority, charger.charger_id),
    )
    allocation_order = [
        charger
        for charger in preferred_order
        if charger.active or not charger.enabled
    ]
    active = [charger for charger in allocation_order if charger.active]
    idle = [charger for charger in allocation_order if not charger.active]

    desired_actual_amps = {charger.charger_id: 0 for charger in inputs.chargers}
    actual_budget_amps = max(0, available_actual_amps)
    seeded_active: list[PlannerCharger] = []

    for charger in active:
        if actual_budget_amps < charger.min_amps:
            break
        desired_actual_amps[charger.charger_id] = charger.min_amps
        actual_budget_amps -= charger.min_amps
        seeded_active.append(charger)

    extra_actual = _distribute_evenly(
        actual_budget_amps,
        seeded_active,
        {
            charger.charger_id: charger.max_amps - charger.min_amps
            for charger in seeded_active
        },
    )
    for charger in seeded_active:
        desired_actual_amps[charger.charger_id] += extra_actual[charger.charger_id]

    preferred_enabled_budget_amps = max(0, available_actual_amps)
    preferred_enabled: list[PlannerCharger] = []
    for charger in allocation_order:
        if preferred_enabled_budget_amps < charger.min_amps:
            break
        preferred_enabled.append(charger)
        preferred_enabled_budget_amps -= charger.min_amps

    ordered_candidate_ids = tuple(charger.charger_id for charger in allocation_order)
    active_ids = tuple(charger.charger_id for charger in active)
    planning_eligible_ids = tuple(
        charger.charger_id for charger in preferred_order if charger.planning_eligible
    )
    preferred_enabled_ids_tuple = tuple(
        charger.charger_id for charger in preferred_enabled
    )
    wakeup_candidate_ids = tuple(
        charger.charger_id
        for charger in idle
        if charger.charger_id in preferred_enabled_ids_tuple
    )
    allocator_state = (
        f"available={available_actual_amps}A "
        f"planned={managed_planned_current}A "
        f"measured={managed_actual_current:.2f}A "
        f"active={len(active)} idle={len(idle)} "
        f"eligible={','.join(planning_eligible_ids) if planning_eligible_ids else 'none'} "
        f"preferred={','.join(preferred_enabled_ids_tuple) if preferred_enabled_ids_tuple else 'none'} "
        f"wakeup={','.join(wakeup_candidate_ids) if wakeup_candidate_ids else 'none'} "
        "distribution=priority_remainder"
    )

    return PlannerResult(
        desired_actual_amps=desired_actual_amps,
        available_actual_amps=available_actual_amps,
        managed_actual_current_amps=managed_actual_current,
        managed_planned_current_amps=managed_planned_current,
        active_managed_charger_count=len(active),
        ordered_candidate_ids=ordered_candidate_ids,
        active_ids=active_ids,
        planning_eligible_ids=planning_eligible_ids,
        preferred_enabled_ids=preferred_enabled_ids_tuple,
        wakeup_candidate_ids=wakeup_candidate_ids,
        allocator_state=allocator_state,
    )
