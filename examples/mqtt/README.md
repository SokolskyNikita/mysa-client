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
[MQTT protocol reference](../../docs/reference/protocol/mqtt.md).

For a runnable opt-in consumer, see [`examples/mqtt_basic.py`](../mqtt_basic.py).
For installation, execution and explicit command opt-in, follow the
[MQTT connection guide](../../docs/guides/connect.md#mqtt).

See the [MQTT reference](../../docs/reference/protocol/mqtt.md) for hardware scope and the limits
of broker acceptance, echo replies, and command acknowledgements.
