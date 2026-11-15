# Cloud Python API

- [Authentication](#authentication)
- [Using the client](#using-the-client)
- [Semantic command execution](#semantic-command-execution)
- [Setpoint rules](#setpoint-rules)
- [How it is organized](#how-it-is-organized)
- [Public API and integration helpers](#public-api-and-integration-helpers)
- [Errors, timeouts, retries, and caller responsibilities](#errors-timeouts-retries-and-caller-responsibilities)
- [Device record and firmware catalog reads](#device-record-and-firmware-catalog-reads)

Start with [connecting clients](../../guides/connect.md).
Wire formats belong to the [cloud protocol reference](../protocol/cloud.md);
recommended readback policy belongs to the [state guide](../../guides/state-and-commands.md).

## Authentication

Use `MysaAuth.sign_in(email, password)` for the Cognito SRP exchange, or
initialize it with a saved refresh token. `async_get_id_token()` handles
refresh; retain a replacement refresh token through `on_refresh_token` when
one is returned. The public Cognito identifiers are in [`const.py`](../../../src/mysa_client/cloud/const.py),
and the exchange is described in the [authentication reference](../protocol/cloud-auth.md#cognito-sign-in).
Credentials and token persistence belong to the caller.

## Using the client

`MysaAuth` and `MysaClient` are exported by [`__init__.py`](../../../src/mysa_client/cloud/__init__.py).
The caller supplies and closes the `aiohttp` session. See the complete
[cloud example](../../../examples/cloud_basic.py) for login, token persistence,
account discovery, reading, and an explicitly enabled setpoint write.

## Semantic command execution

`await client.execute_command(device_id, command, family=family,
capabilities=caps, state=state, command_value=lookup)` validates and sends one
`CloudCommand`, returning `None` on request success. `family`, `capabilities`
and `state` are required keyword context (each can be unknown/`None`);
`command_value` is an optional pending-value lookup. The method performs no
implicit discovery, state reads or confirmation. Validation can raise
`ValueError`; transport errors follow the hierarchy below. Follow the
[confirmation guide](../../guides/state-and-commands.md#send-and-confirm-a-command)
for the caller sequence.

## Setpoint rules

Mysa accepts setpoints only on its step grid, usually 0.5 °C. Every temperature
written through this package goes through `SetpointRules` in
[`setpoints.py`](../../../src/mysa_client/cloud/setpoints.py), which is the only
code that can create `Setpoint` and `TemperatureOffset` values. The builders
`temperature_changes`, `auto_temperature_changes`, `make_state`,
`setpoint_limit_changes`, `temperature_offset_changes`, `climate_command`,
`build_schedule_events`, `ScheduleEventSpec`, `HoldCommand`/`resolve_hold`,
`async_apply_hold`, and the matching `MysaClient` setters accept nothing else
and raise `TypeError` for a plain number (`resolve_hold` and
`build_schedule_events` also take `rules=` and then normalise plain numbers
themselves; see [holds and schedules](#holds-and-schedules)); constructing `Setpoint(21.5)` directly
also raises `TypeError`. Read the value back with `.celsius`. `limit()` returns a
`SetpointLimit`, a `Setpoint` subclass that only `setpoint_limit_changes` and
`MysaClient.set_setpoint_limit` accept; every target writer (cloud, HomeKit and
MQTT) refuses it with `TypeError`, since it was checked against the hardware
range rather than a mode's target range. `require_setpoint` refuses it and
`require_limit` accepts it (and, as before, a target `Setpoint`). The value types
and checks are defined in the dependency-free `mysa_client._temperature`, so
the HomeKit and MQTT packages can share them without importing cloud. Local
writes follow the same rule: `HomeKitDevice.async_write` for `Field.TARGET_TEMPERATURE`
and the MQTT `set_setpoint`, `set_mode(setpoint=...)` and
`encode_thermostat_command(setpoint=...)` take a `Setpoint` too.

`SetpointRules(capabilities=None, homekit_range=None, *, setpoint_min=None,
setpoint_max=None, record=None, cool_min=None, cool_max=None, family=None)`
derives one `SetpointRange(minimum, maximum, step)` per `SetpointKind` (heat,
cool, auto heat, auto cool, and auto, a Mini-Split AC's single auto target),
plus `offset_range` and `min_setpoint_delta`. `family` (exposed as
`rules.family`) is read from `record.model`, else `capabilities.model`, when not
given:

- Heat intersects the HomeKit `(min, max, step)` range, the capability heat
  range and the owner's limits, as `heating_range()` does; the HomeKit step wins.
  `hardware_heat_range` is the same without the owner's limits.
- Cool comes from the capability cool range. Without one it comes from a
  Mini-Split AC's settings `record` (`DeviceRecord.ac_temperature_range(Mode.COOL)`:
  the remote's `SupportedModes[<mode>].temperatures`, else `TempRange`, step 1 °C
  unless the list is finer), and failing that from the HomeKit range. An AC
  record's heat-mode range likewise stands in for a missing capability heat
  range. Other models' records are not read for ranges. The owner's cooling
  limits `cool_min` / `cool_max` (`DeviceState.cool_min` / `cool_max`) narrow
  it. `hardware_cool_range` is the capability or AC record cool range without
  them (the HomeKit range does not count).
- Both heat/cool ranges come from capabilities only. Owner limits apply to
  neither.
- Auto (`SetpointKind.AUTO`) exists only when `family` is `ModelFamily.AC`:
  the record's auto-mode range (`ac_temperature_range(Mode.AUTO)`:
  `SupportedModes["2"].temperatures`, else `TempRange`), else the HomeKit
  range, else 16–30 °C in whole degrees (`AC_MIN_TEMP`–`MAX_TEMP`, what Mysa's
  app 4.21.1 shows an AC without a `TempRange`). For other families its bounds
  are unknown.
- Without capabilities or a record, only the HomeKit range applies. Any part
  may be absent: a missing step is 0.5 °C, and a missing bound stays `None` in
  `range_for` but is checked as described under **Unknown bounds** below.
- A non-finite or absurd input (a bound or owner limit beyond
  `MAX_MAGNITUDE`, 1000 °C; a step that is not in (0, 10] °C; a negative
  `min_setpoint_delta`) raises `SetpointConfigError` (`name`, `value`) when the
  rules are built.

In heat/cool mode (`Mode.AUTO`) only a Mini-Split AC has a single target, as
in Mysa's app 4.21.1 ([auto-mode targets](../protocol/cloud-state.md#auto-mode-targets)):
`kind_for_mode(Mode.AUTO, ModelFamily.AC)` is `SetpointKind.AUTO`, and
`temperature_changes(Mode.AUTO, sp, family=ModelFamily.AC)` and
`climate_command(ModelFamily.AC, Mode.AUTO, temperature=sp)` write
`targetAuto.setpoint`, which `DeviceState.setpoint_for(Mode.AUTO)` reads back
(`auto_setpoint`). For any other family, or none, a single temperature in auto
mode raises `SetpointModeError` in all four. A Central HVAC's auto targets are
the pair: `heat_cool(low, high)` for both, or `heat_cool_side("heat" | "cool",
value, other=...)` to change one and keep the other, written with
`climate_command(..., low=...)` or `high=...` alone; `setpoint_for(Mode.AUTO)`
is `None` for it. 0.3.0 treated a single auto setpoint as the heat target and
wrote `targetHeat`; the app never does. The AC auto and single-side writes are
derived from the app and not verified on a device.

`climate_command(family, selected_mode, *, mode=None, temperature=None,
low=None, high=None)` writes a single `temperature` to `targetHeat` in heat,
emergency heat, off or no mode (the app's writer tests `HEAT || EMERGENCY_HEAT`)
and to `targetCool` in cool. Every refusal raises `SetpointModeError` (`mode`,
possibly `None`): a single temperature in auto mode except on an AC, `low` /
`high` outside auto mode or on anything but a Central HVAC, `temperature`
together with `low` / `high`, and fan-only, dry, sleep or pre-cool.
`validate_cloud_command` checks a single auto side against the state's other
side (or a pending write of it) and refuses it when that side is unknown.
Fan-only, dry, sleep and pre-cool have no setpoint section (see the
[mode table](../protocol/cloud-state.md#mode-and-heater-enums)), so
`temperature_changes` raises `SetpointModeError` for them instead of writing
`targetHeat`; `SetpointRules.setpoint` still checks a value against the heat
range for them, and holds and schedules drop their temperature.

Values round to multiples of the step anchored at 0, halfway values up
(`floor(x / step + 0.5) * step`, then 4 decimals), so 20.25 becomes 20.5 and
22.78 (HA's 73 °F) becomes 23.0. Every method takes `unit="C"` or `"F"`; °F
converts with `(f - 32) / 1.8` before rounding (an offset delta with `/ 1.8`).

| Method | Range used | Returns |
| --- | --- | --- |
| `setpoint(mode, value)` | Cool for `Mode.COOL`; auto for `Mode.AUTO` on a Mini-Split AC (`SetpointModeError` on any other family); heat otherwise | `Setpoint` |
| `heat_cool(low, high)` | Auto heat and auto cool; the rounded gap must be at least `min_setpoint_delta` (0 when unknown) | `(Setpoint, Setpoint)` |
| `heat_cool_side(side, value, *, other)` | Auto heat (`side="heat"`) or auto cool (`"cool"`); then the gap to `other`, the other side's current target in °C or a `Setpoint`. `other=None` raises `SetpointGapUnknownError` (`side`) | `Setpoint` |
| `limit(value, minimum=..., kind=SetpointKind.HEAT)` | Hardware heat range, then strictly below `setpoint_max` (a minimum) or above `setpoint_min` (a maximum). With `kind=SetpointKind.COOL`, `hardware_cool_range` and `cool_max` / `cool_min`; `SetpointModeError` when the model advertises no cooling range. Other kinds raise `ValueError` | `SetpointLimit` |
| `offset(delta)` | Offset range | `TemperatureOffset` |
| `schedule_event(mode, value)` | Same as `setpoint` | `Setpoint` |
| `range_for(kind_or_mode)` | — | `SetpointRange` |
| `fahrenheit_bounds(kind_mode_or_range, delta=False)` | Lowest and highest whole °F whose rounded value is in range; any `SetpointRange` (such as `hardware_heat_range`) works, and `delta=True` treats it as an offset | `(int or None, int or None)` |
| `offset_fahrenheit_bounds()` | Whole °F deltas in `offset_range` | `(int or None, int or None)` |
| `envelope(kinds)` | The smallest range covering every listed kind or mode, for display; unknown bounds become 5 and 30 °C | `SetpointRange` |
| `fahrenheit_envelope(kinds)` | Whole °F of `envelope`, each kind rounded on its own step; unknown bounds become 41 and 86 °F | `(int, int)` |

The module-level `fahrenheit_bounds(range, delta=False)` does the same for a
range on its own, and `limits_ordered(minimum, maximum)` is the owner-limit
comparison.

**Unknown bounds.** A write is checked against the bounds that HomeKit, the
capabilities, the record or the owner advertise. A bound none of them
advertises is replaced, for checking and clamping only, by a conservative
safe envelope: `SAFE_MINIMUM`–`SAFE_MAXIMUM`, 5–35 °C, for targets and limits,
and ±`SAFE_OFFSET`, 5 °C, for offsets. So rules built before capabilities load
(`SetpointRules()`, or HomeKit only for a heat/cool pair or a cool schedule
event) still refuse, or with `strict=False` clamp, a value such as 500 °C. 35 °C
is the highest bound in any captured capability document (Central HVAC cool),
5 °C the app's `MIN_TEMP`, and ±5 °C the captured offset range; an advertised
bound outside the envelope still wins. `range_for` keeps reporting the unknown
bound as `None`, and an error's `limits` shows the envelope bound that applied.
A display envelope needs two ends as well, so `envelope` and `fahrenheit_envelope`
assume `DEFAULT_MINIMUM`–`DEFAULT_MAXIMUM`, 5–30 °C (41–86 °F), for a bound
none of the listed ranges advertises. That is the Mysa app's own default range
(`MIN_TEMP` / `MAX_TEMP`) and the range the MQTT protocol enforces. When the
bounds leave no room for a second value (equal owner limits), the maximum is
one step (one °F) above the minimum.

**Owner limits.** A minimum must be strictly below the maximum. `limit()`
checks the new value against the other current limit and raises
`SetpointLimitOrderError` (`value`, `other`, `minimum`) when they are equal or
reversed, whatever `strict` says; `validate_cloud_command` refuses the same pairs
with `"Minimum setpoint must be below the maximum"` (and the cooling pair with
`"Minimum cooling setpoint must be below the maximum"`). Mysa's app also keeps
the two limits at least 2 °C apart; these rules don't.

**Limit writes.** `setpoint_limit_changes(value, *, minimum, kind=SetpointKind.HEAT,
family=None)` writes `targetHeat.lockoutMin` / `lockoutMax`, or `targetCool` for
`kind=SetpointKind.COOL`. A Mini-Split AC (`family=ModelFamily.AC`) gets the
value under both sections in one body, whatever `kind`, as Mysa's app does.
A cooling limit for a heat-only family raises `SetpointModeError`; any kind
other than heat or cool raises `ValueError` (the app has no auto-section
limit). `MysaClient.set_setpoint_limit(device_id, value, *, minimum,
kind=SetpointKind.HEAT, family=None, route="S")` sends it; cooling limits
have no settings-record field, so a `route` other than `"S"` raises
`ValueError` for them. The read-back is `DeviceState.cool_min` / `cool_max`.
One `kind` parameter, defaulting to heat, keeps 0.3.0 calls unchanged and
matches the `SetpointKind` that `limit()` checked the value against; pass the
same `kind` to both. The cooling and AC encodings are derived from Mysa's app
4.21.1 and not verified on a device. Equal limits are refused
because they leave a single allowed target: every other write would fail and
every range a UI shows would collapse to one point.

With `strict=True` (the default) a rounded value outside its range raises
`SetpointOutOfRangeError` with `requested` (°C before rounding), `rounded`,
`limit` and `bound` (`"min"` or `"max"`). It also names what failed: `limits`
is the whole violated `SetpointRange`, `kind` its `SetpointKind` (`None` for the
hardware limit range and the offset range), `side` is `"heat"` or `"cool"` for
the failed half of a `heat_cool` pair (the heat side is checked first, so this
holds when `low` equals `high`) and `None` otherwise, and `delta` is true for
an offset. `fahrenheit_limit` is the closest allowed whole °F on that bound
(a whole °F delta for an offset). With `strict=False` the result is
clamped to the nearest step-grid value at or inside the limit and its `clamp` is a `SetpointClamp(requested, limit,
bound)` (otherwise `None`) for the caller's log. The clamped value always lies
inside the range. When no step-grid value does (inverted owner limits such as
25 and 20 °C, or 20.2–20.4 °C with a 0.5 °C step), both modes raise
`SetpointRangeEmptyError` with `limits`, `kind` and `side`. NaN, infinity and
values beyond `MAX_MAGNITUDE` raise `SetpointValueError` (with `value`) in both
modes. A heat/cool pair that is too
close always raises `SetpointGapTooSmallError` with `low`, `high`, `gap` and
`required`; the rules never move a value to make room. These errors,
`SetpointGapUnknownError`, `SetpointLimitOrderError`, `SetpointModeError`, `SetpointRangeEmptyError`,
`SetpointValueError` and `SetpointConfigError` subclass `SetpointError`, a
`ValueError`; a non-number value still raises `TypeError`. All numbers are in °C so callers can word
messages in the user's unit.

For Fahrenheit display, `fahrenheit_label(celsius)` rounds halfway values up
like the Mysa app's `Math.round(c * 1.8 + 32)`: 22.5 °C shows as 73 °F, not
Python's 72. Seven 0.5 °C steps between 5 and 40 °C (7.5, 12.5, …, 37.5) have no
whole °F of their own, which is why a 21.5 °C maximum allows up to 71 °F but a
22.5 °C maximum only up to 72 °F. `fahrenheit_offset(delta_c)` gives a whole °F
delta.

<a id="holds-and-schedules"></a>
### Holds and schedules

Modes in holds and schedule events go through `modes.parse_mode`, which
accepts a `Mode`, its name in any case (`"HEAT"`, `"heat"`, `"Fan_Only"`), the
capability names (`"fanOnly"`, `"emergencyHeat"`) and Home Assistant's
lowercase `HVACMode` values (`"heat_cool"` is `Mode.AUTO`), and raises
`UnknownModeError` (a `ValueError` with `value`) for anything else, including a
plain number. `HoldCommand` and `ScheduleEventSpec` normalise their `mode`
the same way.

`resolve_hold(*, requested_mode, current_mode, requested_temperature,
current_temperature, until=None, rules=None, unit="C")` picks the hold's mode
(the requested one, else the current one, else heat) and normalises for that
mode. With `rules`, both temperatures may be plain numbers:
`requested_temperature` is in `unit` and checked strictly; `current_temperature`
is the device's current target in °C and is clamped (`strict=False`), since the
hold only re-sends it. A `Setpoint` is used as given, so typed callers keep
working; without `rules` a plain number raises `TypeError`. An off, fan-only,
dry, sleep or pre-cool hold carries no temperature: both temperatures are dropped
without being checked, so `async_apply_hold` writes none. A heat/cool hold can't carry a single one: a requested
temperature raises `SetpointModeError` (with `mode`), and the current target is
dropped. `async_apply_hold` takes the resolved `HoldCommand`, so it needs no
rules.

`build_schedule_events(events, *, default_mode=Mode.HEAT,
client_key="mysa-client", rules=None, unit="C")` does the same per event: with
`rules`, each plain temperature is in `unit` and goes through
`rules.schedule_event` for the event's `hvac_mode` (`default_mode` when it is
missing or `None`; both take any spelling `parse_mode` accepts), strictly. The temperature
of an `off`, `fan_only`, `dry`, `sleep` or `pre_cool` event is dropped unchecked, and an `auto` event with a temperature raises
`SetpointModeError`.

## How it is organized

| File | Responsibility |
| --- | --- |
| [`auth.py`](../../../src/mysa_client/cloud/auth.py), [`srp.py`](../../../src/mysa_client/cloud/srp.py) | Cognito authentication, serialized token refresh, SRP calculations |
| [`client.py`](../../../src/mysa_client/cloud/client.py) | HTTP requests, endpoint methods, response handling, and explicit validated `execute_command()` |
| [`commands.py`](../../../src/mysa_client/cloud/commands.py) | Semantic controls, enum conversion, schedule encoding |
| [`setpoints.py`](../../../src/mysa_client/cloud/setpoints.py) | Setpoint rounding, range validation, and Fahrenheit display rules |
| [`_temperature.py`](../../../src/mysa_client/_temperature.py) | The `Setpoint`, `SetpointLimit`, `TemperatureOffset` and `SetpointClamp` types and the `require_setpoint`/`require_limit`/`require_offset` checks, outside every protocol package so HomeKit and MQTT can check them without importing cloud; it uses only the standard library. Import them from `setpoints.py`, which re-exports them and alone creates values. |
| [`models.py`](../../../src/mysa_client/cloud/models.py) | Typed response parsing, preserving original JSON in `raw`; `DeviceRecord.ac_temperature_range(mode)` reads a Mini-Split AC remote's per-mode temperatures |
| [`const.py`](../../../src/mysa_client/cloud/const.py), [`exceptions.py`](../../../src/mysa_client/cloud/exceptions.py) | Hosts, protocol constants/enums, errors |

The standalone package has no Home Assistant dependency. The client includes
reusable Mysa-specific validation, state reconciliation, command confirmation,
device interpretation, and optional local/cloud routing; applications own
polling cadence, source/freshness choices, durable storage, and lifecycle. See
the [optional runtime guide](runtime.md) and the HA
[architecture guide](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/architecture.md).
Install the published package from [PyPI](https://pypi.org/project/mysa-client/).

## Public API and integration helpers

The [generated callable and export index](symbols.md#cloud-exports) lists the
current top-level cloud API directly from `__all__`. It also provides signatures
for the client methods.

These submodule helpers are supported for standalone callers and integrations:

| Module | Supported names |
| --- | --- |
| [`commands.py`](../../../src/mysa_client/cloud/commands.py) | `CloudCommand`, `state_command`, `settings_command`, and field encoders including `auto_temperature_changes`, `button_lock_changes`, `display_brightness_changes`, `display_unit_settings`, `fan_speed_changes`, `heater_type_settings`, `hvac_fan_changes`, `mode_changes`, `setpoint_limit_changes`, `swing_changes`, `temperature_changes`, `temperature_offset_changes` |
| [`device_commands.py`](../../../src/mysa_client/cloud/device_commands.py) | `climate_command`, `brightness_command`, `hvac_fan_command`, `hvac_fan_speed_command`, `display_unit_command` |
| [`setpoints.py`](../../../src/mysa_client/cloud/setpoints.py) | `SetpointRules`, `Setpoint`, `TemperatureOffset`, `SetpointKind`, `SetpointRange`, `SetpointClamp`, `SetpointLimit`, the `SetpointError` hierarchy (`SetpointOutOfRangeError`, `SetpointGapTooSmallError`, `SetpointGapUnknownError`, `SetpointLimitOrderError`, `SetpointModeError`, `SetpointRangeEmptyError`, `SetpointValueError`, `SetpointConfigError`), `fahrenheit_bounds`, `fahrenheit_label`, `fahrenheit_offset`, `limits_ordered`, `round_to_step`, `round_half_up`, `to_celsius`, `kind_for_mode`, `require_setpoint`, `require_limit`, `require_offset`, `DEFAULT_STEP`, `DEFAULT_MINIMUM`, `DEFAULT_MAXIMUM`, `SAFE_MINIMUM`, `SAFE_MAXIMUM`, `SAFE_OFFSET`, `MAX_MAGNITUDE` (see [setpoint rules](#setpoint-rules)) |
| [`device.py`](../../../src/mysa_client/cloud/device.py) | `basic_modes`, `brightness_pair`, `heating_range`, `hvac_activity`, `hvac_fan_operation` |
| [`const.py`](../../../src/mysa_client/cloud/const.py) | `API_BASE`, `EXPERIMENTAL_CLOUD_FAMILIES`, `SWING_VERTICAL`, `model_family`; public models and enums are also re-exported at the package level |
| [`models.py`](../../../src/mysa_client/cloud/models.py) | The response models exported by the package root, retained at this submodule path for direct imports |
| [`exceptions.py`](../../../src/mysa_client/cloud/exceptions.py) | The public `MysaError` exception hierarchy, retained at this submodule path for direct imports |
| [`observations.py`](../../../src/mysa_client/cloud/observations.py) | `MEASUREMENT_MAX_AGE`, `MEASUREMENTS`, `STATE_FIELDS`, `TELEMETRY_FIELDS`, `Observation`, `number`, `observe`, `valid_value` |
| [`reconciliation.py`](../../../src/mysa_client/cloud/reconciliation.py) | `reconcile`, `PendingCommand`, `PendingCommandTracker`, `pending_command` |
| [`trace.py`](../../../src/mysa_client/cloud/trace.py) | `route_template` for redacted request-route reporting |
| [`validation.py`](../../../src/mysa_client/cloud/validation.py) | `validate_basic_command` and `validate_cloud_command` for device/capability validation |
| [`schedule_workflow.py`](../../../src/mysa_client/cloud/schedule_workflow.py) | `ScheduleWorkflow`, `ScheduleJournal`, `ScheduleTransport`, and `MysaScheduleTransport` for replacement, clear, and recovery |
| [`holds.py`](../../../src/mysa_client/cloud/holds.py) | `resolve_hold`, `async_apply_hold` |
| [`modes.py`](../../../src/mysa_client/cloud/modes.py) | `parse_mode`, `UnknownModeError` |
| [`account_workflow.py`](../../../src/mysa_client/cloud/account_workflow.py) | Model eligibility, minimal account claim, and best-effort cleanup before account removal |

Other names prefixed with an underscore are private implementation details.

## Errors, timeouts, retries, and caller responsibilities

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
not a general retry or a second measurement source. The client provides
reusable helpers to reconcile supplied observations, validate semantic
commands, track confirmation evidence, and coordinate explicitly supplied
transports. The caller chooses polling/backoff cadence, source/freshness
policy, durable storage, and when to run readbacks or recovery workflows. It
must still treat ambiguous commands as uncertain until reported evidence is
available.

Cognito failures use the `MysaAuthError` hierarchy. The implementation handles
the password-verifier SRP challenge; unsupported challenges raise
`MysaUnsupportedChallengeError`.

<a id="reading-the-legacy-firmware-catalog"></a>

## Device record and firmware catalog reads

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

The envelope evidence is recorded in the [cloud validation record](../../evidence/cloud.md). The legacy catalog is
app-derived for V1/V2, Lite, in-floor, and AC; other hardware has not been
validated. These reads do not add newer V3/Central HVAC/Climate Sensor OTA fleet
support. Settings are parsed without a model whitelist so unknown model strings
and extra fields remain available, without implying control support.

Constructed regression fixtures derived from those envelope shapes:
[single record](../../../tests/fixtures/device_record.json) and
[firmware catalog](../../../tests/fixtures/firmware_catalog.json). They use
synthetic identifiers and are not additional hardware observations.

[Documentation index](../../README.md)

<!-- api-symbol: mysa_client.cloud.client:MysaClient.execute_command -->
<!-- api-symbol: mysa_client.cloud.client:MysaClient.get_firmware_catalog -->
<!-- api-symbol: mysa_client.cloud.auth:MysaAuth.sign_in -->
