"""Cloud protocol fixtures without Home Assistant dependencies."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import inspect
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace
from typing import Any, Self

import aiohttp
from aioresponses import CallbackResult, aioresponses
from yarl import URL

from mysa_client.cloud.const import (
    API_BASE,
    COGNITO_URL,
    COGNITO_USER_POOL_ID,
    LEGACY_API_BASE,
)
from mysa_client.cloud.srp import BIG_N, G, K, pad_hex

FIXTURES = Path(__file__).parent / "fixtures"
DEVICE_ID = "a1b2c3d4e5f6"
USER_ID = "00000000-0000-4000-8000-0000000000b1"
HOME_ID = "00000000-0000-4000-8000-0000000000a1"
EMAIL = "user@example.com"
PASSWORD = "hunter2-but-longer"
REFRESH_TOKEN = "refresh-token-1"


class _CompatibleClientResponse(aiohttp.ClientResponse):
    """Bridge the optional aiohttp 3.13 stream-writer constructor argument."""

    def __init__(
        self, method: str, url: URL, *, stream_writer: Any = None, **kwargs: Any
    ) -> None:
        """Initialize aiohttp's response with a minimal outbound writer."""
        if "stream_writer" in inspect.signature(aiohttp.ClientResponse).parameters:
            kwargs["stream_writer"] = stream_writer or SimpleNamespace(output_size=0)
        super().__init__(method, url, **kwargs)


class StandaloneAiohttpMocker:
    """Aioresponses wrapper with the request records used by our tests."""

    def __init__(self) -> None:
        """Create an inactive request mocker."""
        self._mock = aioresponses()

    def __enter__(self) -> Self:
        """Start intercepting requests and return this mocker."""
        self._mock.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Stop intercepting requests and close recorded responses."""
        self._mock.stop()

    def get(self, url: str | re.Pattern[str], **kwargs: Any) -> None:
        """Register a GET response."""
        self._mock.get(self._url_matcher(url), **self._response_kwargs(url, kwargs))

    def post(self, url: str | re.Pattern[str], **kwargs: Any) -> None:
        """Register a POST response."""
        self._mock.post(self._url_matcher(url), **self._response_kwargs(url, kwargs))

    def patch(self, url: str | re.Pattern[str], **kwargs: Any) -> None:
        """Register a PATCH response."""
        self._mock.patch(self._url_matcher(url), **self._response_kwargs(url, kwargs))

    def delete(self, url: str | re.Pattern[str], **kwargs: Any) -> None:
        """Register a DELETE response."""
        self._mock.delete(self._url_matcher(url), **self._response_kwargs(url, kwargs))

    @staticmethod
    def _url_matcher(url: str | re.Pattern[str]) -> str | re.Pattern[str]:
        """Allow protocol calls to add query parameters to a registered path."""
        if isinstance(url, str) and "?" not in url:
            return re.compile(rf"^{re.escape(url)}(?:\?.*)?$")
        return url

    @staticmethod
    def _response_kwargs(
        url: str | re.Pattern[str], kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        """Translate the familiar json keyword into aioresponses' payload."""
        kwargs.setdefault("response_class", _CompatibleClientResponse)
        if str(url) == COGNITO_URL:
            kwargs.setdefault("repeat", True)
        if "json" in kwargs:
            if "payload" in kwargs:
                raise TypeError("Use either json or payload, not both")
            kwargs["payload"] = kwargs.pop("json")
        return kwargs

    @property
    def mock_calls(self) -> list[tuple[str, URL, Any, Any]]:
        """Recorded method, URL, body, and headers for each request."""
        return [
            (method, url, request.kwargs.get("data"), request.kwargs.get("headers", {}))
            for (method, url), requests in self._mock.requests.items()
            for request in requests
        ]

    def clear_requests(self) -> None:
        """Clear request history while leaving registered responses active."""
        self._mock.requests.clear()


