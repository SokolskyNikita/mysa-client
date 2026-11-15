"""The shared HomeKit-mode to cloud-mode mapping."""

import pytest

from mysa_client.cloud import Mode
from mysa_client.homekit import LocalMode
from mysa_client.runtime.state import (
    LOCAL_MODE_TO_MODE,
    MODE_TO_LOCAL_MODE,
    DeviceView,
)


def test_contents_and_directions() -> None:
    """Four modes each way, matching Mysa Local's LOCAL_MODE_TO_MODE."""
    assert dict(LOCAL_MODE_TO_MODE) == {
        "off": Mode.OFF,
        "heat": Mode.HEAT,
        "cool": Mode.COOL,
        "auto": Mode.AUTO,
    }
    assert dict(MODE_TO_LOCAL_MODE) == {
        Mode.OFF: "off",
        Mode.HEAT: "heat",
        Mode.COOL: "cool",
        Mode.AUTO: "auto",
    }
    for mode in Mode:
        if mode not in MODE_TO_LOCAL_MODE:
            assert mode not in LOCAL_MODE_TO_MODE.values()


def test_local_mode_members_work_both_ways() -> None:
    """LocalMode is a StrEnum: it looks up, and converts back, without a copy."""
    mysa_local = {
        LocalMode.OFF: Mode.OFF,
        LocalMode.HEAT: Mode.HEAT,
        LocalMode.COOL: Mode.COOL,
        LocalMode.AUTO: Mode.AUTO,
    }
    assert set(LocalMode) == set(mysa_local)
    for local, mode in mysa_local.items():
        assert LOCAL_MODE_TO_MODE[local] is mode
        assert LocalMode(MODE_TO_LOCAL_MODE[mode]) is local
        assert MODE_TO_LOCAL_MODE[mode] == local


def test_read_only_and_used_by_device_view() -> None:
    """The maps can't be changed, and DeviceView.mode reads the same table."""
    with pytest.raises(TypeError):
        LOCAL_MODE_TO_MODE["dry"] = Mode.DRY  # type: ignore[index]
    with pytest.raises(TypeError):
        MODE_TO_LOCAL_MODE[Mode.DRY] = "dry"  # type: ignore[index]
    for local, mode in LOCAL_MODE_TO_MODE.items():
        view = DeviceView(local={"target_mode": local}, local_ok=True)
        assert view.mode is mode
    assert DeviceView(local={"target_mode": "dry"}, local_ok=True).mode is None
