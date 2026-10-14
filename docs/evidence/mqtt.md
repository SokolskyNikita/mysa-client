# MQTT validation

Observed October 11–12, 2026 on two Baseboard Lite `BB-V2-0-L` devices
running firmware `3.17.5.13`, on one account.

## What was validated

The legacy Cognito Identity Pool worked for MQTT connect, subscriptions,
publish/receive, echo replies, and live telemetry on the tested account. The
modern Identity Pool also allowed connect and subscribe, but its inspected IoT
policy did not grant publish or receive. A successful connection or
subscription therefore does not prove that messages can be sent or received.
The legacy identity's policy could not be inspected, so its effective grants
are unknown. See the [dated comparison](#dated-authorization-evidence) and the
[authorization model](../reference/protocol/mqtt.md#connection-and-authorization-model).

The Home Assistant integration's real-installation checks, including the
per-device option, are documented in its
[MQTT guide](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/mqtt.md).
That integration uses the library for read-only telemetry; it owns the polling,
freshness, and fallback policy.

The standalone client also received echo replies and live telemetry from both
devices. Its experimental array-form setpoint command was acknowledged by both
in observed runs, with matching reported state and independent HA readback.
One device had failed to acknowledge earlier command variants before a power
cycle. During those tests its MCM `msg: 40` timestamp was within a second of
local time before and after the reboot, while a cloud snapshot continued to
report `latestTelemetry` in 2031. The command's `time` field is the sender's
current Unix time, not a clock-synchronization exchange; no device-side
timestamp rejection rule was established. The reboot coincided with command
acknowledgements returning, but the cause is unknown. A broker PUBACK, echo, or
device acknowledgement alone does not prove physical heater operation.

<a id="live-evidence"></a>

<a id="test-results"></a>

### Dated authorization evidence

<a id="policy-inspection"></a>

On October 11, 2026, the tested modern-pool identity had one attached `default`
policy. The inspected policy allowed CONNECT and SUBSCRIBE, but not Publish or
Receive; its sanitized projection is in
[`iot-policy.limited.json`](../../examples/cloud/iot-policy.limited.json). A
separate probe connected and subscribed successfully, then saw the WebSocket
disconnect on publish; AWS IoT's HTTPS Publish returned 403. During passive
subscriptions no MQTT messages arrived while HTTPS live readings advanced.
Those results apply to that modern-pool identity only.

<a id="october-12-pool-comparison-and-device-control"></a>

On October 12, the same modern User Pool token was exchanged through both
Identity Pools. The modern pool again connected and subscribed but failed the
echo publish. The legacy pool received a QoS 1 PUBACK and matching echo reply;
the legacy app-client/pool pair did too. Trying the legacy app-client token
with the modern pool failed at `GetId`. Both tested Lites then returned legacy
echo replies and MCM `msg: 40` telemetry after live-status requests. AWS denied
inspection of the legacy identity's attached policy, so the policy statements
and effective role permissions remain unknown. These observations explain the
different results seen with the two pools, but they do not establish
permissions for every account or topic.

[Documentation index](../README.md)
