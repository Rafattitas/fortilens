# Repair verification and operating boundaries

The repair keeps existing application paths, auth/helper boundaries, schemas,
accounting formulas, retained data and deployment identities. Production evidence
and backups belong outside Git; this guide uses only synthetic/public examples.

## Deployment lifecycle

Successful operations close prominent progress, refresh settings/service state
and remain in collapsed deployment history. Reload/navigation does not reopen a
completed panel. Running jobs restore polling and lock conflicting operations.
Failures remain until acknowledged; historical details/recovery remain available.
A lost response has an unknown outcome and requires explicit status refresh.
Old job records without revision metadata display unavailable, never today's
revision. Recorded job IDs allow opening older retained details.

Run the existing Python suite and the real browser fixture:

```bash
python -m unittest discover -s tests -v
npm install --prefix /tmp/fortilens-browser --no-save --ignore-scripts playwright@1.62.1
FORTILENS_PLAYWRIGHT_MODULE=/tmp/fortilens-browser/node_modules/playwright FORTILENS_BROWSER_EXECUTABLE=/usr/bin/google-chrome node tests/browser_deployment.cjs
```

Use a fresh development-only dependency directory. Chrome/Playwright are not
production dependencies. The fixture uses synthetic API responses and disposable
browser state; it covers success/reload/navigation, running jobs, duplicate Apply,
failure acknowledgement/recovery history, recorded job lookup, discovery gating,
unknown outcomes and mobile layout. It does not prove live sign-in or collection.

## Configuration drafts and browser autofill

Saving a connection previously scanned all settings controls, including hidden
wizard steps. A password-manager or other DOM write could therefore persist an
unreviewed device identity before its section was opened. The editor now saves
its draft value snapshot and accepts intentional edits from visible, editable
controls. A later hidden overwrite cannot replace a value already reviewed.
Read-only source bindings retain the saved value.

The API token remains masked, uses a separate form association, and is excluded
from the configuration form's password controls. Autocomplete hints discourage
credential-manager classification; browsers and extensions may ignore those
hints. Draft isolation does not depend on them. Normal administrator password
autofill remains supported in the authentication forms.

The real-browser fixture covers first-admin setup and sign-in, unvisited hidden
input/change events and event-free writes, existing and locked source identity,
intentional serial edits followed by hidden mutation, and API-token pollution.
Its synthetic password-manager writes establish the persistence defect; they do
not identify which manager caused a particular live value. Explicit serial
mismatches continue to fail through the existing backend contract.

## Candidate CA assembly and error handling

Apply baseline, saved draft and pending CA removal/replacement before final
validation. CA-off plus retained active CA is a bounded tls_ca_conflict; explicit
removal succeeds only with existing insecure-mode acknowledgement. Old CA files
are retained. Regression tests exercise verified custom/system trust, replacement,
removal, disabling with retained/removing CA and re-enabling verification.
Expected renderer validation errors receive safe 4xx responses; arbitrary input
or exception strings never become browser diagnostics.

## Probe and source evidence

Exporter 1.25 supports case-sensitive prefix include/exclude profiles. The narrow
helper permits explicit exclusion of Log/Fortianalyzer/Status,
System/Interface/Transceivers and System/SensorInfo while retaining required
System/Interface and VirtualWAN/HealthCheck coverage. Existing policy and tokens
are preserved. No default exclusions are imposed on every installation. Review
capabilities and actual endpoint failures before applying a site-specific policy.
In upstream 1.25 both warning and error probe failures can affect aggregate
probe_success; compare current up/probe values, retained label sets, duration and
collector errors. Excluded capabilities remain a warning, not a claim of success.

Accounting delivery is separate from polling. No received delivery stays UNKNOWN;
a successful poll over an empty range cannot certify usage or sender identity.
Existing source contracts remain locked; never edit expected_devid or reset the
database as an automatic repair. Plan migration separately with consistent backups
and historical reconciliation.

## Optional native UDP normalization and packaging

The [native UDP adapter guide](../operations/SYSLOG_NATIVE_UDP.md) describes an optional,
operator-managed rsyslog service for an existing native/default FortiGate sender.
The dedicated nonroot instance wraps the native body as RFC5424 over local TCP
for Alloy without changing firewall settings, accounting scope or the system
logger. The guide records isolated rsyslog 8.2512.0 / Alloy 1.19.2 checks, including
enforced AppArmor, restricted startup and rejection of a disallowed test sender.
Its bounded memory queues and UDP transport do not provide lossless delivery.
The management helper's Docker checks do not monitor this separate host service.

The full distribution includes that guide and both generic rsyslog examples.
The installer runtime allowlist and the 23-file portable payload remain unchanged;
the adapter is not automatically installed or enabled. Release regression checks
require those files in the full archive, exclude them from the portable archive,
and validate local Markdown links from extracted full source. Archive checksums
and source bytes are verified separately from live collection evidence. No tag
or release publication is part of these checks.

## Trust, deployment and preservation

Disabled certificate verification remains an explicit diagnostic warning. A
self-signed certificate with a mismatching IP identity is not fixed merely by
importing a CA. Pinning cannot be deployed from trust-on-first-use or just one
protected client: independently confirm the reference, test mismatch/expiry and
rotation, and cover management, discovery, worker and exporter before enabling it.

Before a controlled rollout, record source/config hashes, registered project,
mounts, volume IDs, dashboard/query state and revision. Keep a private source/config
archive and consistent SQLite backups, rehearse restoration outside production,
test the candidate, then replace only reviewed files and restart only affected
services. Preserve rollback inputs and prior images; never reinitialize, prune,
delete volumes, reset databases or change a firewall to make checks green.
After rollout compare identities/hashes/integrity and multiple bounded samples.
No sender traffic, unsupported optional integrations and missing trusted reference
are external limitations, not successful repairs. Capacity needs measured rates;
device count or empty ingestion cannot certify a large installation.
