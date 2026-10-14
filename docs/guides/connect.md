# Connect a client

Install Python 3.11+ and the required [package extras](../../README.md#installation).
Run these scripts from a checkout after installing the package. Each script
supports offline `--help`; ordinary execution contacts real services/devices.

## Cloud

The [cloud example](../../examples/cloud_basic.py) signs in with SRP, saves the
refresh token through a caller-owned callback, discovers devices, and reads
state. It sends a setpoint command only when `MYSA_SET_CELSIUS` is explicitly
set in the environment. Never put an account password or token in source
control. The example creates its token file with owner-only permissions.

```sh
export MYSA_EMAIL='account@example.com'
export MYSA_PASSWORD='your-password'
export MYSA_REFRESH_TOKEN_FILE="$HOME/.config/mysa-client/refresh-token"
python examples/cloud_basic.py
```

To request a heating target, explicitly set a safe value for your device and
run the example again:

```sh
MYSA_SET_CELSIUS=20.5 python examples/cloud_basic.py
```

The command's successful HTTP response means the API accepted the request. It
does not prove that the thermostat applied it. Read reported state separately
and use an application-specific confirmation policy.

## MQTT

The [MQTT example](../../examples/mqtt_basic.py)
authenticates through the existing cloud client, opens an explicitly requested
MQTT session, and prints decoded device events. It can request live status and
run an MQTT echo check without changing thermostat settings. A setpoint command
is sent only when both `MYSA_DEVICE_ID` and `MYSA_SET_CELSIUS` are explicitly
set; the result distinguishes broker acceptance from a matching device
acknowledgement. MQTT command support is experimental and has only been
validated on Baseboard Lite hardware. See the
[MQTT reference](../reference/protocol/mqtt.md) for evidence and limits.

Run the read-only example after setting the same authentication variables used
by the cloud example:

```sh
python examples/mqtt_basic.py
```

To explicitly request a target, name the device and Celsius value:

```sh
MYSA_DEVICE_ID='your-device-id' MYSA_SET_CELSIUS=20.5 python examples/mqtt_basic.py
```

## HomeKit

The [HomeKit example](../../examples/homekit_basic.py) discovers nearby accessories,
can complete PIN pairing and persist the returned credentials, and can load
those credentials for reads, events, and an explicitly requested target write.
It creates and closes its own asynchronous Zeroconf instance and stops the
protocol controller and device session on exit. Pairing credentials are secret
key material; keep the JSON file private and make a protected backup.

List discovered accessories:

```sh
python examples/homekit_basic.py --discover
```

Pair using the accessory ID shown by discovery and the code printed on the
thermostat's faceplate:

```sh
python examples/homekit_basic.py --pair-id 'AA:BB:CC:DD:EE:FF' --pin 123-45-678
```

Load the saved pairing, observe events, and read semantic values:

```sh
python examples/homekit_basic.py --pairing-file "$HOME/.config/mysa-client/pairing.json"
```

HomeKit write acceptance also is not physical confirmation. The example reads
back the target after a requested write, but the caller remains responsible
for freshness and application policy.


## Lifecycle and errors

| Transport | Canonical lifecycle and error contract |
| --- | --- |
| Cloud | [Caller-owned HTTP session, token persistence, deadlines and retries](../reference/python/cloud.md#errors-timeouts-retries-and-caller-responsibilities) |
| HomeKit | [Caller-owned Zeroconf, device close, pairing persistence and timeouts](../reference/python/homekit.md#lifecycle-and-application-policy) |
| MQTT | [Context manager, bounded events, reconnect and uncertain commands](../reference/python/mqtt.md) |

For stale readings or ambiguous outcomes, use [troubleshooting](troubleshooting.md).

[Documentation index](../README.md)
