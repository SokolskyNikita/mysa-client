# Mysa MQTT

- [Connection and authorization model](#connection-and-authorization-model)
- [Wire protocol](#wire-protocol)
- [Scope and sources](#scope-and-sources)

<a id="legacy-mysa-mqtt-protocol"></a>

`mysa-client` includes an experimental, opt-in client for Mysa's legacy AWS IoT
MQTT service. Installing the optional `mqtt` extra does not connect by itself:
an application chooses the device IDs, creates `MysaMqttClient`, and requests
live reporting. The library can also send experimental Baseboard Lite commands
when a caller explicitly asks it to; Mysa Local for Home Assistant uses MQTT
for read-only telemetry only.

For Python session contracts, use the [MQTT API](../python/mqtt.md);
for dated results, use the [MQTT evidence record](../../evidence/mqtt.md).

The protocol and behavior below are reverse-engineered observations, not a
vendor contract. Hardware evidence is from two Baseboard Lite `BB-V2-0-L`
thermostats running firmware `3.17.5.13` on one Mysa account. It does not
establish behavior for other models or accounts.

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
[array command](../../../examples/mqtt/thermostat-command.array.json) and
[JSON-string command](../../../examples/mqtt/thermostat-command.string.json). The
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

<a id="validation-and-limitations"></a>
<a id="sources-and-unresolved-questions"></a>

## Scope and sources

<a id="validation-criteria-for-a-future-fix"></a>

The protocol and evidence are based on reverse-engineering the original Mysa
apps, the current library implementation, sanitized captures, and the [dated
hardware tests](../../evidence/mqtt.md). The modern-pool policy findings are not evidence about
the legacy pool. The legacy policy and its effective IAM role remain
uninspected; long-term MQTT availability, other accounts, and models other
than the tested Lites remain unvalidated. For complementary HTTPS behavior,
see the [cloud protocol guide](cloud.md). For the
actual device's network behavior, see [firmware and connectivity](firmware-and-connectivity.md).

[Documentation index](../../README.md)
