# Changelog

## Unreleased

- In-Floor floor sensor: `DeviceState.from_legacy()` reads `Infloor` into
  `probe_temperature` and `TrackedSensor` into the new
  `DeviceState.tracked_sensor`; `DeviceRecord.tracked_sensor` reads `Tracking`.
  New `TrackedSensor` enum (`ambient`, `floor`). From third-party In-Floor
  captures; not verified on a device.
- MQTT telemetry is decoded for more models: Baseboard V1 (`MsgType: 0`),
  In-Floor (`msg: 17`) and Mini-Split AC (`msg: 30`). They were reported as
  `MessageKind.OTHER` before. `MqttTelemetry` gains `floor_temperature`,
  `tracked_sensor`, `line_voltage` and `current`. An In-Floor's `duty_cycle` is
  its relay state (`heatStat`), since its own `dutyCycle` field doesn't follow
  the relay. From third-party captures; not verified on a device.
- New `MysaMqttClient(experimental_commands=True)`: also send setpoint, mode and
  hold commands to Baseboard V1, Baseboard V2 and In-Floor, each with its own
  command type (new `command_type()`), and up to 40 °C on an In-Floor. Off by
  default and not verified on a device. `supports_commands()` takes
  `experimental=`, and `encode_thermostat_command()` takes `model=`.
- `validate_cloud_command` refuses an operating mode that the thermostat's
  capabilities don't list, for every model except a Mini-Split AC (whose modes
  come from its remote record). It was checked only for Baseboard V3, Central
  HVAC and Climate Sensor before. Mysa answers 200 to such a mode and drops it.
- Docs: Mysa's cloud doesn't reject setpoints off the step grid (it rounds them
  to 0.1 °C) and doesn't check a write against the thermostat's capabilities;
  the tested outcomes are in the cloud state reference. Also documented: the
  usage fields Mysa supports, the required `source`, and the rate-limit
  headers.
- New `MysaClient.update_schedule()`: replace a weekly schedule's name and
  events in place (`PUT /schedule/{id}`), verified on a Baseboard Lite.
- `MysaClient.get_usage()` takes `fields=` and `raw_fields=`. `UsageDay` and
  `UsageBucket` gain `heat_stage2_seconds`, `emergency_heat_seconds`,
  `cool_seconds`, `cool_stage2_seconds`, `power`, `humidity`,
  `probe_temperature` and `samples`, and `UsageDay.raw` holds the new
  `UsageSample` readings. The default request is unchanged. New constants
  `USAGE_FIELDS` and `USAGE_RAW_FIELDS`. Only heating on-time, the temperature
  value, the sample count and the raw readings are verified, on a Lite.
- **Behavior change:** `encode_schedule_events()` (and so
  `build_schedule_events()`) raises `ValueError` for more than 56 events or two
  events on the same day and time. Mysa's cloud stores such a schedule without
  an error. New `check_schedule_events()` and `MAX_SCHEDULE_EVENTS`.
- New `Capabilities.single_auto_setpoint`.
- Docs: a usage bucket's `t` depends on the range requested (a short range
  gives the bucket's mean room temperature, longer ranges a smoothed value);
  what the schedule route accepts without checking; Mysa's older schedule
  store and why a Lite's schedule is named `Schedule`; how Mysa counts Central
  HVAC stage runtime; the In-Floor range and tracked sensor.

## 0.4.0 (2026-11-15)

- **Breaking:** a single temperature in heat/cool (auto) mode is no longer the
  heat target. Mysa's app 4.21.1 never writes `targetHeat` in auto, so
  `kind_for_mode(Mode.AUTO)`, `SetpointRules.setpoint(Mode.AUTO, ...)`,
  `temperature_changes(Mode.AUTO, ...)` and `climate_command(..., temperature=...)`
  in auto raise `SetpointModeError`, except on a Mini-Split AC.
  `DeviceState.setpoint_for(Mode.AUTO)` returns the new `auto_setpoint` (`None`
  on a Central HVAC) instead of the heat setpoint.
