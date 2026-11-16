# Mysa's official Consumer API

Mysa has an official API for third-party apps, the **Mysa Consumer API**. It
lets an app read and control a customer's thermostats after the customer signs
in with Mysa and approves access. This page says what it is, how to get
started with it, and why this library doesn't use it yet.

Everything here comes from Mysa's published documentation, read on
2026-11-15. We have not called the API ourselves. Where this page and Mysa's
documentation disagree, Mysa's is right.

- [Where to find it](#where-to-find-it)
- [What it offers](#what-it-offers)
- [How to connect](#how-to-connect)
- [Good to know](#good-to-know)
- [Why this library doesn't use it yet](#why-this-library-doesnt-use-it-yet)

## Where to find it

| | |
| --- | --- |
| Getting-started guide and examples | <https://github.com/empoweredhomes/mysa-api-examples> (curl, Python and TypeScript; published 14 October 2026) |
| Reference | <https://consumer-api.mysa.cloud/v1/reference> |
| OpenAPI document | <https://consumer-api.mysa.cloud/v1/openapi.json> (version 1.0.0) |
| Tools for AI assistants (MCP) | <https://consumer-api.mysa.cloud/v1/agents> |
| What Mysa tells customers about connected apps | <https://consumer-api.mysa.cloud/v1/terms> |
| Questions and access requests | `support@getmysa.com` |

## What it offers

The API has 23 operations under `https://consumer-api.mysa.cloud/v1`, grouped
by the permission (scope) each needs:

| Scope | What it allows |
| --- | --- |
| `consumer-api/devices.read` | List thermostats, read what each can do, read current state (one or up to 100 at a time) |
| `consumer-api/devices.control` | Set the mode, heat, cool and auto setpoints, fan, swing, keypad lockout and display brightness |
| `consumer-api/schedules.read` | Read weekly schedules by home, by thermostat or by ID |
| `consumer-api/schedules.write` | Create, replace and delete a weekly schedule; hold the current setting until the next event; return to the schedule |
| `consumer-api/energy.read` | Read history in 30-minute steps: on-time, energy where the thermostat measures it, average temperature and humidity |

It reaches every thermostat the signed-in customer can access, including ones
another household member shared with them. Each thermostat's capability
document lists the modes and values it accepts, and the API checks a request
against that list before accepting it.

The same operations are also offered as tools for AI assistants through a
Model Context Protocol endpoint.

The only open-source project we know of that builds on the API, as of
2026-11-15, is [homeCore](https://github.com/homeCore-io/homeCore), through
its `hc-mysa` plugin.

## How to connect

Mysa's [getting-started guide](https://github.com/empoweredhomes/mysa-api-examples)
has the full steps and runnable examples. In outline:

1. **Ask Mysa for a client.** There is no self-service sign-up. Email
   `support@getmysa.com` with your app's name and publisher, what it does,
   every redirect address it will use, and the scopes it needs. Mysa replies
   with a `client_id` if it approves the app.
2. **Send the customer to Mysa's sign-in page.** The API uses the OAuth 2.0
   authorization code flow with PKCE. The customer signs in and approves
   access on Mysa's page, so your app never sees their password. There is no
   client secret.
3. **Exchange the code for tokens** at
   `https://mysa-backend.mysa.cloud/oauth/token`. You get an access token,
   valid for 60 minutes, and a refresh token.
4. **Call the API** with the access token as a bearer token, for example
   `GET https://consumer-api.mysa.cloud/v1/devices`.

Sign-in and the API are on different hosts: sign-in and tokens on
`mysa-backend.mysa.cloud`, the API on `consumer-api.mysa.cloud`.

## Good to know

These points are all in Mysa's documentation; they are collected here because
they shape how an app is built.

- **Redirect addresses are matched exactly,** including the port. An app that
  runs on the customer's own computer registers a fixed loopback address such
  as `http://127.0.0.1:8765/callback`.
- **A sign-in lasts up to 30 days.** A refresh token stops working 30 days
  after the sign-in that issued it, and refreshing doesn't extend that. The
  customer then signs in again.
- **Setpoints must be on the thermostat's list.** A value between two steps is
  refused rather than rounded. Temperatures are in °C.
- **Setting a heat, cool or auto setpoint also switches to that mode.**
- **A successful write means accepted, not applied.** The change is recorded
  as the thermostat's desired state. A thermostat that is offline applies it
  when it reconnects, and a response header says whether it looked online.
- **Errors are structured.** A refused request says why with a stable code,
  and a rate-limited one says how long to wait.
- **Responses can gain fields.** Ignore fields you don't recognise; send only
  documented fields in requests.
- **A customer disconnects an app through Mysa support.**

## Why this library doesn't use it yet

`mysa-client` and the [Mysa Local](https://github.com/SokolskyNikita/mysa-homeassistant)
integration talk to thermostats locally over HomeKit and, for cloud features,
through the same API Mysa's mobile app uses. We'd like to support the official
API too. A few things need settling first:

- **Access is granted per app.** A library can't give each of its users a
  client, and we don't yet know whether one client could be shared by
  everyone who installs an open-source project. The terms for developers are
  set out in Mysa's written approval, which we haven't asked for or seen.
- **The 30-day sign-in doesn't suit an always-on setup.** A home automation
  system is expected to keep running unattended. Asking the owner to sign in
  again every month would be a step back from how it works today.
- **It doesn't yet cover some settings our users rely on.** Examples are the
  owner's minimum and maximum setpoints, the temperature offset, the heater
  type, the display's units, whether the heater is on right now, and firmware
  checks.
- **It is a cloud API.** This project's main aim is local control, which the
  official API isn't meant to provide. It would sit alongside HomeKit, not
  replace it.
- **We haven't been able to try it.** Without an approved client we can only
  go by the documentation, and we'd want to test against real thermostats
  before shipping anything.

Once those are worked out, we hope to add the official API as a supported way
to reach Mysa's cloud. An API that Mysa documents and versions is a better
long-term footing than one we have to keep up with ourselves.

[Documentation index](../README.md)
