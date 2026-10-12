"""Enforce the standalone package's protocol and dependency boundaries."""

import ast
from pathlib import Path
import subprocess
import sys

import pytest

import mysa_client

PACKAGE = Path(mysa_client.__file__).parent


@pytest.mark.parametrize("protocol", ["cloud", "homekit"])
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
                other = "homekit" if protocol == "cloud" else "cloud"
                assert not module.startswith(f"mysa_client.{other}"), path
                if protocol == "cloud":
                    assert not module.startswith(("aiohomekit", "zeroconf")), path


def test_cloud_imports_without_optional_dependencies() -> None:
    """A fresh interpreter must never need HA or HomeKit for cloud imports."""
    script = """
import importlib.abc
import sys

class RejectOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'homeassistant', 'aiohomekit', 'zeroconf'}:
            raise AssertionError(f'Unexpected optional import: {fullname}')

sys.meta_path.insert(0, RejectOptional())
import mysa_client
from mysa_client.cloud import MysaAuth, MysaClient
assert 'mysa_client.homekit' not in sys.modules
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
