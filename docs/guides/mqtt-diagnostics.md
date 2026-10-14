# Diagnose MQTT authorization

<a id="reproduction"></a>

## Maintainer probe

[`tools/mqtt_probe.py`](../../tools/mqtt_probe.py) is an optional, non-mutating
authorization diagnostic. It discovers account devices, checks MQTT CONNECT
and SUBACK, and can send the non-mutating echo request with `--echo`. It does
not request live telemetry or thermostat commands. Install its diagnostic
dependencies and run it from the repository root:

```sh
python -m pip install mysa-client boto3 'paho-mqtt>=2'
python tools/mqtt_probe.py --credentials /path/to/credentials.json --duration 20
python tools/mqtt_probe.py --credentials /path/to/credentials.json --echo
python tools/mqtt_probe.py --credentials /path/to/credentials.json --identity-pool modern --echo
```

The credentials file accepts a refresh token or email/password; keep it out of
Git and do not put credentials in command-line arguments. The tool suppresses
tokens, signed URLs, device IDs, and raw payloads. A verified echo establishes
publish/receive for that echo exchange, not successful thermostat control.
Policy inspection may be denied, and the diagnostic is not a general AWS IAM
evaluator. Without `--echo`, the tool cannot verify delivery and returns 2
after CONNECT/SUBACK; policy inspection determines whether the result is
`blocked` or `inconclusive`.

| Exit status | Interpretation |
| --- | --- |
| `0`, `authorization: "echo_verified"` | With `--echo`, each selected device returned PUBACK and its matching echo. This verifies the echo exchange, not thermostat control. |
| `1` | Setup or connection/subscription checks did not complete. |
| `2`, `authorization: "blocked"` | CONNECT/SUBACK completed, no echo was verified, and policy inspection matched the previously observed limited modern-pool policy. |
| `2`, `authorization: "inconclusive"` | CONNECT/SUBACK completed, but no echo was verified and policy inspection did not establish that limited policy. |

[Documentation index](../README.md)
