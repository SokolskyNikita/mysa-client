# Legacy Mysa MQTT protocol

**Evidence reviewed: October 10–12, 2026.** Hardware tests used two Mysa
Baseboard Lite thermostats (`BB-V2-0-L`, firmware `3.17.5.13`) on one Mysa
account. `mysa-client` now includes an opt-in MQTT client behind the optional
`mqtt` extra; see [Using the optional client](#using-the-optional-client).
The standalone library never opens an MQTT session automatically: consumers
must install the extra and explicitly create the client and request live
reporting. Mysa Local for Home Assistant opts into this client for read-only
telemetry when Mysa sign-in and cloud access are enabled and both its global
and per-thermostat Live MQTT readings options are on (both default to on).
Freshness and fallback policy belong to the integration; MQTT commands are not
used by this HA flow. See the [HA MQTT guide](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/mqtt.md).

The initial Mysa Local 0.6.0 deployment with this client on Home Assistant
2026.10.1 received MQTT humidity and numeric duty cycle from both installed
Baseboard Lite devices. The per-thermostat option and sustained availability
remain under live validation; see the linked HA guide for the deployment and
intermittency findings.

**Direct MQTT transport works with Mysa's legacy Cognito Identity Pool on the
tested devices. Command responses have varied between runs, and the reason is
unknown.** On October 12, a paired test
used the same account and modern User Pool token with two Identity Pools. The
modern pool allowed CONNECT and SUBSCRIBE but rejected publishing an echo; the
legacy pool returned a QoS 1 PUBACK and the expected echo response. The old
app-client and legacy-pool combination also returned the echo response. With
the legacy pool, both tested Lites returned echoes and produced MCM `msg: 40`
live telemetry after a live-status request. A legacy token could not be
exchanged through the modern pool. This explains the conflicting broker
authorization observations: the October 11 denied publish used the modern
pool, while MQTT publish/echo and live telemetry worked through the legacy
pool.

Hardware command behavior changed after a power cycle on one of the two tested
Lites. The first Lite acknowledged array-form setpoint commands with and without
a seconds-based `Timestamp`; the matching response reported success and the
requested state, and an independent local read confirmed the target. The
JSON-string command form was ignored. Before a power cycle, the second Lite did
not acknowledge the tested array or string variants, and its local target stayed
unchanged. After the power cycle, an array-form request was acknowledged and
independently confirmed by subsequent MQTT telemetry and an independent HA
readback. Both devices were restored to their original targets. The reason the
second device's response changed is unknown; these results do not establish
behavior on other accounts or devices.

These results cover one Mysa account and the tested app-client/pool
combinations; they do not establish behavior for every Mysa user or Lite
thermostat. `mysa-client` provides
[HomeKit](../src/mysa_client/homekit/README.md) for local communication and the
[HTTPS cloud protocol](../src/mysa_client/cloud/README.md) for cloud operations.

## Connection and authorization model

There are three distinct communication paths:

| Path | Transport | Evidence |
| --- | --- | --- |
| Legacy app (3.26/3.54) → Mysa cloud | Direct AWS IoT MQTT over secure WebSocket, using its legacy app client and Identity Pool | Reverse-engineered from the original Mysa apps; legacy pool passed the October 12 echo test |
| Current app → Mysa cloud | HTTPS reads and writes | Reverse-engineered from the original Mysa apps; no direct MQTT client found |
| Thermostat → Mysa cloud | TLS MQTT on port 8883 | Observed device traffic; separate from the mobile client's credentials |

The clients sign into Mysa's Cognito **User Pool**, obtain an ID token, then
exchange it through a configured **Identity Pool** using `GetId` and
`GetCredentialsForIdentity`. The resulting temporary access key, secret key,
and session token sign the AWS IoT WebSocket request. The User Pool is shared,
but the legacy and modern app configurations use different app clients and
Identity Pools. The October 12 paired test shows the Identity Pool selection
changes the observed MQTT authorization result. Public app-client and pool
identifiers are recorded in the
[cloud authentication reference](cloud-auth.md#cognito-sign-in).

The resulting AWS identity belongs to **Mysa's AWS infrastructure**. It
represents the user's Mysa login; it is not an AWS account owned or administered
by the thermostat owner. Keep these identifiers separate:

| Identifier | Purpose |
| --- | --- |
| User Pool `sub` | Mysa user identity; `src.ref` in app command envelopes |
| Identity Pool `IdentityId` | Principal to which AWS IoT policies are attached |
| Device `Id` from the account API | Topic suffix and command destination |
| Fresh UUID client ID | Identifies one MQTT connection |

### AWS permission controls

For authenticated Cognito identities, permissions must be present in both the
Identity Pool's authenticated IAM role and the IoT policy attached to the
identity. A successful Cognito login supplies credentials, not unrestricted
broker access. See [AWS's Cognito authorization model](https://docs.aws.amazon.com/iot/latest/developerguide/cog-iot-policies.html).

The legacy client exercises four independent MQTT data-plane permissions:

| AWS action | Checked when | Resource needed by this client | Modern-pool policy inspected October 11 |
| --- | --- | --- | --- |
| `iot:Connect` | MQTT CONNECT | `client/<clientId>` | Allowed |
| `iot:Subscribe` | MQTT SUBSCRIBE | `topicfilter//v1/dev/<deviceId>/out` | Allowed |
| `iot:Publish` | Each outgoing PUBLISH | `topic//v1/dev/<deviceId>/in` | Absent |
| `iot:Receive` | Each incoming message delivery | `topic//v1/dev/<deviceId>/out` | Absent |

These are resource suffixes under
`arn:aws:iot:us-east-1:<Mysa-AWS-account-id>:`. The doubled slash is intentional:
the topic starts with `/`. Subscribe uses a **topic filter** resource; Publish
and Receive use **topic** resources. See AWS's
[action definitions](https://docs.aws.amazon.com/iot/latest/developerguide/iot-policy-actions.html)
and [resource mapping](https://docs.aws.amazon.com/iot/latest/developerguide/iot-action-resources.html).
This explains why a successful SUBACK can coexist with no delivered messages.

The October 11 policy inspection applies to the modern Identity Pool path only.
The October 12 comparison confirms that legacy-pool credentials can publish
and receive the echo response, but the legacy identity's policy and effective
IAM permissions were not available for inspection. Do not infer its grants
from the modern-pool policy. An attachment operation such
as [`AttachPolicy`](https://docs.aws.amazon.com/iot/latest/apireference/API_AttachPolicy.html)
associates an existing policy with an identity; it does not expand that policy.
These are controls in Mysa's AWS deployment, not settings in the thermostat
owner's personal AWS account.

## Live evidence

### Policy inspection

On October 11, the tested identity had exactly one attached policy, `default`.
`GetPolicy(default)` returned:

[Policy document projection](../examples/cloud/iot-policy.limited.json).

The wildcard covers resources for the two listed actions; it does not grant
other actions. The absence of `iot:Publish` and `iot:Receive` is therefore
material even though connection and subscription requests succeed.

### Test results

The following results are from October 11 using the modern Identity Pool;
preserve them as a dated observation for that configuration, not a statement
about legacy-pool access or current access for every session.

| Check | Observed result | What it establishes |
| --- | --- | --- |
| MQTT 3.1.1 connection | CONNACK `20 02 00 00` | Connection accepted with fresh credentials and a UUID client ID. |
| QoS 1 subscriptions to both devices' `/out` topics | SUBACK granted QoS 1 | Subscriptions accepted, independently reproduced with Paho. |
| Publish to a device's `/in` topic | WebSocket disconnected; AWS IoT HTTPS `Publish` returned 403 `ForbiddenException` | Command publication was forbidden with this modern-pool identity before a device response could be assessed. |
| Passive subscriptions during 20 seconds of HTTPS live reporting | HTTPS readings advanced; no MQTT messages in a 25-second window | Consistent with the missing Receive permission. |

AWS IoT HTTPS `Publish` and Mysa's HTTPS state API are separate paths; the
latter remains usable. See [live-mode and liveness](cloud-state.md#live-mode-and-liveness).
An earlier connection failed before CONNACK; its cause remains unresolved and
does not change the later authorization findings.

### October 12: pool comparison and device control

The paired broker test used the same account. For the modern app client, the
same User Pool token was exchanged through the modern and legacy Identity
Pools. Additional controls used the legacy app-client/pool pair and tried its
token with the modern pool:

| User Pool token and Identity Pool | Observed result |
| --- | --- |
| Modern app client + modern Identity Pool | `GetId`/credential exchange succeeded. The identity's `default` policy was version 2 and allowed CONNECT/SUBSCRIBE only. MQTT CONNECT and SUBSCRIBE succeeded; echo PUBLISH disconnected before PUBACK, and no echo response arrived. |
| Same modern token + legacy Identity Pool | MQTT CONNECT and SUBSCRIBE succeeded; echo PUBLISH received QoS 1 PUBACK and the expected `MsgType: 5` echo response. |
| Legacy app client + legacy Identity Pool | Echo received PUBACK and the expected response. |
| Legacy app-client token + modern Identity Pool | `GetId` failed with `NotAuthorizedException`. |

In a follow-up using the legacy Identity Pool, both tested devices returned
echoes and produced MCM `msg: 40` telemetry after live status was requested.
Inspecting the legacy identity's attached policy returned
`AccessDeniedException`. These results establish publish/receive for the tested
topics, but do not establish the legacy policy statements or prove that every
device topic and command is permitted. Do not include per-user Cognito Identity
IDs or account/device identifiers in reports.

The separate hardware-control test used the source of
[`mysa2mqtt` PR #306 at `f872d79`](https://github.com/bourquep/mysa2mqtt/pull/306/commits/f872d790d296061c1dd9dcb08804e875ec24a98d).
Its active Identity Pool was not recorded. The SDK tries AWS IoT MQTT first
and falls back to Mysa's REST API only when publishing fails. During this run,
MQTT publishes returned successfully and the normal command path did not
invoke REST fallback.

- On one Lite, a 0.5 °C setpoint decrease, off, heat, and restoration all worked.
  Each change was independently observed through local HomeKit in about two
  seconds. This establishes actual MQTT control in that session, beyond broker
  acceptance or an optimistic UI update.
- On the other Lite, the setpoint publish returned successfully, but its local
  target remained unchanged during an approximately 57-second observation.
  The MQTT climate entity initially displayed the requested target, then
  reverted on a subsequent state update. Local HomeKit control still worked.
- In a separate test, simulated MQTT rejection exercised the same SDK's fallback
  with real REST requests. Setpoint/off/heat worked on the first Lite; the second
  still did not apply the REST setpoint within approximately 45 seconds. This
  validates that fallback path under an injected failure, not automatic fallback
  after a real broker rejection. Both devices ended at their original settings.

The REST request matched `mysa-client`'s heating-state encoding. Our integration's
successful local control uses HomeKit, so it is not evidence of a different or
more reliable REST payload. Transport acceptance alone remains insufficient to
confirm that a thermostat applied a command.

The pool comparison explains the earlier broker authorization difference, but
does not explain the device-specific command results. A broker PUBACK and echo
response confirm transport-level publish/receive for the tested echo topic;
they do not confirm thermostat command application.

In the initial variant tests, array-form `cmd` setpoint commands with and
without seconds-based `Timestamp` were confirmed on one Lite by a correlated
MCM `msg: 44` response with `success: 1`, `src.type: 1`, and a matching
`resp_id`, followed by independent local readback of the requested target. The
JSON-string `cmd` form was ignored on that device. Before a power cycle, the
other Lite did not acknowledge the tested array or string forms, including a
timestamped `resp: 2` request, and its local target did not change. Omitting
`resp` and adding a mode field produced the same result.

After that Lite was power-cycled, an array-form request for 21.5 °C was confirmed
by a matching MCM `msg: 44` acknowledgement (`success: 1`, `src.type: 1`, and
matching `resp_id`), two subsequent MCM `msg: 40` telemetry events reporting
21.5 °C, and an independent HA readback after six seconds. Restoring 22 °C was
confirmed by the same acknowledgement, live telemetry, and HA readback.
The MQTT timestamps were already within one second of local time before the
power cycle and remained current after it, so those observations do not support
clock skew as the cause. The cloud `latestTelemetry` timestamp for this device
still reported 2031 after the reboot. The power cycle coincided with recovery,
but the cause remains unknown. Both tested Lites have now acknowledged and
independently reflected an array-form setpoint command in the observed runs;
this is limited evidence and does not establish reliability across sessions,
accounts, or models.

## Legacy wire protocol

The following wire protocol was reverse-engineered from the original Mysa apps.
The October 12 follow-up confirmed MQTT echo transport through the legacy pool
and array-form setpoint control on both tested Lites in observed runs. One Lite
required a power cycle before it acknowledged the request; the cause and
reliability across sessions remain unresolved.

### Session and topics

| Property | App behavior |
| --- | --- |
| Endpoint | `wss://a3q27gia9qg3zy-ats.iot.us-east-1.amazonaws.com/mqtt` |
| Authentication | SigV4, service `iotdevicegateway`, region `us-east-1`; temporary Identity Pool credentials |
| MQTT session | MQTT 3.1.1, fresh UUID client ID, 60-second keepalive |
| Commands | Publish `/v1/dev/{deviceId}/in`, normally QoS 1, not retained |
| Readings | Subscribe `/v1/dev/{deviceId}/out`, QoS 1; no `/batch` subscription in the app |
| Device identifier | Account API `Id`, used verbatim |
| Publish pacing | One queued message per 350 ms |

IoT policy attachment uses the AWS IoT **control plane**, signed for service
`iot`, rather than the `iotdevicegateway` service used by the WebSocket URL.

### Lite commands and responses

The Lite always uses MCM `msg: 44` with `body.type: 5`. Other models take this
path from firmware 3.11.0 for V1, V2 and In-floor, or 3.12.0 for AC. A Lite
setpoint command has this shape; identifiers and times are illustrative:

[Array command](../examples/mqtt/thermostat-command.array.json) and
[JSON-string command](../examples/mqtt/thermostat-command.string.json).

`time` is Unix seconds; `id` is milliseconds. The app sends the command twice:
first with `cmd` as an array, then with that array encoded as a JSON string.
Its newer MQTT client adds a seconds-based `Timestamp`; the generator does
not set `resp`. Other device types are 1 for baseboard V1, 2 for AC, 3 for
In-floor, and 4 for baseboard V2.

| Command field | Meaning |
| --- | --- |
| `sp` | Setpoint in Celsius |
| `md` | Legacy mode: 1 off, 3 heat; distinct from the HTTPS mode enum |
| `ho` | 1 follow schedule, 2 hold |
| `tm` | Execution time in Unix seconds; `-1` means now |
| `br` | Brightness object |

A timed hold contains a hold-now entry and a future follow-schedule entry.
The app does not add `lk`, `stpt`, or `a_sp` to its Lite commands. The audited
Mysa_HA implementation differs by adding `resp: 2`, `stpt`/`a_sp`, and sending
one command representation. Those are compatibility questions, not established
causes of the observed authorization failure.

Device acknowledgements correlate the request through `resp_id` and carry
state in `body.state`, including `md`, `sp`, `ho`, `tr`, `fn`, and `ss`. The
app parser does not inspect `success`, `trig_src`, `br`, or `lk`. Live Lite
telemetry uses MCM `msg: 40`, with `body.ambTemp`, `hum`, `stpt`, and `dtyCycle`.

### Liveness and notifications

| Purpose | Legacy message fields in addition to `Device` and seconds-based `Timestamp` |
| --- | --- |
| Echo | `MsgType: 6`, `EventType: 4294967295`, `EchoID: 418`; expected reply is `MsgType: 5` with the same echo ID |
| Request live reporting | `MsgType: 11`, `Timeout: 180`; app renews every 150 seconds while foregrounded |
| Settings changed | `MsgType: 6`, `EventType: 0`, after the REST settings write |
| Schedule changed | `MsgType: 6`, `EventType: 1`, after the REST schedule write |

Legacy `MsgType` and MCM `msg` are different namespaces. In particular,
legacy `MsgType: 7` is named REBOOT in the app enum, and legacy `MsgType: 40`
is not MCM `msg: 40` telemetry. Neither is a suitable liveness probe. The
legacy source enum defines 0 as device manual and 1 as device schedule.

## Using the optional client

`mysa_client.mqtt.MysaMqttClient` is an opt-in consumer API. Install its
optional extra with `python -m pip install 'mysa-client[mqtt]'`. Pass
`auth.async_get_id_token` from `MysaAuth` and a mapping of account-owned device
IDs to their model strings. That mapping is the device allowlist: the client
does not have a separate per-device enable flag. The client uses Mysa's
legacy Identity Pool by default, waits for CONNECT and all requested
subscriptions, and closes/reconnects within bounded timeouts. It only
subscribes to the IDs supplied by the caller.

`connection_generation` increases after each fully subscribed connection,
including reconnects. Consumers can watch it to request live reporting again
after a new session. Client construction and TLS setup run off the event loop.

The client sends no thermostat command unless the caller explicitly invokes
`set_setpoint` or `set_mode`. `request_live_status` asks for a telemetry stream
that lasts up to 180 seconds; the client does not renew it automatically, so a
consumer that wants continued updates should request it again before it
expires. `echo` sends a non-mutating liveness probe and waits for its matching
reply. The bounded `events()` iterator yields parsed `MqttMessage`
objects with `kind`, `telemetry`, `state`, correlation fields, and the original
payload for advanced use. `MqttMessage.payload` and `raw_payload` are hidden
from `repr` to reduce accidental disclosure.

Commands currently support Baseboard Lite model strings only. A
`CommandResult` separates `broker_accepted` (QoS 1 PUBACK) from
`device_confirmed`. Confirmation requires a successful, correlated MCM `msg: 44`
response from the device with state matching the request; it is still not
proof of physical heater operation. The client emits the app-shaped array form
of `cmd`. In the October 12 live runtime test, both tested Lites confirmed a
0.5 °C setpoint decrease and restoration with correlated device acknowledgements.
Both also returned echo replies and live telemetry. After an intentional socket
disconnect, the client reconnected with a fresh session and both devices echoed
again. The devices were left at their original setpoints. An independent HA
readback matched the commanded state; after reboot, HA's connection path had
switched between local and cloud for the two devices, so this does not establish
a local HomeKit readback on both. These observations cover two Lites on one
account, not other accounts or models. Home Assistant adds its own 30-second
receipt-time freshness and per-field HTTPS fallback around these events; those
are integration policies, not client defaults. See the runnable
[MQTT example](../examples/mqtt_basic.py) and the
[sanitized acknowledgement](../examples/mqtt/device-acknowledgement.json),
[telemetry](../examples/mqtt/live-telemetry.json), and
[echo reply](../examples/mqtt/echo-reply.json).

## Reproduction

[`tools/mqtt_probe.py`](../tools/mqtt_probe.py) is a maintainer diagnostic.
It reuses `mysa_client.cloud.MysaAuth` and `MysaClient`, obtains Identity Pool
credentials, inspects attached policies when permitted, and checks MQTT CONNECT
and per-device SUBACK through Paho. It discovers topic targets from the
authenticated account. By default it is passive; `--echo` publishes a
non-mutating echo to each device and requires matching PUBACK and echo replies.
The default pool is legacy; use `--identity-pool modern` to compare the modern
pool.

From the repository root, use a separate environment and install the standalone
client with the diagnostic-only dependencies:

```sh
python -m pip install mysa-client boto3 'paho-mqtt>=2'
python tools/mqtt_probe.py --credentials /path/to/credentials.json --duration 20
python tools/mqtt_probe.py --credentials /path/to/credentials.json --echo
python tools/mqtt_probe.py --credentials /path/to/credentials.json --identity-pool modern --echo
```

The private JSON accepts `refresh_token` and/or `email` plus `password`.
Omit `--credentials` to supply it through stdin. Keep credentials out of Git
and command-line arguments. The tool reports selected policy and connection
fields without tokens, signed URLs, or message payloads.

| Result | Interpretation |
| --- | --- |
| Exit 0, `authorization: "echo_verified"` | With `--echo`, every account device returned PUBACK and its matching echo. This validates echo transport, not thermostat control. |
| Exit 1 | The diagnostic could not complete the broker connection and all expected subscriptions. |
| Exit 2, `authorization: "blocked"` | CONNECT/SUBACK completed, no echo was verified, and policy inspection matched the modern pool's limited policy. |
| Exit 2, `authorization: "inconclusive"` | CONNECT/SUBACK completed, but echo was not verified and policy inspection did not establish the limited policy. |

The tool is not an IAM evaluator: other policies, different document shapes,
or denied inspection (including `AccessDeniedException`) are not treated as
proof of access. It never attaches a policy or sends thermostat commands. It
does not request live reporting; use the optional MQTT client for telemetry.

## Validation and limitations

Further investigation should capture authorization and session evidence during
successful and failing command runs: record which app-client and Identity Pool
configuration was used, compare available policy and IAM evidence, client
ID/session settings, and command encoding. Separately record broker acceptance,
correlated device responses, and fresh device-reported state. Keep credentials,
per-user Cognito Identity IDs, and account/device identifiers out of public
reports.

A meaningful validation should first confirm delivery of
the safe echo and fresh telemetry, then test a controlled state change and
read it back from the device. CONNACK, SUBACK, or a broker-level publish
acknowledgement alone must not be reported as successful thermostat control.

For complementary HTTPS endpoints and model-specific findings, see the
[cloud API supplementary reference](../src/mysa_client/cloud/README.md).

## Sources and unresolved questions

The original Android apps use MQTT in 3.26.0/3.54.1 and HTTPS in 4.20.1. The older 3.26.0 Lite MSG44 path requires firmware 3.13.0;
the later legacy app removes that gate. Both test devices exceeded it.

The October 11 modern-pool policy was present as `default`, version 2, and
allowed CONNECT/SUBSCRIBE but not Publish/Receive. The October 12 paired test
found that legacy-pool credentials can complete echo and live-telemetry
exchanges. Inspection of the legacy identity's policy returned
`AccessDeniedException`, so its policy statements and effective IAM permissions
remain unknown. No extra per-device attachment or identity registration step
was found in the inspected sign-in path. The pool difference explains the
conflicting broker authorization results, but not the device-specific command
results. Community reports also describe different failure stages and do not
establish one universal cutoff. Do not generalize these observations to all
accounts or devices. `mysa-client` provides an opt-in experimental client;
Mysa Local uses it for account-backed read-only telemetry when cloud access and
its Live MQTT readings option are enabled, while standalone consumers must
explicitly construct it and request live reporting. MQTT
behavior outside the tested pool and Lite-device cases remains unverified.

The later port-8883 credential attempt failed during TLS; see
[device connectivity](firmware-and-connectivity.md#time-transport-and-validation-limits).
There is no demonstrated thermostat-authenticated client here.
