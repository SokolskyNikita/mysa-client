# Mysa Client

`mysa-client` is an asynchronous Python library for the reverse-engineered Mysa
cloud API and the Mysa thermostat HomeKit interface. It exposes the two
protocols independently; it does not choose between them or provide an
application polling, persistence, or device-state confirmation policy.

The API is based on behavior observed in the Mysa apps and, where noted, on
Baseboard Lite hardware. It is not a vendor API contract. The only hardware
validation recorded so far is on two Baseboard Lite `BB-V2-0-L` thermostats
running firmware `3.17.5.13`. Other model behavior is app/code-derived or
simulated and should be treated as experimental until checked on matching
hardware. See the [cloud protocol guide](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/cloud/README.md) and
[HomeKit protocol guide](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/homekit/README.md) for limits and
evidence.

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

To run the examples or develop the library, clone this repository and install
from its checkout:

```sh
git clone https://github.com/SokolskyNikita/mysa-client.git
cd mysa-client
python -m pip install -e '.[homekit]' --group test --group lint --group build
```

Import from `mysa_client.cloud` or `mysa_client.homekit`. Importing the package
root does not load either protocol's optional transport dependencies.

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

Both clients use resources supplied by the caller. Cloud `MysaAuth` and
`MysaClient` share a caller-owned `aiohttp.ClientSession`; close it with an
`async with` block. The cloud client uses a 20-second request timeout and
refreshes/retries once after a 401. It does not automatically retry an
ambiguous command after a timeout, connection loss, cancellation, 429, or
server error. `MysaRateLimitError.retry_after` carries the server's backoff hint
when available. Authentication, connection, rate-limit, not-found, and API
errors derive from `MysaError`.

`HomeKitProtocol` receives an externally managed asynchronous Zeroconf
instance. Start and stop the protocol within that instance's lifetime, close
each loaded `HomeKitDevice`, and then close Zeroconf. Reads have a default
three-second deadline; controller discovery/pairing and accessory metadata
operations have bounded internal timeouts. Writes have no fixed caller
deadline and are not replayed automatically. HomeKit errors derive from
`HomeKitError`, including typed read and rejected-write errors. Pairing
completion awaits the caller's credential-save callback before fetching
accessory details; if saving fails, the library attempts bounded key removal.
That cleanup is best effort if the accessory is unreachable.

The caller owns long-term refresh-token and pairing-data storage, polling,
freshness, retries after ambiguous outcomes, validation against advertised
capabilities, and command confirmation. HomeKit `state` is a cached view, not
a freshness guarantee. Cloud reported state can lag behind a successful
response. A fresh read after a write is useful evidence, but may still not
prove the device's physical behavior.

## Public API

The public cloud surface is exported from `mysa_client.cloud`: `MysaAuth`,
`Tokens`, `MysaClient`, response models, model and command enums, typed errors,
and the documented command helpers. HomeKit's public semantic API is exported
from `mysa_client.homekit`: `HomeKitProtocol`, `HomeKitDevice`, `PairingSession`,
`Field`, mode/action and event models, discovery records, and typed errors.

The Home Assistant consumer also uses supported helpers from
`mysa_client.cloud.commands`, `.const`, `.observations`, `.trace`, and
`.validation`. Those submodule helpers are documented in the [cloud API
surface](https://github.com/SokolskyNikita/mysa-client/blob/main/src/mysa_client/cloud/README.md#public-api-and-integration-helpers).
They are part of the maintained consumer API even when they are not re-exported
at `mysa_client.cloud`. Other underscored names and implementation details are
private.

See [examples](https://github.com/SokolskyNikita/mysa-client/tree/main/examples),
[cloud authentication](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-auth.md),
[cloud state and models](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-state.md),
[cloud workflows](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-workflows.md),
the protocol guides, and the [change log](https://github.com/SokolskyNikita/mysa-client/blob/main/CHANGELOG.md).

## Development and release

The project is licensed under MIT; see [LICENSE](https://github.com/SokolskyNikita/mysa-client/blob/main/LICENSE). Protocol behavior
and test fixtures are maintained in this repository. Automated tests and
documentation checks use simulated data and do not contact Mysa or local
thermostats. Hardware validation must be performed separately with explicit
operator intent and reversible commands.

Release order, tag requirements, and PyPI trusted-publisher setup are documented
in [releasing](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/releasing.md).
