# Mysa Client

> Unofficial and unaffiliated with Mysa. Use at your own risk.

`mysa-client` is an asynchronous Python library for the reverse-engineered Mysa
cloud API, thermostat HomeKit interface, and optional legacy MQTT client. It
exposes these protocols independently; standalone callers must explicitly
create an MQTT client and request live reporting. The package also provides
reusable Mysa device semantics and opt-in workflows for state reconciliation,
command validation and confirmation, routing, telemetry freshness, schedules,
holds, and account cleanup. These helpers do not start polling, connect
transports, or choose an application's persistence and lifecycle policy.

**Powers [Mysa Local for Home Assistant](https://github.com/SokolskyNikita/mysa-homeassistant).**
For Home Assistant / HACS installation, setup, and thermostat entities, use that
integration. This repository owns shared protocol communication, Mysa-specific
device semantics and workflows, opt-in runtime helpers, API documentation, and
standalone Python examples. The integration owns Home Assistant lifecycle,
entities, durable storage adapters, polling cadence, and presentation.

The API is based on behavior observed in the Mysa apps and, where noted, on
Baseboard Lite hardware. It is not a vendor API contract. The only hardware
validation recorded so far is on two Baseboard Lite `BB-V2-0-L` thermostats
running firmware `3.17.5.13`. Other model behavior is app/code-derived or
simulated and should be treated as experimental until checked on matching
hardware. See the [cloud protocol guide](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/cloud/README.md) and
[HomeKit protocol guide](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/homekit/README.md) for limits and
evidence.

## API support and goals

<!-- api-progress:start -->
[![60% of known Mysa API operations fully implemented (66/110)](https://raw.githubusercontent.com/SokolskyNikita/mysa-client/main/docs/api-progress.svg)](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md)

**60% of known Mysa API operations fully implemented (66/110).**

Cloud & account APIs: 54/88 (61.4%); Local & provisioning APIs: 12/22 (54.5%).
<!-- api-progress:end -->

The [API inventory](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md)
tracks known operations and bounded operation families. Only fully implemented
rows count; partial support earns no credit. This measures API breadth, not
hardware validation or unit-test coverage.

**Testing goal: 100% unit-test coverage of client business logic**, including
edge cases and failure paths.

## Installation

Python 3.11 or newer is required. Install the cloud client from PyPI:

```sh
python -m pip install mysa-client
```

That installs the cloud API and its `aiohttp` dependency. HomeKit support is an
optional extra:

```sh
python -m pip install 'mysa-client[homekit]'
```

Direct MQTT support is also an optional extra:

```sh
python -m pip install 'mysa-client[mqtt]'
```

MQTT remains opt-in in the library: standalone callers explicitly create the
client and choose the device IDs to subscribe to in its `devices` mapping.
That mapping is the per-device allowlist; the library does not manage user
settings. Mysa Local for Home Assistant provides global and per-thermostat
switches, both enabled by default, and passes only enabled account devices to
the client. The integration uses MQTT for read-only telemetry when cloud access
and both switches are enabled. Standalone use never opens a connection
automatically; see the [MQTT reference](docs/mqtt.md) for protocol details.

To run the examples or develop the library, clone this repository and install
from its checkout:

```sh
git clone https://github.com/SokolskyNikita/mysa-client.git
cd mysa-client
python -m pip install -e '.[homekit,mqtt]' --group test --group lint --group build
```

Import from `mysa_client.cloud`, `mysa_client.homekit`, or `mysa_client.mqtt`.
Importing the package root does not load HomeKit or MQTT's optional transport
dependencies.

## Cloud example

The [cloud example](https://github.com/SokolskyNikita/mysa-client/blob/main/examples/cloud_basic.py) signs in with SRP, saves the
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

## Device semantics and optional runtime example

The [runtime example](examples/runtime_basic.py) uses synthetic, in-memory
state to show `DeviceView`, semantic command construction, validation, and
reported-state confirmation. It makes no network requests and needs no
credentials:

```sh
python examples/runtime_basic.py
```

`mysa_client.runtime` contains optional composition helpers. Importing it does
not import HomeKit or MQTT transports, connect to a device, or start a task.
Callers explicitly construct each protocol client and pass the transports,
policy choices, callbacks, clocks, and persistence they want to use. See the
[runtime guide](docs/runtime.md) for ownership and composition details.

## MQTT example

The [MQTT example](https://github.com/SokolskyNikita/mysa-client/blob/main/examples/mqtt_basic.py)
authenticates through the existing cloud client, opens an explicitly requested
MQTT session, and prints decoded device events. It can request live status and
run an MQTT echo check without changing thermostat settings. A setpoint command
is sent only when both `MYSA_DEVICE_ID` and `MYSA_SET_CELSIUS` are explicitly
set; the result distinguishes broker acceptance from a matching device
acknowledgement. MQTT command support is experimental and has only been
validated on Baseboard Lite hardware. The Home Assistant integration uses the
client for read-only telemetry when account access, cloud use, and its global
and per-thermostat Live MQTT readings options are enabled. See the
[MQTT reference](docs/mqtt.md) for evidence and limits.

Run the read-only example after setting the same authentication variables used
by the cloud example:

```sh
python examples/mqtt_basic.py
```

To explicitly request a target, name the device and Celsius value:

```sh
MYSA_DEVICE_ID='your-device-id' MYSA_SET_CELSIUS=20.5 python examples/mqtt_basic.py
```

## HomeKit example

The [HomeKit example](https://github.com/SokolskyNikita/mysa-client/blob/main/examples/homekit_basic.py) discovers nearby accessories,
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

## Ownership and error handling

Cloud `MysaAuth` and `MysaClient` share a caller-owned `aiohttp.ClientSession`;
close it with an `async with` block. The cloud client uses a 20-second request timeout and
refreshes/retries once after a 401. It does not automatically retry an
ambiguous command after a timeout, connection loss, cancellation, 429, or
server error. `MysaRateLimitError.retry_after` carries the server's backoff hint
when available. Authentication, connection, rate-limit, not-found, and API
errors derive from `MysaError`.

`HomeKitProtocol` receives an externally managed asynchronous Zeroconf
instance. It creates missing HomeKit discovery listeners and reuses existing
shared browsers. Start and stop the protocol within that instance's lifetime, close
each loaded `HomeKitDevice`, and then close Zeroconf. Reads have a default
three-second deadline; controller discovery/pairing and accessory metadata
operations have bounded internal timeouts. Writes have no fixed caller
deadline and are not replayed automatically. HomeKit errors derive from
`HomeKitError`, including typed read and rejected-write errors. Pairing
completion awaits the caller's credential-save callback before fetching
accessory details; if saving fails, the library attempts bounded key removal.
That cleanup is best effort if the accessory is unreachable.

`MysaMqttClient` owns its Paho connection and reconnect loop inside an
`async with` block and performs bounded cleanup on exit. Its event queue is
bounded and has one async-iterator consumer. The client does not poll or issue
commands by itself: callers explicitly request live reporting, echo checks, or
Lite setpoint/mode commands. A `CommandResult` reports broker acceptance and
device confirmation separately; a matching device acknowledgement still does
not prove physical heater operation.

The client provides reusable helpers for capability validation, independent
observation reconciliation, pending-command tracking, device-state selection,
and optional command routing. The application still chooses when to poll and
read back, which source and freshness policy to use, how long to retain
commands, and how to persist workflow journals and credentials. HomeKit
`state` is a cached view, not a freshness guarantee. Cloud reported state can
lag behind a successful response. A matching report is evidence of device
state, but does not prove physical operation.

## Public API

The public cloud surface is exported from `mysa_client.cloud`: `MysaAuth`,
`Tokens`, `MysaClient`, response models, model and command enums, typed errors,
`CloudCommand`, `state_command`, `settings_command`, `reconcile`,
`PendingCommand`, `PendingCommandTracker`, and the documented command helpers.
`MysaClient.execute_command()` validates and sends one explicit semantic
command using caller-supplied device context; it does not fetch capabilities or
read state automatically. HomeKit's public semantic API is exported
from `mysa_client.homekit`: `HomeKitProtocol`, `HomeKitDevice`, `PairingSession`,
`Field`, mode/action and event models, discovery records, and typed errors.
The optional MQTT API is exported from `mysa_client.mqtt`: `MysaMqttClient`,
`MqttMessage`, `MqttTelemetry`, `DeviceCommandState`, echo/publish/command
result types, message kinds, and typed errors.

Reusable cloud-domain helpers are available from their documented modules:
`cloud.reconciliation` for per-field state selection and pending command
tracking; `cloud.commands`, `.device_commands`, and `.validation` for semantic
commands and capability checks; `cloud.device` for model/device
interpretation; and `cloud.schedule_workflow`, `.holds`, and
`.account_workflow` for opt-in Mysa workflows. `runtime.state.DeviceView`,
`runtime.routing.CommandRouter`, and `runtime.telemetry` compose caller-owned
state sources and transports. Import these modules explicitly. None starts a
connection or background task.

The Home Assistant consumer also uses supported helpers from
`mysa_client.cloud.commands`, `.const`, `.observations`, `.trace`, and
`.validation`. Those submodule helpers are documented in the [cloud API
surface](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/cloud/README.md#public-api-and-integration-helpers).
They are part of the maintained consumer API even when they are not re-exported
at `mysa_client.cloud`. Other underscored names and implementation details are
private.

See the [documentation index](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md)
for cloud and HomeKit APIs, the optional runtime, Bluetooth/SoftAP, MQTT, firmware/connectivity,
Matter research, raw API coverage, and diagnostic tools. Runnable scripts and
sanitized protocol exchanges live in [examples](https://github.com/SokolskyNikita/mysa-client/tree/main/examples).
See the [changelog](https://github.com/SokolskyNikita/mysa-client/blob/main/CHANGELOG.md) for releases.

## Development and release

The project is licensed under MIT; see [LICENSE](https://github.com/SokolskyNikita/mysa-client/blob/main/LICENSE). Protocol behavior
and test fixtures are maintained in this repository. Automated tests and
documentation checks use simulated data and do not contact Mysa or local
thermostats. Hardware validation must be performed separately with explicit
operator intent and reversible commands.

Release order, tag requirements, and PyPI trusted-publisher setup are documented
in [releasing](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/releasing.md).
