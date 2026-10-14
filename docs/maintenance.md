# Documentation maintenance

This repository is the canonical home for Mysa protocol findings and the
standalone client's API. Keep HA product behavior in the
[Home Assistant repository](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/maintenance.md).

## Ownership

| Material | Maintained home |
| --- | --- |
| Entry points and task navigation | [Documentation index](README.md); root README is the package landing page |
| Python imports, callable signatures, return values, errors and lifecycle | [Python references](README.md#python-api-contracts); [generated symbol index](reference/python/symbols.md) derives signatures/exports from source |
| State selection and command-confirmation procedure | [State and commands guide](guides/state-and-commands.md) |
| Application assembly and workflow procedures | [Runtime composition](guides/runtime.md) and [schedule workflows](guides/schedules.md) |
| Wire formats, endpoints, enums and device behavior | [Protocol references](README.md#protocol-references), one owner per topic |
| Dated validation and scope overview | [Evidence index](evidence/README.md); detailed observations stay beside their protocol context or dated record |
| Implementation status and remaining gaps | [API inventory](api-coverage.md); generated summaries are derived views |
| Runnable usage and sanitized exchange records | [Examples](../examples/README.md), linked from the owning guide/reference |
| HA settings, entities, polling, storage adapters and presentation | [Integration docs](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/README.md) |

Each behavior has one authoritative explanation. Other pages give a short summary
and a section link. Keep side-effect cautions at their point of use, preserve
model-specific evidence qualifiers, and leave release notes historical. Do not
copy application-specific defaults into client references. Move a topic only
with its incoming links and published anchors accounted for.

Protocol research can include operations this library does not implement.
Clearly distinguish a recovered route or field from a supported public method.
The client owns reusable Mysa semantics and workflow correctness, while each
application chooses scheduling, source/freshness policy, lifecycle, storage,
and presentation. HA roadmap exclusions do not automatically exclude a
protocol from research or make it a library policy.

## Issue format

Use the issue form's five sections—Summary, Scope, Acceptance criteria,
Validation, and References—for every issue. Keep observed evidence and
implementation scope distinct, and make acceptance criteria observable. Link
dependencies and the stable source tracker ID. Put each task in its owning
repository: client API/protocol work here, and HA integration behavior in the
[Home Assistant repository](https://github.com/SokolskyNikita/mysa-homeassistant/issues).
Cross-link shared work instead of creating duplicate tasks.

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
Markdown links/anchors (including same-repository GitHub `main` URLs), reader-page
reachability, inventory baseline, declared API symbols, fences and tables, strict JSON fixtures, cloud/MQTT
exchange metadata, incoming example links, and obvious secret
patterns. It parses runnable examples and diagnostic scripts without executing
them. It never signs in, discovers devices, or contacts a thermostat. Other external
URLs need a separate review; local checks cannot establish protocol accuracy.

After changing public callables or cloud exports, run
`python tools/update_api_reference.py`; CI uses `--check` to detect stale
signatures. Use `<!-- api-symbol: mysa_client.module:Class.method -->` beside
important hand-written contracts to check symbol existence without importing
transports. This does not verify behavioral claims.

Use relative links within the repository. The root README uses absolute GitHub
links because it is also the PyPI long description; the checker resolves those
against the checkout. New canonical pages must be reachable from `docs/README.md`.
The [published-anchor manifest](published-anchors.json) protects paths and
fragments that existed before the restructure; retain its entries when moving
content. Add published anchors when establishing new compatibility promises.
Forwarding pages retain old anchors and are explicitly marked as compatibility
pages; they do not own current prose.

After changing the raw API inventory, run `python tools/update_api_coverage.py`.
CI uses `--check` to validate API IDs and verify the inventory summary, detailed
map, README percentage, and compact progress bar match the reviewed rows.
Preserve stable IDs and meaningful row scope. Raw method availability is
not a measure of hardware support, integration reliability, or absence of bugs.

Update the authoritative reference first, then affected examples and incoming
links. Keep short forwarding pages for previously published documentation URLs
when useful, but do not maintain duplicated protocol explanations. Release
notes remain historical.
