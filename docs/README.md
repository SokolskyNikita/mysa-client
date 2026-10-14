# Mysa client documentation

<a id="mysa-client-and-protocol-documentation"></a>
<a id="home-assistant-consumer"></a>

Start here for the standalone Python library and reverse-engineered protocols.
[Install the package](../README.md#installation). For Home Assistant setup,
entities and integration settings, use [Mysa Local](https://github.com/SokolskyNikita/mysa-homeassistant).

## Find a task

| I want to… | Start here |
| --- | --- |
| Sign in, discover devices, pair HomeKit, or open MQTT | [Connect a client](guides/connect.md) |
| Send a command and distinguish acceptance from confirmation | [Select state and confirm commands](guides/state-and-commands.md) |
| Combine cloud, HomeKit and MQTT in my application | [Compose the runtime](guides/runtime.md) |
| Replace or clear schedules and recover interrupted work | [Schedule workflows](guides/schedules.md) |
| Apply a hold or clean up an account device | [Hold and account workflows](guides/schedules.md#holds-and-account-cleanup) |
| Diagnose stale readings, a failed write, or missing discovery | [Troubleshooting](guides/troubleshooting.md) |
| Diagnose MQTT credentials and publish/receive permissions | [MQTT diagnostic probe](guides/mqtt-diagnostics.md) |
| Check whether an operation is implemented | [API inventory](api-coverage.md) |
| Check which hardware and behavior were validated | [Evidence and compatibility](evidence/README.md) |
| Run or inspect an example | [Runnable scripts and sanitized exchanges](../examples/README.md) |

<a id="client-api-and-cloud-references"></a>

## Python API contracts

These references own imports, parameters, return values, errors, lifecycle and
helper guarantees. Research routes without client wrappers belong below.

| API | Reference |
| --- | --- |
| Cloud | [Authentication, HTTP client, public helpers and errors](reference/python/cloud.md) |
| HomeKit | [Discovery, pairing persistence, semantic reads/writes and lifecycle](reference/python/homekit.md) |
| MQTT | [Explicit sessions, events, live reporting and command results](reference/python/mqtt.md) |
| Domain helpers and runtime | [Validation, reconciliation, views, routing and workflow adapters](reference/python/runtime.md) |
| Callable lookup | [Generated signatures and cloud exports](reference/python/symbols.md) |

<a id="other-device-protocols-and-research"></a>

## Protocol references

These pages own wire formats and device behavior, including research-only
operations. A documented endpoint is not a promise of a supported method.

| Subject | Reference |
| --- | --- |
| Cloud services | [S/R/L terminology and cross-service mappings](reference/protocol/cloud.md) |
| Authentication | [Cognito, HTTP headers, token roles and browser restrictions](reference/protocol/cloud-auth.md) |
| State and history | [Response shapes, clocks, enums, capabilities and model fields](reference/protocol/cloud-state.md) |
| Account and schedules | [Schedules, holds, shortcuts, enrollment and app-only routes](reference/protocol/cloud-workflows.md) |
| Newer models | [Baseboard V3, Central HVAC and Climate Sensor](reference/protocol/new-cloud-models.md) |
| HomeKit | [Accessory characteristics and Wi-Fi switching observations](reference/protocol/homekit.md) |
| MQTT | [Authorization model, topics, messages and command encodings](reference/protocol/mqtt.md) |
| Firmware and network | [Startup connections, update routes and observed limits](reference/protocol/firmware-and-connectivity.md) |
| BLE and SoftAP | [Wi-Fi provisioning and credential transfer](reference/protocol/bluetooth.md) |
| Matter | [Vendor claims and commissioning research](reference/protocol/matter.md) |

BLE, SoftAP and Matter have no client transport here. The separate
[Wi-Fi setup application](https://github.com/SokolskyNikita/mysa-wifi-setup)
owns its browser implementation. MQTT is optional and experimental.

## Evidence and upkeep

[Evidence index](evidence/README.md) · [Documentation maintenance](maintenance.md) ·
[Release instructions](releasing.md) · [Changelog](../CHANGELOG.md)

Older documentation paths remain as forwarding pages for published links.