- A Mini-Split AC's auto target is written as `targetAuto.setpoint`: pass
  `family=ModelFamily.AC` to `temperature_changes`, `MysaClient.set_temperature`
  or `SetpointRules`, or use `climate_command(ModelFamily.AC, Mode.AUTO,
  temperature=...)`. Its range is the remote's auto-mode list, else its
  temperature range, else HomeKit's, else 16-30 °C in whole degrees. New:
  `SetpointKind.AUTO`, `rules.family`, `DeviceState.auto_setpoint`. Derived from
  Mysa's app; not verified on a device.
- A Central HVAC's auto targets can change one at a time:
  `climate_command(..., low=...)` or `high=...` alone writes only that field, and
  `SetpointRules.heat_cool_side(side, value, other=...)` checks the gap to the
  other side (new `SetpointGapUnknownError` when it is unknown). Derived from
  the app; not verified on a device.
- **Breaking:** `climate_command` raises `SetpointModeError` (still a
  `ValueError`) for every refusal, and accepts emergency heat and no mode as
  heat. `validate_cloud_command` has separate messages for an unknown other side
  and a too-small gap.
- **Breaking:** `SetpointKind` has a new member, `AUTO`; code that iterates
  `SetpointKind` sees it.
- Fix `build_schedule_events`, `resolve_hold`, `HoldCommand`, `ScheduleEventSpec`
  and `make_state` refusing their own `Mode` values: `Mode.HEAT` raised
  `KeyError: '4'`, and `HoldCommand(mode="heat")` dropped its temperature. They
  accept a `Mode`, its name in any case, a capability name or a Home Assistant
  mode name (`"heat_cool"` is auto) through the new `parse_mode`, and raise the
  new `UnknownModeError` for anything else.
