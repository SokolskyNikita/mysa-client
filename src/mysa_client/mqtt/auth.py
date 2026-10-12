"""Cognito Identity and SigV4 helpers for Mysa's legacy MQTT endpoint."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
from urllib.parse import quote, urlsplit

from .errors import MqttAuthenticationError


@dataclass(frozen=True, slots=True)
class AwsCredentials:
    """Short-lived AWS credentials, hidden from repr to avoid accidental leaks."""

    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    session_token: str = field(repr=False)


def user_pool_sub(id_token: str) -> str:
    """Read the Cognito user-pool subject used as `src.ref` in MCM messages."""
    if len(id_token) > 16_384:
        raise MqttAuthenticationError("The token provider returned an invalid ID token")
    try:
        encoded_payload = id_token.split(".")[1]
        encoded_payload += "=" * (-len(encoded_payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(encoded_payload))
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise MqttAuthenticationError(
            "The token provider returned an invalid ID token"
        ) from None
    subject = claims.get("sub") if isinstance(claims, dict) else None
    if not isinstance(subject, str) or not subject or len(subject) > 256:
        raise MqttAuthenticationError("The ID token has no usable user subject")
    return subject


def get_identity_credentials(
    id_token: str,
    *,
    region: str,
    identity_pool_id: str,
    user_pool_id: str,
) -> AwsCredentials:
    """Exchange a Mysa User Pool token for temporary Identity Pool credentials."""
    try:
        import boto3  # type: ignore[import-untyped]
        from botocore import UNSIGNED  # type: ignore[import-untyped]
        from botocore.config import Config  # type: ignore[import-untyped]
        from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
    except ImportError:
        raise MqttAuthenticationError(
            "MQTT credential exchange requires boto3 and botocore"
        ) from None

    identity_id: str | None = None
    raw: dict[str, object] | None = None
    access_key: object = None
    secret_key: object = None
    session_token: object = None
    try:
        identity = boto3.client(
            "cognito-identity",
            region_name=region,
            config=Config(
                connect_timeout=8,
                read_timeout=8,
                retries={"max_attempts": 0},
                signature_version=UNSIGNED,
            ),
        )
        provider = f"cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        logins = {provider: id_token}
        result = identity.get_id(IdentityPoolId=identity_pool_id, Logins=logins)
        identity_value = result.get("IdentityId")
        if isinstance(identity_value, str) and identity_value:
            identity_id = identity_value
            response = identity.get_credentials_for_identity(
                IdentityId=identity_id,
                Logins=logins,
            )
            raw_value = response.get("Credentials")
            if isinstance(raw_value, dict):
                raw = raw_value
                access_key = raw.get("AccessKeyId")
                secret_key = raw.get("SecretKey")
                session_token = raw.get("SessionToken")
    except (BotoCoreError, ClientError, KeyError, TypeError, ValueError):
        # SDK exceptions may include identity IDs, request data, or token values.
        raise MqttAuthenticationError(
            "Could not obtain temporary MQTT credentials"
        ) from None
    if not isinstance(identity_id, str) or not identity_id:
        raise MqttAuthenticationError("Cognito returned no identity")
    if not isinstance(raw, dict):
        raise MqttAuthenticationError("Cognito returned no temporary credentials")
    if not isinstance(access_key, str) or not access_key:
        raise MqttAuthenticationError("Cognito returned incomplete credentials")
    if not isinstance(secret_key, str) or not secret_key:
        raise MqttAuthenticationError("Cognito returned incomplete credentials")
    if not isinstance(session_token, str) or not session_token:
        raise MqttAuthenticationError("Cognito returned incomplete credentials")
    return AwsCredentials(access_key, secret_key, session_token)


def signed_websocket_path(
    credentials: AwsCredentials,
    *,
    endpoint: str,
    region: str,
    expires: int = 300,
) -> str:
    """Build the SigV4 websocket path without returning the signed URL to callers."""
    try:
        from botocore.auth import SigV4QueryAuth  # type: ignore[import-untyped]
        from botocore.awsrequest import AWSRequest  # type: ignore[import-untyped]
        from botocore.credentials import Credentials  # type: ignore[import-untyped]
        from botocore.exceptions import BotoCoreError
    except ImportError:
        raise MqttAuthenticationError("MQTT signing requires botocore") from None

    try:
        request = AWSRequest(method="GET", url=f"https://{endpoint}/mqtt")
        signer = SigV4QueryAuth(
            Credentials(credentials.access_key_id, credentials.secret_access_key),
            "iotdevicegateway",
            region,
            expires=expires,
        )
        signer.add_auth(request)
        parsed = urlsplit(request.url)
        path = parsed.path or "/mqtt"
        query = parsed.query
        if credentials.session_token:
            query += "&" if query else ""
            query += "X-Amz-Security-Token=" + quote(credentials.session_token, safe="")
    except (BotoCoreError, TypeError, ValueError, AttributeError):
        # Never surface the signed URL or its credential-bearing query string.
        raise MqttAuthenticationError(
            "Could not sign the MQTT websocket request"
        ) from None
    else:
        return f"{path}?{query}" if query else path
