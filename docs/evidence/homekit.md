# HomeKit validation

On October 12, 2026, an installed 0.1.1 wheel in a Python 3.11 environment without
Home Assistant discovered and authenticated to both tested Baseboard Lites.
Read-only checks covered advertised capabilities, all five semantic fields,
repeat reads, fresh-session reconnection, and shutdown without pending tasks.
Targets, modes, display units, and saved pairing credentials remained unchanged.
The passive observation window saw no value-change events; it did not validate
notification delivery after a device change or exercise writes/pairing.

For the October 10–11 Lite accessory and Wi-Fi switching observations, see the
[accessory reference](../reference/protocol/homekit.md). Those findings retain the
characteristic-level context required to interpret them.

[Documentation index](../README.md)
