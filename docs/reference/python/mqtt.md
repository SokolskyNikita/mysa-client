# MQTT Python API

- [Using the optional client](#using-the-optional-client)
- [Reconnection](#reconnection)

For topic formats, message enums and authentication, see the [wire reference](../protocol/mqtt.md).
For hardware scope, see [MQTT evidence](../../evidence/mqtt.md).

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
[runnable example](../../../examples/mqtt_basic.py) shows authentication, events,
echo, live reporting, and an explicitly gated setpoint command. The
[sanitized message examples](../../../examples/mqtt/README.md) show fixture
provenance and payloads.

## Reconnection

The standalone client reconnects after transport loss, calls its token provider
again, obtains a new temporary AWS credential set, and resubscribes. It exposes
`connection_generation` so consumers can detect a fully subscribed new session.
It does not automatically restart a live-reporting request or replay a command
after reconnecting.

[Documentation index](../../README.md)

<!-- api-symbol: mysa_client.mqtt.client:MysaMqttClient.request_live_status -->
<!-- api-symbol: mysa_client.mqtt.client:MysaMqttClient.set_setpoint -->
