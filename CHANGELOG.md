# Changelog

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
