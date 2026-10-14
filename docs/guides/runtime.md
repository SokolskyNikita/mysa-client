# Compose the runtime

Use this path when an application needs shared Mysa semantics across transports.
For a single transport, begin with [connecting a client](connect.md). The
[runtime API](../reference/python/runtime.md) owns the helper contracts and
[source signatures](../reference/python/symbols.md#runtime-and-domain-helpers)
show the constructor/callback interfaces.

## Assembly sequence

1. Create the transports and credential stores you need. Keep their ownership
   in your application; installing or constructing helpers opens no connection.
2. Acquire independent observations. Preserve each source's measurement clock
   and retain settings records separately from device reports.
3. Reconcile cloud observations, then construct a `DeviceView` with local values,
   cloud state, MQTT readings and your health/freshness callbacks. Choose the
   source order deliberately. Follow the [selection guide](state-and-commands.md).
4. Build semantic commands and validate them against current device context.
   If using `CommandRouter`, supply its transport adapters, readback callbacks,
   error classification and notification hooks. Keep the router alive for the
   device so an outstanding local write can be reconciled.
5. Run your polling and message-consumption tasks. Feed MQTT events into
   `MqttTelemetryState`; use `MqttLiveStatusRenewal` to determine request deadlines,
   then perform those requests through the connected client.
6. During shutdown, stop scheduling new work, await the router's `async_stop()`,
   and close device sessions/transports within their owning contexts. Shut down
   HomeKit before its caller-owned Zeroconf instance.

## Start without a thermostat

Run [runtime_basic.py](../../examples/runtime_basic.py) from an installed checkout:

```sh
python examples/runtime_basic.py
```

The script demonstrates a synthetic view, validated command and confirmation
from a fabricated later report. It performs no network operations. Replace its
fabricated observations with your own acquisitions only after defining their
freshness policy; receipt time is not necessarily measurement time.

Durable schedule changes need a journal adapter and separate per-device locking;
see [schedule workflows](schedules.md). The runtime does not provide a scheduler
or durable credential/journal store.

[Documentation index](../README.md)
