# MQTT message examples

These records show app-derived commands and sanitized messages received during
the October 12, 2026 hardware tests:

- [Array-form Lite command](thermostat-command.array.json)
- [JSON-string Lite command](thermostat-command.string.json)
- [Device command acknowledgement](device-acknowledgement.json)
- [Live MCM `msg: 40` telemetry](live-telemetry.json)
- [Legacy MQTT echo reply](echo-reply.json)

The two command examples are synthetic, app-derived envelopes. The three
incoming-message examples are sanitized from live messages; device identifiers,
timestamps, and message/correlation IDs were replaced. The probe did not retain
incoming QoS or retain flags, so these fields are marked unknown.

Both tested Lites returned echo replies and produced telemetry after a live
status request through the legacy Identity Pool. An array-form setpoint command
was acknowledged and independently read back on one Lite. The second Lite did
not acknowledge the tested command variants before a power cycle, but afterward
acknowledged an array-form command; two live telemetry events and a delayed
independent HA readback matched the requested target. A subsequent live test
using `MysaMqttClient` confirmed setpoint decrease and restoration on both devices.
It also confirmed reconnect behavior: after an intentional socket disconnect,
both devices echoed again on the fresh session. Both were left at their original
targets. The independent HA readback used local communication for one device and
cloud for the other after the reboot, so it does not establish local readback
for both. A JSON-string command was ignored on the first Lite. The reason the
second device recovered after reboot remains unknown. These observations apply
to the tested devices and configuration only. See the
[MQTT protocol reference](../../docs/mqtt.md) for authorization details and
hardware evidence.

For a runnable opt-in consumer, see [`examples/mqtt_basic.py`](../mqtt_basic.py).
Install the optional MQTT extra from PyPI with
`python -m pip install 'mysa-client[mqtt]'`; see the root
[installation instructions](../../README.md#installation).
The example subscribes to account devices, prints parsed events, requests echo
and live telemetry, and sends a setpoint only when both `MYSA_DEVICE_ID` and
`MYSA_SET_CELSIUS` are explicitly set.

These records are evidence, not replay scripts or proof that a command will
work on another device. Broker acceptance, an echo, or a command acknowledgement
alone must not be treated as physical confirmation.
