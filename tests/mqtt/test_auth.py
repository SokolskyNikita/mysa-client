"""Tests for optional MQTT Cognito and SigV4 authentication helpers."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from urllib.parse import parse_qs, urlsplit

import pytest

from mysa_client.mqtt.auth import (
    AwsCredentials,
    MqttAuthenticationError,
    get_identity_credentials,
    signed_websocket_path,
    user_pool_sub,
)


def _token(claims: object) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_user_pool_sub_reads_subject_from_id_token() -> None:
    """The MCM app source reference comes from the user-pool `sub` claim."""
    assert user_pool_sub(_token({"sub": "user-subject"})) == "user-subject"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-jwt",
        "header.!!!!.signature",
        _token([]),
        _token({"sub": ""}),
        _token({"sub": "x" * 257}),
        "x" * 16_385,
    ],
)
def test_user_pool_sub_rejects_malformed_or_unusable_claims(token: str) -> None:
    """Bad token data is reduced to the public MQTT authentication error."""
    with pytest.raises(
        MqttAuthenticationError, match=r"invalid ID token|usable user subject"
    ):
        user_pool_sub(token)


class _FakeBotoCoreError(Exception):
    """Minimal typed SDK error accepted by the sanitized error handler."""


def _install_fake_cognito(
    monkeypatch: pytest.MonkeyPatch,
    identity_client: object,
) -> tuple[object, object]:
    """Install a small boto3/botocore seam without real AWS calls."""
    unsigned = object()
    client_calls: list[tuple[str, dict[str, object]]] = []

    class FakeConfig:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    def make_client(service_name: str, **kwargs: object) -> object:
        client_calls.append((service_name, kwargs))
        return identity_client

    boto3_module = ModuleType("boto3")
    boto3_module.client = make_client  # type: ignore[attr-defined]
    botocore_module = ModuleType("botocore")
    botocore_module.UNSIGNED = unsigned  # type: ignore[attr-defined]
    config_module = ModuleType("botocore.config")
    config_module.Config = FakeConfig  # type: ignore[attr-defined]
    exceptions_module = ModuleType("botocore.exceptions")
    exceptions_module.BotoCoreError = _FakeBotoCoreError  # type: ignore[attr-defined]
    exceptions_module.ClientError = _FakeBotoCoreError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "boto3", boto3_module)
    monkeypatch.setitem(sys.modules, "botocore", botocore_module)
    monkeypatch.setitem(sys.modules, "botocore.config", config_module)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exceptions_module)
    return client_calls, unsigned


def test_get_identity_credentials_uses_unsigned_cognito_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cognito identity exchange is anonymous and uses the Mysa User Pool token."""
    token = _token({"sub": "user-subject"})
    identity_id = "us-east-1:identity-private"
    temporary = {
        "AccessKeyId": "ACCESS_KEY_PRIVATE",
        "SecretKey": "SECRET_KEY_PRIVATE",
        "SessionToken": "SESSION_TOKEN_PRIVATE",
    }

    class FakeIdentityClient:
        calls: list[tuple[str, dict[str, object]]] = []

        def get_id(self, **kwargs: object) -> dict[str, str]:
            self.calls.append(("get_id", kwargs))
            return {"IdentityId": identity_id}

        def get_credentials_for_identity(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(("get_credentials_for_identity", kwargs))
            return {"Credentials": temporary}

    fake_identity = FakeIdentityClient()
    client_calls, unsigned = _install_fake_cognito(monkeypatch, fake_identity)

    credentials = get_identity_credentials(
        token,
        region="us-east-1",
        identity_pool_id="us-east-1:pool-id",
        user_pool_id="us-east-1_user-pool",
    )

    assert len(client_calls) == 1
    service, options = client_calls[0]
    assert service == "cognito-identity"
    assert options["region_name"] == "us-east-1"
    config = options["config"]
    assert config is not None
    config_kwargs = vars(config)["kwargs"]
    assert config_kwargs["signature_version"] is unsigned
    assert config_kwargs["connect_timeout"] == 8
    assert config_kwargs["read_timeout"] == 8
    assert fake_identity.calls == [
        (
            "get_id",
            {
                "IdentityPoolId": "us-east-1:pool-id",
                "Logins": {
                    "cognito-idp.us-east-1.amazonaws.com/us-east-1_user-pool": token
                },
            },
        ),
        (
            "get_credentials_for_identity",
            {
                "IdentityId": identity_id,
                "Logins": {
                    "cognito-idp.us-east-1.amazonaws.com/us-east-1_user-pool": token
                },
            },
        ),
    ]
    assert credentials == AwsCredentials(
        "ACCESS_KEY_PRIVATE", "SECRET_KEY_PRIVATE", "SESSION_TOKEN_PRIVATE"
    )
    assert "ACCESS_KEY_PRIVATE" not in repr(credentials)
    assert "SECRET_KEY_PRIVATE" not in repr(credentials)
    assert "SESSION_TOKEN_PRIVATE" not in repr(credentials)


def test_identity_exchange_errors_do_not_expose_token_or_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SDK errors can contain request data, but the public error stays generic."""
    token = _token({"sub": "TOKEN_MARKER_PRIVATE"})
    identity_marker = "IDENTITY_MARKER_PRIVATE"

    class FailingIdentityClient:
        def get_id(self, **_kwargs: object) -> dict[str, str]:
            return {"IdentityId": identity_marker}

        def get_credentials_for_identity(self, **_kwargs: object) -> dict[str, object]:
            raise _FakeBotoCoreError(f"{identity_marker} {token}")

    fake_identity = FailingIdentityClient()
    _install_fake_cognito(monkeypatch, fake_identity)

    with pytest.raises(MqttAuthenticationError) as error:
        get_identity_credentials(
            token,
            region="us-east-1",
            identity_pool_id="us-east-1:pool-id",
            user_pool_id="us-east-1_user-pool",
        )

    assert str(error.value) == "Could not obtain temporary MQTT credentials"
    assert token not in str(error.value)
    assert identity_marker not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"Credentials": {}},
        {"Credentials": {"AccessKeyId": "ak", "SecretKey": "sk"}},
    ],
)
def test_identity_exchange_rejects_incomplete_credentials(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
) -> None:
    """Temporary AWS credentials must include all three secret values."""

    class IncompleteIdentityClient:
        def get_id(self, **_kwargs: object) -> dict[str, str]:
            return {"IdentityId": "identity"}

        def get_credentials_for_identity(self, **_kwargs: object) -> dict[str, object]:
            return response

    _install_fake_cognito(monkeypatch, IncompleteIdentityClient())
    with pytest.raises(MqttAuthenticationError, match=r"no temporary|incomplete"):
        get_identity_credentials(
            _token({"sub": "user-subject"}),
            region="us-east-1",
            identity_pool_id="us-east-1:pool-id",
            user_pool_id="us-east-1_user-pool",
        )


def test_signed_websocket_path_encodes_session_token() -> None:
    """SigV4 output carries its temporary session token as encoded query data."""
    pytest.importorskip("botocore")
    token = "session /+&=marker"
    path = signed_websocket_path(
        AwsCredentials("ACCESS_KEY", "SECRET_KEY_PRIVATE", token),
        endpoint="mqtt.example.invalid",
        region="us-east-1",
        expires=120,
    )

    parsed = urlsplit(path)
    query = parse_qs(parsed.query)
    assert parsed.path == "/mqtt"
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["120"]
    assert query["X-Amz-Signature"]
    assert query["X-Amz-Security-Token"] == [token]
    assert token not in path
    assert "SECRET_KEY_PRIVATE" not in path


def test_missing_optional_dependencies_have_safe_actionable_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing extras produce install guidance without exposing request data."""
    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(
        MqttAuthenticationError,
        match="MQTT credential exchange requires boto3 and botocore",
    ):
        get_identity_credentials(
            "TOKEN_PRIVATE",
            region="us-east-1",
            identity_pool_id="us-east-1:pool-id",
            user_pool_id="us-east-1_user-pool",
        )

    for module_name in (
        "botocore",
        "botocore.auth",
        "botocore.awsrequest",
        "botocore.credentials",
        "botocore.exceptions",
    ):
        monkeypatch.setitem(sys.modules, module_name, None)
    with pytest.raises(MqttAuthenticationError, match="MQTT signing requires botocore"):
        signed_websocket_path(
            AwsCredentials("ACCESS_PRIVATE", "SECRET_PRIVATE", "TOKEN_PRIVATE"),
            endpoint="mqtt.example.invalid",
            region="us-east-1",
        )


def test_importing_auth_helpers_does_not_load_optional_runtime_dependencies() -> None:
    """Auth data helpers remain importable without boto3, botocore or Paho."""
    package_root = str(Path(__file__).resolve().parents[2] / "src")
    script = f"""
import importlib.abc
import sys
sys.path.insert(0, {package_root!r})

class RejectOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {{'boto3', 'botocore', 'paho'}}:
            raise AssertionError(f'Unexpected optional import: {{fullname}}')

sys.meta_path.insert(0, RejectOptional())
from mysa_client.mqtt.auth import user_pool_sub
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'paho' not in sys.modules
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and inline import check
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
