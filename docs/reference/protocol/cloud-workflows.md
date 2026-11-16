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

Read schedules with `GET /schedule?homeId={home}&includeLegacyBaseboard=true&includeLegacyAc=true&includeLegacyInfloor=true`. It returns a list of objects with `id`, `name`, `reusable`, `homeId`, `deviceType`, `deviceIds`, `events`, `createdAt` and `updatedAt`. No schedule returned 404; Python converts that to an empty list.

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
here with `Version` `"3.0.0"` and its events keyed `BB`. Preserve the native metadata;
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
A Lite uses the model key `BB2L`, for example
`{"0":{"1437":{"BB2L":"3|22|%|%|%|%|%"}}}`.

Saved templates are read through `/homes/{home}/savedSchedule`; create at
`POST /homes/savedSchedule/create`, change name/events/apply using
`PUT /homes/savedSchedule/{id}/name`, `/events`, or `/apply`, and delete with
`DELETE /homes/savedSchedule/{id}`. Name, event and apply bodies respectively
carry `Name`, `Events`, and `Devices`. Applying creates separate `V2Schedules`
resources; deleting a template does not remove those device schedules. Event
editing worked on L but returned 404 on R. Do not infer schedule absence from
an empty modern list or an old REC `Schedule` pointer.

For the supported helper and adapter contracts, see [schedule workflow API](../python/runtime.md#schedule-hold-and-account-workflows).

### Holds

With a schedule active, a plain setpoint write held the new value until the next event. Explicit holds use `POST /schedule/hold`:

[Permanent hold exchange](../../../examples/cloud/hold.permanent.json)

For a timed hold, use `"holdType":"temporary"` and add `"endTime":"<ISO UTC timestamp>"`. Both returned 201 with a `holdId`. Changing the hold kept the same ID.

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
| Verify cloud connection | Wait up to five minutes for a newer `identity` timestamp from `GET /state/{dev}` |
| Finalize pairing | `POST /pairing/finalize` with `{"deviceId":"{dev}"}` |
| Register in OTA fleet | `POST /ota/fleet/devices` with `{"deviceIds":["{dev}"],"fleetName":"<fleet>"}`; fleets are `bb3-prod`, `lv-prod`, `cs-prod` and `whc-prod` (Water Heater Controller) |
| Write display format | The app writes the account's Celsius or Fahrenheit setting to the device |
| Obtain Matter setup code | `GET /matter/qr/{dev}` returns `{qrCode,manualCode}`; requested later from the app's Home Assistants page, not during setup |

The stages are in the order Mysa's app 4.21.1 runs them once Wi-Fi is up,
after credential transfer; fleet registration is its "register device" setup
stage. The app ignores a `/pairing/finalize` error and continues. A failed
fleet registration is shown as a setup failure, but the format write still
runs. Subsequent
firmware management uses the [newer OTA service](firmware-and-connectivity.md#newer-ota-service).

## Additional endpoints

These additional routes have no dedicated `mysa-client` methods unless described earlier.

| Request | Finding |
| --- | --- |
| `GET /homes` | Tested home had `Zones: []` |
| `GET /devices/{dev}/telemetry_alerts` | Lite returned `{"Items":[]}` |
| `GET /smart-early-on/{dev}/availability` | Lite returned `available:false` with `model-not-eligible:BB-V2-0-L`; the server enforces this restriction |
| `POST /energy/v3/{home}/home` | Whole-home energy returned 404 on the Lite account. Neither Mysa's app 3.65.1 nor 4.21.1 has an `/energy/v3` route |
| `POST /telemetry/usage/zone/{id}`, `POST /telemetry/usage/home/{id}` | Where Mysa's app 4.21.1 reads zone and home energy, with `{start,end,timezone,resolution}` (`resolution` is `halfHour` or `day`); it reads `total`, `bucketStarts` and per-device `devices[].total` / `energy` as kWh. On the Lite account both routes answered 200 with `{start,end,timezone,resolution,bucketStarts,devices,total}` (2026-11-15); `devices` was empty and `total` 0, since a Lite reports no energy. A home ID on the zone route returned 404 |
| `GET /homes/{home}/savedSchedule` | Returned `{"SavedSchedules":[]}` |
| `GET /schedule/compatibility/{dev}` | Returned `[]` |
| `GET app-prod /devices/state` | Legacy state: timestamped `CorrectedTemp`, `SensorTemp`, `HeatSink`, `Humidity`, `SetPoint`, `Duty`, `OnTime`, `OffTime`, `Lock`, `Brightness`, `Mode`, `TstatMode`, `ScheduleMode`, `HoldTime`, `Rssi`, `Connected`, `Current`, `Voltage`, `FreeHeap`. An In-Floor also has `Infloor` (floor probe, °C) and `TrackedSensor` (3 floor, 5 ambient), per a third-party capture ([bourquep/mysa2mqtt issue 323](https://github.com/bourquep/mysa2mqtt/issues/323)); the device record's `Tracking` uses the same numbering |
| `GET /feature-flags/features/check?versionNumber={clientVersion}` | Returns `{"features":{"<NAME>":{"value":true}}}`; all returned flags in the test were true except `HIDE_MATTER_IN_APP` |
| `POST /network-diagnostics/test` | Uploads the phone's `WifiConnection`, `SpeedTest` and `MysaConnectivity` results |
| `GET /network-diagnostics/history` | Reads those uploads; neither diagnostics route probes the thermostat |
| `PATCH /users/{user}` | Sign-in metadata: `LastAppVersion`, `OperatingSystem`, `DeviceType`, `LanguagePreference` |
| `POST app-prod /users/pushNotification` | Registers a push token |

Other GET routes seen include `/homes/{home}/geofences`, `/homes/reminders/{home}`, `/homes/{home}/users`, `/invite/users/{user}`, `/devices/{dev}/drstate` (see [demand-response state](cloud-state.md#demand-response-state)), `/communication-preferences/` and notification preferences under `/preferences/User_{user}_Home_{home}/Notifications`.

Model and firmware flags include `LV_ACCESS`, `LV_AUTOMODE_FW`, `LV_ADAPTIVE_BRIGHTNESS_FW`, `LV_STAGED_AUX_HEAT_FW`, `LV_FREEZE_PROTECTION_ENABLE_FW`, `LV_TEMP_DIFFERENTIAL_FW`, `LV_EXTERNAL_PROBE_FW`, `BB3_SMART_EARLY_ON_ACCESS`, `ENROLL_BB3_DR`, `SMART_EARLY_ON_V2_ACCESS`, `ADAPTIVE_BRIGHTNESS_ACCESS`, `STAGED_AUX_HEAT_ACCESS` and `DR_ENROLMENT_WEB_FLOW_ACCESS`. Their presence in a response does not establish that a particular thermostat supports the feature.

Additional legacy routes include `/energy/device|zone|home/{id}`,
`/batchReadings/v2/{dev}`, `/schedules`, `/schedules/device/update/{dev}` and
`/devices/capabilities`; their tested availability and host differences are recorded in the
[primary cloud reference](cloud.md#read-matrix).
Property-manager requests use `GET /zones` on `dash-api-v1.mysa.cloud`.
`/users/readingsForUser` is a defined but unverified route. Legacy NFC setup
uses `GET /nfc/auth-key`; it has no identified role in current provisioning.

## Retained app-only workflow mechanics

These are recovered legacy app request shapes, preserved for interpreting existing
account data. Use the primary reference for live R/L availability. The table
does not claim delivery of alerts, notifications, geofence effects, or invitations.
Mysa's app 3.65.1 uses the verbs given on this page for `/schedules/v2/`,
saved schedules, `/scene`, zones, geofences, reminders and telemetry alerts.
It sends them all to `app-prod.mysa.cloud`, and its DELETE requests carry
their parameters as a JSON body.

| Workflow | App request mechanics |
| --- | --- |
| Telemetry alerts | `GET/PUT/DELETE /devices/{dev}/telemetry_alerts`; PUT sends an alert object, DELETE identifies `ThresholdCondition` and `ThresholdMetric` |
| Zones | `POST /zones/` carries `devices` and `zone`; `POST /zones/{id}` changes `Name`; DELETE removes the zone. App 3.65.1 reads zones with `GET /zones`; app 4.21.1 has no zone read. On a Lite account, creating a zone set the device record's `Zone` and deleting it (204) cleared it; `GET /zones/{id}` takes a zone ID, and a home ID there returned 403 (2026-11-15) |
| Geofences | `POST/PATCH/GET/DELETE /geofence/` with optional ID; crossings carry `Users`, `Phone`, `CrossDirection`; app 3.65.1 also has an unauthenticated ("unsecured") background crossing path, not a generic control credential; app 4.21.1 does not have it |
| Reminders | `GET/POST/PUT/DELETE /homes/reminders/` with the relevant ID; PUT creates a reminder and POST with its ID updates (resets) it, in both app 3.65.1 and 4.21.1; writes wrap the reminder in `payload` |
| Home records | `POST /homes`, `PUT /homes/{id}` for fields such as `Name` or `Address.PostalCode`, and `DELETE /homes/{id}`. The electricity rate `ERate` is a field of the home record: Mysa's app 4.21.1 reads it there and computes cost itself as kWh × `ERate`; the server sends no cost |
| User profile/preferences | `PATCH /users/{user}` for fields such as `LanguagePreference` or `PrimaryHome`; notification settings use `GET/PUT /preferences/User_{user}_Home_{home}/Notifications` |
| Home access | `GET /homes/{home}/users`; `POST /homes/revokeUserHomeAccess/{user}` carries `Home`; `/users/{user}/updateUserDeviceAccess` carries `DeviceToRevoke`, `DeviceToGrantAccess`, `Home` |
| Invitations | `POST /invite/` carries `Owner`, `Home`, `Invitee`, `Devices`; lists use `/invite/users/{user}` and `/invite/homes/{home}`; `PUT /invite/{id}/` carries `Action` or `Devices` |
| Account deletion/migration | `POST /users/{user}/deleteUser` carries `userSub`, `reason`, `email`, `forgotten`; legacy `POST /homes/migrate` carries `From`, `To`, `Devices` |
| Push/notifications | `/users/pushNotification` registers phone tokens; `/notifications` accepts notification keys; neither establishes delivery to the user |
| Legacy aggregate energy | `POST /energy/device/{dev}`, `/energy/home/{home}`, `/energy/zone/{id}` use `Id`, `Scope`, `Res`, `Timestamp`, `TimeZone`, `PhoneTimezone`; Scope is Day/Week/Month and Res is halfHour/oneHour/Day |
| AC anonymous setup | `POST /users/unauth` with `clientId` obtains temporary credentials in the recovered AC IR setup flow; its current permissions are unknown and it is not an established normal-control alternative |

Older shortcut applications can use `/scene/apply/{id}` behind an app flag;
current recovered clients use `/homes/shortcut/apply/{id}`. Do not assume every
route declared in a bundle has a live caller: `/users/readingsForUser` was defined
without an identified old-app call site. Property-manager `GET /zones` on its
separate host and Alexa authorization are app services outside this client.

[Documentation index](../../README.md)
