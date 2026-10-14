# Evidence and compatibility

This index owns the overview of validation scope. Detailed findings retain their
model, firmware, date, provenance and confirmation limits beside the relevant
protocol or dated record. These are reverse-engineered observations, not a
vendor API contract.

## What has been checked

| Surface | Evidence | Limits |
| --- | --- | --- |
| Cloud | [October 10–12, 2026 findings](cloud.md) on two Baseboard Lite `BB-V2-0-L` devices, firmware `3.17.5.13` | Setpoint/mode application differed between devices; full schedule execution, notification delivery and firmware installation remain unverified |
| HomeKit | [October 12 standalone wheel check](homekit.md), plus [October 10–11 accessory observations](../reference/protocol/homekit.md) | Read-only wheel checks did not validate event delivery after a change or pairing/writes |
| MQTT | [October 11–12 pool comparison and device checks](mqtt.md) on those two Lites and one account | Legacy policy could not be inspected; other models/accounts remain unvalidated |
| BLE | [October 11 Lite provisioning and GATT observations](../reference/protocol/bluetooth.md#scope-and-compatibility) | Other legacy models need validation; newer credential transfer is app-derived |
| Network and firmware | [October 11–12 observations](../reference/protocol/firmware-and-connectivity.md#sources) | Firmware check host established; image path and installation of an offered release unknown |
| Newer cloud models | [Android 4.20.1 field mappings and provenance](../reference/protocol/new-cloud-models.md#evidence-and-limits) | App-derived/synthetic, without matching hardware validation |
| Matter | [Vendor documentation and open questions](../reference/protocol/matter.md) | No transport implementation or captured hardware profile |

## Interpreting evidence

An accepted API request, a stored settings echo, a device report, independent
local readback and physical operation establish different claims. A successful
request alone establishes neither freshness nor heater operation. Use the
[confirmation guide](../guides/state-and-commands.md) to decide how to present
an outcome to the application.

[Cloud](../../examples/cloud/README.md) and [MQTT](../../examples/mqtt/README.md)
exchange records identify `live-sanitized`, `app-derived`, or `constructed`
provenance. Constructed fixtures test a shape; they add no hardware evidence.
The [API inventory](../api-coverage.md) measures callable operation breadth,
not compatibility or validation strength.

To add a finding, follow [evidence and sanitization](../maintenance.md#evidence-and-sanitization).

[Documentation index](../README.md)
