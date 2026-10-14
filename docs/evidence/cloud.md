# Cloud validation

## Client implementation and validation

The client exposes the subset recorded in the [API inventory](../api-coverage.md), with
model-specific mappings that remain app/code-derived unless hardware evidence is
identified. Automated protocol tests use simulated responses and do not establish
device compatibility. Live validation currently covers two Baseboard Lite
`BB-V2-0-L` devices on firmware `3.17.5.13`; their cloud setpoint and mode
application differed. Other model profiles, full schedule execution, notification
delivery, and installation of a new firmware release remain unverified. The
[Home Assistant cloud tracker](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/implementation/cloud.md)
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

<a id="integration-status-and-validation"></a>

[Documentation index](../README.md)
