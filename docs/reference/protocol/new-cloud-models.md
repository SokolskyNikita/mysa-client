# Newer cloud models: API shapes and evidence

- [Model and capability data](#model-and-capability-data)
- [Central HVAC activity and fan fields](#central-hvac-activity-and-fan-fields)
- [Central HVAC configuration writes](#central-hvac-configuration-writes)
- [Water Heater Controller](#water-heater-controller)
- [Linking and provisioning](#linking-and-provisioning)
- [Evidence and limits](#evidence-and-limits)

This reference records cloud fields and commands for Baseboard V3 (`BB-V3-0`),
Central HVAC (`ST-V1-0`, `ST-V1`, `LV`), Climate Sensor (`CS-V1-0`, `CS-V1`)
and Water Heater Controller (`WHC-V1-0`, `WHC-V1`).
It complements the [account enrollment sequence](cloud-workflows.md#baseboard-v3-central-hvac-and-climate-sensor)
and the general [state reference](cloud-state.md). These findings are
app-derived and have not been verified on newer-model hardware. They describe
observed API shapes; they do not promise that every firmware, account, or client
supports every field.

## Model and capability data

`mysa-client` classifies these model identifiers in
[`const.py`](../../../src/mysa_client/cloud/const.py). Device state parsing preserves
newer-model fields such as `targetAuto`, HVAC stage reports, fan configuration,
and `hvacState`; see [`models.py`](../../../src/mysa_client/cloud/models.py).

Capability documents use `features` groups including `climateControl`,
`sensing`, `interface`, `connectivity`, and `smart`, along with `hardware` and
`system` data. Read the capabilities returned for the individual device. Do
not infer a usable feature from a model name or an absent capability document.

The app-derived observations identify HVAC modes by the capability document's
names. In structured state, `modes.mode` uses `CONTROL_MODE_TYPE`; AUTO is 1,
heat is 4, and cool is 3. An AUTO target is represented under `targetAuto` as
`heatSetpoint` and `coolSetpoint`; ordinary heat and cool targets use
`targetHeat.setpoint` and `targetCool.setpoint`. The two AUTO targets form one
range and should be treated together.

## Central HVAC activity and fan fields

The app derives current HVAC activity from reported `modes.hvacStates`, with
`latestTelemetry.reading.hvacState` as another stage report. Its decoded stage
bits are:

| Stage | Bit value |
|---|---:|
| Heat stages 1 and 2 | 256 and 512 |
| Auxiliary heat | 4096 |
| Cool stages 1 and 2 | 1024 and 2048 |
| Fan stage | 8192 |

Treat these as app-derived encodings, not a stable server contract. A selected
mode alone does not show that equipment is active. The state may omit activity
or report it late.

In Mysa's app 4.21.1 (not tried on a device), `modes.reported.hvacStates` is
an object with the members `HeatingStage1`, `HeatingStage2`, `CoolingStage1`,
`CoolingStage2` and `AuxHeat`. The app uses it when it is present and decodes
the `hvacState` bit field only when it is not. The low bits of `hvacState` are
relays, which the app decodes but does not use for the activity display:

| Relay | Bit value |
|---|---:|
| W1 and W2 | 1 and 2 |
| G | 4 |
| Y1 and Y2 | 8 and 16 |

Fan operation and advanced circulation settings use separate fields. The
app's fan-program writes use `hvacConfig.advFanAlwaysOn`,
`hvacConfig.advFanRuntimeSeconds`, and
`hvacConfig.advFanRuntimePeriodSeconds`. A circulation program configured in
Mysa's app can appear as `runtime`. The regular fan-speed enum for newer
capability-based models is `CONTROL_FAN_SPEED`: 0 is off, 1–7 are speeds 1–7,
8 is sleep, and 9 is auto. This enum does not describe the legacy Mini-Split AC
remote mapping in the [AC controls reference](cloud-state.md#ac-fan-speed).

Structured state changes use `POST /state/{dev}/update` with `source: 3`, the
same app source used by other current-client commands. In the package,
[`commands.py`](../../../src/mysa_client/cloud/commands.py) builds the AUTO target
and fan-configuration changes. Server acceptance is not proof of reported
state or physical operation; apply the normal [write confirmation policy](../../guides/state-and-commands.md#recommended-reconciliation-of-cloud-writes).

## Central HVAC configuration writes

These are the Central HVAC settings Mysa's app 4.21.1 writes, none tried on a
device. Each is `POST /state/{dev}/update` with `"source": 3` and one section,
except the reversing valve. The app sends a value as entered, without
converting or checking it; an on/off setting is the number 1 or 0. The other
writes the app makes are in the
[state reference](cloud-state.md#state-writes-mysas-app-makes).

| Setting | Section and fields | Values |
| --- | --- | --- |
| Auto mode enabled | `targetAuto.enabled` | 1 on, 0 off |
| Auto deadband | `targetAuto.deadband` | As entered |
| Stage-two delta, heat / cool | `hvacConfig.advHeatStageTwoDelta` / `advCoolStageTwoDelta` | As entered |
| Stage-two delay, heat / cool | `hvacConfig.advHeatStageTwoDelayMinutes` / `advCoolStageTwoDelayMinutes` | Minutes |
| Fan ramp | `hvacConfig.advFanRampMinutes` | Minutes |
| Maximum fan speed, heat / cool | `hvacConfig.advMaxHeatFanSpeed` / `advMaxCoolFanSpeed` | 1 low, 2 medium, 3 high |
| Staged auxiliary heat | `hvacConfig.stagedAux1HeatEnabled` | 1 on, 0 off |
| Auxiliary heat type | `hvacConfig.aux1HeatType` | Not established |
| Auxiliary heat delta / delay | `hvacConfig.stagedAux1HeatDelta` / `stagedAux1HeatDelayMinutes` | As entered / minutes |
| Heat, cool, auxiliary heat uses the fan | `hvacConfig.advHeatUsesFan`, `advCoolUsesFan`, `advAuxUsesFan` | 1 on, 0 off |
| Fan always on | `hvacConfig.advFanAlwaysOn` | 1 on, 0 off |
| Minimum fan runtime | `hvacConfig.advFanRuntimeSeconds` and `advFanRuntimePeriodSeconds`, in one request | Seconds |
| Freeze protection | `hvacConfig.advFreezeProtectionEnabled` | 1 on, 0 off |
| Temperature differential | `hvacConfig.temperatureDifferential` | As entered |
| Reversing valve | `POST /capabilities/config/set-reversing-valve` with `{"deviceId": ..., "configCode": ..., "energized": ...}`, no `source` | `configCode` is the thermostat's stored HVAC configuration code; its format and the `energized` values are not established. A wrong value swaps heating and cooling |

## Water Heater Controller

Everything in this section is from Mysa's app 4.21.1 and none of it has been
seen on a device; app 3.65.1 does not know the product. The models are
`WHC-V1-0` and `WHC-V1`, also sent in lower case (`whc-v1-0`, `whc-v1`); the
app treats all four alike. The controller switches power to an electric water
heater whose own thermostat stays in charge, and has a tank temperature probe
and a leak probe. It has a capability document, and no heat setpoint: the app
refuses to send it an ordinary setpoint write.

This library recognises the four model strings as `ModelFamily.WATER_HEATER`
and has no other support for the product. Because `targetHeat.setpoint` is
the thermal safety limit below, `temperature_changes` and
`setpoint_limit_changes` raise `SetpointModeError` when given that family,
and `validate_cloud_command` refuses a setpoint or setpoint limit for it.
These checks need the family; a caller that passes none is not protected.
Whether Mysa's server or the controller would apply such a write was not
tested.

| State or capability path | Meaning |
| --- | --- |
| `modes.reported.mode` | 4 on, 1 idle, 0 off |
| `tank.reported.setpoint`, `tank.reported.capacity` | Tank setpoint (°C) and capacity (US gallons) |
| `targetHeat.reported.setpoint` | Thermal safety limit, taken as 49 °C when absent. The app never writes it |
| `tracking.reported.probeOffset` | Temperature correction (°C) |
| `latestTelemetry.reading.probeTemperature` | Tank temperature (°C); the app ignores −30 or below, and any value while the probe fault is set |
| `leak.reported.state` | 0 unknown, 1 dry, 2 wet, 3 shorted (a leak-probe fault) |
| `power.reported.wattage`, `voltage`, `current` | The app shows 0 W when the mode is off or the reading's `dutyCycle` or `current` is 0; the unit of `current` is not established |
| Capability `features.smart.tankSetpoint` | `value`, `validValues` (the setpoints offered, °C), `userControllable` |
| Capability `features.smart.tankCapacity` | `value`, `userControllable` |
| Capability `features.smart.diagnostics.probeFault.value` | `true` when the temperature probe is open or shorted |

Writes are `POST /state/{dev}/update`, one key per request:

| Setting | Body | Values |
| --- | --- | --- |
| Mode | `{"source": 3, "modes": {"mode": m}}` | 4 on, 1 idle, 0 off |
| Tank setpoint | `{"source": 3, "tank": {"setpoint": c}}` | °C, one of the capability's `validValues` |
| Tank capacity | `{"source": 3, "tank": {"capacity": g}}` | Whole US gallons, 1–1000 |
| Temperature correction | `{"source": 3, "tracking": {"probeOffset": c}}` | °C, one of the capability's temperature-offset values |

The app describes the modes this way: on, the tank heats as needed; idle,
power is off and heating starts only if the water falls below the thermal
safety limit; off, heating never starts. Turning the controller off therefore
disables thermal safety, and the app asks for confirmation first.

A schedule for this model has `deviceType: "WHC"` and mode-only events: each
event's `state` is exactly `{"modes": {"mode": m}}`. A
[hold](cloud-workflows.md#holds)'s `state` has the same shape. Usage is energy
only; the request is in the
[usage table](cloud-state.md#usage).

## Linking and provisioning

The app exposes a sensor-linking flow for Central HVAC through `GET` and
`POST /linking`, with `sourceId`, `targetId`, and `weight`; Python reads links
with [`get_sensor_links()`](../python/cloud.md#demand-response-fleet-firmware-and-sensor-link-reads)
but cannot change them. Account enrollment,
credential transfer, fleet registration, pairing finalization, and Matter
setup-code routes are documented in the [newer-model enrollment sequence](cloud-workflows.md#baseboard-v3-central-hvac-and-climate-sensor).
That sequence is not a hardware-validated setup guide.

## Evidence and limits

The API field paths, stage decoding, and fan-program fields above come from
Mysa's Android app 4.20.1. The `hvacStates` members, the relay bits, the
configuration-write table and the Water Heater Controller section come from
app 4.21.1.

The Home Assistant repository's
[newer-model fixtures](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/tests/fixtures/app_4201_new_models.json)
are synthetic scenarios built from those app-derived shapes. They are not
captured device responses. The corresponding
[integration tests](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/tests/test_new_cloud_models.py)
check the integration's parsing, entities, and command behavior against the
synthetic data. They do not establish backend permissions, exact device
capabilities, or physical application.

[Documentation index](../../README.md)
