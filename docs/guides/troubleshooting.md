# Troubleshoot a client

Use the symptom to select a reference. Keep request acceptance, observed state
and physical operation separate; preserve source timestamps when investigating.

| Symptom | Check next |
| --- | --- |
| HTTP succeeds but temperature is old | Inspect each field's observation time; a connected flag does not establish freshness. See [live reporting behavior](../reference/protocol/cloud-state.md#live-mode-and-liveness) and [selection policy](state-and-commands.md#recommended-reconciliation-of-cloud-reads). |
| Command accepted but target unchanged | Inspect reported state, not only `desired` or stored settings. Keep the command pending/uncertain; follow [write confirmation](state-and-commands.md#recommended-reconciliation-of-cloud-writes) before retrying. |
| Cloud readings disagree | Normalize clocks, enums and source semantics. R/L are host aliases, not independent measurements; see [service mappings](../reference/protocol/cloud.md). |
| 401, 403, 404, 429, or timeout | Consult [typed errors and retry behavior](../reference/python/cloud.md#errors-timeouts-retries-and-caller-responsibilities). A 403 can reflect permissions or route availability; a 404 is normalized only by specific resource helpers. |
| Browser signs in but cannot read state | Cognito and API CORS behavior differ; see [browser restrictions](../reference/protocol/cloud-auth.md#browser-access). |
| MQTT connects/subscribes but receives nothing | Connect, subscribe, publish and receive have distinct permissions. Run the [diagnostic probe](mqtt-diagnostics.md); review the [pool evidence](../evidence/mqtt.md). |
| MQTT becomes quiet after reconnect | Check `connection_generation` and renew live reporting; see [session behavior](../reference/python/mqtt.md#reconnection). |
| HomeKit is missing or pairing fails | Allow a bounded discovery window, check the advertised endpoint, and distinguish saved keys from a paired advertisement. See [discovery/persistence](../reference/python/homekit.md#discovery-and-pairing-persistence) and [Lite observations](../reference/protocol/homekit.md#discovery-and-pairing). |
| HomeKit cache is stale | Cache access is not a fresh read. Use the [read/event contract](../reference/python/homekit.md#characteristics-and-device-operations) and your polling policy. |
| Schedule edit was interrupted | Preserve the journal and [recover before editing again](schedules.md#replace-or-clear). |
| A documented route has no method | Check the [API inventory](../api-coverage.md); protocol research includes unimplemented operations. |
| Another model behaves differently | Check [evidence and compatibility](../evidence/README.md), then the device's capabilities rather than extrapolating Lite findings. |

For MQTT authorization probes use [mqtt_probe.py](../../tools/mqtt_probe.py).
For an existing Home Assistant HomeKit diagnostic export use
[hapdump.py](../../tools/hapdump.py), with the [accessory reference](../reference/protocol/homekit.md#homekit-wi-fi-switching)
for its scope. Keep tokens, pairing keys and raw household identifiers out of reports.

[Documentation index](../README.md)
