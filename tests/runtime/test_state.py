"""Cross-source selection without Home Assistant or transport SDKs."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from mysa_client.cloud import DeviceState, Mode, ModelFamily
from mysa_client.runtime.state import DeviceView


def state(**changes):
    return replace(DeviceState.from_api("device", {}), **changes)


def test_local_control_and_live_telemetry_have_independent_sources():
    view = DeviceView(
        local_ok=True,
        local={
            "current_temperature": 21,
            "target_mode": "heat",
            "current_action": "heating",
        },
        state=state(room_temperature=18, humidity=35, duty_cycle=0.1),
        cloud_fresh=lambda _: True,
        mqtt={"humidity": 45, "duty_cycle": 0.3},
    )
    assert view.temperature == 21
    assert view.mode == Mode.HEAT
    assert view.action == "heating"
    assert view.measurement("humidity") == (45, "mqtt")
    assert view.heat_fraction == 0.3


def test_missing_local_value_does_not_borrow_cloud_control_value():
    view = DeviceView(
        local_ok=True, state=state(room_temperature=18), cloud_fresh=lambda _: True
    )
    assert view.temperature is None
    assert view.mode is None


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, 101])
def test_invalid_live_humidity_falls_back_to_fresh_cloud(value):
    view = DeviceView(
        state=state(humidity=35), cloud_fresh=lambda _: True, mqtt={"humidity": value}
    )
    assert view.measurement("humidity") == (35, "cloud")
    assert replace(view, cloud_fresh=lambda _: False).measurement("humidity") == (
        None,
        None,
    )


def test_freshness_is_independent_for_hvac_activity():
    view = DeviceView(
        family=ModelFamily.CENTRAL_HVAC,
        state=state(mode=Mode.HEAT, hvac_state=256, room_temperature=21),
        cloud_fresh=lambda name: name == "room_temperature",
    )
    assert view.temperature == 21
    assert view.action is None
    assert replace(view, cloud_fresh=lambda _: True).action == "heating"


def test_cloud_average_is_not_local_relay_state():
    view = DeviceView(
        state=state(mode=Mode.HEAT, duty_cycle=0.4),
        cloud_ok=True,
        cloud_fresh=lambda _: True,
    )
    assert view.heat_fraction == 0.4
    assert replace(view, cloud_ok=False).heat_fraction is None
    assert replace(view, state=state(mode=Mode.COOL)).heat_fraction == 0


def test_selection_does_not_mutate_observation_clock():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    reported = DeviceState.from_api(
        "device",
        {
            "latestTelemetry": {
                "reading": {"timestamp": now.isoformat(), "humidity": 40}
            }
        },
    )
    view = DeviceView(state=reported, cloud_fresh=lambda _: True)
    assert view.measurement("humidity") == (40, "cloud")
    assert reported.observations["humidity"].observed_at == now
