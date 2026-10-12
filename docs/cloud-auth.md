# Cloud authentication

This page records the observed Cognito exchange and HTTP authentication mechanics used by Mysa's cloud services. The [cloud protocol reference](../src/mysa_client/cloud/README.md) describes endpoint availability, S/R/L terminology, limits, and how `mysa-client` uses the authentication API. These findings are reverse-engineered observations, not a vendor contract.

The protocols were reverse-engineered from the original Mysa apps. Hardware observations cover Baseboard Lite `BB-V2-0-L`, firmware `3.17.5.13`. Other-model behavior remains app/code-derived unless explicitly stated.

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
identify the protocol family. The client's host selection is described in the
[cloud guide](../src/mysa_client/cloud/README.md#how-it-is-organized).

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

The October 12 MQTT comparison found that the same modern User Pool token
could be exchanged through either Identity Pool, but only the legacy pool
passed the tested MQTT echo exchange. The legacy app-client token worked with
the legacy pool and was rejected by the modern pool with `NotAuthorizedException`.
The modern pool's attached IoT policy was inspected; inspecting the legacy
identity's policy returned `AccessDeniedException`, so its policy and effective
IAM permissions remain unknown. See the detailed
[MQTT evidence](mqtt.md#october-12-pool-comparison-and-device-control).

The modern app client has no client secret. Cognito requests go to `https://cognito-idp.us-east-1.amazonaws.com/` with:

```http
X-Amz-Target: AWSCognitoIdentityProviderService.<Action>
Content-Type: application/x-amz-json-1.1
```

No AWS request signature is required for these user-pool calls. Sign-in uses `InitiateAuth` with `AuthFlow: "USER_SRP_AUTH"` and `AuthParameters` containing `USERNAME` and `SRP_A`. A `PASSWORD_VERIFIER` challenge follows. `RespondToAuthChallenge` sends `PASSWORD_CLAIM_SIGNATURE`, `PASSWORD_CLAIM_SECRET_BLOCK`, `TIMESTAMP` and `USERNAME` as `ChallengeResponses`, plus the challenge session if supplied. The response contains `IdToken`, `AccessToken`, `RefreshToken` and `ExpiresIn: 3600`.

The Python SRP implementation uses Cognito's 3072-bit group, SHA-256 and a 16-byte derived authentication key. Use the challenge's `USER_ID_FOR_SRP` when computing the response, even when sign-in began with an email address. Cognito also expects an English UTC timestamp with an unpadded day, for example `Fri Jan 2 03:04:05 UTC 2026`. The [SRP implementation](../src/mysa_client/cloud/srp.py) contains the calculation.

API authorization headers, token handling and supported challenges are described
in the cloud guide: [authentication](../src/mysa_client/cloud/README.md#using-the-client) and [request handling](../src/mysa_client/cloud/README.md#errors-timeouts-retries-and-caller-responsibilities).

Refresh uses `GetTokensFromRefreshToken` with:

[Cognito refresh request (credential placeholder only)](../examples/cloud/cognito-refresh.request.json)

The observed refresh response supplied ID and access tokens with a one-hour
lifetime and no replacement refresh token. The Identity Pool is not needed for
HTTPS; the examined mobile app uses it for SNS push registration. Its credential exchange and
MQTT role are covered in the [MQTT connection model](mqtt.md#connection-and-authorization-model).

For HTTP errors and retries, see the [cloud error reference](../src/mysa_client/cloud/README.md#errors-timeouts-retries-and-caller-responsibilities).
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
