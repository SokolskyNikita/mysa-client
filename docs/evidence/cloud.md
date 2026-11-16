# Cloud validation

## Client implementation and validation

The client exposes the subset recorded in the [API inventory](../api-coverage.md), with
model-specific mappings that remain app/code-derived unless hardware evidence is
identified. Automated protocol tests use simulated responses and do not establish
device compatibility. Live validation currently covers two Baseboard Lite
`BB-V2-0-L` devices on firmware `3.17.5.13`, which did not apply cloud setpoint
and mode writes the same way (see
[state and settings confirmation](../reference/protocol/cloud-state.md#state-and-settings-confirmation)). Other model profiles, full schedule execution, notification
delivery, and installation of a new firmware release remain unverified. The
[Home Assistant management inventory](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/implementation/progress.md)
separately records integration policy, gaps, and validation work.

## Validation references

HTTP/parser behavior is covered by [`tests/cloud/test_client.py`](../../tests/cloud/test_client.py)
and SRP by [`tests/cloud/test_srp.py`](../../tests/cloud/test_srp.py), using
fixtures rather than live devices. The [protocol matrices](../reference/protocol/cloud.md#readwrite-comparisons)
retain per-operation distinctions between accepted, stored, reported and
independently applied changes. See the [confirmation guide](../guides/state-and-commands.md)
for how to use these findings.

## Device record and firmware catalog evidence

The envelopes were checked against saved October 2026 responses from Baseboard
Lite (`BB-V2-0-L`, firmware `3.17.5.13`) on both hosts. The fixtures are
constructed regression data, not additional live observations. See the
[response contracts](../reference/python/cloud.md#device-record-and-firmware-catalog-reads).

## Read-only additions in 0.4.0

On 2026-11-15, read-only GET requests against both Baseboard Lites
(`BB-V2-0-L`, firmware `3.17.5.13`) returned: `{"DeviceDrState": {}}` from
`/devices/{id}/drstate` on both hosts; HTTP 404 from
`/ota/update/available/{id}`; `[]` from `/linking?targetId={id}`; and the
reading, power and diagnostics values used by the
[health and electrical fields](../reference/python/cloud.md#device-health-and-electrical-fields).
Both heaters were idle, so every load-related value was 0. Everything beyond
those answers (populated demand-response records, fleet offers and statuses,
link entries, probe temperature and the meaning of power values) comes from
static analysis of Mysa's Android app 4.21.1 and is not verified on a device.
The sanitized results are in [reads_0_4_0.json](../../tests/fixtures/reads_0_4_0.json).

<a id="integration-status-and-validation"></a>

[Documentation index](../README.md)
