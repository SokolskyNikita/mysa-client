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

### Custom-authorizer sign-in

A second sign-in path exists; `MysaMqttClient` does not use it.
`GET /iot-token` on `mysa-backend.mysa.cloud` returns
`{token, endpoint, expiresIn: 3600, signature}`. The client connects over
WebSocket to that endpoint with the query parameters
`x-amz-customauthorizer-name`, `x-amz-customauthorizer-signature` and `token`,
with no Identity Pool credentials. Mysa's app 3.65.1 uses this path by default
(one token per connection, no refresh, up to three immediate connect retries,
no automatic reconnect) and the Identity Pool path as its fallback.

On a Baseboard Lite on 2026-11-15, a hand-built client connected through this
path, subscribed to `/v1/dev/{deviceId}/out`, got PUBACKs for its publishes
and received `msg: 40` telemetry. A `msg: 44` setpoint command published
through it was not applied, with and without `resp: 2`, while the same
command through the Identity Pool path was applied a few minutes later. This
was one test, and a fault in the hand-built publish is not ruled out, so
whether commands work on this path is not established. See the
[dated entry](../../evidence/mqtt.md#custom-authorizer-path).

<a id="legacy-wire-protocol"></a>

## Wire protocol

The original Android clients use MQTT in versions 3.26.0 and 3.54.1; the
examined 4.20.1 client uses HTTPS for its Mysa API operations. App 3.65.1 also
uses MQTT; its HTTPS requests all go to `app-prod.mysa.cloud`, except
`GET /iot-token`, which goes to `mysa-backend.mysa.cloud`. The device's own
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

Other models answer the same live-telemetry request with different messages.
The client decodes these from third-party captures
([dlenski/mysotherm](https://github.com/dlenski/mysotherm),
[bourquep/mysa2mqtt](https://github.com/bourquep/mysa2mqtt) and
[kgelinas/Mysa_HA issue 43](https://github.com/kgelinas/Mysa_HA/issues/43));
none is verified on a device here.

| Model | Message | Fields read |
| --- | --- | --- |
| Baseboard V1 | Flat `MsgType: 0`, no envelope or `body`, device in `Device` | `MainTemp`, `Humidity`, `SetPoint`, and `Current` in amps (0 while idle). No duty cycle |
| In-Floor | `msg: 17`, or `msg: 40` with the same body | `ambTemp`, `hum`, `stpt`, `flrSnsrTemp`, `trackedSnsr` (3 floor, 5 ambient), `lineVtg`, and `heatStat` (1 or 0) as the duty cycle |
| Mini-Split AC | `msg: 30` | `ambTemp`, `hum`, `stpt`, `dtyCycle` |

On an In-Floor, the body's own `dutyCycle` is not the heating state: three
units reported about 0.43 around the clock, idle or heating. The client reads
`heatStat` and ignores it. One capture shows `heatStat` and `lineVtg` beside
`body`, so both places are read.

For these models the legacy HTTP state can be far behind. One account with ten
Baseboard V1 and In-Floor units had every field about 26 hours old while MQTT
was current.

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

By default the command path supports Baseboard Lite model strings only. It
publishes MCM `msg: 44`, `body.type: 5`, with `cmd` as an array. The
application-derived examples show the observed array and JSON-string forms:
[array command](../../../examples/mqtt/thermostat-command.array.json) and
[JSON-string command](../../../examples/mqtt/thermostat-command.string.json). The
library sends the array form. On one tested Lite the JSON-string form was
ignored; this does not establish behavior for all firmware.

| Field | Meaning |
| --- | --- |
| `sp` | Celsius setpoint |
| `md` | Legacy mode: `1` off, `3` heat. The full list in Mysa's app 3.65.1 is 1 off, 2 auto, 3 heat, 4 cool, 5 fan, 6 dry |
| `ho` | `1` follow schedule, `2` hold |
| `tm` | Execution time in Unix seconds; `-1` means now |
| `br` | Brightness object |

Mysa's app 3.65.1 also defines a lock key, `lk`, but never sends it.

The message envelope's `time` is Unix seconds and `id` is a request ID. The
user-pool subject is included in `src.ref`; the account device ID is in
`dest.ref`. Device acknowledgements use MCM `msg: 44`, `resp_id` correlation,
and reported state in `body.state`. A QoS 1 PUBACK only confirms broker
acceptance. A correlated successful acknowledgement confirms the device
reported matching state, not that the heater physically operated.

### Commands for other models

A thermostat ignores a command whose `body.type` belongs to another model.
The types are those Mysa's app 3.65.1 sends, and dlenski/mysotherm and
bourquep/mysa2mqtt give the same numbers:

| Model | `body.type` |
| --- | --- |
| Baseboard V1 | 1 |
| Mini-Split AC | 2 |
| In-Floor | 3 |
| Baseboard V2 | 4 |
| Baseboard Lite | 5 |

`MysaMqttClient(experimental_commands=True)` sends setpoint, mode and hold
commands to Baseboard V1, Baseboard V2 and In-Floor with their own type, and
allows an In-Floor setpoint up to 40 °C. None of this is verified on a device
here. A Mini-Split AC stays excluded: owners report that a setpoint or fan
command sent without the current mode turns the unit off, and that a setpoint
must be a whole number
([kgelinas/Mysa_HA issue 40](https://github.com/kgelinas/Mysa_HA/issues/40)).

<a id="when-app-3-65-1-uses-msg-44"></a>

Mysa's app 3.65.1 sends `msg: 44` only to a thermostat whose installed
firmware is at or above a minimum for its model. This is what the app does;
no thermostat with older firmware was available to try it.

| Model | `msg: 44` from firmware |
| --- | --- |
| Baseboard Lite | Always, with no firmware test |
| Baseboard V1 | 3.11.0 |
| Baseboard V2 | 3.11.0 |
| In-Floor | 3.11.0 |
| Mini-Split AC | 3.12.0 |

The installed versions `1.1.3.7` and `1.1.0.72` pass the test outright, and a
device with no firmware record fails it. Below the minimum the app uses older
messages in place of `msg: 44`.

Mysa's broker refuses publishes for some accounts. It closes the connection
about 100 ms after a publish to `/in` while subscriptions keep working
([bourquep/mysa2mqtt issue 299](https://github.com/bourquep/mysa2mqtt/issues/299),
a Baseboard V1), or refuses the connection outright
([issue 323](https://github.com/bourquep/mysa2mqtt/issues/323), three
In-Floors). Keep an HTTP write path next to MQTT.

Mysa's app 3.65.1 always sends `resp: 2` and publishes at QoS 1. It builds
the envelope keys in the order `ver`, `src`, `dest`, `time`, `msg`, `id`,
`resp`, `body`. It publishes each command twice to `/v1/dev/{deviceId}/in`,
about 350 ms apart and with the same `id` and `time`: first with `cmd` as an
array, then with `cmd` as a JSON string. Its newer MQTT client also appends a
top-level `Timestamp` (Unix seconds) when it publishes; its older client sends
none. A third-party report that newer app versions send the command without
`resp: 2` and at QoS 0 (dlenski/mysotherm, 2026-11-03) does not describe app
3.65.1. The client omits `resp` and publishes at QoS 1.

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
