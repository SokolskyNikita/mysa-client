# Mysa HomeKit protocol

This package implements local Mysa control through HomeKit
Accessory Protocol (HAP) over IP. It uses `aiohomekit` for controller operation,
mDNS discovery, pairing exchanges, encrypted accessory sessions, and HAP
requests. It translates Mysa's accessory database and those operations into a
semantic API; it does not implement the underlying HAP transport or cryptography.

For Mysa-specific hardware observations, unsupported characteristics, and Wi-Fi
migration limits, see the
[accessory reference](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/homekit.md).
For integration feature parity, implementation gaps, and completion criteria,
see the
[HomeKit tracker](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/implementation/homekit.md).

## How it is organized

- [`controller.py`](controller.py) wraps `aiohomekit.Controller`: discovery,
  PIN pairing, loading saved credentials, unpairing, and copying a controller
  pairing. It translates library failures into package exceptions.
- [`device.py`](device.py) parses the accessory database, maps thermostat
  characteristics, encodes commands, and handles cached state and notifications.
- [`advertisement.py`](advertisement.py) parses plain mDNS/TXT data supplied by
  the HA adapter and updates endpoint values in saved pairing data. Its stable
  pairing identity excludes changing network addresses.
- [`models.py`](models.py) and [`exceptions.py`](exceptions.py) define semantic
  records and errors. [`__init__.py`](__init__.py) exports the public API.

## Characteristics and device operations

An accessory describes its services and characteristics, each addressed by an
accessory ID and instance ID. `device.py` locates the thermostat service and
builds a private address map. Callers use `Field` values instead:

| Semantic field | HAP characteristic and interpretation |
| --- | --- |
| `CURRENT_TEMPERATURE` | Current Temperature; numeric Celsius |
| `TARGET_TEMPERATURE` | Target Temperature; numeric Celsius |
| `CURRENT_ACTION` | Current Heating/Cooling State: 0 = idle, 1 = heating, 2 = cooling |
| `TARGET_MODE` | Target Heating/Cooling State: 0 = off, 1 = heat, 2 = cool, 3 = auto |
| `DISPLAY_UNIT` | Temperature Display Units: 0 = `"C"`, 1 = `"F"` |

Display units do not change the Celsius values of temperature characteristics.
Modes and actions are exposed as `LocalMode` and `LocalAction`; unknown mode,
action, or unit encodings decode to `None`. Accessory-information characteristics
supply model, manufacturer, serial, and firmware. A Mysa-specific MAC
characteristic supplies the Wi-Fi identity used to match a cloud device.

The runnable [HomeKit example](../../../examples/homekit_basic.py) covers
discovery, PIN pairing, protected persistence, saved-pairing reuse, event
notifications, reads, optional writes, and cleanup. The caller creates and
owns the asynchronous Zeroconf instance:

```python
from mysa_client.homekit import Field, HomeKitProtocol


async def read_temperature(protocol: HomeKitProtocol, saved_pairing: dict):
    device = protocol.load_pairing(saved_pairing)
    try:
        await device.async_connect()
        values = await device.async_read([Field.CURRENT_TEMPERATURE])
        return values[Field.CURRENT_TEMPERATURE]
    finally:
        await device.async_close()
```

`async_connect()` loads the accessory database and subscribes to events. Later
calls refresh only when requested or due. Configuration/reconnect notifications
request remapping; an application can react to them and use a 15-minute metadata
backstop. A failed refresh has a 60-second retry cooldown.
Refresh replaces field addresses, limits, supported modes, cached values and
subscriptions, removing vanished fields. Capability-change events allow HA to
create newly discovered entities after an offline startup. Subscription failure
still permits polling. Missing-address reads get one refresh/retry per request,
with a 60-second cooldown between such recovery attempts.

Reads update the semantic cache but do not emit ordinary value events; an
associated metadata refresh can emit a capability/value event. A failed or missing
requested essential field raises `HomeKitCharacteristicReadError`; a missing
optional display-unit reading can be omitted. `state` is a read-only view of
the latest observed or accepted values, not a freshness guarantee.

