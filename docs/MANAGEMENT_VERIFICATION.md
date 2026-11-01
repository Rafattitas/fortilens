# Web management verification

This phase adds the administrative foundation; it does not change traffic
accounting formulas or certify live FortiGate numeric accuracy.

## Automated checks

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r management/requirements-test.txt
python -m unittest discover -s tests -v
```

On November1,2026, **153 tests passed on Windows/Python3.12** with the management
dependencies installed: the existing131 tests plus22 management tests. They cover
first-run bootstrapping, password hashes, one-time closure, sessions, login rate
limits, CSRF/origin/role checks, private error responses, explicit secret changes,
revision conflicts/stale plans, file fingerprints, read-only discovery/WAN
confirmation, existing configurations/secrets/images/mounts, conservative policy
and source-scope locks, occupied ports, interrupted jobs and admin recovery.

The dependency-free CLI matrix intentionally skips the management module when
FastAPI/Pydantic/PyYAML are absent. A separate CI job installs these dependencies
and runs the entire suite, so the management tests are not silently omitted from
management CI. Production and test dependencies are pinned separately.

## Real isolated Docker acceptance

```bash
python tests/integration_management.py
```

Requires a nonroot Docker-enabled **test user** on Linux, Compose v2 and OpenSSL.
The test creates its own temporary root, random project/image/container names,
volumes and loopback ports. It never accepts an existing monitoring path. It removes
only its disposable resources after completion. Do not substitute company source
data or use a production installation as a fixture.

The Linux acceptance run passed with an actual nonroot web container, authenticated
Unix-socket helper and Docker Engine. It verified:

- Management starts without monitoring/configuration; settings require login.
- One-time admin creation, real certificate rejection, custom-CA success and
  read-only synthetic interface discovery/explicit WAN confirmation.
- Browser-equivalent configuration review/apply starts all eight actual services.
- Native Grafana health and protected fixed-period reports work through the bridge.
- Importing an existing running installation does not reinitialize it. A subsequent
  settings edit and configuration rollback preserve secrets, images, mounts and
  saved dashboard resource version, and work without a reachable source API.
- The management process has no Docker socket or installation bind and runs nonroot.
- Management health/auth/settings/diagnostics remain usable with Grafana and Loki
  deliberately stopped in the test project.

The strengthened test additionally checks an actual accounting-volume marker and
read-only SQLite integrity after Apply and rollback. CI runs this current test.

These are **synthetic metadata and isolated service checks**, not a real FortiGate,
complete traffic logging, real WAN health or a company capacity benchmark. Missing
telemetry stays unknown. No production services, dashboards, alerts or OS packages
were changed to run this lab.

## Real installer CI

The `Real Ubuntu systemd installer` CI job uses a disposable Ubuntu24.04 VM. It
explicitly installs Python venv prerequisites, runs the actual `sudo ./install.sh`
twice, verifies both system services and the web user's lack of Docker-group
membership, then drives the real installed portal/helper through the same
acceptance scenario. The root `--installed` test mode refuses ordinary hosts and
is only for that ephemeral Actions VM. Check the feature branch's CI result before
treating a new revision as a verified Ubuntu installer release.

## Browser review

The isolated UI preview was inspected in a real browser: bootstrap/admin sign-in,
the general/connection/discovery/storage/review sections, write-only token clearing
and explicit WAN confirmation. At390px, the document/body had no horizontal
overflow; navigation and the interface table scroll inside their own components.
Screenshots and private host/lab evidence are kept outside the public repository.
The preview uses labelled synthetic metadata; the Docker checks above provide
actual service-start evidence.

## Optional real FortiGate validation

On a separately authorized installation, create a narrowly scoped read-only API
user and valid trusted certificate/CA; Test Connection and Discover Interfaces.
Check serial/VDOM/roles/VLAN parent metadata against the firewall console. Have its
administrator configure traffic logging/syslog, then review doctor freshness,
receipt timestamps, accounting lag and coverage. Test permitted API failures without
modifying firewall policies. Do not inject synthetic records into production or
interpret a successful setup as proof of billing accuracy. See [WEB_SETUP](WEB_SETUP.md)
and [accounting verification](VERIFICATION.md).
