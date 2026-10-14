# Select state and confirm commands

Read the [cloud service notation](../reference/protocol/cloud.md#context-and-conventions)
for S/R/L and ST/TEL/LT/REC. This guide owns the recommended selection and
confirmation procedure; [runtime contracts](../reference/python/runtime.md)
define what the helpers actually do.

## Send and confirm a command

1. Acquire state and capabilities for the selected device. Use `reconcile()` to
   preserve per-field source clocks; readbacks remain caller-scheduled.
2. Build a `CloudCommand`, for example with `climate_command()`. Its payload and
   normalized expected effects describe the same intended operation.
3. Snapshot the pre-write state with `pending_command()` and track it with
   `PendingCommandTracker`. Provide pending values when validating dependent
   settings so newer intent can supersede earlier commands.
4. Call `MysaClient.execute_command()` with explicit family, capabilities and
   state. It validates and sends without implicit reads. An ambiguous transport
   failure leaves the outcome uncertain; inspect state before retrying.
5. Feed later acquired reports through reconciliation and the tracker. Present
   confirmed, pending, superseded, expired or uncertain results according to the
   available evidence. Matching stored settings are not device confirmation.

The [synthetic runtime example](../../examples/runtime_basic.py) demonstrates
steps 1–3 and 5 without sending a command. [Python contracts](../reference/python/runtime.md#state-and-command-evidence)
and [callable signatures](../reference/python/symbols.md) define the helpers;
the policies below explain which observations to supply.

## Recommended reconciliation of cloud reads

The following is a suggested cloud-only selection policy. Applications choose their own
intervals and freshness thresholds; these recommendations are not transport guarantees.
Use **S + R** for normal cloud state acquisition: one S batch and one R legacy
state request at the configured cloud interval. Cache REC separately. Use L
as a replacement for a failing R route with backoff, not a third permanent
poll. Do not average values or count matching R/L responses as corroboration.

### One selection rule, applied per field

1. **Normalize.** Decode each source into the same units/enums and preserve
   `source`, `observed_at`, and `fetched_at`. Parse ISO timestamps and Unix
   seconds/milliseconds. Use the individual LT field's `t`, TEL's reading
   timestamp, or the relevant ST section timestamp; never borrow a sibling
   field's timestamp. Keep target temperature separate from an effective
   target altered by a hold or other control behavior.
2. **Validate.** Reject missing, malformed, non-finite, or semantically invalid
   values. Zero and `false` are valid where the field allows them. A timestamp
   more than one minute in the future is invalid (a suggested guard); do not let it win a
   newest-value comparison. A valid clock on one section does not validate
   another section.
3. **Choose by the field policy below.** For **Latest** fields, choose the
   newest valid, fresh observation; ties prefer S, then R, then L. Include the
   cached observation so a delayed response cannot roll a newer value back. An
   un-timestamped measurement is not fresh merely because its HTTP response
   just arrived. For **Config** fields, use the named authority/fallback;
   their change timestamps may legitimately be old. REC is stored intent,
   never another device measurement.
4. **Keep provenance.** Retain last-known values with their original age when
   no fresh candidate exists; expose stale/unavailable state rather than
   refreshing their timestamps. A missing field or failed device in a batch
   must not erase good data for other fields/devices. Keep pending writes
   separate from confirmed readings.

For measurement freshness, a suggested starting limit is **35 minutes outside
live reporting**, because ordinary Lite uploads can be 12–30 minutes apart.
During an established live-reporting session, use **max(90 seconds, three poll
intervals)**. These are configurable policy defaults, not vendor guarantees
or a claim that a 30-minute-old temperature is real-time. A future-dated
measurement is rejected rather than having its clock silently corrected.
For a configuration field with an invalid timestamp, do not use that timestamp
to outrank another source; retain its unconfirmed/last-known status.

| Field / operation | Selection behavior |
| --- | --- |
| Room temperature / humidity | **Latest** S TEL vs R/L LT. Use `CorrectedTemp`, never internal sensor temperatures. Accept valid humidity even when the Lite capability flag says false |
| Heating target / mode | Use the newest plausibly timestamped device report from ST or LT; use TEL only after matching target semantics and decoding its different mode enum. Unlike measurements, an unchanged target need not expire after 35 minutes. Without comparable timestamps prefer ST, then LT, and mark freshness unknown. Never use REC `Mode` or S `desired` as confirmation |
| Cooling target / fan / swing | **Config:** ST; REC only describes allowed choices. No verified legacy-state fallback in this guide |
| Minimum / maximum target | **Config:** ST pair, then REC pair if absent; validate minimum ≤ maximum. Never turn an uncertain/fallback value into permission to exceed known device limits |
| Button lock | Prefer the newest valid device report from ST or LT when timestamps are comparable; otherwise ST, then LT. REC is a stored-setting fallback only |
| Wake / idle brightness | **Config:** ST pair, then REC pair. LT's single brightness value cannot reconstruct both settings |
| Screen units | **Config:** REC `Format`, ST as fallback. ST has lagged the screen setting. This selects the configured screen units, not proof of physical application or the application's preferred display unit |
| Temperature offset | **Config:** ST only; absent means unknown, not zero |
| Heater profile | **Config:** ST, then REC `DutyCycleOpt` as stored configuration; validate against the device family |
| Heating activity | **Latest** normalized duty/interval observation. Keep each source's interval meaning; do not add R/L duty or runtime together or infer heater-off from Lite's zero power fields |
| Runtime / history | Prefer S usage `oH1` for runtime; R batch history only as an explicit fallback after bucket/unit normalization. Fetch bounded periods and do not sum overlapping sources. Historical samples do not become current readings |
| Identity / name / time zone / firmware | REC for identity/name/time zone; firmware information endpoint for offered/installed version, with ST identity as installed-version fallback. Cache metadata rather than treating old unchanged values as stale telemetry |
| Capabilities / model controls | S capabilities plus REC model/remote metadata. Preserve hardware limits; do not invent unsupported controls. Model-specific configuration has no general Lite fallback |
| Schedules / holds | Read S schedules and holds, plus legacy schedule sources when inspecting existing Mysa automation. An empty S list does not establish that no legacy schedule exists. Do not use REC `Schedule` or LT hold hints as the complete resource |
| Shortcuts / demand response | On-demand shortcut inspection through R/L. For demand response use ST when present, otherwise the dedicated R route; no merging of unrelated account mode fields |
| Connection / health | Keep request success and data freshness separate for S and legacy reads. A 200 or connected flag cannot make stale measurements fresh; track selected source and age per field. Do not let one failing API mark both paths failed |
| RSSI / internal temperatures / heap | Apply **Latest** when the application requests diagnostics; retain units and timestamps |
| Lite electrical values and unreliable energy summaries | Do not interpret zero electrical fields or inconsistent summaries as measured heater power or reliable consumption |

Cache records and metadata at a slower interval than live state; choose refresh
periods in the consuming application. The library does not schedule polling.
Refresh affected configuration after a command and after rediscovery. Read
optional schedules/history/diagnostics on demand or when that feature is
actually enabled. These are cloud fetch rules, not local/cloud routing rules.

## Recommended reconciliation of cloud writes

**Choose one route per semantic command. Do not broadcast every write to S,
R, and L, and do not copy discrepant readings back into the other APIs.**
Most differences reflect lag or representation, not three stores to repair.

| Command | Preferred route | Alternative / verification |
| --- | --- | --- |
| Setpoint / mode; supported AC controls | S | No verified R/L replacement. Confirm device-reported values with the read policy; REC `Mode` is not a fallback |
| Offset | S | No verified alternative; confirm ST |
| Lock / brightness / setpoint limits | S | R settings are a validated alternative; L is the same legacy path on another host. Select an alternative for a known route/device limitation, not as routine fan-out. Confirm ST or equivalent LT; a REC echo alone is insufficient |
| Screen units | R settings | L if that route is unavailable; S is also supported. Read REC for configured units and ST for reported confirmation when it catches up |
| Heater profile | R settings | L as host fallback; S is also supported. Read REC and ST, preserving a pending/mismatched state if application is unconfirmed |
| Existing device schedule creation | S, only when explicitly using Mysa scheduling | Keep an existing legacy schedule in its native format unless deliberately migrating it. Avoid creating parallel S/v1/v2 schedules for the same intent. Full-body in-place editing is outside client scope |
| Schedule removal / hold / resume | Remove through the resource's native route; S for holds | Re-read that schedule/hold source. Removing a template is not removing its applied schedules; a hold record alone does not confirm an immediate target change |
| Firmware update / temporary live reporting | S dedicated endpoint | Confirm firmware availability/version or advancing telemetry, respectively; no R/L duplicate trigger |
| Claim / remove / replace | Existing explicit setup/recovery method | Use the documented operation and host; never run these as automatic write-repair strategies |
| Time zone | R, only for an explicit Mysa schedule configuration request | Verify REC; changing an application's time zone must not silently rewrite the thermostat |

Validate capabilities and ranges, serialize changes per device, and send the
smallest semantic change. Send both brightness values together; preserve a
valid minimum/maximum pair. A setpoint change and a mode change are distinct
commands unless deliberately combined in one S body. Use the mode-specific
AC fan conversion in `commands.py`; raw enum values are not interchangeable.

Record an accepted command as **pending**, then re-read its confirmation
source. Matching `desired` or echoed REC is not physical confirmation. If the
requested value already matched the previous confirmed state, treat it as an
idempotent no-op, not a liveness test. A changed device report can confirm
application; where only REC is available, report stored configuration without
claiming device confirmation.

An explicit unsupported-route response can justify a validated alternative.
After a timeout or 200 without confirmation, re-read first: the command may
still apply. Do not immediately resend through another API, oscillate settings
to force synchronization, or apply a late retry after a newer user command.
Use bounded retries/backoff. Resource creation, firmware triggers, and other
non-idempotent actions require checking their outcome before any retry.

[Documentation index](../README.md)
