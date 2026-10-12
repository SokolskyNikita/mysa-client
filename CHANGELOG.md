# Changelog

## 0.1.1 (2026-10-12)

- Fix HomeKit startup with a fresh caller-owned `AsyncZeroconf`: create missing
  discovery listeners and clean up only listeners owned by the client. Existing
  shared discovery browsers, including Home Assistant's, remain caller-owned.
- Make the discovery example wait for late mDNS results and print each accessory
  once during its listening window.
- Consolidate protocol references, diagnostic tools, examples, and raw API
  coverage in this repository, separate from Home Assistant behavior docs.

## 0.1.0 (2026-10-12)

Initial standalone release, extracted from Mysa Local for Home Assistant.

- Extract the cloud and HomeKit protocol clients into `mysa_client`.
- Add caller-owned session, credential persistence, and semantic device APIs.
- Add standalone usage examples and document reverse-engineering and hardware
  validation limits.

- Preserve the existing protocol APIs and MIT attribution; keep HTTP sessions,
  Zeroconf, polling, and credential persistence under caller ownership.
- Support Python 3.11–3.14, cloud-only installation, the optional HomeKit extra,
  and typed wheel distributions.
- Cover authentication, retries, parsing, pairing rollback, and example
  lifecycle with an independent test suite that does not install Home Assistant.
