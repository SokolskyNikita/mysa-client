# Replace schedules and recover interrupted work

Use `ScheduleWorkflow` for an explicit replacement or clear operation. Direct
cloud schedule methods are lower-level primitives. For payload shapes and
modern/v1/v2 differences, see the [wire reference](../reference/protocol/cloud-workflows.md#schedules-holds-and-shortcuts).

## Prepare the workflow

Supply `MysaScheduleTransport(client)` (or a compatible `ScheduleTransport`), a
persistent `ScheduleJournal`, and the device ID, home ID and device type. The
[journal interface and helper contracts](../reference/python/runtime.md#schedule-hold-and-account-workflows)
are authoritative; [callable signatures](../reference/python/symbols.md#runtime-and-domain-helpers)
show the required arguments.

The journal must survive process restarts. Implement `async_load()`,
`async_save(data)` and `async_remove()` with the durability your application
requires. A save must finish before destructive operations proceed. Serialize
operations for the same device and retain the journal after cancellation or an
uncertain outcome.

## Replace or clear

1. At startup, recover any interrupted transaction with `async_recover()` under
   the same per-device lock used for edits. If recovery is uncertain, surface
   the error and inspect actual schedules before starting more changes.
2. For replacement, call `async_set(events)` with semantic events, or
   `async_set_encoded(encoded_events)` for already encoded events. For removal,
   call `async_clear()`. Set/clear also invoke recovery internally.
3. Let the workflow inspect all supported schedule formats and preserve other
   devices on shared schedules. Do not infer absence from only the modern list
   or from the settings record's schedule pointer.
4. On failure or cancellation, keep the recovery data and reconnect before
   attempting recovery. A replacement can involve several remote writes and
   is not atomic. Do not blindly replay creation requests.
5. Read back the resource when checking its configuration. A saved schedule
   does not establish that its events will execute on the hardware.

The workflow replaces by detach/recreate; it does not expose the recovered
in-place `PUT /schedule/{id}` operation or saved-template management. Those
implementation gaps are recorded in the [API inventory](../api-coverage.md#schedules-and-templates).

## Holds and account cleanup

For a hold, first obtain the current mode/target context, then use `resolve_hold()`
and `async_apply_hold()` with callbacks for the hold request and immediate state
application. Choose the local/cloud route in your application and confirm the
resulting device state separately. See the [hold wire rules](../reference/protocol/cloud-workflows.md#holds)
for active-schedule and timestamp requirements.

For account removal, deliberately choose between `delete_device()` (record only)
and `async_remove_from_account()` (best-effort schedule/shortcut cleanup, then
record removal). Inspect `AccountCleanupResult.complete` and `error_type` so a
cleanup failure is visible. Failure deleting the account record propagates.
Sensor/HVAC links, group membership and local application data require separate
handling. For claiming a returning device with minimal defaults, use
`async_add_to_account()` after checking model eligibility. These are explicit
account operations, not automatic repair actions.

[Documentation index](../README.md)
