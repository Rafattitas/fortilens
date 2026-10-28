# Verification and compatibility

The first release is early access. This document separates automated source checks, controlled integration evidence, and checks that still require real hardware.

## Automated suite

```bash
python3 -m unittest discover -s tests -v
python3 portable/setup.py bundle --destination dist/fortilens-single-device.zip
```

The current-source suite has56 tests covering configuration validation, query scoping, packaging, readiness classifications/freshness, read-only inventory, pagination, field sanitization, explicit interface choices, usage sample periods/replay candidates/identity changes, and candidate preservation. The v0.1.0 release included35 tests. CI runs Python3.10,3.12, and3.13 on Linux, plus Python3.12 on Windows for configuration/test portability. The IANA timezone case skips on a configuration-only host without a timezone database; UTC remains supported. CI does not connect to a FortiGate or start Docker.

## Controlled integration evidence

Before public preparation, clean Linux/amd64 Docker installations were exercised in an isolated project with separate volumes and ports. Configuration validators for Loki, Alloy, and Prometheus passed. Six services started, both datasources became ready, and the 26-template APIv2 dashboard imported successfully.

A synthetic RFC5424 TCP record reached Loki and returned 300 bytes, matching 100 TX +200 RX. Readiness reflected a deliberately stopped test service and missing SLA measurements without fabricating WAN state.

A local trusted-CA HTTPS fixture exercised GET-only inventory requests, bounded pagination, secret-field stripping, initial confirmed-interface import, and post-start dashboard candidate preservation. Promtool fixtures checked literal interface names and VDOM scoping. These fixtures are controlled evidence, not FortiGate hardware certification.

## Still pending

- Real-device onboarding and API compatibility across FortiOS versions and permission profiles.
- Live numeric SLA and interface inventory validation on a newly onboarded FortiGate.
- Continuous weekly/monthly traffic reconciliation, replay deduplication, and complete coverage.
- ARM images and non-Linux service deployment.
- Upgrade/migration procedures for existing deployments and broader production load testing.

Report your OS/architecture, Docker version, FortiOS version, relevant permissions, and sanitized command output when helping validate another environment. Never attach tokens, unredacted configs, or client logs.
