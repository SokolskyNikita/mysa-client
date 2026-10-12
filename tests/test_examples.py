"""Lifecycle checks for the standalone examples, with every boundary mocked."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import runpy
import stat
import sys
from types import SimpleNamespace

import pytest

from mysa_client.homekit import Field, HomeKitEvent


@pytest.fixture
def cloud_example(monkeypatch: pytest.MonkeyPatch):
    """Load the cloud example with an in-memory HTTP session and clients."""
    module = importlib.import_module("examples.cloud_basic")
    session = _FakeSession()
    auth_instances: list[_FakeAuth] = []
    client_instances: list[_FakeCloudClient] = []
    refresh_during_bootstrap: str | None = None

    def set_bootstrap_refresh(value: str | None) -> None:
        nonlocal refresh_during_bootstrap
        refresh_during_bootstrap = value

    class Auth(_FakeAuth):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            auth_instances.append(self)

    class Client(_FakeCloudClient):
        def __init__(self, current_session, auth, **kwargs):
            super().__init__(current_session, auth, **kwargs)
            self.refresh_during_bootstrap = refresh_during_bootstrap
            client_instances.append(self)

    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda: session)
    monkeypatch.setattr(module, "MysaAuth", Auth)
    monkeypatch.setattr(module, "MysaClient", Client)
    return SimpleNamespace(
        module=module,
        session=session,
        auths=auth_instances,
        clients=client_instances,
        set_bootstrap_refresh=set_bootstrap_refresh,
    )


class _FakeSession:
    def __init__(self) -> None:
        self.entered = False
        self.closed = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *_exc_info) -> None:
        self.closed = True


class _FakeAuth:
    def __init__(
        self,
        session,
        refresh_token,
        *,
        user_agent,
        on_refresh_token,
        email=None,
        password=None,
    ) -> None:
        self.session = session
        self.refresh_token = refresh_token
        self.user_agent = user_agent
        self.on_refresh_token = on_refresh_token
        self.email = email
        self.password = password
        self.sign_in_calls: list[tuple[str, str]] = []

    async def sign_in(self, email: str, password: str) -> None:
        self.sign_in_calls.append((email, password))
        self.on_refresh_token("signed-in-refresh-token")


class _FakeCloudClient:
    def __init__(self, session, auth, **kwargs) -> None:
        self.session = session
        self.auth = auth
        self.kwargs = kwargs
        self.refresh_during_bootstrap = None
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def bootstrap(self):
        self.calls.append(("bootstrap", ()))
        if self.refresh_during_bootstrap is not None:
            self.auth.on_refresh_token(self.refresh_during_bootstrap)
        return SimpleNamespace(devices={"device-1": SimpleNamespace(name="Hall")})

    async def get_state(self, device_id: str):
        self.calls.append(("get_state", (device_id,)))
        return SimpleNamespace(
            model="Mysa",
            mode=None,
            room_temperature=20.5,
            heat_setpoint=21,
            reading_time="now",
        )

    async def get_capabilities(self, device_id: str):
        self.calls.append(("get_capabilities", (device_id,)))
        return SimpleNamespace(heat_setpoint_range=(5, 30), modes=())

    async def set_temperature(self, *args) -> None:
        self.calls.append(("set_temperature", args))


@pytest.fixture
def homekit_example(monkeypatch: pytest.MonkeyPatch):
    """Load the HomeKit example with fake discovery and accessory sessions."""
    module = importlib.import_module("examples.homekit_basic")
    zeroconf = _FakeZeroconf()
    protocol = _FakeHomeKitProtocol()
    monkeypatch.setattr(module, "AsyncZeroconf", lambda: zeroconf)
    monkeypatch.setattr(module, "HomeKitProtocol", lambda _zeroconf: protocol)
    return SimpleNamespace(module=module, zeroconf=zeroconf, protocol=protocol)


class _FakeZeroconf:
    def __init__(self) -> None:
        self.closed = False

    async def async_close(self) -> None:
        self.closed = True


class _FakePairingSession:
    def __init__(self, pairing_data: dict[str, object]) -> None:
        self.pairing_data = pairing_data
        self.pin: str | None = None

    async def async_complete(self, pin: str, *, on_pairing_saved):
        self.pin = pin
        await on_pairing_saved(self.pairing_data)
        return self.pairing_data, SimpleNamespace(model="Mysa", firmware="1.2")


class _FakeHomeKitDevice:
    def __init__(self, *, read_error: Exception | None = None) -> None:
        self.read_error = read_error
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.listener = None
        self.unsubscribed = False

    @property
    def temperature_range(self):
        return 7, 28, 1

    def add_listener(self, listener):
        self.listener = listener

        def unsubscribe() -> None:
            self.unsubscribed = True

        return unsubscribe

    async def async_connect(self) -> None:
        self.calls.append(("connect", ()))

    async def async_read(self, fields):
        fields = tuple(fields)
        self.calls.append(("read", fields))
        if self.read_error is not None:
            raise self.read_error
        if self.listener is not None:
            self.listener(
                HomeKitEvent.create({Field.CURRENT_TEMPERATURE: 20.5}, reconnected=True)
            )
        return {
            field: {
                Field.CURRENT_TEMPERATURE: 20.5,
                Field.TARGET_TEMPERATURE: 21,
                Field.CURRENT_ACTION: "heating",
                Field.TARGET_MODE: "heat",
            }[field]
            for field in fields
        }

    async def async_write(self, values) -> None:
        self.calls.append(("write", (values,)))

    async def async_close(self) -> None:
        self.calls.append(("close", ()))


class _FakeHomeKitProtocol:
    def __init__(self, *, read_error: Exception | None = None) -> None:
        self.started = False
        self.stopped = False
        self.discoveries = 0
        self.pairing_data = {"AccessoryPairingID": "AA:BB", "secret": "private"}
        self.pairing_session = _FakePairingSession(self.pairing_data)
        self.device = _FakeHomeKitDevice(read_error=read_error)
        self.loaded_pairing = None

    async def async_start(self) -> None:
        self.started = True

    async def async_stop(self) -> None:
        self.stopped = True

    async def async_discover(self):
        self.discoveries += 1
        yield SimpleNamespace(
            endpoint=None,
            id="AA:BB",
            name="Hall",
            model="Mysa",
            paired=False,
        )

    async def async_start_pairing(self, pairing_id: str):
        assert pairing_id == "AA:BB"
        return self.pairing_session

    def load_pairing(self, pairing_data):
        self.loaded_pairing = pairing_data
        return self.device


def _cloud_args(**changes):
    values = {"device_id": "device-1", "set_celsius": None}
    values.update(changes)
    return SimpleNamespace(**values)


def _homekit_args(pairing_file: Path, **changes):
    values = {
        "discover": False,
        "use_saved_pairing": False,
        "pair_id": None,
        "pin": None,
        "pairing_file": pairing_file,
        "set_target_c": None,
        "listen_seconds": 0,
    }
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("example_name", ["cloud_basic.py", "homekit_basic.py"])
def test_import_and_help_do_not_start_network_sessions(
    example_name: str, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Imports and argparse help stay inert before an explicit operation."""
    import aiohttp
    from zeroconf import asyncio as zeroconf_asyncio

    def unexpected_network_start(*_args, **_kwargs):
        raise AssertionError("help must not start a network session")

    monkeypatch.setattr(aiohttp, "ClientSession", unexpected_network_start)
    monkeypatch.setattr(zeroconf_asyncio, "AsyncZeroconf", unexpected_network_start)
    monkeypatch.setattr(sys, "argv", [example_name, "--help"])
    monkeypatch.setattr("asyncio.run", unexpected_network_start)
    with pytest.raises(SystemExit) as error:
        runpy.run_path(
            str(Path(__file__).parents[1] / "examples" / example_name),
            run_name="__main__",
        )
    assert error.value.code == 0
    assert "usage:" in capsys.readouterr().out


