# Domain helpers and runtime Python API

- [Semantic cloud operations](#semantic-cloud-operations)
- [State and command evidence](#state-and-command-evidence)
- [Optional routing and telemetry helpers](#optional-routing-and-telemetry-helpers)
- [Schedule, hold, and account workflows](#schedule-hold-and-account-workflows)
- [Example](#example)

<a id="domain-helpers-and-optional-runtime"></a>

`mysa-client` provides reusable Mysa device behavior above its independent
cloud, HomeKit, and MQTT protocol clients. These helpers interpret device
reports, build and validate semantic commands, track evidence, and run explicit
workflows. They do not create a protocol client, open a connection, start
polling, or launch background tasks when imported or constructed.

Applications remain responsible for credentials, transport/session lifetime,
poll cadence, source and freshness choices, task scheduling, durable storage,
and presenting results. The helpers accept caller-selected state, transports,
clocks, callbacks, and journal adapters so they can run without Home Assistant.
Home Assistant uses the same client semantics and supplies its own lifecycle,
storage, and UI adapters.

## Semantic cloud operations

`mysa_client.cloud.device_commands` builds a `CloudCommand` from a device
operation. Its payload is the Mysa request representation; its `expected`
fields describe the normalized state that later readback can confirm. The same
description can be checked with `cloud.validation.validate_cloud_command`
before a caller sends it. `cloud.device` contains shared interpretation for
capabilities, HVAC activity and fan settings, brightness pairs, and heating
ranges. Lower-level field encoders remain in `cloud.commands`.
`MysaClient.execute_command()` validates and sends a command when explicitly
called, using the device family, capabilities, state, and pending-value lookup
that the caller supplies. It does not read device state or capabilities first.

For example, `climate_command()` expresses a heat/cool mode or target,
`brightness_command()` adjusts wake and sleep values together, and
`hvac_fan_command()` encodes Central HVAC fan operation. These helpers do not
send commands. The caller passes the returned payload to the selected client
and owns transport errors and retry decisions.

## State and command evidence

`cloud.reconciliation.reconcile()` selects usable reported observations by
field while keeping their source and measurement clock. It can combine
structured state, legacy state, a stored settings record, and the previous
view without making an old measurement look fresh. The application supplies
the observations and current time; the helper does not fetch or poll.

`pending_command()` snapshots the pre-write state. `PendingCommand` and
`PendingCommandTracker` distinguish unchanged intent, accepted/pending writes,
stored settings, changed post-submission reports, expiry, and supersession.
A settings-record echo can show that configuration was stored; a command is
confirmed only by matching reported state with suitable post-submission
evidence. Tracking does not itself issue a readback. The caller decides when
to refresh and how long to retain this state.

`runtime.state.DeviceView` composes supplied local values, normalized cloud
state, and MQTT measurements into semantic values such as temperature, mode,
action, display unit, and heat fraction. Callers select whether the local path
is healthy, which telemetry sources are fresh, and the source order. This view
does not own source lifetimes or refresh stale data.

## Optional routing and telemetry helpers

`runtime.routing.CommandRouter` is an optional local-first command coordinator.
It accepts a semantic local transport and callbacks for cloud access, state
readback, error classification, and application notifications. It serializes
writes and tracks an outstanding local write so an uncertain fallback can be
reconciled in order. It does not import a HomeKit SDK or create either
transport. The caller constructs it after transports are ready and calls
`async_stop()` as part of its own shutdown sequence.

`runtime.telemetry.MqttTelemetryState` filters an explicitly supplied device
allowlist, rejects retained, duplicate, invalid, or stale messages, keeps
per-field receipt times, and reports when a value expires. Feed it messages
from an already managed `MysaMqttClient` session. `MqttLiveStatusRenewal`
calculates when live status should be requested and the next retry deadline;
the caller performs the request. MQTT connection setup, reconnects, and task
management remain with the application. This runtime does not enable MQTT
automatically.

## Schedule, hold, and account workflows

`cloud.schedule_workflow.ScheduleWorkflow` compares and replaces or clears
device schedules and recovers interrupted changes. It writes a version-1
journal before destructive operations; the journal shape matches the existing
Home Assistant recovery data. Supply a `ScheduleJournal` implementation and a
`ScheduleTransport` (or adapt `MysaClient` with `MysaScheduleTransport`). The
caller owns durable storage, per-device locking, cancellation task management,
and when to call `async_recover()` at startup or before another edit.

`cloud.holds.resolve_hold()` applies Mysa's mode and OFF/setpoint rules.
`async_apply_hold()` sequences the hold request and immediate state application
through caller-provided callbacks. `cloud.account_workflow` includes model
eligibility, minimal account claiming, and best-effort schedule/shortcut
cleanup before account removal. These functions run only when called; applications own user confirmation,
error presentation and recovery UI.

## Example

Follow [runtime composition](../../guides/runtime.md) for the assembly sequence
and the synthetic runnable example. Hardware scope remains in the
[evidence index](../../evidence/README.md).

[Documentation index](../../README.md)

<!-- api-symbol: mysa_client.cloud.reconciliation:PendingCommandTracker -->
<!-- api-symbol: mysa_client.cloud.schedule_workflow:ScheduleWorkflow.async_recover -->
<!-- api-symbol: mysa_client.runtime.state:DeviceView -->
<!-- api-symbol: mysa_client.runtime.routing:CommandRouter -->
<!-- api-symbol: mysa_client.runtime.telemetry:MqttTelemetryState -->
