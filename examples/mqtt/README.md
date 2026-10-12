# MQTT message examples

These files show two app-derived encodings of a legacy Lite thermostat command:

- [Array-form command](thermostat-command.array.json)
- [JSON-string command](thermostat-command.string.json)

Identifiers and timestamps are synthetic. The samples are not captured live
exchanges: no successful publish or device acknowledgement was observed. The
tested identity could connect and subscribe, but its inspected IoT policy did
not grant Publish or Receive. See the [MQTT protocol reference](../../docs/mqtt.md)
for evidence, authorization limits, and safe validation criteria.

These are inert evidence records, not replayable commands or proof of device
control. Do not infer that a network-level acknowledgement confirms that a
thermostat changed state.