A command such as
`await device.async_write({Field.TARGET_TEMPERATURE: 20.5})` changes the target.
Writes reject unsupported fields and invalid mode/unit encodings, inspect
per-characteristic failure statuses, and update the cache and listeners when
accepted. They do not perform an independent readback. Numeric range validation
and command policy belong to the caller. Notifications become immutable
`HomeKitEvent` objects with semantic values and failed fields; error text may
retain a HAP status code, but callers do not need to interpret wire payloads.

Capabilities expose available fields, supported modes, and advertised numeric
setpoint limits and step. `ready` means essential characteristics exist, not
that telemetry is fresh. `diagnostics()` returns model, manufacturer, firmware,
semantic values, and field metadata without pairing credentials or HAP IDs.

## Discovery and pairing persistence

HAP IP accessories advertise `_hap._tcp.local.` over mDNS. The TXT record's
`id` identifies the accessory, `md` names its model, and `sf` indicates pairing
availability. Saved pairing keys authorize later sessions without another PIN
exchange; their endpoint can change when DHCP or discovery supplies a new address.

`parse_advertisement()` requires identity/model TXT fields, normalizes them,
and validates the endpoint, rejecting invalid ports or unusable addresses.
`HomeKitProtocol.async_discover()` and `async_find()` expose discovery records;
`async_start_pairing()` starts the PIN exchange and returns a `PairingSession`.
Saved pairing data remains an opaque credential dictionary for callers to store.

Once the accessory accepts the controller key, `PairingSession.async_complete()`
invokes its required `on_pairing_saved` callback before requesting accessory
details. It awaits and shields the callback from cancellation; the caller owns
storage and durability. If persistence fails, the protocol attempts bounded
removal of the newly added key and re-raises the original error. Pair-copy uses
the same persistence rule and attempts cleanup when an add request fails
ambiguously. Pairing rollback and device-session close have time limits;
controller shutdown delegates directly to `aiohomekit`.

This coordination is best effort: an unreachable accessory may retain a key
when rollback fails. Cleanup errors are logged without replacing the original
failure. If persistence succeeds but the accessory-information read fails,
credentials are retained so setup can resume without pairing again. The caller
owns durable storage, recovery UI, and protection of the saved key material.

## Lifecycle and application policy

Create `HomeKitProtocol` with an asynchronous Zeroconf instance supplied and
owned by the caller. Start and stop the protocol before closing Zeroconf. For
each loaded device, remove event listeners and call `async_close()` when the
session ends. Subscription failure still permits caller-managed polling; this
package does not run a poll loop. Home Assistant's lifecycle integration is
described in its
[architecture guide](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/architecture.md).

Known transport/authentication errors become `HomeKitError` subclasses. Reads
have a default three-second deadline; writes have no fixed caller deadline and
are not retried by the library. Discovery lookup and pairing start have
30-second bounds, pairing completion has a 60-second bound, and accessory
metadata reads have a 30-second bound. Device close and pairing rollback have
five- and fifteen-second bounds respectively. The application decides how to
handle a timeout or another ambiguous write and how to confirm reported state.

The protocol modules import neither Home Assistant nor the cloud package.
`aiohomekit`, `cryptography`, and `zeroconf` are installed through the optional
`mysa-client[homekit]` extra; cloud-only users do not need them.

## Tests

- [`test_homekit_protocol.py`](../../../tests/homekit/test_homekit_protocol.py):
  parsing, encoding, invalid values, read failures, advertisements, and pairing
  persistence/rollback behavior using fakes.
- [`test_pairing_protocol_failures.py`](../../../tests/homekit/test_pairing_protocol_failures.py):
  lost replies and cancellation after a pairing-add request.
- [`test_homekit_refresh.py`](../../../tests/homekit/test_homekit_refresh.py):
  characteristic and subscription refresh behavior.
- [Home Assistant lifecycle tests](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/tests/test_homekit.py):
  adapter lifecycle in the consuming integration.

These tests establish controlled behavior; real-device validation is a separate
check, and compatibility varies with the accessory's exposed characteristics.
