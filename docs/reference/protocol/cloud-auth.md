# Cloud authentication

- [HTTP authorization](#http-authorization)
- [Connection and authentication](#connection-and-authentication)

This page records the observed Cognito exchange and HTTP authentication mechanics used by Mysa's cloud services. The [cloud protocol reference](cloud.md) describes endpoint availability, S/R/L terminology, limits, and how `mysa-client` uses the authentication API. These findings are reverse-engineered observations, not a vendor contract.

The protocols were reverse-engineered from the original Mysa apps. Hardware observations cover Baseboard Lite `BB-V2-0-L`, firmware `3.17.5.13`. Other-model behavior remains app/code-derived unless explicitly stated.

## HTTP authorization

<a id="shared-authentication"></a>

All S/R/L HTTPS paths use the same Cognito ID token, sent raw in
`authorization` with no `Bearer` prefix. Send `accept: application/json`,
a descriptive `user-agent`, and `content-type: application/json` for JSON
bodies. R and L do not require a separate login or AWS request signature.

## Connection and authentication

### Service hosts

| Host | Use |
| --- | --- |
| `mysa-backend.mysa.cloud` | Current HTTPS API and migrated legacy REST routes |
| `app-prod.mysa.cloud` | Legacy REST host; still advertised as `regionApiBaseUrl`, with some push-registration and account routes |
| `cognito-idp.us-east-1.amazonaws.com` | Sign-in and token refresh |
| `i18n.mysa.cloud` | Static country and time-zone lists |
| `chunk.mysa.cloud/track` | Analytics |

Routing depends on feature flags: `MONOLITH_API_ACCESS` moves legacy routes to
`mysa-backend`, and `DISABLE_LEGACY_FALLBACK` disables retrying them on `app-prod`.
Both were enabled for the tested account. A host name alone therefore does not
identify the protocol family. Service aliases are defined in the [cloud overview](cloud.md#context-and-conventions);
client fallback is part of the [HTTP contract](../python/cloud.md#errors-timeouts-retries-and-caller-responsibilities).

### Cognito sign-in

The current and legacy clients use the same User Pool, but the app-client and
Identity Pool configuration differs. These are public application identifiers;
the table does not contain a user's Cognito `IdentityId`:

| Setting | Value |
| --- | --- |
| AWS region | `us-east-1` |
| User pool | `us-east-1_GUFWfhI7g` |
| Modern app client | `6cktj934gasnc72f7jo2cmf6rt` |
| Modern Identity Pool | `us-east-1:e27f246e-ad23-41ce-ac80-eda86f8be424` |
| Legacy app client (app 3.26/3.54) | `19efs8tgqe942atbqmot5m36t3` |
| Legacy Identity Pool (app 3.26/3.54) | `us-east-1:ebd95d52-9995-45da-b059-56b865a18379` |

For the dated MQTT comparison of these app-client and Identity Pool
configurations, see the [MQTT authorization evidence](../../evidence/mqtt.md#dated-authorization-evidence).

The modern app client has no client secret. Cognito requests go to `https://cognito-idp.us-east-1.amazonaws.com/` with:

```http
X-Amz-Target: AWSCognitoIdentityProviderService.<Action>
Content-Type: application/x-amz-json-1.1
```

No AWS request signature is required for these user-pool calls. Sign-in uses `InitiateAuth` with `AuthFlow: "USER_SRP_AUTH"` and `AuthParameters` containing `USERNAME` and `SRP_A`. A `PASSWORD_VERIFIER` challenge follows. `RespondToAuthChallenge` sends `PASSWORD_CLAIM_SIGNATURE`, `PASSWORD_CLAIM_SECRET_BLOCK`, `TIMESTAMP` and `USERNAME` as `ChallengeResponses`, plus the challenge session if supplied. The response contains `IdToken`, `AccessToken`, `RefreshToken` and `ExpiresIn: 3600`.

The Python SRP implementation uses Cognito's 3072-bit group, SHA-256 and a 16-byte derived authentication key. Use the challenge's `USER_ID_FOR_SRP` when computing the response, even when sign-in began with an email address. Cognito also expects an English UTC timestamp with an unpadded day, for example `Fri Jan 2 03:04:05 UTC 2026`. The [SRP implementation](../../../src/mysa_client/cloud/srp.py) contains the calculation.

For token persistence and supported challenges, see the Python
[authentication](../python/cloud.md#authentication) and
[error](../python/cloud.md#errors-timeouts-retries-and-caller-responsibilities) contracts.

Refresh uses `GetTokensFromRefreshToken` with:

[Cognito refresh request (credential placeholder only)](../../../examples/cloud/cognito-refresh.request.json)

The observed refresh response supplied ID and access tokens with a one-hour
lifetime and no replacement refresh token. The Identity Pool is not needed for
HTTPS; the examined mobile app uses it for SNS push registration. The MQTT
client separately exchanges the User Pool ID token through an Identity Pool;
see the [MQTT reference](mqtt.md#connection-and-authorization-model).

For HTTP errors and retries, see the [cloud error reference](../python/cloud.md#errors-timeouts-retries-and-caller-responsibilities).
The examined mobile app treats 409 as success generally; the Python client handles it specially
only for [device claims](cloud-workflows.md#claiming-an-older-model).

### Browser access

The tested APIs do not support ordinary cross-origin browser access:

| Service | Browser behavior |
| --- | --- |
| Cognito | Browser sign-in allowed |
| `mysa-backend` | Authorization/Content-Type preflights returned 404; actual responses also lacked `Access-Control-Allow-Origin` |
| `app-prod` | Legacy-route preflights allowed access, but actual responses lacked `Access-Control-Allow-Origin`; newer pairing, state and bootstrap routes returned 403 |

Successful Cognito sign-in therefore does not make Mysa's API responses readable
by a webpage. Cloud requests need a native client, a server-side process, or an
extension with the appropriate host permissions.

[Documentation index](../../README.md)
