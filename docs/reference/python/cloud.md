# Cloud Python API

- [Authentication](#authentication)
- [Using the client](#using-the-client)
- [Semantic command execution](#semantic-command-execution)
- [How it is organized](#how-it-is-organized)
- [Public API and integration helpers](#public-api-and-integration-helpers)
- [Errors, timeouts, retries, and caller responsibilities](#errors-timeouts-retries-and-caller-responsibilities)
- [Device record and firmware catalog reads](#device-record-and-firmware-catalog-reads)

Start with [connecting clients](../../guides/connect.md).
Wire formats belong to the [cloud protocol reference](../protocol/cloud.md);
recommended readback policy belongs to the [state guide](../../guides/state-and-commands.md).

## Authentication

Use `MysaAuth.sign_in(email, password)` for the Cognito SRP exchange, or
initialize it with a saved refresh token. `async_get_id_token()` handles
refresh; retain a replacement refresh token through `on_refresh_token` when
one is returned. The public Cognito identifiers are in [`const.py`](../../../src/mysa_client/cloud/const.py),
and the exchange is described in the [authentication reference](../protocol/cloud-auth.md#cognito-sign-in).
Credentials and token persistence belong to the caller.

## Using the client

`MysaAuth` and `MysaClient` are exported by [`__init__.py`](../../../src/mysa_client/cloud/__init__.py).
The caller supplies and closes the `aiohttp` session. See the complete
[cloud example](../../../examples/cloud_basic.py) for login, token persistence,
account discovery, reading, and an explicitly enabled setpoint write.

## Semantic command execution

`await client.execute_command(device_id, command, family=family,
capabilities=caps, state=state, command_value=lookup)` validates and sends one
`CloudCommand`, returning `None` on request success. `family`, `capabilities`
and `state` are required keyword context (each can be unknown/`None`);
`command_value` is an optional pending-value lookup. The method performs no
implicit discovery, state reads or confirmation. Validation can raise
`ValueError`; transport errors follow the hierarchy below. Follow the
[confirmation guide](../../guides/state-and-commands.md#send-and-confirm-a-command)
for the caller sequence.

## How it is organized

| File | Responsibility |
| --- | --- |
| [`auth.py`](../../../src/mysa_client/cloud/auth.py), [`srp.py`](../../../src/mysa_client/cloud/srp.py) | Cognito authentication, serialized token refresh, SRP calculations |
| [`client.py`](../../../src/mysa_client/cloud/client.py) | HTTP requests, endpoint methods, response handling, and explicit validated `execute_command()` |
| [`commands.py`](../../../src/mysa_client/cloud/commands.py) | Semantic controls, enum conversion, schedule encoding |
| [`models.py`](../../../src/mysa_client/cloud/models.py) | Typed response parsing, preserving original JSON in `raw` |
| [`const.py`](../../../src/mysa_client/cloud/const.py), [`exceptions.py`](../../../src/mysa_client/cloud/exceptions.py) | Hosts, protocol constants/enums, errors |

The standalone package has no Home Assistant dependency. The client includes
reusable Mysa-specific validation, state reconciliation, command confirmation,
device interpretation, and optional local/cloud routing; applications own
polling cadence, source/freshness choices, durable storage, and lifecycle. See
the [optional runtime guide](runtime.md) and the HA
[architecture guide](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/architecture.md).
Install the published package from [PyPI](https://pypi.org/project/mysa-client/).

## Public API and integration helpers

The [generated callable and export index](symbols.md#cloud-exports) lists the
current top-level cloud API directly from `__all__`. It also provides signatures
for the client methods.

These submodule helpers are supported for standalone callers and integrations:

| Module | Supported names |
| --- | --- |
| [`commands.py`](../../../src/mysa_client/cloud/commands.py) | `CloudCommand`, `state_command`, `settings_command`, and field encoders including `auto_temperature_changes`, `button_lock_changes`, `display_brightness_changes`, `display_unit_settings`, `fan_speed_changes`, `heater_type_settings`, `hvac_fan_changes`, `mode_changes`, `setpoint_limit_changes`, `swing_changes`, `temperature_changes`, `temperature_offset_changes` |
| [`device_commands.py`](../../../src/mysa_client/cloud/device_commands.py) | `climate_command`, `brightness_command`, `hvac_fan_command`, `hvac_fan_speed_command`, `display_unit_command` |
| [`device.py`](../../../src/mysa_client/cloud/device.py) | `basic_modes`, `brightness_pair`, `heating_range`, `hvac_activity`, `hvac_fan_operation` |
| [`const.py`](../../../src/mysa_client/cloud/const.py) | `API_BASE`, `EXPERIMENTAL_CLOUD_FAMILIES`, `SWING_VERTICAL`, `model_family`; public models and enums are also re-exported at the package level |
| [`models.py`](../../../src/mysa_client/cloud/models.py) | The response models exported by the package root, retained at this submodule path for direct imports |
| [`exceptions.py`](../../../src/mysa_client/cloud/exceptions.py) | The public `MysaError` exception hierarchy, retained at this submodule path for direct imports |
| [`observations.py`](../../../src/mysa_client/cloud/observations.py) | `MEASUREMENT_MAX_AGE`, `MEASUREMENTS`, `STATE_FIELDS`, `TELEMETRY_FIELDS`, `Observation`, `number`, `observe`, `valid_value` |
| [`reconciliation.py`](../../../src/mysa_client/cloud/reconciliation.py) | `reconcile`, `PendingCommand`, `PendingCommandTracker`, `pending_command` |
| [`trace.py`](../../../src/mysa_client/cloud/trace.py) | `route_template` for redacted request-route reporting |
| [`validation.py`](../../../src/mysa_client/cloud/validation.py) | `validate_basic_command` and `validate_cloud_command` for device/capability validation |
| [`schedule_workflow.py`](../../../src/mysa_client/cloud/schedule_workflow.py) | `ScheduleWorkflow`, `ScheduleJournal`, `ScheduleTransport`, and `MysaScheduleTransport` for replacement, clear, and recovery |
| [`holds.py`](../../../src/mysa_client/cloud/holds.py) | `resolve_hold`, `async_apply_hold` |
| [`account_workflow.py`](../../../src/mysa_client/cloud/account_workflow.py) | Model eligibility, minimal account claim, and best-effort cleanup before account removal |

Other names prefixed with an underscore are private implementation details.

## Errors, timeouts, retries, and caller responsibilities

The current transport uses a 20-second timeout. A 401 causes one forced token
refresh and retry; a remaining 401 or 403 raises `MysaApiError`, not automatic
reauthentication. Route absence or permissions can explain 403. A 404 raises
`MysaNotFoundError`; only resource-specific helpers such as schedule/hold reads
normalize an absent resource to an empty result. A 429 raises
`MysaRateLimitError` with numeric `Retry-After` when supplied; timeout,
transport failure, or 5xx raises `MysaConnectionError`. There is no general
transport retry loop. A 401 retry only follows a forced token refresh; ambiguous
commands are never replayed after cancellation, timeout, or connection loss. The
legacy host fallback is limited to known operations and eligible failures; it is
not a general retry or a second measurement source. The client provides
reusable helpers to reconcile supplied observations, validate semantic
commands, track confirmation evidence, and coordinate explicitly supplied
transports. The caller chooses polling/backoff cadence, source/freshness
policy, durable storage, and when to run readbacks or recovery workflows. It
must still treat ambiguous commands as uncertain until reported evidence is
available.

Cognito failures use the `MysaAuthError` hierarchy. The implementation handles
the password-verifier SRP challenge; unsupported challenges raise
`MysaUnsupportedChallengeError`.

<a id="reading-the-legacy-firmware-catalog"></a>

## Device record and firmware catalog reads

`await client.get_device(device_id)` sends `GET /devices/{id}` and returns a
`DeviceRecord` from the response's `{"Device": {"Id": "…", "Model": "…", …}}`
envelope. It reads stored settings, not live state. The required `Id` must match
the requested ID. Missing optional settings remain unknown, unknown fields stay
in `raw`, and the model cache used by legacy state parsing is updated.

`await client.get_firmware_catalog()` sends `GET /devices/firmware` and returns
`dict[str, FirmwareRecord]`, keyed by device ID. Its wire envelope is
`{"Firmware": {"device-id": {"Device": "device-id", "InstalledVersion": "3.17.5.13"}}}`.
`FirmwareRecord` exposes `device_id`, nullable `installed_version`, and `raw`.
This is the account's catalog of reported installed versions, not a list of
releases available to install. It does not infer update availability or an
allowed version; use `get_firmware_info(id)` for that separate service. Catalog
and live-state versions may disagree in age; neither read overwrites the other.

Both methods try the modern host first, then use the existing legacy read
fallback and five-minute host cache, independently of other operations. A 404
on both hosts raises `MysaNotFoundError`; a malformed envelope or record raises
`MysaApiError`. An empty `Firmware` object is a valid empty catalog. A catalog
entry must identify the matching `Device`; missing/null `InstalledVersion`
remains `None`, while a non-string version is rejected. Invalid entries fail the
whole call rather than silently returning partial catalog data.

The envelope evidence is recorded in the [cloud validation record](../../evidence/cloud.md). The legacy catalog is
app-derived for V1/V2, Lite, in-floor, and AC; other hardware has not been
validated. These reads do not add newer V3/Central HVAC/Climate Sensor OTA fleet
support. Settings are parsed without a model whitelist so unknown model strings
and extra fields remain available, without implying control support.

Constructed regression fixtures derived from those envelope shapes:
[single record](../../../tests/fixtures/device_record.json) and
[firmware catalog](../../../tests/fixtures/firmware_catalog.json). They use
synthetic identifiers and are not additional hardware observations.

[Documentation index](../../README.md)

<!-- api-symbol: mysa_client.cloud.client:MysaClient.execute_command -->
<!-- api-symbol: mysa_client.cloud.client:MysaClient.get_firmware_catalog -->
<!-- api-symbol: mysa_client.cloud.auth:MysaAuth.sign_in -->