def load_json(name: str) -> Any:
    """Load a sanitized protocol fixture."""
    return json.loads((FIXTURES / name).read_text())


def make_jwt(exp: float | None = None, sub: str = USER_ID) -> str:
    """Build an unsigned JWT with the claims inspected by the client."""

    def b64(data: dict[str, Any]) -> str:
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    claims = {"sub": sub, "exp": int(exp or time.time() + 3600), "email": EMAIL}
    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


class FakeCloud:
    """A minimal Cognito and Mysa API simulation for the protocol tests."""

    def __init__(self, mock: StandaloneAiohttpMocker) -> None:
        """Register dynamic Cognito and backend responses."""
        self.mock = mock
        self.state: dict[str, Any] = copy.deepcopy(
            load_json("state_batch.json")[DEVICE_ID]["data"]
        )
        self.devices = load_json("devices.json")
        self.bootstrap = load_json("bootstrap.json")
        self.capabilities = load_json("capabilities_bb_v2_0_l.json")
        self.firmware = {
            "update": False,
            "installedVersion": "3.17.5.13",
            "allowedVersion": "3.17.5.13",
        }
        self.usage = load_json("usage.json")
        self.state_history = load_json("state_history.json")
        self.hold: dict[str, Any] | None = None
        self.schedules: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.settings: list[dict[str, Any]] = []
        self.backend_status: int | None = None
        self.path_status: dict[str, int] = {}
        self.refresh_ok = True
        self.refresh_calls = 0
        self.password = PASSWORD
        self._srp: dict[str, Any] = {}
        self.apply_updates = True
        self.auto_telemetry = True

        base = API_BASE
        device = DEVICE_ID
        mock.post(COGNITO_URL, callback=self._cognito, repeat=True)
        mock.get(
            f"{base}/mobile/bootstrap?versionNumber=4.20.1",
            callback=self._json(lambda: self.bootstrap),
            repeat=True,
        )
        mock.get(
            f"{base}/devices", callback=self._json(lambda: self.devices), repeat=True
        )
        mock.post(f"{base}/state/batch", callback=self._batch, repeat=True)
        mock.get(
            f"{base}/devices/state",
            callback=self._json(lambda: {"DeviceStatesObj": {}}),
            repeat=True,
        )
        mock.get(
            f"{LEGACY_API_BASE}/devices/state",
            callback=self._json(lambda: {"DeviceStatesObj": {}}),
            repeat=True,
        )
        mock.get(
            f"{LEGACY_API_BASE}/devices",
            callback=self._json(lambda: self.devices),
            repeat=True,
        )
        mock.get(
            f"{base}/state/history/{device}",
            callback=self._json(lambda: self.state_history),
            repeat=True,
        )
        mock.post(f"{base}/state/{device}/update", callback=self._update, repeat=True)
        mock.post(f"{base}/devices/{device}/", callback=self._settings, repeat=True)
        mock.get(
            f"{base}/state/{device}",
            callback=self._json(lambda: self.state),
            repeat=True,
        )
        mock.get(
            f"{base}/capabilities/{device}",
            callback=self._json(lambda: self.capabilities),
            repeat=True,
        )
        mock.get(
            f"{base}/devices/update_available/{device}",
            callback=self._json(lambda: self.firmware),
            repeat=True,
        )
        mock.post(
            re.compile(r".*/telemetry/usage/[^/]+$"),
            callback=self._json(lambda: self.usage, method="POST"),
            repeat=True,
        )
        mock.get(f"{base}/schedule/hold/{device}", callback=self._get_hold, repeat=True)
        mock.delete(
            f"{base}/schedule/hold/{device}", callback=self._delete_hold, repeat=True
        )
        mock.post(f"{base}/schedule/hold", callback=self._set_hold_checked, repeat=True)
        mock.get(f"{base}/schedule", callback=self._get_schedules, repeat=True)
        mock.post(f"{base}/schedule", callback=self._create_schedule, repeat=True)
        mock.delete(
            re.compile(r".*/schedule/[^/]+/attach/[^/]+$"),
            callback=self._delete_schedule,
            repeat=True,
        )

    @property
    def on_account(self) -> bool:
        """Whether the test thermostat is on the account."""
        return DEVICE_ID in self.devices["DevicesObj"]

    def _resp(
        self,
        method: str,
        url: URL,
        *,
        status: int = 200,
        body: Any = None,
        text: str | None = None,
    ) -> CallbackResult:
        """Create a response, honoring broad and path-specific error switches."""
        if (
            self.backend_status is not None
            and url.host != "cognito-idp.us-east-1.amazonaws.com"
        ):
            status = self.backend_status
            text = "error"
        if url.path in self.path_status:
            status = self.path_status[url.path]
            text = "refused"
        return CallbackResult(
            method=method,
            status=status,
            payload=body if body is not None and text is None else None,
            body=text or "",
            response_class=_CompatibleClientResponse,
        )

    def _json(self, getter: Any, *, method: str = "GET") -> Any:
        async def handler(url: URL, **_kwargs: Any) -> CallbackResult:
            return self._resp(method, url, body=getter())

        return handler

    def calls(self, method: str, path: str) -> list[Any]:
        """Bodies of recorded requests to one path."""
        return [
            json.loads(data) if isinstance(data, str) and data else data
            for request_method, url, data, _headers in self.mock.mock_calls
            if request_method.lower() == method.lower() and url.path == path
        ]

    async def _cognito(self, url: URL, **kwargs: Any) -> CallbackResult:
        body = json.loads(kwargs.get("data") or "{}")
        if "AuthFlow" in body:
            large_a = int(body["AuthParameters"]["SRP_A"], 16)
            salt = "a1b2c3d4e5f60718"
            identity = f"{COGNITO_USER_POOL_ID.split('_')[1]}{USER_ID}:{self.password}"
            x = int(
                hashlib.sha256(
                    bytes.fromhex(
                        pad_hex(salt) + hashlib.sha256(identity.encode()).hexdigest()
                    )
                ).hexdigest(),
                16,
            )
            verifier = pow(G, x, BIG_N)
            small_b = 987654321
            large_b = (K * verifier + pow(G, small_b, BIG_N)) % BIG_N
            u = int(
                hashlib.sha256(
                    bytes.fromhex(pad_hex(large_a) + pad_hex(large_b))
                ).hexdigest(),
                16,
            )
            s = pow(large_a * pow(verifier, u, BIG_N), small_b, BIG_N)
            prk = hmac.new(
                bytes.fromhex(pad_hex(f"{u:x}")),
                bytes.fromhex(pad_hex(s)),
                hashlib.sha256,
            ).digest()
            self._srp["key"] = hmac.new(
                prk, b"Caldera Derived Key\x01", hashlib.sha256
            ).digest()[:16]
            self._srp["secret"] = base64.b64encode(b"secret-block").decode()
            payload = {
                "ChallengeName": "PASSWORD_VERIFIER",
                "ChallengeParameters": {
                    "USER_ID_FOR_SRP": USER_ID,
                    "USERNAME": USER_ID,
                    "SALT": salt,
                    "SRP_B": f"{large_b:x}",
                    "SECRET_BLOCK": self._srp["secret"],
                },
            }
            return self._resp("POST", url, body=payload)
        if "ChallengeName" in body:
            answers = body["ChallengeResponses"]
            message = (
                COGNITO_USER_POOL_ID.split("_")[1].encode()
                + USER_ID.encode()
                + base64.b64decode(self._srp["secret"])
                + answers["TIMESTAMP"].encode()
            )
            expected = base64.b64encode(
                hmac.new(self._srp["key"], message, hashlib.sha256).digest()
            ).decode()
            if answers["PASSWORD_CLAIM_SIGNATURE"] != expected:
                return self._resp(
                    "POST",
                    url,
                    status=400,
                    body={
                        "__type": "NotAuthorizedException",
                        "message": "Incorrect username or password.",
                    },
                )
            return self._resp(
                "POST",
                url,
                body={
                    "AuthenticationResult": {
                        "IdToken": make_jwt(),
                        "AccessToken": "access",
                        "RefreshToken": REFRESH_TOKEN,
                        "ExpiresIn": 3600,
                    }
                },
            )
        self.refresh_calls += 1
        if not self.refresh_ok:
            return self._resp(
                "POST",
                url,
                status=400,
                body={
                    "__type": "NotAuthorizedException",
                    "message": "Refresh Token has expired",
                },
            )
        return self._resp(
            "POST",
            url,
            body={"AuthenticationResult": {"IdToken": make_jwt(), "ExpiresIn": 3600}},
        )

    async def _batch(self, url: URL, **kwargs: Any) -> CallbackResult:
        if self.auto_telemetry:
            self.state["latestTelemetry"]["reading"]["timestamp"] = time.time()
        if not self.on_account:
            error = {
                "message": "User does not have access",
                "code": "PERMISSION_DENIED",
            }
            payload = {DEVICE_ID: {"error": error}}
        else:
            payload = {DEVICE_ID: {"data": self.state}}
        return self._resp("POST", url, body=payload)

    async def _update(self, url: URL, **kwargs: Any) -> CallbackResult:
        body = json.loads(kwargs.get("data") or "{}")
        self.updates.append(body)
        if self.apply_updates:
            for section, fields in body.items():
                if section == "source":
                    continue
                node = self.state.setdefault(section, {})
                for part in ("desired", "reported"):
                    node.setdefault(part, {}).update(fields)
                    node[part]["timestamp"] = time.time()
        return self._resp("POST", url, body={"message": "ok"})

    async def _settings(self, url: URL, **kwargs: Any) -> CallbackResult:
        body = json.loads(kwargs.get("data") or "{}")
        self.settings.append(body)
        self.devices["DevicesObj"][DEVICE_ID].update(body)
        return self._resp("POST", url, body=body)

    async def _get_hold(self, url: URL, **kwargs: Any) -> CallbackResult:
        if self.hold is None:
            return self._resp(
                "GET", url, status=404, body={"message": "No active hold"}
            )
        return self._resp("GET", url, body=self.hold)

    async def _set_hold_checked(self, url: URL, **kwargs: Any) -> CallbackResult:
        if not self.schedules:
            return self._resp(
                "POST",
                url,
                status=404,
                body={
                    "message": "Device is not following any schedule. Cannot create a hold."
                },
            )
        self.hold = json.loads(kwargs.get("data") or "{}")
        return self._resp("POST", url, status=201, body={"holdId": "h1"})

    async def _delete_hold(self, url: URL, **_kwargs: Any) -> CallbackResult:
        self.hold = None
        return self._resp("DELETE", url, body={"message": "restored"})

    async def _get_schedules(self, url: URL, **_kwargs: Any) -> CallbackResult:
        if not self.schedules:
            return self._resp(
                "GET", url, status=404, body={"message": "Schedule not found"}
            )
        return self._resp("GET", url, body=self.schedules)

    async def _create_schedule(self, url: URL, **kwargs: Any) -> CallbackResult:
        body = json.loads(kwargs.get("data") or "{}")
        body["id"] = f"s{len(self.schedules) + 1}"
        self.schedules.append(body)
        return self._resp("POST", url, status=201, body={"scheduleId": body["id"]})

    async def _delete_schedule(self, url: URL, **_kwargs: Any) -> CallbackResult:
        schedule_id = url.path.split("/")[2]
        self.schedules = [
            schedule for schedule in self.schedules if schedule["id"] != schedule_id
        ]
        return self._resp("DELETE", url, body={"message": "deleted"})