async def test_cloud_sign_in_persists_rotated_token_and_closes_session(
    cloud_example, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """First sign-in stores refreshed credentials and closes its HTTP session."""
    token_file = tmp_path / "config" / "refresh-token"
    monkeypatch.setenv("MYSA_REFRESH_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("MYSA_EMAIL", "person@example.test")
    monkeypatch.setenv("MYSA_PASSWORD", "example-password")
    cloud_example.set_bootstrap_refresh("rotated-refresh-token")

    await cloud_example.module.main(_cloud_args())

    auth = cloud_example.auths[0]
    client = cloud_example.clients[0]
    assert auth.sign_in_calls == [("person@example.test", "example-password")]
    assert token_file.read_text(encoding="utf-8").strip() == "rotated-refresh-token"
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert [name for name, _args in client.calls] == ["bootstrap", "get_state"]
    assert cloud_example.session.entered
    assert cloud_example.session.closed


async def test_cloud_reuses_saved_token_and_closes_session_after_failure(
    cloud_example, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A saved token skips sign-in, while API errors still close the session."""
    token_file = tmp_path / "refresh-token"
    token_file.write_text("saved-refresh-token\n", encoding="utf-8")
    monkeypatch.setenv("MYSA_REFRESH_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("MYSA_EMAIL", raising=False)
    monkeypatch.delenv("MYSA_PASSWORD", raising=False)

    async def fail_bootstrap(_self):
        raise RuntimeError("offline")

    monkeypatch.setattr(_FakeCloudClient, "bootstrap", fail_bootstrap)
    with pytest.raises(RuntimeError, match="offline"):
        await cloud_example.module.main(_cloud_args())

    assert cloud_example.auths[0].refresh_token == "saved-refresh-token"
    assert cloud_example.auths[0].sign_in_calls == []
    assert cloud_example.session.entered
    assert cloud_example.session.closed


async def test_cloud_refreshes_saved_token_without_sign_in(
    cloud_example, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A successful refresh replaces the saved token without asking for a password."""
    token_file = tmp_path / "refresh-token"
    token_file.write_text("saved-refresh-token\n", encoding="utf-8")
    monkeypatch.setenv("MYSA_REFRESH_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("MYSA_EMAIL", raising=False)
    monkeypatch.delenv("MYSA_PASSWORD", raising=False)
    cloud_example.set_bootstrap_refresh("renewed-refresh-token")

    await cloud_example.module.main(_cloud_args())

    assert cloud_example.auths[0].refresh_token == "saved-refresh-token"
    assert cloud_example.auths[0].sign_in_calls == []
    assert token_file.read_text(encoding="utf-8").strip() == "renewed-refresh-token"
    assert [name for name, _args in cloud_example.clients[0].calls] == [
        "bootstrap",
        "get_state",
    ]
    assert cloud_example.session.closed


async def test_homekit_discovery_only_stops_all_sessions(
    homekit_example, tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery-only mode reports results and closes without loading a pairing."""
    monkeypatch.setattr(homekit_example.module, "DISCOVERY_SECONDS", 0.001)
    args = _homekit_args(tmp_path / "pairing.json", discover=True)

    await homekit_example.module.run(args)

    assert homekit_example.protocol.discoveries == 1
    assert homekit_example.protocol.loaded_pairing is None
    assert homekit_example.protocol.started
    assert homekit_example.protocol.stopped
    assert homekit_example.zeroconf.closed
    assert "id=AA:BB" in capsys.readouterr().out


async def test_homekit_discovery_polls_late_snapshot_and_deduplicates(
    homekit_example, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Discovery polls after an empty initial cache and prints an ID once."""
    module = homekit_example.module

    class DelayedDiscovery:
        def __init__(self) -> None:
            self.calls = 0

        async def async_discover(self):
            self.calls += 1
            if self.calls == 1:
                return
            yield SimpleNamespace(
                endpoint=None,
                id="AA:BB",
                name="Hall",
                model="Mysa",
                paired=True,
            )
            yield SimpleNamespace(
                endpoint=None,
                id="aa:bb",
                name="Hall",
                model="Mysa",
                paired=True,
            )

    protocol = DelayedDiscovery()

    async def finish_after_repeated_snapshot(_seconds: float) -> None:
        if protocol.calls == 3:
            raise TimeoutError

    monkeypatch.setattr(module.asyncio, "sleep", finish_after_repeated_snapshot)
    await module.discover(protocol, seconds=5)

    output = capsys.readouterr().out
    assert protocol.calls == 3
    assert output.count("name='Hall'") == 1


async def test_homekit_pairing_persists_then_reads_and_closes(
    homekit_example, tmp_path: Path, capsys
) -> None:
    """Pairing callback stores credentials before a normal read lifecycle."""
    pairing_file = tmp_path / "pairing.json"
    args = _homekit_args(
        pairing_file,
        pair_id="AA:BB",
        pin="123-45-678",
    )

    await homekit_example.module.run(args)

    protocol = homekit_example.protocol
    assert protocol.pairing_session.pin == "123-45-678"
    assert json.loads(pairing_file.read_text(encoding="utf-8")) == protocol.pairing_data
    assert stat.S_IMODE(pairing_file.stat().st_mode) == 0o600
    assert protocol.loaded_pairing == protocol.pairing_data
    assert [name for name, _args in protocol.device.calls] == [
        "connect",
        "read",
        "close",
    ]
    assert protocol.device.unsubscribed
    assert protocol.stopped
    assert homekit_example.zeroconf.closed
    assert '"reconnected": true' in capsys.readouterr().out


async def test_homekit_reuses_saved_pairing_reads_events_and_shuts_down(
    homekit_example, tmp_path: Path, capsys
) -> None:
    """Saved credentials drive connect, read, event, and normal shutdown."""
    pairing_file = tmp_path / "pairing.json"
    saved_data = {"AccessoryPairingID": "AA:BB", "secret": "from-disk"}
    pairing_file.write_text(json.dumps(saved_data), encoding="utf-8")

    await homekit_example.module.run(
        _homekit_args(pairing_file, use_saved_pairing=True)
    )

    protocol = homekit_example.protocol
    assert protocol.loaded_pairing == saved_data
    assert protocol.device.unsubscribed
    assert [name for name, _args in protocol.device.calls] == [
        "connect",
        "read",
        "close",
    ]
    assert protocol.stopped
    assert homekit_example.zeroconf.closed
    assert '"reconnected": true' in capsys.readouterr().out


async def test_homekit_failure_still_unsubscribes_and_shuts_down(
    homekit_example, tmp_path: Path
) -> None:
    """Read failures unsubscribe and close both the accessory and protocol."""
    pairing_file = tmp_path / "pairing.json"
    pairing_file.write_text(
        json.dumps({"AccessoryPairingID": "AA:BB", "secret": "from-disk"}),
        encoding="utf-8",
    )
    homekit_example.protocol.device.read_error = RuntimeError("read failed")

    with pytest.raises(RuntimeError, match="read failed"):
        await homekit_example.module.run(
            _homekit_args(pairing_file, use_saved_pairing=True)
        )

    protocol = homekit_example.protocol
    assert protocol.device.unsubscribed
    assert [name for name, _args in protocol.device.calls] == [
        "connect",
        "read",
        "close",
    ]
    assert protocol.stopped
    assert homekit_example.zeroconf.closed


def test_runtime_example_confirms_synthetic_command_without_network(
    monkeypatch, capsys
):
    """The standalone domain example exercises real helpers without a session."""
    module = importlib.import_module("examples.runtime_basic")
    monkeypatch.setattr(sys, "argv", ["runtime_basic.py"])
    module.main()
    result = json.loads(capsys.readouterr().out)
    assert result["network"] == "none"
    assert result["selected_state"]["temperature"] == 19.8
    assert result["semantic_command"]["expected"] == {"heat_setpoint": 21}
    assert result["confirmation"]["status"] == "confirmed"
