# MQTT message examples

These files are protocol examples, not replay scripts:

- [Array-form Lite command](thermostat-command.array.json)
- [JSON-string Lite command](thermostat-command.string.json)
- [Device command acknowledgement](device-acknowledgement.json)
- [Live MCM `msg: 40` telemetry](live-telemetry.json)
- [Legacy MQTT echo reply](echo-reply.json)

The two command examples are synthetic, app-derived envelopes. The three
incoming-message examples are sanitized live messages; device identifiers,
timestamps, and message/correlation IDs were replaced. The probe did not retain
incoming QoS or retain flags, so those fields are marked unknown. For the
single source of hardware and authorization findings, see the
[MQTT protocol reference](../../docs/mqtt.md).

For a runnable opt-in consumer, see [`examples/mqtt_basic.py`](../mqtt_basic.py).
Install the optional MQTT extra from PyPI with
`python -m pip install 'mysa-client[mqtt]'`; see the root
[installation instructions](../../README.md#installation).
The example subscribes only to discovered account devices, prints parsed
events, requests echo and live telemetry, and sends a Lite setpoint only when
both `MYSA_DEVICE_ID` and `MYSA_SET_CELSIUS` are explicitly set.

See the [MQTT reference](../../docs/mqtt.md) for hardware scope and the limits
of broker acceptance, echo replies, and command acknowledgements.
