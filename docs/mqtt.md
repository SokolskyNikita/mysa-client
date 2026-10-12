# Mysa MQTT

<a id="legacy-mysa-mqtt-protocol"></a>

`mysa-client` includes an experimental, opt-in client for Mysa's legacy AWS IoT
MQTT service. Installing the optional `mqtt` extra does not connect by itself:
an application chooses the device IDs, creates `MysaMqttClient`, and requests
live reporting. The library can also send experimental Baseboard Lite commands
when a caller explicitly asks it to; Mysa Local for Home Assistant uses MQTT
for read-only telemetry only.

The protocol and behavior below are reverse-engineered observations, not a
vendor contract. Hardware evidence is from two Baseboard Lite `BB-V2-0-L`
thermostats running firmware `3.17.5.13` on one Mysa account. It does not
establish behavior for other models or accounts.

## What was validated

The legacy Cognito Identity Pool worked for MQTT connect, subscriptions,
publish/receive, echo replies, and live telemetry on the tested account. The
modern Identity Pool also allowed connect and subscribe, but its inspected IoT
policy did not grant publish or receive. A successful connection or
subscription therefore does not prove that messages can be sent or received.
The legacy identity's policy could not be inspected, so its effective grants
are unknown. See [authorization and connection](#connection-and-authorization-model)
for the dated comparison.

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

## Connection and authorization model

The clients sign in to Mysa's Cognito User Pool, then exchange the ID token
through an Identity Pool for short-lived AWS credentials. Those credentials
sign a secure WebSocket connection to AWS IoT. `MysaMqttClient` uses Mysa's
legacy Identity Pool by default; advanced callers can provide different pool
configuration. The tested modern User Pool token also worked through the legacy
pool. An old app-client token worked through the legacy pool but was rejected
by the modern pool. The public app-client and pool identifiers are listed in
the [cloud authentication reference](cloud-auth.md#cognito-sign-in).

This is Mysa's AWS infrastructure and Mysa account authentication. The
thermostat owner's own AWS account is not involved. The MQTT path does not use
HomeKit pairing or its eight-digit setup code, but it does require valid Mysa
account authentication and whatever AWS permissions Mysa has granted that
identity.

<a id="aws-permission-controls"></a>

AWS evaluates separate data-plane permissions for each operation:

| Action | Operation | Resource suffix used by this client |
| --- | --- | --- |
| `iot:Connect` | MQTT CONNECT | `client/<clientId>` |
| `iot:Subscribe` | MQTT SUBSCRIBE | `topicfilter//v1/dev/<deviceId>/out` |
| `iot:Publish` | Outgoing message | `topic//v1/dev/<deviceId>/in` |
| `iot:Receive` | Incoming delivery | `topic//v1/dev/<deviceId>/out` |

The leading slash in each topic makes the doubled slash in the resource suffix
intentional. AWS also requires the authenticated Cognito role and IoT policy to
allow the operation. See AWS's [Cognito authorization guide](https://docs.aws.amazon.com/iot/latest/developerguide/cog-iot-policies.html),
[action definitions](https://docs.aws.amazon.com/iot/latest/developerguide/iot-policy-actions.html),
and [resource mapping](https://docs.aws.amazon.com/iot/latest/developerguide/iot-action-resources.html).

<a id="live-evidence"></a>

<a id="test-results"></a>

### Dated authorization evidence

<a id="policy-inspection"></a>

On October 11, 2026, the tested modern-pool identity had one attached `default`
policy. The inspected policy allowed CONNECT and SUBSCRIBE, but not Publish or
Receive; its sanitized projection is in
[`iot-policy.limited.json`](../examples/cloud/iot-policy.limited.json). A
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

<a id="legacy-wire-protocol"></a>

## Wire protocol

The original Android clients use MQTT in versions 3.26.0 and 3.54.1; the
examined 4.20.1 client uses HTTPS for its Mysa API operations. The device's own
TLS MQTT connection on port 8883 is a separate path with separate
authentication. No thermostat-authenticated MQTT client has been established.

<a id="session-and-topics"></a>

| Property | Observed legacy app behavior |
| --- | --- |
| Endpoint | `wss://a3q27gia9qg3zy-ats.iot.us-east-1.amazonaws.com/mqtt` |
| Authentication | SigV4 WebSocket request using temporary Identity Pool credentials; service `iotdevicegateway`, region `us-east-1` |
| MQTT | MQTT 3.1.1, fresh UUID client ID, 60-second keepalive |
| Device topic | `/v1/dev/{deviceId}/in` for publishes; `/v1/dev/{deviceId}/out` for subscriptions |
| QoS | Normally QoS 1; app publishes are not retained |
| Device ID | Account API `Id`, used verbatim |
| Publish pacing | The observed app queued publishes about 350 ms apart; the Python client does not impose that delay |

AWS IoT policy management uses the separate control-plane service name `iot`.
The standalone client reconnects after transport loss, calls its token provider
again, obtains a new temporary AWS credential set, and resubscribes. It exposes
`connection_generation` so consumers can detect a fully subscribed new session.
It does not automatically restart a live-reporting request or replay a command
after reconnecting.

<a id="liveness-and-notifications"></a>

### Telemetry and liveness messages

MCM `msg: 40` carries device telemetry. For tested Lite messages, the client
normalizes room temperature, humidity, setpoint, and numeric duty cycle from
`body` (or `body.reading`). Missing values remain `None`. The parser records
local receipt time; it does not treat a device timestamp as a freshness
guarantee.

The legacy event envelope uses `MsgType`, a different namespace from MCM
`msg`:

| Purpose | Request | Expected response |
| --- | --- | --- |
| Echo/liveness | `MsgType: 6`, `EventType: 4294967295`, unique `EchoID` | `MsgType: 5` with the same `EchoID` |
| Request live telemetry | `MsgType: 11`, `Timeout` from 1 to 180 seconds | Device publishes telemetry during the requested interval when available |

The app requests 180 seconds and renews around every 150 seconds while
foregrounded. The library exposes `request_live_status()` but leaves renewal to
the caller. Legacy `MsgType: 7` is named REBOOT in the app enum; it is not an
echo or telemetry check. The legacy source enum uses 0 for device manual and 1
for device schedule; these values are distinct from the MCM `src.type` field.
`MsgType: 40` must not be confused with MCM `msg: 40`.

<a id="lite-commands-and-responses"></a>

### Lite commands

The experimental command path currently supports Baseboard Lite model strings
only. It publishes MCM `msg: 44`, `body.type: 5`, with `cmd` as an array. The
application-derived examples show the observed array and JSON-string forms:
[array command](../examples/mqtt/thermostat-command.array.json) and
[JSON-string command](../examples/mqtt/thermostat-command.string.json). The
library sends the array form. On one tested Lite the JSON-string form was
ignored; this does not establish behavior for all firmware.

| Field | Meaning |
| --- | --- |
| `sp` | Celsius setpoint |
| `md` | Legacy mode: `1` off, `3` heat |
| `ho` | `1` follow schedule, `2` hold |
| `tm` | Execution time in Unix seconds; `-1` means now |
| `br` | Brightness object |

The message envelope's `time` is Unix seconds and `id` is a request ID. The
user-pool subject is included in `src.ref`; the account device ID is in
`dest.ref`. Device acknowledgements use MCM `msg: 44`, `resp_id` correlation,
and reported state in `body.state`. A QoS 1 PUBACK only confirms broker
acceptance. A correlated successful acknowledgement confirms the device
reported matching state, not that the heater physically operated.

## Using the optional client

Install the extra with:

```sh
python -m pip install 'mysa-client[mqtt]'
```

Create `MysaMqttClient` with a token provider such as
`auth.async_get_id_token` and a mapping of account device IDs to model names.
The mapping is an explicit allowlist: omitting a device keeps it out of MQTT
subscriptions. The client copies this mapping at construction; create a new
client if the selected devices change. Open the client with `async with`, call
`request_live_status(device_id)`, and consume its single bounded
`events()` iterator. Live reporting lasts at most 180 seconds per request, so
the caller should renew it before it expires. The default request is 180
seconds; valid requests range from 1 to 180 seconds. Re-check
`connection_generation` and request live status again after a reconnect.
`dropped_events` reports events lost if the bounded queue fills. Paho client
construction and TLS setup run off the asyncio event loop.

The client also provides a non-mutating `echo()` check. `set_setpoint()` and
`set_mode()` send real thermostat commands and should be used only when a
consumer intentionally wants to change a supported Lite. `CommandResult`
reports `broker_accepted` and `device_confirmed` separately: the first reflects
the QoS 1 PUBACK, while the second requires a matching successful device
acknowledgement. A timeout can leave the outcome uncertain, and the client does
not replay the command. The
[runnable example](../examples/mqtt_basic.py) shows authentication, events,
echo, live reporting, and an explicitly gated setpoint command. The
[sanitized message examples](../examples/mqtt/README.md) show fixture
provenance and payloads.

<a id="reproduction"></a>

## Maintainer probe

[`tools/mqtt_probe.py`](../tools/mqtt_probe.py) is an optional, non-mutating
authorization diagnostic. It discovers account devices, checks MQTT CONNECT
and SUBACK, and can send the non-mutating echo request with `--echo`. It does
not request live telemetry or thermostat commands. Install its diagnostic
dependencies and run it from the repository root:

```sh
python -m pip install mysa-client boto3 'paho-mqtt>=2'
python tools/mqtt_probe.py --credentials /path/to/credentials.json --duration 20
python tools/mqtt_probe.py --credentials /path/to/credentials.json --echo
python tools/mqtt_probe.py --credentials /path/to/credentials.json --identity-pool modern --echo
```

The credentials file accepts a refresh token or email/password; keep it out of
Git and do not put credentials in command-line arguments. The tool suppresses
tokens, signed URLs, device IDs, and raw payloads. A verified echo establishes
publish/receive for that echo exchange, not successful thermostat control.
Policy inspection may be denied, and the diagnostic is not a general AWS IAM
evaluator. Without `--echo`, the tool cannot verify delivery and returns 2
after CONNECT/SUBACK; policy inspection determines whether the result is
`blocked` or `inconclusive`.

| Exit status | Interpretation |
| --- | --- |
| `0`, `authorization: "echo_verified"` | With `--echo`, each selected device returned PUBACK and its matching echo. This verifies the echo exchange, not thermostat control. |
| `1` | Setup or connection/subscription checks did not complete. |
| `2`, `authorization: "blocked"` | CONNECT/SUBACK completed, no echo was verified, and policy inspection matched the previously observed limited modern-pool policy. |
| `2`, `authorization: "inconclusive"` | CONNECT/SUBACK completed, but no echo was verified and policy inspection did not establish that limited policy. |

<a id="validation-and-limitations"></a>
<a id="sources-and-unresolved-questions"></a>

## Scope and sources

<a id="validation-criteria-for-a-future-fix"></a>

The protocol and evidence are based on reverse-engineering the original Mysa
apps, the current library implementation, sanitized captures, and the dated
hardware tests above. The modern-pool policy findings are not evidence about
the legacy pool. The legacy policy and its effective IAM role remain
uninspected; long-term MQTT availability, other accounts, and models other
than the tested Lites remain unvalidated. For complementary HTTPS behavior,
see the [cloud protocol guide](../src/mysa_client/cloud/README.md). For the
actual device's network behavior, see [firmware and connectivity](firmware-and-connectivity.md).
