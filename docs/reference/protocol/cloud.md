# Cloud services and protocol mappings

- [Context and conventions](#context-and-conventions)
- [The three API varieties](#the-three-api-varieties)
- [Read/write comparisons](#readwrite-comparisons)
- [Detailed wire rules](#detailed-wire-rules)

<a id="mysa-cloud-protocol"></a>

## Context and conventions

Mysa exposes overlapping cloud services: structured device state, older
telemetry, and stored account/device settings. A successful request does not
necessarily mean fresh data or an applied thermostat command. This reference owns the cloud service taxonomy and cross-service read/write mappings.
For client contracts, use the [Python API](../python/cloud.md);
for selection and confirmation, use the [state guide](../../guides/state-and-commands.md).

The three access paths used below are:

| Shorthand | API variety | HTTPS base URL |
| --- | --- | --- |
| **S** | Structured/current services | `https://mysa-backend.mysa.cloud` |
| **R** | Legacy REST through the modern host | `https://mysa-backend.mysa.cloud` |
| **L** | Legacy REST through the older host | `https://app-prod.mysa.cloud` |

**R and L are two entry points to overlapping services, not independent
thermostat measurements.** The distinction matters for route availability;
S and R share a hostname but have different response formats.

Notation: `{dev}` is the account's device ID, `{home}` its home ID, and `{id}`
the resource ID returned by creation/discovery. **ST** means S's `reported`
state sections, **TEL** its `latestTelemetry.reading`, **LT** the legacy
`DeviceStatesObj[dev]` telemetry, and **REC** the device's settings record.
Temperatures and setpoints are Celsius regardless of screen units. Decode
mode, lock, and fan enums by API/model before comparing them.

The live findings apply to two Baseboard Lites (`BB-V2-0-L`, firmware
`3.17.5.13`), checked October 10–12, 2026. Other-model mappings are identified
as such. This is a reverse-engineered protocol, not a vendor API contract.

<a id="requests-and-state"></a>

## The three API varieties

### S — structured/current services

Use the modern base URL and [shared authentication](cloud-auth.md#http-authorization). Discover the account
with `GET /mobile/bootstrap?versionNumber={APP_VERSION}`, using `APP_VERSION`
from `const.py`, then request state for the discovered IDs:

[Batch-state exchange with existing response fixture](../../../examples/cloud/state-batch.success.json)

A batch returns `{device_id: {"data": state}}`; inspect individual entries for
failure. `GET /state/{dev}` returns one state object directly. ST contains
configuration/control sections such as `targetHeat`, `modes`, and
`physicalInterface`; TEL contains measurements. Where a section has
`desired` and `reported`, only `reported` is evidence of applied state.

Commands use section changes with `source: 3`:

[Setpoint request and accepted reply](../../../examples/cloud/state-update.setpoint.json)

This requests a setpoint change; it does not select heat mode. Source 3 appears
as `app` in change history. Current capability, usage, schedule, hold, and
live-mode routes are listed in the comparison tables. S's state routes return
403 on L; changing the hostname does not provide a second structured API.

### R — legacy REST on the modern host

Use the same modern base URL, ID token, and headers. Two distinct reads matter:

- `GET /devices/state` returns LT: `DeviceStatesObj[dev]`, generally with
  independent `{v, t}` pairs for each field.
- `GET /devices` returns `DevicesObj[dev]` settings records; `GET /devices/{dev}`
  reads one record. REC describes stored configuration, not live measurements.

Settings writes use `POST /devices/{dev}/` with the fields to change; preserve
the trailing slash. For example, `{"Format":"fahrenheit"}` changes the
thermostat's screen setting. The response can echo stored values without
proving application. Legacy history and schedule endpoints also use R.

### L — legacy REST on the older host

Use `https://app-prod.mysa.cloud` with the **same authentication and request
formats as R**. For example, `GET /devices/state` and
`POST /devices/{dev}/` work on both. Treat L as a compatibility route for
specific operations, not an additional source to poll continuously.

Availability is operation-specific: saved-schedule event editing works on L
but returns 404 on R; some R discovery routes and current services return 403
on L. The client's existing `replace_device()` uses L's `/devices/purgeadd`.
Do not replace the entire client's base URL to obtain legacy telemetry.

## Read/write comparisons

`—` means no applicable mapping in our evidence, not proof that no future
endpoint could expose it. **Reported** means the changed value appeared in ST;
**applied** means thermostat behavior independently confirmed it. **Stored**
means only a record round trip was established. Model-specific entries below
come from app/code mappings, not Lite hardware validation.

### Read matrix

R and L cells name the data source, not just a similarly named field. `= R`
means the same route/format is available; it is not another vote for a value.

| Value | S | R | L |
| --- | --- | --- | --- |
| Room temperature / humidity | TEL `roomTemperature` / `humidity` | LT `CorrectedTemp.v` / `Humidity.v` | = R |
| Internal temperatures | TEL `rawTemperature`, `coreTemperature` | LT `SensorTemp.v`, `HeatSink.v` | = R |
| Heating target | ST `targetHeat.setpoint`; TEL `heatSetpoint` | LT `SetPoint.v` | = R |
| Operating mode | ST `modes.mode`; TEL `mode` uses a different enum | LT `Mode.v`, `TstatMode.v`, each with its own enum | = R |
| Cooling / AC controls | ST `targetCool.setpoint`, `modes.fan_mode`, `verticalSwingState`, `horizontalSwingState` | REC `SupportedModes` / `SupportedCaps` describes available commands, not current state | = R formats; other-model support not live-verified |
| Minimum / maximum target | ST `targetHeat.lockoutMin` / `lockoutMax` | REC `MinSetpoint` / `MaxSetpoint` | = R |
| Button lock | ST `physicalInterface.lockout` | LT `Lock.v`; REC `ButtonState` | = R |
| Wake / idle brightness | ST `physicalInterface.activeIntensity` / `idleIntensity` | REC `MaxBrightness` / `MinBrightness`; LT `Brightness.v` is only one value | = R |
| Screen units | ST `physicalInterface.format` | REC `Format` | = R |
| Temperature offset | ST `tracking.ambientOffset` | — | — |
| Heater profile | ST `bbConfig.controlType` | REC `DutyCycleOpt` | = R |
| Heating activity / interval runtime | TEL `dutyCycle`, `onTime` | LT `Duty.v`, `OnTime.v`, `OffTime.v` | = R |
| Electrical data | TEL `voltage`, `maxCurrent`, `powerConsumed`, `instantLoad`; ST `power` | LT `Voltage.v`, `Current.v`; REC `Voltage` is configuration | = R |
| RSSI / free heap | TEL `rssi`, `freeHeap` | LT `Rssi.v`, `FreeHeap.v` | = R |
| Connection / timestamps | `latestTelemetry.isConnected`, `lastConnected`; TEL `timestamp`; ST section timestamps | LT `Connected.v/t`, individual field `t`, top-level `Timestamp` | = R |
| Identity / names / time zone | ST `identity.model`, `fw`; bootstrap account data | REC `Id`, `Model`, `Name`, `Home`, `TimeZone` | = R |
| Capabilities | `GET /capabilities/{dev}` | `GET /devices/capabilities`; REC for model-specific command metadata | Capability-list route returns 403; REC still works |
| Firmware availability | — | `GET /devices/update_available/{dev}`; `/devices/firmware` lists versions | = R |
| Usage / raw history | `POST /telemetry/usage/{dev}` | `POST /batchReadings/v2/{dev}` | Same route, but bucket keys can differ |
| Change history | `GET /state/history/{dev}` | `POST /energy/setpoints/device/{dev}` for target changes | = R |
| Active schedules / holds | `GET /schedule?homeId={home}`; `/schedule/hold/{dev}` | `GET /schedules` (v1), `/schedules/v2/{dev}` (v2); LT `ScheduleMode` / `HoldTime` are hints | = R |
| Shortcuts / demand response | ST `drState` where present | `GET /homes/{home}/scenes`; `/devices/{dev}/drstate` | = R |

### Write matrix

S field paths below are sent in `POST /state/{dev}/update`; R/L settings
fields are sent in `POST /devices/{dev}/`. These are available routes, not a
recommendation to use them all.

| Operation | S | R | L |
| --- | --- | --- | --- |
| Heating target | `targetHeat.setpoint`: applied on one Lite, unconfirmed on the other | — | — |
| Heat / off | `modes.mode` = 4 / 0: same mixed application | REC `Mode` object: stored only, **not a working mode command** | = R |
| AC target / fan / swing | `targetCool.setpoint`, `modes.fan_mode`, directional swing fields; model-specific | No verified direct control mapping | = R |
| Button lock | `physicalInterface.lockout` = 3 / 0: reported | `ButtonState` = `Locked` / `Unlocked`: reported | = R |
| Wake / idle brightness | `physicalInterface.activeIntensity` / `idleIntensity`: reported | `MaxBrightness` / `MinBrightness`: reported | = R |
| Temperature offset | `tracking.ambientOffset`: reported | — | — |
| Setpoint limits | `targetHeat.lockoutMin` / `lockoutMax`: reported | `MinSetpoint` / `MaxSetpoint`: reported | = R |
| Screen units | `physicalInterface.format` = `C` / `F`: applied | `Format` = `celsius` / `fahrenheit`: applied | = R |
| Heater profile | `bbConfig.controlType` = 8 / 9: reported | `DutyCycleOpt` = 8 / 9: reported | = R |
| Device name / time zone | — | `Name` / `TimeZone`: stored | = R |
| Schedules | `POST /schedule`; full-body `PUT /schedule/{id}`; `DELETE /schedule/{id}/attach/{dev}` | v1 `POST /schedules/device/update/{dev}`; v2 `POST /schedules/v2/`, `DELETE /schedules/v2/{id}` | = R, with the [v1 body difference](cloud-workflows.md#legacy-schedules-and-saved-templates) |
| Hold / resume | `POST /schedule/hold`; `DELETE /schedule/hold/{dev}`: stored/removed | — | — |
| Firmware trigger | `POST /devices/{dev}/firmware-update` with JSON `{}`: accepted | — | Same trigger returns 403 |
| Live reporting | `POST /mysa-legacy/stream/home/{home}` with `{"timeout":seconds}`: accepted | — | Same route returns 403 |
| Claim / remove / replace | — | `POST /devices`, `DELETE /devices/{dev}` | Claim/delete work; existing replacement uses `POST /devices/purgeadd` |

The remaining app-oriented operations are intentionally compact here: shortcut
edit/apply, alert, zone, geofence, and reminder record operations worked on R
and L; shortcut application was inconsistent. Saved-template create/rename/
apply/delete worked on both, but event editing worked only on L (R: 404).
`ecoMode` and `Animation` settings persisted on both without verified physical
effects. Other account/app administration is known from app sources, not
validated as thermostat functionality.

<a id="important-wire-rules"></a>
<a id="other-protocol-and-implementation-details"></a>

## Detailed wire rules

- [State, model enums and usage/history](cloud-state.md)
- [Modern/legacy schedules, holds and account operations](cloud-workflows.md)
- [Firmware request bodies and update evidence](firmware-and-connectivity.md)

These topic references own the detailed request shapes. [Python contracts](../python/cloud.md)
state what the client implements; [selection and confirmation](../../guides/state-and-commands.md)
explains how to combine the results.

[Documentation index](../../README.md)
