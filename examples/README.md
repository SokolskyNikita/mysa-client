# Examples

The Python scripts are runnable consumer examples. They create network
connections only when executed without `--help`; automated documentation checks
parse source and JSON but never run them or contact a Mysa account/accessory.

- [Cloud login, discovery, read, and optional heating target](cloud_basic.py)
- [HomeKit discovery, pairing, saved-pairing read, events, and optional write](homekit_basic.py)
- [Sanitized cloud request/response data](cloud/README.md)
- [Sanitized MQTT message formats](mqtt/README.md)

`MYSA_SET_CELSIUS` and `--set-target-c` explicitly enable device-changing
commands. Keep them unset during routine reads and never use them as an
automated smoke test. Commands can affect thermostat operation even when the
API response only confirms that the request was accepted.

## Inert cloud exchanges

Files in [cloud/](cloud/) are evidence records, not replay scripts. They separate
request metadata from response data and mark whether a body is complete, a
projection, a fixture, or unknown. Constructed examples illustrate shapes and
must not be counted as additional validation. App-derived examples reflect
reverse engineering of the original Mysa app. No credentials, pairing keys,
signed URLs, real household IDs, or Wi-Fi passwords belong here. The fixture
payloads are synthetic or sanitized and do not imply broader hardware support.

Public Cognito pool and app-client identifiers are configuration values, not
secrets. Any token shown in an example is a nonfunctional placeholder.

Protocol diagnostic tools and their dependencies are documented in the
[MQTT reference](../docs/mqtt.md#reproduction) and the
[HomeKit reference](../docs/homekit.md). They are maintainer tools, not
additional supported library transports.