- Add cooling setpoint limits (#10): `setpoint_limit_changes`,
  `SetpointRules.limit` and `MysaClient.set_setpoint_limit` take
  `kind=SetpointKind.COOL` and write `targetCool.lockoutMin` / `lockoutMax`; a
  Mini-Split AC (`family=ModelFamily.AC`) gets the value under both sections.
  New rule inputs `cool_min` / `cool_max` and `hardware_cool_range`. Models
  without a cooling range are refused. Derived from the app; not verified on a
  device.
- Add `MysaClient.get_demand_response_state()` and `DemandResponseState` for
  `GET /devices/{id}/drstate` (#5). An empty answer (`has_data` false, confirmed
  on Baseboard Lite firmware 3.17.5.13) stays distinct from an explicit `none`;
  `in_event(now)` applies the app's rule.
- Add health and electrical fields to `DeviceState` (#1): `free_heap`,
  `raw_temperature`, `core_temperature` and `voltage` (confirmed on the Lite);
  `probe_temperature`, `power_wattage`, `power_duty_cycle` and `power_time` (from
  the app); `power_current` (mA) and `max_current` (A, the highest load current
  seen), from third-party captures; `power_consumed`, `instant_load`, `on_time`
  and `effective_setpoint`, whose units or meaning are unverified. Plus the
  `power_load_watts` and `ground_fault` properties, and
  `demand_response_state` (the text next to `in_demand_response`).
- Add read-only firmware calls for the newer models (#19):
  `get_fleet_firmware_availability()`, which returns `in_fleet=False` for a
  device outside the service (confirmed on the Lite), `get_fleet_firmware_status()`,
  `FleetUpdateStatus`, `normalize_firmware_version()` and
  `firmware_versions_match()`. Starting and cancelling an update are not included.
- Add `MysaClient.get_sensor_links()` and `SensorLink` for `GET /linking` (#13,
  read only).
- Add `homekit.ESSENTIAL_FIELDS` and `HomeKitDevice.healthy`.
- Add `runtime.errors.error_category()`, `is_outcome_unknown()` and
  `ERROR_CATEGORIES`. **Changed:** `CommandRouter` uses them when the caller
  passes no classifier; it used to record every error as `unexpected_error` and
  never treat a cloud error as ambiguous.
- Add `runtime.state.LOCAL_MODE_TO_MODE` and `MODE_TO_LOCAL_MODE`.
- Add `MysaClient.get_usage_today()`, `UsageToday` and `local_day_start()`: usage
  since the thermostat's local midnight, correct on 23- and 25-hour days.
- **Breaking:** a bound no range advertises is no longer unchecked. Writes are
  checked and clamped against `SAFE_MINIMUM`-`SAFE_MAXIMUM` (5-35 °C) for
  targets and owner limits and ±`SAFE_OFFSET` (5 °C) for offsets in its place,
  so `SetpointRules().setpoint(None, 500)` raises `SetpointOutOfRangeError`
  (or clamps to 35 °C with `strict=False`), as do a heat/cool pair with no
  auto ranges and a cool schedule event with no cool range. An advertised
  bound outside the envelope still wins, and `range_for` still reports an
  unknown bound as `None`. Code that built rules without ranges to write
  values outside 5-35 °C must now pass a range.
- **Breaking:** `SetpointRules.limit` returns a `SetpointLimit`, a new
  `Setpoint` subclass. `setpoint_limit_changes` and
  `MysaClient.set_setpoint_limit` accept it; every target writer
  (`temperature_changes`, `auto_temperature_changes`, `make_state`,
  `climate_command`, `ScheduleEventSpec`, `build_schedule_events`,
  `HoldCommand`/`resolve_hold`, `async_apply_hold`, HomeKit
  `TARGET_TEMPERATURE` and the MQTT setpoint writers) raises `TypeError` for
  it, as does `require_setpoint`. The new `require_limit` accepts both. A
  `SetpointLimit` no longer compares equal to a target `Setpoint` of the same
  temperature.
- **Breaking:** a non-strict call never returns a value outside its range.
  When no step-grid value lies inside it (inverted owner limits such as 25 and
  20 °C, or 20.2-20.4 °C on a 0.5 °C step), strict and non-strict calls raise
  the new `SetpointRangeEmptyError` instead of returning 25.0 or 20.5.
- **Breaking:** NaN, infinity and temperatures beyond `MAX_MAGNITUDE`
  (1000 °C) raise the new `SetpointValueError` in strict and non-strict calls,
  instead of `OverflowError` (for 1e308) or a plain `ValueError`. Building
  `SetpointRules` with a non-finite or absurd bound, step or owner limit, or a
  negative `min_setpoint_delta`, raises the new `SetpointConfigError` instead
  of failing later or (for a NaN `setpoint_min`) silently ignoring the bound.
  All three subclass `SetpointError`, so they are still `ValueError`s.
- **Breaking:** `temperature_changes` raises `SetpointModeError` for fan-only,
  dry, sleep and pre-cool instead of writing `targetHeat`, matching the
  protocol's mode table, which lists no setpoint section for them. Holds
  (`resolve_hold`) and schedule events (`build_schedule_events`) in those modes
  now drop their temperature without checking it, as for off, instead of
  raising for a value outside the heat range; `async_apply_hold` therefore no
  longer re-sends a temperature after a hold in those modes.

## 0.3.0 (2026-11-15)

- **Breaking:** every temperature write now requires a typed value from the
  new `SetpointRules` (in `mysa_client.cloud.setpoints`, also exported from
  `mysa_client.cloud`). `temperature_changes`, `auto_temperature_changes`,
  `make_state`, `setpoint_limit_changes`, `climate_command` (`temperature`,
  `low`, `high`), `ScheduleEventSpec.temperature`, `build_schedule_events`
  event temperatures, `HoldCommand`/`resolve_hold`, `async_apply_hold`, and
  `MysaClient.set_temperature`/`set_setpoint_limit` take a `Setpoint`;
  `temperature_offset_changes` and `MysaClient.set_temperature_offset` take a
  `TemperatureOffset`. A plain number raises `TypeError`, and neither type can
  be constructed directly. `async_apply_hold`'s `set_temperature` callback now
  receives the `Setpoint`. Migrate with `rules = SetpointRules(capabilities,
  homekit_range, setpoint_min=..., setpoint_max=...)` and
  `rules.setpoint(mode, value)`, `rules.heat_cool(low, high)`,
  `rules.limit(value, minimum=...)`, `rules.offset(delta)` or
  `rules.schedule_event(mode, value)`. Local writes follow the same rule:
  `HomeKitDevice.async_write` takes a `Setpoint` for
  `Field.TARGET_TEMPERATURE` and writes its `.celsius`;
  `Field.CURRENT_TEMPERATURE` is now refused as read-only. MQTT
  `MysaMqttClient.set_setpoint` (whose `celsius` parameter is renamed
  `setpoint`), `set_mode(setpoint=...)` and
  `encode_thermostat_command(setpoint=...)` take a `Setpoint`. A plain number
  raises `TypeError` before anything is sent.
- `SetpointRules` rounds to each range's own step (0.5 °C when none is
  advertised), anchored at 0 with halfway values rounded up, accepts °C or °F
  input, and either raises `SetpointOutOfRangeError` or, with `strict=False`,
  clamps and records a `SetpointClamp`. Heat/cool pairs closer than the
  device's minimum gap raise `SetpointGapTooSmallError`. This fixes rejected
  writes for Fahrenheit picks such as 73 °F (22.78 °C).
- Add `fahrenheit_label`, `fahrenheit_offset`, and
  `SetpointRules.fahrenheit_bounds` for whole-°F display that matches the Mysa
  app, which rounds 22.5 °C up to 73 °F.
- `SetpointOutOfRangeError` names what failed: `limits` (the violated
  `SetpointRange`), `kind` (its `SetpointKind`, or `None` for the hardware
  limit and offset ranges), `side` (`"heat"`/`"cool"` for a `heat_cool` pair,
  correct even when low equals high) and `delta` (an offset), plus
  `fahrenheit_limit`, the closest allowed whole °F.
- `SetpointRules.fahrenheit_bounds` also takes any `SetpointRange` and
  `delta=True`; add `offset_fahrenheit_bounds()`, `envelope(kinds)` and
  `fahrenheit_envelope(kinds)`, and the module-level `fahrenheit_bounds`. An
  envelope assumes `DEFAULT_MINIMUM`/`DEFAULT_MAXIMUM` (5–30 °C, the Mysa
  app's and the MQTT protocol's range) for an unknown bound; validation still
  checks only advertised bounds.
- **Breaking:** owner limits must be strictly ordered. `SetpointRules.limit`
  checks the new value against the other current limit and raises the new
  `SetpointLimitOrderError`; `validate_cloud_command` now also refuses equal limits,
  with the message "Minimum setpoint must be below the maximum".
  `limits_ordered()` is the shared comparison.
- **Breaking:** in heat/cool mode a single setpoint is the heat target in
  `kind_for_mode`, `temperature_changes`, `SetpointRules.setpoint` and
  `DeviceState.setpoint_for`. `kind_for_mode(Mode.AUTO)` and
  `SetpointRules.setpoint(Mode.AUTO, ...)` no longer raise, and
  `DeviceState.setpoint_for(Mode.AUTO)` returns the heat setpoint instead of
  `None`. `climate_command(..., temperature=...)` in `Mode.AUTO` still raises;
  pass `low` and `high`.
- `resolve_hold` and `build_schedule_events` take `rules=` and `unit=` and
  normalise plain-number temperatures themselves (a hold's requested
  temperature strictly, its current target with `strict=False`). Typed
  `Setpoint` values still work. A temperature on a heat/cool hold or event
  raises the new `SetpointModeError`; an `off` schedule event's temperature is
  dropped.
- Add `Field.AMBIENT_LIGHT`: HomeKit reads Current Ambient Light Level (lux)
  when the accessory advertises a Light Sensor service. It is read-only, and a
  failed light read is reported in `failed_fields` without failing the
  thermostat read.
- Fix: a Mini-Split AC (and any thermostat without a capability cool range)
  had no cool range. `SetpointRules(record=...)` reads the AC's remote ranges
  through the new `DeviceRecord.ac_temperature_range(mode)`
  (`SupportedModes[<mode>].temperatures`, else `TempRange`), and cooling falls
  back to the HomeKit range.
- Fix: `CommandRouter` now confirms a timed-out local `Setpoint` write that
  landed, matching the device's float or int read-back against `.celsius`, and
  records plain `.celsius` values instead of `Setpoint` objects in history,
  pending and reconciliation records.

Thanks to @dlenski for reporting the HomeKit light sensor (#29), and to @giesler, @neotrantor, @mcrutherford and @noahtallen, whose Fahrenheit reports and fixes in Mysa Local (SokolskyNikita/mysa-homeassistant#27, SokolskyNikita/mysa-homeassistant#32, SokolskyNikita/mysa-homeassistant#33, SokolskyNikita/mysa-homeassistant#34) led to `SetpointRules`.

## 0.2.1 (2026-10-12)

- Preserve the original HomeKit discovery failure during the metadata retry
  cooldown instead of reporting missing required fields before a database has
  been read. Expose pending refresh, discovery failure, and missing-field details
  in semantic diagnostics. Ordinary reconnect continues to reuse saved keys.

## 0.2.0 (2026-10-12)

- Add reusable cloud-domain helpers for per-field observation reconciliation,
  pending-command confirmation and supersession, capability validation,
  device interpretation, and semantic command construction with expected
  effects derived from the same command description.
- Add opt-in runtime helpers for device views, local/cloud command routing,
  MQTT telemetry freshness and live-status renewal. They accept caller-owned
  state, transports, policy inputs, and callbacks; importing or constructing
  them does not connect, poll, persist, or start tasks.
- Add journal-backed schedule replacement/recovery, reusable hold resolution
  and application, and account claim/removal cleanup workflows. Existing
  Home Assistant schedule journal records remain compatible through the
  caller-supplied journal interface.
- Add a synthetic, in-memory runtime example that demonstrates state
  selection, semantic command validation, and reported-state confirmation
  without network access or hardware.

## 0.1.3 (2026-10-12)

- Add `get_device(id)` for a typed single settings record and
  `get_firmware_catalog()` for typed legacy installed-version records.
- Export `FirmwareRecord`, preserve unknown fields, validate response identities,
  and keep catalog versions separate from per-device update offers.
- Document response contracts, legacy fallback, errors, and model applicability;
  cover missing records, empty catalogs, and malformed responses.

## 0.1.2 (2026-10-12)

- Add an opt-in MQTT client behind the optional `mqtt` extra. It exposes
  subscribed device events, explicit live-status and echo requests, and
  experimental Lite setpoint/mode commands whose results distinguish broker
  acceptance from device confirmation. Standalone consumers choose when to
  connect and request telemetry; no transport is enabled automatically.
- Keep MQTT client construction and TLS setup off the event loop. Expose a
  connection generation counter so consumers can renew live reporting after
  reconnecting.
- Document the optional transport, observed authorization limits, and hardware
  validation. MQTT stays experimental; command acceptance and device
  acknowledgement are distinct from physical heater operation.

## 0.1.1 (2026-10-12)

- Fix HomeKit startup with a fresh caller-owned `AsyncZeroconf`: create missing
  discovery listeners and clean up only listeners owned by the client. Existing
  shared discovery browsers, including Home Assistant's, remain caller-owned.
- Make the discovery example wait for late mDNS results and print each accessory
  once during its listening window.
- Consolidate protocol references, diagnostic tools, examples, and raw API
  coverage in this repository, separate from Home Assistant behavior docs.

## 0.1.0 (2026-10-12)

Initial standalone release, extracted from Mysa Local for Home Assistant.

- Extract the cloud and HomeKit protocol clients into `mysa_client`.
- Add caller-owned session, credential persistence, and semantic device APIs.
- Add standalone usage examples and document reverse-engineering and hardware
  validation limits.

- Preserve the existing protocol APIs and MIT attribution; keep HTTP sessions,
  Zeroconf, polling, and credential persistence under caller ownership.
- Support Python 3.11–3.14, cloud-only installation, the optional HomeKit extra,
  and typed wheel distributions.
- Cover authentication, retries, parsing, pairing rollback, and example
  lifecycle with an independent test suite that does not install Home Assistant.
