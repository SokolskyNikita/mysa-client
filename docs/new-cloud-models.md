# Newer cloud models: API shapes and evidence

This reference records cloud fields and commands for Baseboard V3 (`BB-V3-0`),
Central HVAC (`ST-V1-0`, `ST-V1`, `LV`) and Climate Sensor (`CS-V1-0`, `CS-V1`).
It complements the [account enrollment sequence](cloud-workflows.md#baseboard-v3-central-hvac-and-climate-sensor)
and the general [state reference](cloud-state.md). These findings are
app-derived and have not been verified on newer-model hardware. They describe
observed API shapes; they do not promise that every firmware, account, or client
supports every field.

## Model and capability data

`mysa-client` classifies these model identifiers in
[`const.py`](../src/mysa_client/cloud/const.py). Device state parsing preserves
newer-model fields such as `targetAuto`, HVAC stage reports, fan configuration,
and `hvacState`; see [`models.py`](../src/mysa_client/cloud/models.py).

Capability documents use `features` groups including `climateControl`,
`sensing`, `interface`, `connectivity`, and `smart`, along with `hardware` and
`system` data. Read the capabilities returned for the individual device. Do
not infer a usable feature from a model name or an absent capability document.

The app-derived observations identify HVAC modes by the capability document's
names. In structured state, `modes.mode` uses `CONTROL_MODE_TYPE`; AUTO is 1,
heat is 4, and cool is 3. An AUTO target is represented under `targetAuto` as
`heatSetpoint` and `coolSetpoint`; ordinary heat and cool targets use
`targetHeat.setpoint` and `targetCool.setpoint`. The two AUTO targets form one
range and should be treated together.

## Central HVAC activity and fan fields

The app derives current HVAC activity from reported `modes.hvacStates`, with
`latestTelemetry.reading.hvacState` as another stage report. Its decoded stage
bits are:

| Stage | Bit value |
|---|---:|
| Heat stages 1 and 2 | 256 and 512 |
| Auxiliary heat | 4096 |
| Cool stages 1 and 2 | 1024 and 2048 |
| Fan stage | 8192 |

Treat these as app-derived encodings, not a stable server contract. A selected
mode alone does not show that equipment is active. The state may omit activity
or report it late.

Fan operation and advanced circulation settings use separate fields. The
app's fan-program writes use `hvacConfig.advFanAlwaysOn`,
`hvacConfig.advFanRuntimeSeconds`, and
`hvacConfig.advFanRuntimePeriodSeconds`. A circulation program configured in
Mysa's app can appear as `runtime`. The regular fan-speed enum for newer
capability-based models is `CONTROL_FAN_SPEED`: 0 is off, 1–7 are speeds 1–7,
8 is sleep, and 9 is auto. This enum does not describe the legacy Mini-Split AC
remote mapping in the [AC controls reference](cloud-state.md#ac-fan-speed).

Structured state changes use `POST /state/{dev}/update` with `source: 3`, the
same app source used by other current-client commands. In the package,
[`commands.py`](../src/mysa_client/cloud/commands.py) builds the AUTO target
and fan-configuration changes. Server acceptance is not proof of reported
state or physical operation; apply the normal [write confirmation policy](../src/mysa_client/cloud/README.md#recommended-reconciliation-of-cloud-writes).

## Linking and provisioning

The app exposes a sensor-linking flow for Central HVAC through `GET` and
`POST /linking`, with `sourceId`, `targetId`, and `weight`. Account enrollment,
credential transfer, fleet registration, pairing finalization, and Matter
setup-code routes are documented in the [newer-model enrollment sequence](cloud-workflows.md#baseboard-v3-central-hvac-and-climate-sensor).
That sequence is not a hardware-validated setup guide.

## Evidence and limits

The API field paths, stage decoding, and fan-program fields above come from a
local decompilation of Mysa Android 4.20.1. The inspected
`assets/index.android.bundle` matches the saved APK and has SHA-256
`1cf8a5c0fd83fd1e2a355abd9d22fb70434ca023140d18bc56f5be4927538dde`.
Relevant app code includes `CAPABILITY_PATHS`, `TELEMETRY_PATHS`,
`decodeHvacState`, `getHeatCoolStagesFromModes`, `getValidFanSpeeds`,
`setFanAlwaysOn`, and `setMinimumFanRuntime`.

The Home Assistant repository's
[newer-model fixtures](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/tests/fixtures/app_4201_new_models.json)
are synthetic scenarios built from those app-derived shapes. They are not
captured device responses. The corresponding
[integration tests](https://github.com/SokolskyNikita/mysa-homeassistant/blob/main/tests/test_new_cloud_models.py)
check the integration's parsing, entities, and command behavior against the
synthetic data. They do not establish backend permissions, exact device
capabilities, or physical application.
