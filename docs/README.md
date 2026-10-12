# Mysa client and protocol documentation

> **Unofficial project.** This project is not affiliated with, endorsed by, or supported by Mysa. **Use at your own risk.** The software is provided "as is", without warranty.

This repository owns the reusable client API, Mysa wire-protocol references,
sanitized example data, diagnostic tools, and raw API coverage. Start with the
[client README](../README.md) for installation, lifecycle ownership, and runnable
examples. Protocol research describes known behavior even where this client
has no implementation; it is not a promise of support.

## Client API and cloud references

| Reference | Contents |
| --- | --- |
| [Cloud client and endpoint guide](../src/mysa_client/cloud/README.md) | Public API, S/R/L services, endpoints, timeouts, errors, and command semantics |
| [Authentication](cloud-auth.md) | Cognito SRP and refresh exchanges, token roles, browser restrictions |
| [State and models](cloud-state.md) | Discovery, response shapes, units, clocks, enums, capabilities, and history |
| [Account and schedule workflows](cloud-workflows.md) | Claim/removal, schedules, holds, shortcuts, provisioning, and app-only operations |
| [Newer cloud models](new-cloud-models.md) | Model-specific protocol mappings, capabilities, and evidence limits |
| [HomeKit client API](../src/mysa_client/homekit/README.md) | Discovery, pairing, persistence callbacks, reads, writes, events, and cleanup |
| [Raw API coverage](api-coverage.md) | Callable client operations and known gaps, separate from HA behavior |

## Other device protocols and research

| Reference | Contents |
| --- | --- |
| [HomeKit accessory behavior](homekit.md) | HAP characteristics and observed Mysa-specific behavior |
| [Bluetooth and SoftAP](bluetooth.md) | Wi-Fi setup, scanning, and newer-model credential transfer |
| [MQTT](mqtt.md) | Authentication, authorization limits, message formats, and diagnostic probing |
| [Firmware and connectivity](firmware-and-connectivity.md) | Startup, connections, update routes, and observed limits |
| [Matter](matter.md) | Research status and commissioning/validation gaps; no client implementation |
| [Examples](../examples/README.md) | Runnable consumer scripts and inert cloud/MQTT evidence records |

The [Wi-Fi setup application](https://github.com/SokolskyNikita/mysa-wifi-setup)
is a separate product. Keeping its protocol reference here does not make BLE,
SoftAP, MQTT, or Matter supported transports in `mysa-client`.

## Home Assistant consumer

The [Mysa Local documentation](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/README.md)
owns HA setup, entities, polling, routing, credential storage, command
confirmation, recovery, and integration-level validation. Its
[management coverage](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/implementation/progress.md)
is separate from this library's raw API coverage.

## Evidence and upkeep

References distinguish app-derived behavior, synthetic fixtures, and hardware
observations. Most live evidence is from two Baseboard Lites (`BB-V2-0-L`,
firmware `3.17.5.13`); it does not establish behavior on other models. API
acceptance, stored configuration, device reports, and physical operation are
separate claims. See [documentation maintenance](maintenance.md),
[release instructions](releasing.md), and the [changelog](../CHANGELOG.md).
