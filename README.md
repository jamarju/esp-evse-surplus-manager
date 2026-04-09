# esp-evse-surplus-manager

[![Tests](https://github.com/jamarju/esp-evse-surplus-manager/actions/workflows/tests.yml/badge.svg)](https://github.com/jamarju/esp-evse-surplus-manager/actions/workflows/tests.yml)

![esp-evse-surplus-manager banner](assets/esp-evse-surplus-manager-light.jpg)

Home Assistant custom integration for solar-surplus charging with chargers running the [ESP-EVSE firmware](https://github.com/jamarju/esp-evse). 

This is **not** for the stock OpenEVSE firmware integration in Home Assistant.

## Requirements

- OpenEVSE with color LCD (TFT) display, flashed with [ESP-EVSE firmware](https://github.com/jamarju/esp-evse)
- A grid power and voltage meter (eg. Shelly ProEM, your inverter, etc.) already integrated with Home Assistant
- Solar surplus :)

## What It Does

- Diverts solar surplus to one or more ESP-EVSE chargers
- Splits power equally across all active chargers
- Allocates scarce current by priority: the highest-priority charger starts first, and lower-priority chargers join only when enough surplus remains for their minimum current
- Enforces a configurable grid import limit
- Uses a 5 minute contactor lockout to prevent contactor chattering
- Uses a 1 minute sustained state-change guard to ignore short transients such as passing clouds
- Supports per-charger manual override
- Self-corrects pilot setpoint vs actual current draw offsets
- Automatically forwards measured grid voltage to ESP-EVSE `set_voltage` numbers to get correct V/kW/kWh readings on the ESP-EVSE display
- Supports config-flow setup through the Home Assistant UI

## Install

1. Add this repository to HACS as a custom repository of type `Integration`.
2. Install `esp-evse-surplus-manager`.
3. Restart Home Assistant.
4. Add the integration from `Settings -> Devices & services`.

Then configure:

- Grid power sensor: choose the entity that provides grid power measurements **(positive import, negative export)**
- Grid voltage sensor: choose the entity that provides grid voltage measurements
- Planner period: choose a value that gives your car enough time to ramp up/down to the setpoint. Default: 20 seconds. If your car takes longer than this to reach the setpoint, you should turn it up or the system may oscillate.
- Contactor lockout time: limits the contactor switching period to reduce wear. Default: 300 seconds.
- Expose debug sensors: adds diagnostic sensors for available current, managed actual current, managed planned current, active managed charger count, and allocator state

On the next screen, select your ESP-EVSE devices and their priority numbers. Lower numbers have higher priority.

To add more chargers later, use `Reconfigure` from the integration menu.

## Grid Import Limiter Function

The single most important setting in this integration is the `Max grid import` number entity on the Surplus Controller.

It defines the maximum number of watts the EV chargers are allowed to import from the grid. On every planner tick, the controller tries to reach that value as closely as possible without exceeding it.

Examples:

- `0 W`: pure surplus charging, with no intentional grid import
- `5000 W`: use whatever surplus there is plus up to 5 kW from the grid
- `-500 W`: reserve 500W for export, use the remaining surplus for chargers

## House Batteries

If you do not have batteries, setting `Max grid import` to `0 W` usually means "charge only from surplus".

If you do have batteries, things are less obvious. The grid power meter only sees the net power at the grid connection point. That means your site can still show `0 W` import while the batteries are actually discharging into the house or into the EV chargers.

So with batteries, targeting `0 W` import is not enough: the EVs may still end up drawing energy from the house batteries.

A practical workaround is to set a small negative target such as `-500 W`. That tells the controller to keep the site exporting about `500 W`. If your battery system is configured to prioritize charging over export, sustained `500 W` export means either that the batteries are already full, or that they are still charging but the available surplus exceeds their maximum charge power. It also means the batteries cannot be discharging through the EVs, because battery discharge would offset local import demand and the grid meter would move back toward `0 W` instead of staying at `-500 W`.

## Under The Hood

Every planner tick, the integration does this:

1. It reads grid power and grid voltage.
2. It compares the measured grid power with your configured grid-import limit. That gap is converted from watts to amps by dividing by grid voltage.
3. It adds back the current already assigned to chargers that are behaving normally, so managed EV load is not counted twice as ordinary house load.
4. It floors that result to a whole number of amps and treats it as the charging budget for this tick.
5. It then looks at chargers that have a car plugged in, are not in manual override, and sorts them by priority.
6. A charger is plannable if it is connected, enabled, not in manual override, has a positive pilot, and is charging.
7. Chargers that are already really charging share the budget evenly. If there is enough budget left for one or more additional chargers to receive their minimum current, those chargers become wake-up candidates and are first offered `6 A`. They are not offered their fair share yet, because the car may already be full, so the controller first pokes the car to see whether it actually wants to charge.
8. If a charger is enabled but not charging, it stays on a `6 A` poke, but it is skipped for future slot selection, giving lower-priority chargers a chance to wake up instead.
9. Before the controller actually flips a contactor, the requested change must stay valid for 1 minute. This filters short transients such as passing clouds or brief house-load spikes.
10. The controller also enforces a 5 minute contactor lockout. After a real on/off switch change is observed, that contactor cannot be flipped again until the lockout time has expired.
11. Both guards are based on observed reality, not just what the planner wants. So a manual switch change is also remembered, and when you later return to auto mode the next automatic flip still has to respect those timers.

In short: the planner decides who should charge and by how much, and the controller applies those decisions slowly enough to avoid contactor wear and cloud-induced thrashing.

Manual override suppresses automatic current and enable writes for that charger. The charger is then treated as ordinary house load through the net grid meter.

## Testing

The repo includes pure offline planner/controller tests:

```bash
python3 -m unittest discover -s tests
```
