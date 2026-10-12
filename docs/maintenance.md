# Documentation maintenance

This repository is the canonical home for Mysa protocol findings and the
standalone client's API. Keep HA product behavior in the
[Home Assistant repository](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/maintenance.md).

## Ownership

| Material | Maintained home |
| --- | --- |
| Public client APIs, lifecycle, errors, and ownership | [Cloud guide](../src/mysa_client/cloud/README.md), [HomeKit guide](../src/mysa_client/homekit/README.md), and [examples](../examples/README.md) |
| Wire formats, endpoints, device behavior, and app-derived research | References in the [protocol index](README.md) |
| Raw method coverage and protocol implementation gaps | [API inventory](api-coverage.md) |
| Sanitized HTTP/MQTT exchanges | [Examples](../examples/README.md) |
| HA configuration, entities, orchestration, and recovery | [Integration docs](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/README.md) |
| HA acceptance criteria and hardware workflow checks | [Management inventory](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/implementation/progress.md) |

Protocol research can include operations this library does not implement.
Clearly distinguish a recovered route or field from a supported public method.
HA roadmap exclusions do not automatically exclude a protocol from research or
make it a library policy.

## Evidence and sanitization

Keep the source app version, observation date, model, firmware, host, and
confirmation limits beside each finding. Hardware validation currently centers
on Baseboard Lite `BB-V2-0-L`, firmware `3.17.5.13`; mappings from other models
remain app-derived or simulated unless matching hardware evidence is recorded.

Distinguish API acceptance, stored configuration, device reports, independent
local confirmation, and physical behavior. Missing metadata stays unknown.
Constructed examples illustrate a shape and are not additional live evidence.
Never infer broad support from a feature flag, an accepted write, or a route name.

Use synthetic identifiers and nonfunctional credential placeholders. Keep
account tokens, pairing keys, Wi-Fi passwords, signed URLs, private research,
and raw captures out of this repository and its distributions. Preserve source
provenance when moving an example; update its relative links and fixture paths.

## Offline checks

Run `python tools/check_docs.py` and `git diff --check`. The checker validates
Markdown links/anchors, fences and tables, strict JSON fixtures, cloud/MQTT
exchange metadata, incoming example links, and obvious secret
patterns. It parses runnable examples and diagnostic scripts without executing
them. It never signs in, discovers devices, or contacts a thermostat. External
URLs need a separate review; local checks cannot establish protocol accuracy.

After changing the raw API inventory, run `python tools/update_api_coverage.py`.
CI uses `--check` to validate API IDs and verify the inventory summary, detailed
map, README percentage, and compact progress bar match the reviewed rows.
Preserve stable IDs and meaningful row scope. Raw method availability is
not a measure of hardware support, integration reliability, or absence of bugs.

Update the authoritative reference first, then affected examples and incoming
links. Keep short forwarding pages for previously published documentation URLs
when useful, but do not maintain duplicated protocol explanations. Release
notes remain historical.
