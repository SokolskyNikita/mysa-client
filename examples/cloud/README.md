# Sanitized cloud exchanges

These JSON files describe observed or reconstructed request/response shapes;
they are evidence records, not replay scripts. Example IDs match their
filenames. `bodyFile` points to the corresponding fixture when the full body
is kept separately. Values are sanitized, constructed, or app-derived as
described in each record's evidence metadata.

| Example | Description |
| --- | --- |
| [bootstrap.success.json](bootstrap.success.json) | Account, home, and device bootstrap |
| [capabilities.lite.json](capabilities.lite.json) | Baseboard Lite capability response |
| [cognito-refresh.request.json](cognito-refresh.request.json) | Refresh-token request with placeholders |
| [device-claim.minimal.json](device-claim.minimal.json) | Minimal legacy device claim |
| [devices.success.json](devices.success.json) | Legacy account device records |
| [firmware-availability.no-update.json](firmware-availability.no-update.json) | Firmware availability with no offered update |
| [firmware-trigger.empty-object.json](firmware-trigger.empty-object.json) | Accepted firmware trigger request |
| [firmware-trigger.no-body-error.json](firmware-trigger.no-body-error.json) | Firmware trigger without the required body |
| [hold.permanent.json](hold.permanent.json) | Permanent schedule hold |
| [iot-policy.limited.json](iot-policy.limited.json) | Limited IoT policy projection |
| [legacy-state.mixed-time.json](legacy-state.mixed-time.json) | Legacy state with field-level timestamps |
| [live-mode.request.json](live-mode.request.json) | Temporary live-reporting request |
| [schedule-create.full-body.json](schedule-create.full-body.json) | Modern schedule creation |
| [schedule-update.full-body.json](schedule-update.full-body.json) | Full-body modern schedule update |
| [schedule-v1-clear.legacy-host.json](schedule-v1-clear.legacy-host.json) | Legacy V1 schedule clear through the older host |
| [schedule-v1-clear.modern-host.json](schedule-v1-clear.modern-host.json) | Legacy V1 schedule clear through the modern host |
| [schedule-v2-delete.with-body.json](schedule-v2-delete.with-body.json) | Legacy V2 schedule deletion |
| [schedule-v2-update.full-body.json](schedule-v2-update.full-body.json) | Legacy V2 schedule update |
| [shortcut.replace-actions.json](shortcut.replace-actions.json) | Shortcut action-map replacement |
| [state-batch.success.json](state-batch.success.json) | Structured batch-state response |
| [state-history.success.json](state-history.success.json) | Structured state-change history |
| [state-update.ac-fan.json](state-update.ac-fan.json) | AC fan setting request |
| [state-update.ac-swing.json](state-update.ac-swing.json) | Directional swing request |
| [state-update.remote-tracking.json](state-update.remote-tracking.json) | Remote temperature-tracking request |
| [state-update.setpoint.json](state-update.setpoint.json) | Heating setpoint request and acceptance |
| [state.future-telemetry.json](state.future-telemetry.json) | State response with additional telemetry fields |
| [usage.expanded.json](usage.expanded.json) | Expanded usage/history request |
| [usage.summary.json](usage.summary.json) | Usage summary request |

Successful command responses establish request acceptance only. They do not
establish that a thermostat applied a setting or completed a firmware update.
