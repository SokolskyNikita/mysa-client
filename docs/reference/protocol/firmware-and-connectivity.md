# Firmware and device connectivity

- [Thermostat startup and connectivity](#thermostat-startup-and-connectivity)
- [Firmware](#firmware)
- [Time, transport, and validation limits](#time-transport-and-validation-limits)
- [Sources](#sources)

This page owns firmware wire mechanics and device network observations. The
[cloud overview](cloud.md) defines S/R/L notation; the
[API inventory](../../api-coverage.md) records method availability.

The protocols were reverse-engineered from the original Mysa apps. Hardware observations cover Baseboard Lite `BB-V2-0-L`, firmware `3.17.5.13`. Other-model behavior remains app/code-derived unless explicitly stated.

## Thermostat startup and connectivity

Observed on Baseboard Lite (`BB-V2-0-L`) firmware `3.17.5.13`:

| Connection | Destination | Behavior |
| --- | --- | --- |
| Network setup | Local Wi-Fi and DHCP | Joins the network and obtains an address independently of cloud availability |
| Time synchronization | `time.nist.gov` → `ntp1.glb.nist.gov`, UDP 123 | Requests time about every 15 seconds |
| Cloud messaging | `a3q27gia9qg3zy-ats.iot.us-east-1.amazonaws.com`, TCP 8883 | Opens an MQTT/TLS connection; the normal connection exchanges keepalive traffic about every minute |
| Firmware check | `devices10010.getmysa.com`, TCP 443 | Contacts this host during startup and after a cloud update trigger; the HTTP path remains unknown |

When cloud connections fail, the thermostat can remain joined to Wi-Fi. MQTT
retries include fresh DNS lookups and different broker addresses, settling to
roughly one retry cycle per minute in the observed outage. A successful MQTT
connection was not required for the startup firmware check. Restoring normal
network access allowed cloud messaging and real-time NTP replies to resume
without another reset.

The firmware HTTPS client rejected an untrusted server certificate before
sending an HTTP request. Neither blocked cloud ports, failed firmware/MQTT DNS
lookups, nor that TLS failure produced an observed HTTP/80 fallback during the
bounded observations. This does not rule
out behavior on other firmware or after a longer outage.

## Firmware

For installed-version records, see the [Python firmware catalog contract](../python/cloud.md#device-record-and-firmware-catalog-reads).

### Legacy devices

For V1/V2/Lite, In-floor and AC, `GET /devices/update_available/{dev}` returns:

[Firmware availability exchange](../../../examples/cloud/firmware-availability.no-update.json)

Both control generations use the same availability endpoint and
`GET /devices/firmware` for installed versions. Their update triggers differ:

| Path | Trigger and completion |
| --- | --- |
| Current HTTPS | `POST /devices/{dev}/firmware-update`; a JSON `{}` body is the form tested here and was accepted. Poll availability until `update` is false |
| Legacy MQTT | `MsgType: 6, EventType: 2`; poll the same availability endpoint, with boot messages `MsgType: 10` or MCM `msg: 61` as additional completion signals |

Mysa's app 3.65.1 has no `/devices/{dev}/firmware-update` route.

The phone requests an update; it does not transfer the firmware image. Reference
clients poll every 10–20 seconds and may repeat the trigger while an update is
available, with a six-minute limit in the current pairing flow. BLE credential
transfer is documented [separately](bluetooth.md#credential-transfer-on-newer-models).

Mysa's app 4.21.1 sends this request with no body and no `Content-Type`
header. The form tested here is a [JSON `{}`](../../../examples/cloud/firmware-trigger.empty-object.json)
body, which was accepted on a Baseboard Lite in October 2026. Whether Mysa
accepts the request without a body was not tested, and neither form has been
tried against an offered update; the
[no-body example](../../../examples/cloud/firmware-trigger.no-body-error.json)
(415) is a constructed example, not a capture.
With no update available, the `{}` trigger returned HTTP 200 with
`{"triggered":true,...}`, followed by incoming traffic on the thermostat's
existing MQTT/TLS connection and an HTTPS connection to
`devices10010.getmysa.com:443`. This identifies the host contacted for the check,
not a firmware-image URL or the app-facing `/ota/*` paths. No firmware download,
reboot or version change occurred; installation of an available release remains
unverified. See [startup and connectivity](#thermostat-startup-and-connectivity)
for the device's network behavior.

`MysaClient.start_firmware_update()` sends the tested JSON `{}` body. The
consuming application owns opt-in UI, polling, and completion handling; see the
HA [firmware workflow](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/docs/cloud-workflows.md#firmware)
for that integration's behavior. Neither the client method nor its offline tests
establish successful installation of an offered release.

### Newer OTA service

The newer fleet-based OTA protocol for V3, Central HVAC and Climate Sensor uses `GET /ota/update/available/{dev}`, `POST /ota/update` with `{deviceIds,versionId,requestSource:"USER",...}`, `GET /ota/update/{requestId}` and `PUT /ota/update/{requestId}/cancel`. Fleet registration happens during pairing.

Mysa's app 4.21.1 chooses this service by model alone: `BB-V3-0`; `ST-V1-0`, `ST-V1` and `LV`; `CS-V1-0` and `CS-V1`; and the water-heater controller `WHC-V1-0` and `WHC-V1`, also in their lower-case spellings `whc-v1-0` and `whc-v1`. Other models use the legacy routes above.

Availability takes an optional `currentVersion` query. The app reads `updateAvailable` (default false), `message` and `firmwareVersion.{id, version, description, releaseNotes}`; `id` is opaque. The Lite returned 404, "No fleet found containing device", from this endpoint, again on 2026-11-15 ([example](../../../examples/cloud/ota-availability.not-in-fleet.json)).

Status returns `status`, one of `QUEUED`, `IN_PROGRESS`, `SUCCEEDED`, `FAILED`, `TIMED_OUT`, `REJECTED`, `REMOVED` or `CANCELED`, and `percentComplete` (0–100). The app's poller does not trust the status alone: it waits for the device's `identity.reported.fw` to equal the target version after trimming, removing a leading `v`, and dropping anything from the first `-`. After a `SUCCEEDED` status it rechecks three more times before accepting it without a match. Everything here except the Lite's 404 is from the app; no fleet device has been observed.

<a id="fleet-update-start-and-poller-in-app-4-21-1"></a>

How Mysa's app 4.21.1 starts and follows a fleet update is below. All of it
is what the app does; none of it has been tried, since no fleet model was
available.

| Step | App 4.21.1 |
| --- | --- |
| Start | `POST /ota/update` with `{deviceIds: [dev], versionId, requestSource: "USER", executionTimeout: {timeoutInMinutes: 5, retries: 0}, failureRetries: 1}`; `versionId` is `firmwareVersion.id` from the availability answer |
| Answer | `requestIds`, an array. The app follows the first ID only, and takes `firmwareVersion.version` as the target version |
| Poll | `GET /ota/update/{requestId}` at once, then every 10 seconds, each time with a read of the device's `identity.reported`. Of the status answer it reads only `status` |
| Success | `identity.reported.fw` matches the target (the comparison above), whatever the status; or the `SUCCEEDED` rechecks above run out |
| Failure without a cancel | `status` is `FAILED`, `TIMED_OUT`, `CANCELED`, `REJECTED` or `REMOVED` and the firmware does not match |
| Timeout | 6 minutes after the start: the app sends the cancel request and reports a failure |
| Failed checks | Six checks in a row that raise an error: cancel and failure |
| Restart on the wrong firmware | The app keeps the `identity.reported.timestamp` it first sees while the status is `IN_PROGRESS`. A newer timestamp while the status is still `IN_PROGRESS` and the firmware still does not match: cancel and failure |
| Cancel | `PUT /ota/update/{requestId}/cancel` with no body |

Python reads availability and status with [`get_fleet_firmware_availability()` and `get_fleet_firmware_status()`](../python/cloud.md#demand-response-fleet-firmware-and-sensor-link-reads), returning the 404 as "not in a fleet", and offers the version comparison as `firmware_versions_match()`. It does not start or cancel fleet updates.

## Time, transport, and validation limits

Cloud telemetry has included implausible future timestamps even when other
readings were current. The cause and recovery time remain unresolved. Treat
future timestamps as invalid for current telemetry selection; HTTP success does
not establish freshness or device clock accuracy.

A Cognito credential attempt on MQTT port 8883 closed during TLS, before MQTT
or subscription. The same app identity connected/subscribed over WebSocket on
443. The thermostat's own credential material was not recovered or reproduced;
app credentials and device credentials remain distinct.

No firmware binary, reusable thermostat private key, or app-side image transfer
was identified by reverse-engineering the original apps. The setup RSA public key encrypts Wi-Fi
credentials, and AC `mqttFileDelivery`/IR signatures concern remote codes, not
thermostat firmware. `CUSTOM_REBOOT_REASONS.REBOOT_URL_MANAGER = 10` is a
reason label, not a command. No request in Mysa's apps sends MCM `REQUEST_REBOOT = 8`;
legacy `MsgType: 10` is a boot notification, and MCM `msg: 10` is a different enum.

The device's firmware-check HTTP path and installation of an offered release
remain unverified. Newer-model OTA, credential transfer, and device-level MQTT
authentication also remain unvalidated.

## Sources

Existing network observations are from October 11–12, 2026. App evidence uses
Android 3.26.0/3.54.1 for legacy notifications and 4.20.1 for HTTPS and fleet OTA.
App behavior does not establish the behavior of the device's downloader. See the
[documentation maintenance guide](../../maintenance.md).

[Documentation index](../../README.md)
