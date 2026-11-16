# Cloud account and schedule workflows

- [Schedules, holds and shortcuts](#schedules-holds-and-shortcuts)
- [Adding and removing devices](#adding-and-removing-devices)
- [Additional endpoints](#additional-endpoints)
- [Retained app-only workflow mechanics](#retained-app-only-workflow-mechanics)

This is the canonical reference for observed account, schedule, hold, and shortcut request mechanics. The [cloud protocol reference](cloud.md) describes endpoint availability, S/R/L terminology, and cross-service mappings. These findings are reverse-engineered observations, not a Mysa API contract; `mysa-client` wraps only a subset of the routes described here.

The protocols were reverse-engineered from the original Mysa apps. Hardware observations cover Baseboard Lite `BB-V2-0-L`, firmware `3.17.5.13`. Other-model behavior remains app/code-derived unless explicitly stated.

- [Schedules, holds and shortcuts](#schedules-holds-and-shortcuts)
- [Account enrollment and removal](#adding-and-removing-devices)
- [Other account and app routes](#additional-endpoints)

## Schedules, holds and shortcuts

### Weekly schedules

`POST /schedule` creates and attaches a schedule. The response was 201 with `{"scheduleId":"..."}`.

[Full schedule creation exchange](../../../examples/cloud/schedule-create.full-body.json)

Weekdays use Sunday = `"0"`; execution times use local `HH:MM:00`. Schedule times follow the device's `TimeZone`; callers are responsible for choosing the intended local time. `clientKey` identifies the submitting client.

Read schedules with `GET /schedule?homeId={home}&includeLegacyBaseboard=true&includeLegacyAc=true&includeLegacyInfloor=true`. It returns a list of objects with `id`, `name`, `reusable`, `homeId`, `deviceType`, `deviceIds`, `events`, `createdAt` and `updatedAt`. No schedule returned 404; Python converts that to an empty list. In place of `homeId=` the route also takes `scheduleId=` (one schedule, including a reusable one with no devices) or `deviceId=` (the schedule attached to a device); both answered on a Baseboard Lite account on 2026-11-15, and Mysa's app 4.21.1 uses them.

Mysa keeps some older thermostats' schedules in an older store that has no
custom names, and this route translates to and from it. A Baseboard Lite is
one of them: the name it is given is not kept and reads back as `Schedule`
(after a create and after an in-place update), weekdays become integers,
events are sorted Sunday-first, and `clientKey` becomes `v2-migration`. Which
other models use the older store is not established. Compare event behavior
rather than requiring the submitted and returned JSON to match exactly.

Mysa states these limits for one schedule: at most 56 events, at most one
event per day and time, and at most 50 thermostats. Mysa's app 4.21.1 uses a
higher event limit for some models: 70 for a Baseboard Lite, a Mini-Split AC,
and other models from a certain firmware version on, and 56 for a Baseboard
V1 and the rest (derived from the app; the firmware versions are not
verified). This route does not enforce any of them. Tested on a Baseboard Lite on 2026-11-15, each of these was
answered 201 and stored:

| Request | Answer |
| --- | --- |
| 57 events | 201 |
| Two events on the same day and time | 201 |
| An event setpoint between steps (22.25) | 201 |
| An event mode the thermostat doesn't list (cool) | 201 |
| An unknown field in the body | 201 |
| `dayOfWeek` as an integer instead of a string | 201 |
| No `reusable` field | 201 |

A Baseboard Lite stored a 70-event schedule and ran an event from it on time
(2026-11-15); its legacy V2 view held the same 70 events. The other cases
above were deleted at once, so whether a thermostat runs them is not known.
`encode_schedule_events()` and `MysaClient.update_schedule()` refuse a
repeated day and time and more than `max_events` events
(`check_schedule_events()`): 70 by default, and `schedule_event_limit(family)`
gives a model's own limit (70 for a Baseboard Lite and an AC, 56 otherwise).
Setpoints and modes are checked when the events are built.

`DELETE /schedule/{scheduleId}/attach/{dev}` detaches the device. For an unshared
schedule, the response was "Schedule unattached from device and deleted".
In-place editing is verified through `PUT /schedule/{id}`; see the [full-body update example](../../../examples/cloud/schedule-update.full-body.json). Send all six fields:
`name`, `events`, `deviceType`, `homeId`, `deviceIds`, and `reusable`. A body
containing only name/events fails with 400 `Invalid deviceType: undefined`.
The schedule keeps its ID, and the new events were read back from a Baseboard
Lite on 2026-11-15. Acceptance and record readback did not establish complete
scheduled execution. `MysaClient.update_schedule()` sends this request.
For supported replacement and recovery, follow the [schedule guide](../../guides/schedules.md).

`POST /schedule/{scheduleId}/attach/{dev}` with no body attaches an existing
schedule; the answer was 200 "Schedule attached to device" (Baseboard Lite,
2026-11-15). Afterwards `GET /schedule?deviceId=` showed it with the name
`Schedule`, `reusable: false` and the device attached, so attaching a
reusable template to a Lite rewrites its name and `reusable` in that view.
Detaching returned it to a reusable template with no devices. This client has
no wrapper for the attach request.

No direct delete for a schedule was found (Baseboard Lite account,
2026-11-15): `DELETE /schedule/{id}` answered 404, and
`DELETE /homes/savedSchedule/{id}` answered 403 for a modern template. A
reusable template with no devices was removed by `PUT /schedule/{id}` with
`reusable: false` and one device, then detaching, which answered "unattached
from device and deleted". Mysa's app 4.21.1 never deletes a schedule either;
it detaches every device.

What Mysa's app 4.21.1 puts in an event, by mode and model (derived from the
app). On a Baseboard Lite the server stored a repeated day and time and 70
events (2026-11-15, above); the per-model shapes below have not been tried on
a device. Mode values are the [control mode enum](cloud-state.md#mode-and-heater-enums).

| Part of the event's `state` | When the app sends it |
| --- | --- |
| `modes: {mode}` | Every event |
| `targetHeat: {setpoint}` | Mode heat or emergency heat |
| `targetHeat: {setpoint}` and `targetCool: {setpoint}` | Mode fan |
| `targetCool: {setpoint}` | Any other mode except off, dry included |
| No setpoint target | Mode off |
| `modes.fan_mode` | An AC and other models with a fan |
| `tracking: {tracking}` | An In-Floor, from its floor or ambient sensor choice |
| `targetAuto: {heatSetpoint, coolSetpoint}` | A Central HVAC in auto |
| `targetAuto: {setpoint}` | An AC in auto |

The app sets `clientKey` to `App-` plus its version (`App-4.21.1`) and
`executionTime` to `HH:mm:ss`. When an event is added for a day and time that
already has one, the app replaces the earlier event; it does not refuse the
new one. On a schedule read it treats an error whose message is "Schedule not
found", and an access-revoked error, as "no schedule".

For an AC that moves to a newer remote code set, app 4.21.1 converts its
stored schedule and shortcuts with `POST /schedules/v2/{scheduleId}/migrateACv3`
and `POST /homes/{home}/shortcut/migrateACv3`, each with the body
`{"DeviceId": "{dev}"}` (derived from the app, not tried on a device).

### Legacy schedules and saved templates

Read v1 with `GET /schedules`. To clear one device, POST
`/schedules/device/update/{dev}` with seven empty weekday arrays in
`ScheduledActions`; R also requires `Device` and `Model`. Preserve other devices.
Read v2 with `GET /schedules/v2/{dev}` using the device ID. Create with
`POST /schedules/v2/` and update with `PUT /schedules/v2/`: `Schedule` carries
`Version`, `Owner`, `Events` (and `Id` for update), with sibling `Devices` and
`Model`. On a Baseboard Lite (2026-11-15) a PUT with the existing `Id` kept the
schedule's id, and a POST with the same body gave it a new id. A PUT for an
`Id` whose schedule had been deleted answered 200 but attached nothing: the
thermostat still had no schedule. `restore_native_schedule()` therefore uses
PUT only while every listed thermostat still follows that schedule, and POST
otherwise. A schedule made through the modern `/schedule` route read back
here with `Version` `"3.0.0"` and its events keyed `BB`; the V2 view of that
schedule had the keys `LastUpdated`, `SavedScheduleId`, `Owner`, `Version`,
`Events`, `Id` and `Hash` (Baseboard Lite, 2026-11-15). Preserve the native metadata;
do not derive `Model` or `Owner` from an event key. See the
[v2 update body](../../../examples/cloud/schedule-v2-update.full-body.json),
[v2 delete body](../../../examples/cloud/schedule-v2-delete.with-body.json), and
v1 clear variants for [R](../../../examples/cloud/schedule-v1-clear.modern-host.json)
and [L](../../../examples/cloud/schedule-v1-clear.legacy-host.json). Delete at
`/schedules/v2/{id}` using the schedule ID and a JSON body with `Devices` and
`Model`. A device ID and schedule ID are not interchangeable.

V2 `Events` maps weekday number → minute number → model command. The legacy
command fields are `mode|setpoint|fanSpeed|brightness|trackingSensor|lowerSetpoint|upperSetpoint`,
with `%` for unset fields. Mode is the legacy enum, not S's mode enum.
The model key of a Lite's events is not always the same. An older Lite
schedule read back keyed `BB2L` (October 2026), for example
`{"0":{"1437":{"BB2L":"3|22|%|%|%|%|%"}}}`. A schedule made through the modern
`/schedule` route read back in the V2 view keyed `BB` (Baseboard Lite,
2026-11-15), and Mysa's app 3.65.1 keys Baseboard V2 and Lite actions under
`BB` when it builds a V2 schedule. Which key a Lite's own V2 schedule returns
today was not re-read. Replay whatever key the read returned.

Saved templates are read through `/homes/{home}/savedSchedule`; create at
`POST /homes/savedSchedule/create`, change name/events/apply using
`PUT /homes/savedSchedule/{id}/name`, `/events`, or `/apply`, and delete with
`DELETE /homes/savedSchedule/{id}`. Name, event and apply bodies respectively
carry `Name`, `Events`, and `Devices`. Applying creates separate `V2Schedules`
resources; deleting a template does not remove those device schedules. Event
editing worked on L but returned 404 on R. Do not infer schedule absence from
an empty modern list or an old REC `Schedule` pointer.
The create body in Mysa's app 3.65.1 is
`{Name, Events, OwnerId, HomeId, AppliedDevices: []}`, with `Events` in the
V2 form described above. App 4.21.1 has the create, name, apply and delete
routes but no `/events` route, so it cannot edit a saved template's events;
that is consistent with the 404 on R. These routes were exercised in October
2026; the create body's field list is derived from the app.

A modern template is a schedule with `reusable: true` and `deviceIds: []`.
`POST /schedule` with those answered 201, and the schedule then listed in the
home as a reusable schedule with no devices (Baseboard Lite account,
2026-11-15). Mysa's app 4.21.1 saves a template this way and renames it with
`PUT /schedule/{id}`.

For the supported helper and adapter contracts, see [schedule workflow API](../python/runtime.md#schedule-hold-and-account-workflows).

### Holds

With a schedule active, a plain setpoint write held the new value until the next event. Explicit holds use `POST /schedule/hold`:

[Permanent hold exchange](../../../examples/cloud/hold.permanent.json)

For a timed hold, use `"holdType":"temporary"` and add `"endTime":"<ISO UTC timestamp>"`. Both returned 201 with a `holdId`. Changing the hold kept the same ID: a temporary hold sent after a permanent one kept its `holdId` (Baseboard Lite, 2026-11-15).

A hold record does not move the setpoint. On a Baseboard Lite on 2026-11-15, with a schedule attached and the setpoint at 22 °C, a permanent hold whose state was 21 °C in heat mode was answered 201 and read back, and the reported setpoint stayed 22 for the next 150 seconds, both without `startTime` and with one ten seconds ahead. A caller that wants the temperature to change must also write the setpoint.

The `state` Mysa's app 4.21.1 puts in a hold is the thermostat's current
state, not a new target (derived from the app; only the heat-mode shape was
sent to a Baseboard Lite, in the test above):

| Part of the hold's `state` | When the app sends it |
| --- | --- |
| `modes: {mode}` | The reported mode, when there is one |
| `modes.fan_mode` | The reported fan mode, when a mode and a fan mode are both reported |
| `targetHeat: {setpoint}` | Mode heat or emergency heat |
| `targetCool: {setpoint}` | Mode cool |
| No setpoint | Any other mode (auto, off, fan, dry) |

The setpoint is the desired heat or cool setpoint when the state has one,
otherwise the reported one. With no state for the device the app sends an
empty `state`.

The server rejects a start time that has already passed with "Start time must be greater than current time". The Python client starts the hold ten seconds in the future and formats timestamps with milliseconds and a `Z` suffix. `startTime` is optional: a hold sent without one was answered 201 and read back with the time of the request as its start (Baseboard Lite, 2026-11-15), which is how Mysa's app 4.21.1 sends it. An `endTime` that has passed is refused with "End time must be greater than current time".

A hold body needs `holdType`: one with only `deviceId` is answered 400. The observed hold workflow requires an active schedule. Treat a creation-time 404 as inconclusive unless the response confirms that no schedule is active.

`GET /schedule/hold/{dev}` reads the active hold; 404 means none. `DELETE /schedule/hold/{dev}` resumes the schedule. In the test, it returned "Device restored to follow its schedule" and restored the scheduled setpoint. Python treats a missing hold on resume as already complete.

For the hold helper contract, see [runtime workflows](../python/runtime.md#schedule-hold-and-account-workflows).

### Home, Away and Vacation shortcuts

`GET /homes/{home}/scenes` returns `{"Scenes":[...]}`. Default shortcut IDs are `{home}_home`, `{home}_away` and `{home}_vacation`.

Edit a shortcut with `PATCH /scene/{shortcutId}`:

[Shortcut action replacement exchange](../../../examples/cloud/shortcut.replace-actions.json)

The PATCH returned 202. Treat `DeviceActions` as the replacement action map. Use `{"DeviceActions":{},"AllDevices":true}` to clear the action map.

Apply with `POST /homes/shortcut/apply/{shortcutId}` and `{"Timestamp":<Unix seconds>}`; the response was `Ok`. Later shortcut-application results were inconsistent;
acceptance is not confirmation of the resulting thermostat state. The Python client implements reading and editing shortcuts for account cleanup. It has no apply wrapper.

How Mysa's app 4.21.1 creates and fills shortcuts (derived from the app). The
PATCH and the apply request were tested on a Baseboard Lite account in
October 2026; the create request has not been tried.

| Item | What the app sends |
| --- | --- |
| Default shortcuts | When a home has none, three `POST /scene` requests, each `{Home, Name, DeviceActions: {}, AllDevices: true}` with `Name` `"Away"`, `"Home"` or `"Vacation"`. The app does not choose the IDs |
| Edit | `PATCH /scene/{shortcutId}` with `{DeviceActions, AllDevices: false}`, where `DeviceActions` is the existing map with the changed devices merged in |
| Action `type` | 1 setpoint, 2 return to schedule, 3 hold |
| Hold action fields | `mode` (the [control mode enum](cloud-state.md#mode-and-heater-enums)), `holdTillKey`, `secToHold`, `version: "V2"`, and when they apply `setpoint`, `fanSpeed`, `infloorSensor`, `stateChange`, `linking` |
| `holdTillKey` | 0 until changed, 1 until the next schedule event, 2 until a chosen time |
| Apply | `POST /homes/shortcut/apply/{shortcutId}` with `{Timestamp}` in Unix seconds, the current time unless the caller gives one |

The app has no request that deletes a shortcut.

## Adding and removing devices

For Wi-Fi provisioning and its relationship to local pairing and account enrollment, see the [Bluetooth reference](bluetooth.md#scope-and-compatibility). The sections below cover cloud account operations.

### Claiming an older model

This flow applies to Baseboard V1/V2/Lite, In-floor and AC. Account claims use
`POST /devices`; the device need not already be verified online. Mysa's app
3.65.1 then allows up to four minutes for an MQTT echo check (Bluetooth setup
only). App 4.21.1 has no echo check: on every setup path it waits up to five
minutes for a newer `identity` timestamp in the device's cloud state.

All claims contain `Id`, `Model`, `Name`, `Home`, `TimeZone` and `LastPaired`.
`Home` is a home ID, or `"PROPERTY_HOME"` in the legacy property-manager flow.
The supplied name, home and time zone are written to the account record.

| Claim style | Additional fields and behavior |
| --- | --- |
| Minimal legacy claim | AC adds `SetupRequired: true`; Lite adds `schedGlobalOffset: 0` |
| Current legacy-device setup (app 4.21.1) | Adds `Animation: "off"`, `ecoMode: 1`, and `Format` for every model; Baseboard V2 and Lite also set `DutyCycleOpt: 9` |
| Newer credential-based setup | Sends the six fields above plus `Animation`, `ecoMode` and `Format`, and nothing else, after credential transfer; see the [registration sequence](#baseboard-v3-central-hvac-and-climate-sensor) |

For the current legacy-device defaults, AC adds only `SetupRequired: true`; BB1 uses
`DutyCycleOpt: 0`; In-floor uses `DutyCycleOpt: 0`, `FloorOhms: 10`, `FloorType: 1`,
`IsMysaFloorSensor: true`, `MaxFloorTemp: 28`, `MaxSetpoint: 28` and `Tracking: 3`.
Baseboard V1, V2 and In-floor also send `schedGlobalOffset: 1` unless the
app's stored record for the device already has 0, 1 or 30; the Lite always
sends `schedGlobalOffset: 0`. A claim with no model gets the same body as a
newer model.
`ecoMode: 1` means off. These defaults can overwrite settings on re-addition.

A successful re-add returned 201. A 409 means the device is already on the
account: the existing record is returned and the submitted fields are not
written. Legacy replacement uses `POST app-prod /devices/purgeadd`, which still
worked in validation. A 409 recovery flow may write settings separately; that
is a distinct request and should be verified against the resulting record.

What Mysa's app 4.21.1 does around the claim (derived from the app, not tried
on a device):

| Step | App behavior |
| --- | --- |
| Claim answered 409 | The app does not read the returned record. It refreshes its account data, writes `ecoMode: 1` to the device record in a separate request, and carries on with the online check as after a 201. It has no replace request: `/devices/purgeadd` is only in app 3.65.1 |
| Online check | Reads the device's cloud state at once, then every 5 seconds for up to 300 seconds. It passes when `identity.reported.timestamp` is newer than a baseline; if the first read already passes there is no polling. On a timeout setup stops with a verification failure |
| Baseline, Bluetooth setup | The device's `identity.reported.timestamp`, read before the Wi-Fi password is sent. When that read fails or gives no usable timestamp, the phone's time minus 10 seconds |
| Baseline, access-point setup | Always the phone's time minus 10 seconds |

So a thermostat that was online before setup passes only once the cloud shows
a newer `identity` timestamp than the one read just before the password was
sent.

An ownership conflict can produce "User is not allowed to access the specified
home". Its HTTP status and another-account behavior remain unverified. Mysa's
app 4.21.1 treats a claim error whose message contains that text as a
thermostat that belongs to another account. It retries a failed claim only
when the error message contains "No Internet Connection": up to three
retries, waiting 3, 6 and 9 seconds. The
legacy-device claim body contains no possession proof beyond the device ID;
validation covered re-adding a previously owned Lite, not claiming a device
never associated with the account.

`add_device()` returns `True` on success or `False` on 409. The optional
`cloud.account_workflow.async_add_to_account()` uses the minimal claim body so
existing device settings are not overwritten by app defaults. `replace_device()`
uses `purgeadd`, where omitted settings may be reset. Both methods reject
unknown models and families requiring Bluetooth credentials.

### Deletion and re-addition

`DELETE /devices/{dev}` has no body and returned 204. Both hosts worked in the test; Python uses `mysa-backend`.

A complete account-removal workflow includes:

1. Refuse deletion in the UI unless the user is the home's admin.
2. Remove the thermostat's actions from shortcuts.
3. Detach its schedule through `/schedule/{id}/attach/{dev}`, or remove a legacy schedule through `/schedules/v2`.
4. Delete sensor/HVAC links and clear `GroupName` on grouped devices.
5. Delete the account record and remove the device from local app data.

`delete_device()` deletes only the account record. The opt-in
`cloud.account_workflow.async_remove_from_account()` first attempts schedule
and shortcut cleanup, returns a structured result if that best-effort cleanup
fails, and then deletes the account record. Sensor/HVAC links, group membership,
and local app data are outside this helper. The application chooses how to
present cleanup errors; see the [runtime contract](../python/runtime.md).

After deletion, the Lite disappeared from `GET /devices` and bootstrap's `AllowedDevices`. Its individual state endpoint returned 404 with "User does not have access to this device"; the batch endpoint returned `PERMISSION_DENIED` for that ID. The thermostat stayed connected to Mysa and HomeKit control kept working.

Re-adding a previously owned Lite preserved working HomeKit control and restored
cloud access. Supplied claim fields overwrite the settings record: including
`DutyCycleOpt: 9` changed the stored heater type to baseboard while reported
`controlType` remained 8. Omitted brightness, button-timing and voltage settings
returned unchanged, but that does not guarantee retention of every omitted field.

For returning devices, `add_device(app_defaults=False)` uses the minimal claim
without heater defaults. Name, home and time zone are still written; explicit
`heater_type` and `extra` fields are also sent.

### Baseboard V3, Central HVAC and Climate Sensor

These cloud steps supplement the [Bluetooth credential-transfer flow](bluetooth.md#credential-transfer-on-newer-models).
This newer provisioning flow is app-derived and untested on hardware here. See
the [newer-model API reference](new-cloud-models.md) for model and state shapes.

| Stage | Request or condition |
| --- | --- |
| Claim on account | `POST /devices` with base identity/home fields, without legacy heater defaults |
| Verify cloud connection | Wait up to five minutes for a newer `identity` timestamp in the device's cloud state; which request the app makes for that read is not established |
| Finalize pairing | `POST /pairing/finalize` with `{"deviceId":"{dev}"}` |
| Register in OTA fleet | `POST /ota/fleet/devices` with `{"deviceIds":["{dev}"],"fleetName":"<fleet>"}`; fleets are `bb3-prod`, `lv-prod`, `cs-prod` and `whc-prod` (Water Heater Controller) |
| Write display format | The app writes the account's Celsius or Fahrenheit setting to the device |
| Obtain Matter setup code | `GET /matter/qr/{dev}` returns `{qrCode,manualCode}`; requested later from the app's Home Assistants page, not during setup. For a model without Matter the route answers 200 with an `error` field instead (Baseboard Lite, 2026-11-15; see the [Matter reference](matter.md#documented-device-support)) |

The stages are in the order Mysa's app 4.21.1 runs them once Wi-Fi is up,
after credential transfer; fleet registration is its "register device" setup
stage. The app ignores a `/pairing/finalize` error and continues. A failed
fleet registration is shown as a setup failure, but the format write still
runs. Subsequent
firmware management uses the [newer OTA service](firmware-and-connectivity.md#newer-ota-service).

App 4.21.1 builds the fleet name; it holds no list of fleets. The name is the
model's short name in lower case, with `hvac` replaced by `lv`, followed by
`-prod`. The four names in the table follow from that rule and were not each
seen in the app (derived from the app, not tried on a device).

## Additional endpoints

These additional routes have no dedicated `mysa-client` methods unless described earlier.

| Request | Finding |
| --- | --- |
| `GET /homes` | Tested home had `Zones: []` |
| `GET /devices/{dev}/telemetry_alerts` | Lite returned `{"Items":[]}` |
| `GET /smart-early-on/{dev}/availability` | Lite returned `available:false` with `model-not-eligible:BB-V2-0-L`; the server enforces this restriction. On 2026-11-15 the answer was `{available, code, reason, detail}`, with `available: false` and `code: "model-not-eligible"` |
| `GET /smart-early-on/{dev}` | Lite returned `{deviceId, enabled: false}` (2026-11-15) |
| `PUT /smart-early-on/{dev}` | With `{enabled}`, how Mysa's app 4.21.1 turns Smart Early On on or off. Derived from the app; the write has not been tried (the two reads above answered on a Lite) |
| `GET /pairing/eligibility/{dev}` | Returns `{deviceId, canPair, reason, tenantMode}`. A Lite already on the account answered `canPair: true`, `reason: null`, `tenantMode: false` (2026-11-15). In Mysa's app 4.21.1 the reasons `NOT_OWNER` and `NOT_OWNER_TENANT_MODE` block pairing |
| `POST /energy/v3/{home}/home` | Whole-home energy returned 404 on the Lite account. Neither Mysa's app 3.65.1 nor 4.21.1 has an `/energy/v3` route |
| `POST /telemetry/usage/zone/{id}`, `POST /telemetry/usage/home/{id}` | Where Mysa's app 4.21.1 reads zone and home energy, with `{start,end,timezone,resolution}` (`resolution` is `halfHour` or `day`); it reads `total`, `bucketStarts` and per-device `devices[].total` / `energy` as kWh. On the Lite account both routes answered 200 with `{start,end,timezone,resolution,bucketStarts,devices,total}` (2026-11-15); `devices` was empty and `total` 0, since a Lite reports no energy. A home ID on the zone route returned 404 |
| `GET /homes/{home}/savedSchedule` | Returned `{"SavedSchedules":[]}` |
| `GET /schedule/compatibility/{dev}` | Returned `[]`. It accepts `reusable=true` or `false`; a Lite answered `[]` either way, also while a reusable template existed (2026-11-15) |
| `POST /schedule/compatibility` | With `{scheduleId, deviceIds}`, a Lite answered `{compatible: [dev], incompatible: [], incompatibilitySummary: {}}` (2026-11-15) |
| `GET app-prod /devices/state` | Legacy state: timestamped `CorrectedTemp`, `SensorTemp`, `HeatSink`, `Humidity`, `SetPoint`, `Duty`, `OnTime`, `OffTime`, `Lock`, `Brightness`, `Mode`, `TstatMode`, `ScheduleMode`, `HoldTime`, `Rssi`, `Connected`, `Current`, `Voltage`, `FreeHeap`. An In-Floor also has `Infloor` (floor probe, °C) and `TrackedSensor` (3 floor, 5 ambient), per a third-party capture ([bourquep/mysa2mqtt issue 323](https://github.com/bourquep/mysa2mqtt/issues/323)); the device record's `Tracking` uses the same numbering |
| `GET /feature-flags/features/check?versionNumber={clientVersion}` | Returns `{"features":{"<NAME>":{"value":true}}}`; all returned flags in the test were true except `HIDE_MATTER_IN_APP`. For `versionNumber=4.21.1` the route returned 21 flags on 2026-11-15, again all true except that one. The same flags also arrive in the bootstrap response as `featureFlags`; Mysa's app 4.21.1 reads `features[NAME].value` |
| `POST /network-diagnostics/test` | Uploads the phone's `WifiConnection`, `SpeedTest` and `MysaConnectivity` results |
| `GET /network-diagnostics/history` | Reads those uploads; neither diagnostics route probes the thermostat |
| `PATCH /users/{user}` | Sign-in metadata: `LastAppVersion`, `OperatingSystem`, `DeviceType`, `LanguagePreference` |
| `POST app-prod /users/pushNotification` | Registers a push token |

Other GET routes seen include `/homes/{home}/geofences`, `/homes/reminders/{home}`, `/homes/{home}/users`, `/invite/users/{user}`, `/devices/{dev}/drstate` (see [demand-response state](cloud-state.md#demand-response-state)), `/communication-preferences/` and notification preferences under `/preferences/User_{user}_Home_{home}/Notifications`.

Model and firmware flags include `LV_ACCESS`, `LV_AUTOMODE_FW`, `LV_ADAPTIVE_BRIGHTNESS_FW`, `LV_STAGED_AUX_HEAT_FW`, `LV_FREEZE_PROTECTION_ENABLE_FW`, `LV_TEMP_DIFFERENTIAL_FW`, `LV_EXTERNAL_PROBE_FW`, `BB3_SMART_EARLY_ON_ACCESS`, `ENROLL_BB3_DR`, `SMART_EARLY_ON_V2_ACCESS`, `ADAPTIVE_BRIGHTNESS_ACCESS`, `STAGED_AUX_HEAT_ACCESS` and `DR_ENROLMENT_WEB_FLOW_ACCESS`. The 21 flags returned on 2026-11-15 also included `MQTT_HOME_BASED_CLIENT` and three `LEGACY_*_MIGRATION` flags. Their presence in a response does not establish that a particular thermostat supports the feature.

Additional legacy routes include `/energy/device|zone|home/{id}`,
`/batchReadings/v2/{dev}`, `/schedules`, `/schedules/device/update/{dev}` and
`/devices/capabilities`; their tested availability and host differences are recorded in the
[primary cloud reference](cloud.md#read-matrix).
Property-manager requests use `GET /zones` on `dash-api-v1.mysa.cloud`.
`/users/readingsForUser` is a defined but unverified route. Legacy NFC setup
uses `GET /nfc/auth-key`; it has no identified role in current provisioning.

## Retained app-only workflow mechanics

These are request shapes from Mysa's older apps, preserved for interpreting existing
account data. Use the primary reference for live R/L availability. The table
does not claim delivery of alerts, notifications, geofence effects, or invitations.
Mysa's app 3.65.1 uses the verbs given on this page for `/schedules/v2/`,
saved schedules, `/scene`, zones, geofences, reminders and telemetry alerts.
It sends them all to `app-prod.mysa.cloud`, and its DELETE requests carry
their parameters as a JSON body.

| Workflow | App request mechanics |
| --- | --- |
| Telemetry alerts | `GET/PUT/DELETE /devices/{dev}/telemetry_alerts`; PUT sends an alert object, DELETE identifies `ThresholdCondition` and `ThresholdMetric`. Values and bodies are under [Telemetry alert values](#telemetry-alert-values) |
| Zones | `POST /zones/` carries `devices` and `zone`; `POST /zones/{id}` changes `Name`; DELETE removes the zone. App 3.65.1 reads zones with `GET /zones`; app 4.21.1 has no zone read. On a Lite account, creating a zone set the device record's `Zone` and deleting it (204) cleared it; `GET /zones/{id}` takes a zone ID, and a home ID there returned 403 (2026-11-15). On a Baseboard Lite on 2026-11-15, `POST /zones` with `{zone, devices: [dev]}` answered 201 `{Zone: {Id, Name, Owner}}`, `POST /zones/{id}` with `{Name}` answered 200 and echoed the name, and `POST /devices/{dev}/` with `{Zone}` answered 200. The zone routes worked for the Lite although its capabilities say `zoning: false`; what that flag governs is not established. In Mysa's app 4.21.1, adding a device to a zone also attaches the zone's schedule and applies its hold (app-derived, not tested) |
| Geofences | `POST/PATCH/GET/DELETE /geofence/` with optional ID; crossings carry `Users`, `Phone`, `CrossDirection`; app 3.65.1 also has an unauthenticated ("unsecured") background crossing path, not a generic control credential; app 4.21.1 does not have it |
| Reminders | `GET/POST/PUT/DELETE /homes/reminders/` with the relevant ID; PUT creates a reminder and POST with its ID updates (resets) it, in both app 3.65.1 and 4.21.1; writes wrap the reminder in `payload`. On a Baseboard Lite account `GET /homes/reminders/{home}` answered 200 `{reminders: []}` (2026-11-15); no reminder write was tried. In app 4.21.1 the `payload` is `{Frequency, HomeId, ExecutionTime, Event: {UserId, DeviceId, EventType}}`, plus `Id` and `reminderId` (the same value) when the reminder already has an ID. `Frequency` is in seconds, `ExecutionTime` is Unix seconds (now plus `Frequency`), and `EventType` is 2 for a Central HVAC and 1 otherwise (derived from the app) |
| Home records | `POST /homes`, `PUT /homes/{id}` for fields such as `Name` or `Address.PostalCode`, and `DELETE /homes/{id}`. The electricity rate `ERate` is a field of the home record: Mysa's app 4.21.1 reads it there and computes cost itself as kWh × `ERate`; the server sends no cost |
| User profile/preferences | `PATCH /users/{user}` for fields such as `LanguagePreference` or `PrimaryHome`; notification settings use `GET/PUT /preferences/User_{user}_Home_{home}/Notifications` |
| Home access | `GET /homes/{home}/users`; `POST /homes/revokeUserHomeAccess/{user}` carries `Home`; `/users/{user}/updateUserDeviceAccess` carries `DeviceToRevoke`, `DeviceToGrantAccess`, `Home` |
| Invitations | `POST /invite/` carries `Owner`, `Home`, `Invitee`, `Devices`; lists use `/invite/users/{user}` and `/invite/homes/{home}`; `PUT /invite/{id}/` carries `Action` or `Devices` |
| Account deletion/migration | `POST /users/{user}/deleteUser` carries `userSub`, `reason`, `email`, `forgotten`; legacy `POST /homes/migrate` carries `From`, `To`, `Devices` |
| Push/notifications | `/users/pushNotification` registers phone tokens; `/notifications` accepts notification keys; neither establishes delivery to the user |
| Legacy aggregate energy | `POST /energy/device/{dev}`, `/energy/home/{home}`, `/energy/zone/{id}` use `Id`, `Scope`, `Res`, `Timestamp`, `TimeZone`, `PhoneTimezone`; Scope is Day/Week/Month and Res is halfHour/oneHour/Day. Both hosts still answered on 2026-11-15. For a Baseboard Lite, `/energy/device/{dev}` returned `{Id, Scope, Res, Timestamp, TimeZone, TotalEnergy, AverageTemp, AverageHumidity, TotalOnTime, EnergyReadings[], Version}`, each reading with `AvgReturnTemp`, `AvgReturnHumidity`, `AvgInFloorTemp`, `Energy`, `NumOfReadings`, `WeightAvgTotalEnergy`, `WeightAvgOnTime`, `OnTime` and `Timestamp`; `/energy/home/{home}` returned `EnergyReadings: {}` and `Version: "Home V1"`. Every figure was 0 for the Lite, which measures no energy |
| AC anonymous setup | `POST /users/unauth` with `clientId` obtains temporary credentials in the AC IR setup flow of Mysa's older apps; its current permissions are unknown and it is not an established normal-control alternative |

Older shortcut applications can use `/scene/apply/{id}` behind an app flag;
Mysa's current apps use `/homes/shortcut/apply/{id}`. Do not assume every
route an app defines is ever called: Mysa's older app defines
`/users/readingsForUser` but no request to it was found. Property-manager `GET /zones` on its
separate host and Alexa authorization are app services outside this client.

### Telemetry alert values

How Mysa's apps 3.65.1 and 4.21.1 read and write temperature and humidity
alerts at `/devices/{dev}/telemetry_alerts` (derived from the apps). On a
Baseboard Lite the read answered `{"Items":[]}`; no alert write or delete has
been tried on a device.

| Request | Shape |
| --- | --- |
| `GET` | `{Items: [{Value, Version, ThresholdCondition, ThresholdMetric}]}` |
| `PUT` | One request per alert and device, with exactly five keys: `{Version: 1, ThresholdCondition, ThresholdMetric, AlertKey, Value}`. `Value` is in °C for a temperature alert and in percent for humidity. The body holds no device ID |
| `DELETE` | `{ThresholdCondition, ThresholdMetric}` as a JSON body |

`ThresholdCondition` is 1 for a minimum and 2 for a maximum.
`ThresholdMetric` is 1 for temperature and 2 for humidity. `AlertKey` is
`"<metric>_<condition>"`:

| Alert | `AlertKey` | `ThresholdCondition` | `ThresholdMetric` | Default and range in app 4.21.1 |
| --- | --- | --- | --- | --- |
| Too cold | `"1_1"` | 1 | 1 | 7 °C, 1–15 |
| Too hot | `"1_2"` | 2 | 1 | 30 °C, 16–40 |
| Humidity low | `"2_1"` | 1 | 2 | 30 %, 5–95 |
| Humidity high | `"2_2"` | 2 | 2 | 60 %, 5–95 |

For example, a too-hot alert at 30 °C is
`{"Version": 1, "ThresholdCondition": 2, "ThresholdMetric": 1, "AlertKey": "1_2", "Value": 30}`.

[Documentation index](../../README.md)
