# Mysa cloud protocol

- [API varieties](#the-three-api-varieties)
- [Operations](#operations-and-relevance-to-home-assistant)
- [Read/write mappings](#readwrite-comparisons)
- [Recommended reads](#recommended-reconciliation-of-cloud-reads)
- [Recommended writes](#recommended-reconciliation-of-cloud-writes)
- [Client implementation and validation](#client-implementation-and-validation)

Supporting references: [authentication](../../../docs/cloud-auth.md),
[state and model fields](../../../docs/cloud-state.md),
[workflows](../../../docs/cloud-workflows.md), and
[firmware/connectivity](../../../docs/firmware-and-connectivity.md).

## Context and conventions

Mysa exposes overlapping cloud services: structured device state, older
telemetry, and stored account/device settings. A successful request does not
necessarily mean fresh data or an applied thermostat command. This guide
covers the useful operations, their API mappings, and **recommended cloud-only
selection rules** for an integrating application. Endpoint support and Mysa Local policy are tracked separately; see the [Home Assistant integration tracker](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/implementation/cloud.md).

Applications should own automations, scenes, presence, notifications, and dashboards.
Mysa's cloud remains useful for device control, hardware settings, runtime,
firmware, and inspecting existing Mysa schedules that could affect commands.
HomeKit/cloud reconciliation is outside this document.

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

### Shared authentication

All three paths use the **same Cognito ID token**, sent raw in
`authorization` with **no `Bearer` prefix**. Send `accept: application/json`,
a descriptive `user-agent`, and `content-type: application/json` for JSON
bodies. No separate login or AWS request signing is needed for R or L.

Use `MysaAuth.sign_in(email, password)` for the Cognito SRP exchange, or
initialize it with a saved refresh token. `async_get_id_token()` handles
refresh; retain a replacement refresh token through `on_refresh_token` when
one is returned. The public Cognito identifiers are in [`const.py`](const.py),
and the exchange is described in the [authentication reference](../../../docs/cloud-auth.md#cognito-sign-in).
Credentials and token persistence belong to the caller.

<a id="requests-and-state"></a>

## The three API varieties

### S — structured/current services

Use the modern base URL and shared authentication above. Discover the account
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

## Operations and relevance to Home Assistant

The [raw API inventory](../../../docs/api-coverage.md) tracks callable library
methods and known protocol gaps. A documented route is not necessarily exposed
by this client. Consumer-specific choices about entities, scheduling, polling,
and app-feature replacements belong to the application's own design; the
[Home Assistant feature scope](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/cloud-scope.md)
records those choices for Mysa Local.

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
| Schedules | `POST /schedule`; full-body `PUT /schedule/{id}`; `DELETE /schedule/{id}/attach/{dev}` | v1 `POST /schedules/device/update/{dev}`; v2 `POST /schedules/v2/`, `DELETE /schedules/v2/{id}` | = R, with v1 body difference noted below |
| Hold / resume | `POST /schedule/hold`; `DELETE /schedule/hold/{dev}`: stored/removed | — | — |
| Firmware trigger | `POST /devices/{dev}/firmware-update` with JSON `{}`: accepted | — | Same trigger returns 403 |
| Live reporting | `POST /mysa-legacy/stream/home/{home}` with `{"timeout":seconds}`: accepted | — | Same route returns 403 |
| Claim / remove / replace | — | `POST /devices`, `DELETE /devices/{dev}` | Claim/delete work; existing replacement uses `POST /devices/purgeadd` |

The remaining app-oriented operations are intentionally compact here: shortcut
edit/apply, alert, zone, geofence, and reminder record operations worked on R
and L; shortcut application was inconsistent. Saved-template create/rename/
apply/delete worked on both, but event editing worked only on L (R: 404).
`ecoMode` and `Animation` settings persisted on both without verified physical
effects. These results do not justify expanding the HA client with those app
features. Other account/app administration is known from app sources, not
validated as thermostat functionality.

## Recommended reconciliation of cloud reads

The following is a conservative cloud-only selection policy for an integrating application. Home Assistant maintains its own acquisition and selection implementation in the [cloud tracker](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/implementation/cloud.md).
Use **S + R** for normal cloud state acquisition: one S batch and one R legacy
state request at the configured cloud interval. Cache REC separately. Use L
as a replacement for a failing R route with backoff, not a third permanent
poll. Do not average values or count matching R/L responses as corroboration.

### One selection rule, applied per field

1. **Normalize.** Decode each source into the same units/enums and preserve
   `source`, `observed_at`, and `fetched_at`. Parse ISO timestamps and Unix
   seconds/milliseconds. Use the individual LT field's `t`, TEL's reading
   timestamp, or the relevant ST section timestamp; never borrow a sibling
   field's timestamp. Keep target temperature separate from an effective
   target altered by a hold or other control behavior.
2. **Validate.** Reject missing, malformed, non-finite, or semantically invalid
   values. Zero and `false` are valid where the field allows them. A timestamp
   more than one minute in the future is invalid (the existing HA guard); do not let it win a
   newest-value comparison. A valid clock on one section does not validate
   another section.
3. **Choose by the field policy below.** For **Latest** fields, choose the
   newest valid, fresh observation; ties prefer S, then R, then L. Include the
   cached observation so a delayed response cannot roll a newer value back. An
   un-timestamped measurement is not fresh merely because its HTTP response
   just arrived. For **Config** fields, use the named authority/fallback;
   their change timestamps may legitimately be old. REC is stored intent,
   never another device measurement.
4. **Keep provenance.** Retain last-known values with their original age when
   no fresh candidate exists; expose stale/unavailable state rather than
   refreshing their timestamps. A missing field or failed device in a batch
   must not erase good data for other fields/devices. Keep pending writes
   separate from confirmed readings.

For measurement freshness, a suggested starting limit is **35 minutes outside
live reporting**, because ordinary Lite uploads can be 12–30 minutes apart.
During an established live-reporting session, use **max(90 seconds, three poll
intervals)**. These are configurable policy defaults, not vendor guarantees
or a claim that a 30-minute-old temperature is real-time. A future-dated
measurement is rejected rather than having its clock silently corrected.
For a configuration field with an invalid timestamp, do not use that timestamp
to outrank another source; retain its unconfirmed/last-known status.

| Field / operation | Selection and HA behavior |
| --- | --- |
| Room temperature / humidity | **Latest** S TEL vs R/L LT. Use `CorrectedTemp`, never internal sensor temperatures. Accept valid humidity even when the Lite capability flag says false |
| Heating target / mode | Use the newest plausibly timestamped device report from ST or LT; use TEL only after matching target semantics and decoding its different mode enum. Unlike measurements, an unchanged target need not expire after 35 minutes. Without comparable timestamps prefer ST, then LT, and mark freshness unknown. Never use REC `Mode` or S `desired` as confirmation |
| Cooling target / fan / swing | **Config:** ST; REC only describes allowed choices. No verified legacy-state fallback in this guide |
| Minimum / maximum target | **Config:** ST pair, then REC pair if absent; validate minimum ≤ maximum. Never turn an uncertain/fallback value into permission to exceed known device limits |
| Button lock | Prefer the newest valid device report from ST or LT when timestamps are comparable; otherwise ST, then LT. REC is a stored-setting fallback only |
| Wake / idle brightness | **Config:** ST pair, then REC pair. LT's single brightness value cannot reconstruct both settings |
| Screen units | **Config:** REC `Format`, ST as fallback. ST has lagged the screen setting. This selects the configured screen units, not proof of physical application or HA's preferred display unit |
| Temperature offset | **Config:** ST only; absent means unknown, not zero |
| Heater profile | **Config:** ST, then REC `DutyCycleOpt` as stored configuration; validate against the device family |
| Heating activity | **Latest** normalized duty/interval observation. Keep each source's interval meaning; do not add R/L duty or runtime together or infer heater-off from Lite's zero power fields |
| Runtime / history | Prefer S usage `oH1` for runtime; R batch history only as an explicit fallback after bucket/unit normalization. Fetch bounded periods and do not sum overlapping sources. Historical samples do not become current readings |
| Identity / name / time zone / firmware | REC for identity/name/time zone; firmware information endpoint for offered/installed version, with ST identity as installed-version fallback. Cache metadata rather than treating old unchanged values as stale telemetry |
| Capabilities / model controls | S capabilities plus REC model/remote metadata. Preserve hardware limits; do not invent unsupported controls. Model-specific configuration has no general Lite fallback |
| Schedules / holds | Read S schedules and holds, plus legacy schedule sources when inspecting existing Mysa automation. An empty S list does not establish that no legacy schedule exists. Do not use REC `Schedule` or LT hold hints as the complete resource |
| Shortcuts / demand response | On-demand shortcut inspection through R/L. For demand response use ST when present, otherwise the dedicated R route; no merging of unrelated account mode fields |
| Connection / health | Keep request success and data freshness separate for S and legacy reads. A 200 or connected flag cannot make stale measurements fresh; track selected source and age per field. Do not let one failing API mark both paths failed |
| RSSI / internal temperatures / heap | Apply **Latest** when the application requests diagnostics; retain units and timestamps |
| Lite electrical values and unreliable energy summaries | Do not interpret zero electrical fields or inconsistent summaries as measured heater power or reliable consumption |

Cache records and metadata at a slower interval than live state; choose refresh
periods in the consuming application. The library does not schedule polling.
Refresh affected configuration after a command and after rediscovery. Read
optional schedules/history/diagnostics on demand or when that feature is
actually enabled. These are cloud fetch rules, not local/cloud routing rules.

## Recommended reconciliation of cloud writes

**Choose one route per semantic command. Do not broadcast every write to S,
R, and L, and do not copy discrepant readings back into the other APIs.**
Most differences reflect lag or representation, not three stores to repair.

| Command | Preferred route | Alternative / verification |
| --- | --- | --- |
| Setpoint / mode; supported AC controls | S | No verified R/L replacement. Confirm device-reported values with the read policy; REC `Mode` is not a fallback |
| Offset | S | No verified alternative; confirm ST |
| Lock / brightness / setpoint limits | S | R settings are a validated alternative; L is the same legacy path on another host. Select an alternative for a known route/device limitation, not as routine fan-out. Confirm ST or equivalent LT; a REC echo alone is insufficient |
| Screen units | R settings | L if that route is unavailable; S is also supported. Read REC for configured units and ST for reported confirmation when it catches up |
| Heater profile | R settings | L as host fallback; S is also supported. Read REC and ST, preserving a pending/mismatched state if application is unconfirmed |
| Existing device schedule creation | S, only when explicitly using Mysa scheduling | Keep an existing legacy schedule in its native format unless deliberately migrating it. Avoid creating parallel S/v1/v2 schedules for the same intent. Full-body in-place editing is outside client scope |
| Schedule removal / hold / resume | Remove through the resource's native route; S for holds | Re-read that schedule/hold source. Removing a template is not removing its applied schedules; a hold record alone does not confirm an immediate target change |
| Firmware update / temporary live reporting | S dedicated endpoint | Confirm firmware availability/version or advancing telemetry, respectively; no R/L duplicate trigger |
| Claim / remove / replace | Existing explicit setup/recovery method | Use the documented operation and host; never run these as automatic write-repair strategies |
| Time zone | R, only for an explicit Mysa schedule configuration request | Verify REC; changing HA's time zone must not silently rewrite the thermostat |
| **Skip** operations, cloud renaming, app-only settings | No new write path | Use HA's corresponding features; no background synchronization into the Mysa app |

Validate capabilities and ranges, serialize changes per device, and send the
smallest semantic change. Send both brightness values together; preserve a
valid minimum/maximum pair. A setpoint change and a mode change are distinct
commands unless deliberately combined in one S body. Use the mode-specific
AC fan conversion in `commands.py`; raw enum values are not interchangeable.

Record an accepted command as **pending**, then re-read its confirmation
source. Matching `desired` or echoed REC is not physical confirmation. If the
requested value already matched the previous confirmed state, treat it as an
idempotent no-op, not a liveness test. A changed device report can confirm
application; where only REC is available, report stored configuration without
claiming device confirmation.

An explicit unsupported-route response can justify a validated alternative.
After a timeout or 200 without confirmation, re-read first: the command may
still apply. Do not immediately resend through another API, oscillate settings
to force synchronization, or apply a late retry after a newer user command.
Use bounded retries/backoff. Resource creation, firmware triggers, and other
non-idempotent actions require checking their outcome before any retry.

## Other protocol and implementation details

### Important wire rules

| Area | Rule |
| --- | --- |
| Time / mode | Legacy field timestamps can mix seconds and milliseconds in one response. S `modes.mode`, TEL `mode`, and LT mode fields have different enums; Lite heat is S mode 4 but TEL mode 3 |
| Telemetry quality | One Lite returned implausible future TEL timestamps while LT returned current readings. Freshness must be per field/source, not inferred from HTTP success |
| Electrical data | Lite current/power fields were zero and voltage was configured; legacy device energy summaries also produced implausible averages. Neither establishes measured consumption |
| Usage | `/telemetry/usage/{dev}` uses `timezone`, Unix-second `start`/`end`, `summary`, and `aggregatedReadings`; raw readings need the expanded request described in the [usage reference](../../../docs/cloud-state.md#usage) |
| Legacy batch history | `/batchReadings/v2/{dev}` takes `attributes`, `start`, `end`, `attributesProcesses`; relevant fields include `h`, `c`, `s`, `a.oC`, `a.oH`, `ms`, `fs`, `tm`, `t`. Use `sum` for runtime counters, `none` for raw values; normalize timestamp-keyed buckets before use |
| Modern schedules | Events use Sunday = `"0"`, local `HH:MM:00`, `clientKey`, and semantic `state`. Use the device's `TimeZone`. In-place PUT needs `name`, `events`, `deviceType`, `homeId`, `deviceIds`, `reusable`; a name/events-only body fails |
| Legacy v1 schedules | POST body uses `ScheduledActions`, mapping weekday names to `{Minute,SetPoint}` entries. R additionally requires `Device` and `Model`. Seven empty weekday arrays clear events |
| Legacy v2 schedules | POST body: `Schedule` with `Version`, `Owner`, `Events` (plus `Id` for update), `Devices`, and `Model`. GET uses the **device ID**; DELETE uses the **schedule ID** with `Devices` and `Model` in its body |
| Schedule discovery | Modern lists can omit legacy schedules; REC `Schedule` can retain a deleted ID. Inspect the actual legacy resources. Saved-template apply creates separate `V2Schedules` resources |
| Holds | `startTime` must be in the future; the client adds ten seconds. Temporary holds add `endTime`. Both are UTC ISO timestamps. Do not infer an immediate setpoint change merely from hold creation |
| Firmware | POST the trigger with JSON `{}`; a bodyless request returned 415. Trigger acceptance is not completed installation. No newer release was available during validation |
| Newer-model features | Lite rejected Smart Early On eligibility and had no newer OTA fleet. Remote tracking was not reported after a direct write. Gate these features by model; accepting `desired` is not support |

For legacy v2 schedules, `Events` maps weekday number → minute number → device
command. A Lite example is `{"0":{"1437":{"BB2L":"3|22|%|%|%|%|%"}}}`.
The pipe-separated command has its own legacy enum/field ordering; do not
insert S's mode number. Detailed other-model and provisioning notes remain in
the [state and model reference](../../../docs/cloud-state.md) and
[account/schedule workflows](../../../docs/cloud-workflows.md). This guide
owns the reconciled protocol conclusions and selection recommendations.

<a id="integration-status-and-validation"></a>

### Client implementation and validation

The client exposes the documented endpoints and semantic encoders, with
model-specific mappings that remain app/code-derived unless hardware evidence is
identified. Automated protocol tests use simulated responses and do not establish
device compatibility. Live validation currently covers two Baseboard Lite
`BB-V2-0-L` devices on firmware `3.17.5.13`; their cloud setpoint and mode
application differed. Other model profiles, full schedule execution, notification
delivery, and installation of a new firmware release remain unverified. The
[Home Assistant cloud tracker](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/implementation/cloud.md)
separately records integration policy, gaps, and validation work.

### Using the client

`MysaAuth` and `MysaClient` are exported by [`__init__.py`](__init__.py).
The caller supplies and closes the `aiohttp` session. See the complete
[cloud example](../../../examples/cloud_basic.py) for login, token persistence,
account discovery, reading, and an explicitly enabled setpoint write.

### How it is organized

| File | Responsibility |
| --- | --- |
| [`auth.py`](auth.py), [`srp.py`](srp.py) | Cognito authentication, serialized token refresh, SRP calculations |
| [`client.py`](client.py) | HTTP requests, endpoint methods, response handling |
| [`commands.py`](commands.py) | Semantic controls, enum conversion, schedule encoding |
| [`models.py`](models.py) | Typed response parsing, preserving original JSON in `raw` |
| [`const.py`](const.py), [`exceptions.py`](exceptions.py) | Hosts, protocol constants/enums, errors |

The standalone package has no Home Assistant dependency. The integration owns
polling, per-field freshness, command confirmation, and local/cloud
reconciliation; see its
[architecture guide](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/architecture.md).
Install the published package from [PyPI](https://pypi.org/project/mysa-client/).

### Public API and integration helpers

The supported top-level API is exported by [`__init__.py`](__init__.py):
`MysaAuth`, `Tokens`, `MysaClient`, `Bootstrap`, `Capabilities`,
`DeviceRecord`, `DeviceState`, `FirmwareInfo`, `FirmwareRecord`, `StateChange`, `UsageBucket`,
`UsageDay`, `Mode`, `AcFan`, `ButtonLock`, `HeaterType`, `DutyCycleOpt`,
`Swing`, `ModelFamily`, `MysaError` and its typed subclasses, plus
`ScheduleEventSpec`, `encode_schedule_events`, `make_state`, `model_family`,
`value_range`, `iso_utc`, and `jwt_claims`.

These additional submodule helpers are also supported because the Home
Assistant consumer imports them directly:

| Module | Supported names |
| --- | --- |
| [`commands.py`](commands.py) | `auto_temperature_changes`, `button_lock_changes`, `display_brightness_changes`, `display_unit_settings`, `fan_speed_changes`, `heater_type_settings`, `hvac_fan_changes`, `mode_changes`, `setpoint_limit_changes`, `swing_changes`, `temperature_changes`, `temperature_offset_changes` |
| [`const.py`](const.py) | `API_BASE`, `EXPERIMENTAL_CLOUD_FAMILIES`, `SWING_VERTICAL`, `model_family`; public models and enums are also re-exported at the package level |
| [`models.py`](models.py) | The response models exported by the package root, retained at this submodule path for direct imports |
| [`exceptions.py`](exceptions.py) | The public `MysaError` exception hierarchy, retained at this submodule path for direct imports |
| [`observations.py`](observations.py) | `MEASUREMENT_MAX_AGE`, `MEASUREMENTS`, `STATE_FIELDS`, `TELEMETRY_FIELDS`, `Observation`, `number`, `observe`, `valid_value` |
| [`trace.py`](trace.py) | `route_template` for redacted request-route reporting |
| [`validation.py`](validation.py) | `validate_basic_command` for conservative validation of experimental models |

Other names prefixed with an underscore are private implementation details.

### Errors, timeouts, retries, and caller responsibilities

The current transport uses a 20-second timeout. A 401 causes one forced token
refresh and retry; a remaining 401 or 403 raises `MysaApiError`, not automatic
reauthentication. Route absence or permissions can explain 403. A 404 raises
`MysaNotFoundError`; only resource-specific helpers such as schedule/hold reads
normalize an absent resource to an empty result. A 429 raises
`MysaRateLimitError` with numeric `Retry-After` when supplied; timeout,
transport failure, or 5xx raises `MysaConnectionError`. There is no general
transport retry loop. A 401 retry only follows a forced token refresh; ambiguous
commands are never replayed after cancellation, timeout, or connection loss. The
legacy host fallback is limited to known operations and eligible failures; it is
not a general retry or a second measurement source. The caller owns polling,
backoff, freshness, and deciding how to reconcile an uncertain command.

Cognito failures use the `MysaAuthError` hierarchy. The implementation handles
the password-verifier SRP challenge; unsupported challenges raise
`MysaUnsupportedChallengeError`.

### Validation references

HTTP/parser behavior is covered by [`tests/cloud/test_client.py`](../../../tests/cloud/test_client.py)
and SRP by [`tests/cloud/test_srp.py`](../../../tests/cloud/test_srp.py), using
fixtures rather than live devices. Live checks established the distinctions
between accepted writes, stored records, and reported/applied values above;
full scheduled execution, notification delivery, and installation of a new
firmware release were not established. The two Lites also differed in cloud
setpoint/mode application, so confirmation remains necessary even for a
supported route.

### Device record and firmware catalog reads

`await client.get_device(device_id)` sends `GET /devices/{id}` and returns a
`DeviceRecord` from the response's `{"Device": {"Id": "…", "Model": "…", …}}`
envelope. It reads stored settings, not live state. The required `Id` must match
the requested ID. Missing optional settings remain unknown, unknown fields stay
in `raw`, and the model cache used by legacy state parsing is updated.

`await client.get_firmware_catalog()` sends `GET /devices/firmware` and returns
`dict[str, FirmwareRecord]`, keyed by device ID. Its wire envelope is
`{"Firmware": {"device-id": {"Device": "device-id", "InstalledVersion": "3.17.5.13"}}}`.
`FirmwareRecord` exposes `device_id`, nullable `installed_version`, and `raw`.
This is the account's catalog of reported installed versions, not a list of
releases available to install. It does not infer update availability or an
allowed version; use `get_firmware_info(id)` for that separate service. Catalog
and live-state versions may disagree in age; neither read overwrites the other.

Both methods try the modern host first, then use the existing legacy read
fallback and five-minute host cache, independently of other operations. A 404
on both hosts raises `MysaNotFoundError`; a malformed envelope or record raises
`MysaApiError`. An empty `Firmware` object is a valid empty catalog. A catalog
entry must identify the matching `Device`; missing/null `InstalledVersion`
remains `None`, while a non-string version is rejected. Invalid entries fail the
whole call rather than silently returning partial catalog data.

The envelopes were checked against saved October 2026 responses from Baseboard
Lite (`BB-V2-0-L`, firmware `3.17.5.13`) on both hosts. The legacy catalog is
app-derived for V1/V2, Lite, in-floor, and AC; other hardware has not been
validated. These reads do not add newer V3/Central HVAC/Climate Sensor OTA fleet
support. Settings are parsed without a model whitelist so unknown model strings
and extra fields remain available, without implying control support.

Constructed regression fixtures derived from those envelope shapes:
[single record](../../../tests/fixtures/device_record.json) and
[firmware catalog](../../../tests/fixtures/firmware_catalog.json). They use
synthetic identifiers and are not additional hardware observations.
