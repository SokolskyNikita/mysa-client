"""Enforce the standalone package's protocol and dependency boundaries."""

import ast
from pathlib import Path
import subprocess
import sys

import pytest

import mysa_client

PACKAGE = Path(mysa_client.__file__).parent


@pytest.mark.parametrize("protocol", ["cloud", "homekit", "mqtt"])
def test_protocol_import_boundary(protocol: str) -> None:
    """Each protocol depends only on itself and declared external libraries."""
    for path in (PACKAGE / protocol).glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert node.level <= 1, path
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [name.name for name in node.names]
            else:
                continue
            for module in modules:
                assert not module.startswith(("homeassistant", "custom_components"))
                for other in {"cloud", "homekit", "mqtt"} - {protocol}:
                    assert not module.startswith(f"mysa_client.{other}"), path
                if protocol == "cloud":
                    assert not module.startswith(
                        ("aiohomekit", "zeroconf", "boto3", "botocore", "paho")
                    ), path


def test_cloud_imports_without_optional_dependencies() -> None:
    """A fresh interpreter must never need HA, HomeKit or MQTT for cloud imports."""
    script = """
import importlib.abc
import sys

class RejectOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'homeassistant', 'aiohomekit', 'zeroconf', 'boto3', 'botocore', 'paho'
        }:
            raise AssertionError(f'Unexpected optional import: {fullname}')

sys.meta_path.insert(0, RejectOptional())
import mysa_client
from mysa_client.cloud import MysaAuth, MysaClient
assert 'mysa_client.homekit' not in sys.modules
assert 'mysa_client.mqtt' not in sys.modules
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and import check
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_typing_marker_is_installed() -> None:
    """Typed consumers need the marker in editable and wheel installs."""
    assert (PACKAGE / "py.typed").is_file()


def test_mqtt_imports_without_transports_or_other_protocols() -> None:
    """Inspecting MQTT types must neither load optional SDKs nor sign in."""
    script = """
import importlib.abc
import sys

class RejectTransport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'homeassistant', 'aiohomekit', 'zeroconf', 'boto3', 'botocore', 'paho'
        } or fullname in {'mysa_client.cloud', 'mysa_client.homekit'}:
            raise AssertionError(f'Unexpected transport import: {fullname}')

sys.meta_path.insert(0, RejectTransport())
from mysa_client.mqtt import MysaMqttClient, parse_message
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and import check
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_entire_distribution_is_framework_independent() -> None:
    """New domain and runtime modules must stay usable outside HA."""
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [name.name for name in node.names]
            else:
                continue
            assert not any(
                module.startswith(("homeassistant", "custom_components"))
                for module in modules
            ), path


def test_runtime_imports_do_not_load_transport_sdks_or_start_tasks() -> None:
    """State, workflow and routing APIs work in a cloud-only installation."""
    script = """
import importlib.abc
import sys

class RejectSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'homeassistant', 'aiohomekit', 'zeroconf', 'boto3', 'botocore', 'paho'}:
            raise AssertionError(f'Unexpected optional import: {fullname}')

sys.meta_path.insert(0, RejectSDK())
from mysa_client.runtime.routing import CommandRouter
from mysa_client.runtime.state import DeviceView
from mysa_client.runtime.telemetry import MqttTelemetryState
from mysa_client.cloud.schedule_workflow import ScheduleWorkflow
from mysa_client.cloud.account_workflow import can_claim
from mysa_client.cloud.device_commands import climate_command
from mysa_client.cloud.reconciliation import PendingCommandTracker
assert 'mysa_client.homekit' not in sys.modules
assert DeviceView().temperature is None
MqttTelemetryState()
PendingCommandTracker()
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and import check
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
