# Changelog

## Unreleased

- **Breaking:** every temperature write now requires a typed value from the new
  `SetpointRules` (in `mysa_client.cloud.setpoints`, also exported from
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
  `rules.schedule_event(mode, value)`.
- `SetpointRules` rounds to each range's own step (0.5 °C when none is
  advertised), anchored at 0 with halfway values rounded up, accepts °C or °F
  input, and either raises `SetpointOutOfRangeError` or, with `strict=False`,
  clamps and records a `SetpointClamp`. Heat/cool pairs closer than the
  device's minimum gap raise `SetpointGapTooSmallError`. This fixes rejected
  writes for Fahrenheit picks such as 73 °F (22.78 °C).
- Add `fahrenheit_label`, `fahrenheit_offset`, and
  `SetpointRules.fahrenheit_bounds` for whole-°F display that matches the Mysa
  app, which rounds 22.5 °C up to 73 °F.

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
