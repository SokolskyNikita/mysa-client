# Mysa Client

> Unofficial and unaffiliated with Mysa. Use at your own risk.

An asynchronous Python library for Mysa cloud, HomeKit, and optional legacy
MQTT access, with reusable device semantics and opt-in runtime workflows.
Applications own credentials, connections, scheduling, and persistence.

**[Documentation and task index](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md)** ·
[Connect a client](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/guides/connect.md) ·
[Examples](https://github.com/SokolskyNikita/mysa-client/blob/main/examples/README.md) · [Changelog](https://github.com/SokolskyNikita/mysa-client/blob/main/CHANGELOG.md)

Powers [Mysa Local for Home Assistant](https://github.com/SokolskyNikita/mysa-homeassistant).
Use that repository for HACS installation, entities, and integration settings.

## Installation

Python 3.11 or newer is required:

```sh
python -m pip install mysa-client
```

Optional transports:

```sh
python -m pip install 'mysa-client[homekit,mqtt]'
```

Import `mysa_client.cloud`, `mysa_client.homekit`, or `mysa_client.mqtt`.
Installing extras or importing the package does not start a connection.

## Cloud example

From a checkout, after installation, run the read-only default example:

```sh
export MYSA_EMAIL='account@example.com'
export MYSA_PASSWORD='your-password'
export MYSA_REFRESH_TOKEN_FILE="$HOME/.config/mysa-client/refresh-token"
python examples/cloud_basic.py
```

The script persists refresh tokens with owner-only permissions. Keep passwords
and tokens out of source control. Set `MYSA_SET_CELSIUS` only to intentionally
change a device target; see [connecting clients](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/guides/connect.md).
An accepted request does not establish that the thermostat applied it.

## Device semantics and optional runtime example

```sh
python examples/runtime_basic.py
```

This synthetic example needs no credentials and makes no network requests.
See [state and command confirmation](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/guides/state-and-commands.md) and
[runtime composition](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/guides/runtime.md).

## HomeKit example

See [discovery, pairing, and saved credentials](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/guides/connect.md#homekit).

## MQTT example

See [explicit MQTT sessions and live reporting](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/guides/connect.md#mqtt).
MQTT commands are experimental; consult the [evidence](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/evidence/mqtt.md).

## API support and goals

<!-- api-progress:start -->
[![60.9% of known Mysa API operations fully implemented (67/110)](https://raw.githubusercontent.com/SokolskyNikita/mysa-client/main/docs/api-progress.svg)](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md)

**60.9% of known Mysa API operations fully implemented (67/110).**

Cloud & account APIs: 55/88 (62.5%); Local & provisioning APIs: 12/22 (54.5%).
<!-- api-progress:end -->

The [API inventory](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/api-coverage.md) measures implemented operation breadth,
not hardware validation or test coverage. Hardware observations cover two
Baseboard Lites (`BB-V2-0-L`, firmware `3.17.5.13`); other-model mappings remain
app-derived or simulated unless stated. See [evidence and compatibility](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/evidence/README.md).
Testing goal: 100% unit-test coverage of client business logic, including failures.

## Public API

Use the [Python API index](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/README.md#python-api-contracts) for cloud,
HomeKit, MQTT, and runtime contracts, including supported submodule imports.

## Ownership and error handling

Transport guides own deadlines, typed errors, retries, and resource cleanup.
Start with [client lifecycle](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/guides/connect.md#lifecycle-and-errors).

## Development and release

```sh
git clone https://github.com/SokolskyNikita/mysa-client.git
cd mysa-client
python -m pip install -e '.[homekit,mqtt]' --group test --group lint --group build
```

See [documentation maintenance](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/maintenance.md) for offline checks and
[releasing](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/releasing.md) for validation and publishing. Licensed under
[MIT](https://github.com/SokolskyNikita/mysa-client/blob/main/LICENSE). Hardware validation is separate from simulated tests.
