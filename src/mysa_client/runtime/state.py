"""A read-only device view composed from caller-selected state sources.

The caller supplies already-normalized local values, fresh MQTT values and a
cloud freshness predicate. No transports, polling or background tasks start.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from mysa_client.cloud import DeviceRecord, DeviceState, Mode, ModelFamily
from mysa_client.cloud.const import EXPERIMENTAL_CLOUD_FAMILIES
from mysa_client.cloud.device import hvac_activity
from mysa_client.cloud.observations import number, valid_value


def _not_fresh(_name: str) -> bool:
    return False


@dataclass(frozen=True, slots=True)
class DeviceView:
    """Semantic readings with explicit local-first and telemetry preferences."""

    state: DeviceState | None = None
    record: DeviceRecord | None = None
    family: ModelFamily | None = None
    local: Mapping[str, Any] = field(default_factory=dict)
    local_ok: bool = False
    cloud_ok: bool = False
    cloud_fresh: Callable[[str], bool] = _not_fresh
    mqtt: Mapping[str, float | None] = field(default_factory=dict)
    telemetry_order: tuple[str, ...] = ("mqtt", "cloud")

    def measurement(self, name: str) -> tuple[float | None, str | None]:
        """Select bounded telemetry, without refreshing its observation clock."""
        for source in self.telemetry_order:
            if source == "mqtt":
                value = self.mqtt.get(name)
            elif source == "cloud" and self.state and self.cloud_fresh(name):
                value = getattr(self.state, name, None)
            else:
                continue
            if valid_value(name, value) and (numeric := number(value)) is not None:
                return numeric, source
        return None, None

    @property
    def temperature(self) -> float | None:
        """Use the local thermostat while healthy, otherwise a fresh cloud reading."""
        if self.local_ok:
            return number(self.local.get("current_temperature"))
        if self.state and self.cloud_fresh("room_temperature"):
            return self.state.room_temperature
        return None

    @property
    def mode(self) -> Mode | None:
        """Translate semantic local modes without importing a transport SDK."""
        if self.local_ok:
            value = self.local.get("target_mode")
            if not isinstance(value, str):
                return None
            return {
                "off": Mode.OFF,
                "heat": Mode.HEAT,
                "cool": Mode.COOL,
                "auto": Mode.AUTO,
            }.get(value)
        return self.state.mode if self.state else None

    @property
    def target(self) -> float | None:
        """The temperature target for the selected operating mode."""
        if self.local_ok:
            return number(self.local.get("target_temperature"))
        return self.state.setpoint_for(self.state.mode) if self.state else None

    @property
    def action(self) -> str | None:
        """Interpret activity without treating an average duty cycle as a relay."""
        mode = self.mode
        if self.family == ModelFamily.CENTRAL_HVAC:
            return (
                hvac_activity(self.state, self.cloud_fresh, mode)
                if self.state
                else None
            )
        if mode == Mode.OFF:
            return "off"
        if self.local_ok:
            value = self.local.get("current_action")
            return str(value) if value in ("heating", "cooling", "idle") else None
        if (
            not self.state
            or self.state.heating is None
            or not self.cloud_fresh("duty_cycle")
        ):
            return None
        if not self.state.heating:
            return "idle"
        return "cooling" if mode == Mode.COOL else "heating"

    @property
    def heat_fraction(self) -> float | None:
        """Live duty cycle, local relay state, or a fresh cloud heating average."""
        live = self.mqtt.get("duty_cycle")
        if valid_value("duty_cycle", live) and (numeric := number(live)) is not None:
            return 0.0 if self.mode is not None and self.mode != Mode.HEAT else numeric
        if self.local_ok:
            action = self.action
            return float(action == "heating") if action is not None else None
        if not self.cloud_ok or not self.state or self.state.mode is None:
            return None
        if self.state.mode != Mode.HEAT:
            return 0.0
        return (
            self.measurement("duty_cycle")[0]
            if self.cloud_fresh("duty_cycle")
            else None
        )

    @property
    def display_unit(self) -> str | None:
        """Local display setting, legacy stored preference, or reported state."""
        if self.local_ok and self.local.get("display_unit") in ("C", "F"):
            return str(self.local["display_unit"])
        if (
            self.family not in EXPERIMENTAL_CLOUD_FAMILIES
            and self.record
            and self.record.display_unit
        ):
            return self.record.display_unit
        return self.state.display_unit if self.state else None
