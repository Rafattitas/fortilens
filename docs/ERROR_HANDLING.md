# Error handling and operational recovery

FortiLens treats a failed request, missing telemetry and confirmed zero usage as
different observations. Error handling must not reconstruct missing traffic,
change accounting/WAN calculations, relax authentication or retry a mutation
whose outcome is unknown. Existing successful responses, CLI arguments, source
contracts, database schemas, service identities and deployment paths remain.

## Boundaries

| Boundary | Behavior |
|---|---|
| Management HTTP | Existing domain errors/statuses are preserved. Invalid input remains a bounded validation response. Unexpected operational/storage failures return safe unavailable errors; unexpected programming failures return a generic server error with the normal security/cache headers. |
| Management helper | Requests and responses are bounded and shape checked. Peer UID/key checks remain mandatory. Incomplete bodies, malformed responses and client disconnects cannot expose input-bearing exception text or bypass auth. |
| Deployment jobs | Failed queueing releases the deployment lock. Partial file installation uses the durable plan marker for conservative configuration recovery. Failure to save a final status must not undo an already committed configuration. |
| Configuration/history | Corrupt or unreadable state/plan files fail closed with an actionable message. They are not silently initialized over an installation. Unreadable historical jobs do not prevent helper startup; files remain for host review. |
| Reporting and gateway | Network/JSON/storage failures produce safe unavailable evidence. Known reporting errors remain recognizable; raw/unrecognized upstream failure bodies are not forwarded. Reads, bodies and proxy responses have explicit limits. |
| Worker | Failed ingestion does not advance a checkpoint or invent traffic. Secondary failure while recording an error cannot terminate the worker silently. Bind/start/shutdown paths close resources without closing a database underneath an active writer. |
| CLI/installer/tools | Expected file, decoding, process and upstream failures produce fixed diagnostics and nonzero exits. Cancellation is explicit. Only temporary files created by the failed operation are cleaned; existing user files, volumes and databases are retained. |
| Browser transport | Requests have deadlines including response parsing, validate JSON envelopes and display safe connection/timeout/response failures. Sessions are rejected on 401 even for a non-JSON body. POST failure can mean an unknown outcome: reload status before retrying. |

## Diagnostics and privacy

Management failure logs contain fixed event names and coarse categories such as
storage, I/O, invalid data and unexpected failure. They do not log exception
strings/tracebacks, request bodies, credentials, raw customer logs, internal URLs
or configuration content. Secondary logging failures must not replace the
primary operation's result. Service diagnostics likewise use fixed error codes.

Inspect private host logs and diagnostics through the operator's normal trusted
access. Do not paste raw service output or private configuration into a public
issue. Supply the source revision, safe error code, affected operation and a
synthetic reproduction following [SECURITY.md](../SECURITY.md).

The private configuration audit append follows state commits. If that append
fails, the management diagnostic logger records the storage failure; it must not
claim the committed operation was undone. Authentication decisions retain their
separate transactional boundary.

## Unknown outcomes and recovery

1. After a timed-out save/apply/rollback request, reload Settings and the recorded
   job status before deciding whether to repeat an operation. Client cancellation
   does not cancel an operation already accepted by the helper or Docker engine.
2. Review the applied revision and registered Compose identity. Configuration
   rollback restores reviewed files; it is not database/history restoration and
   does not certify the running containers' state after an interrupted engine call.
3. When disk-full prevents writing job history, the helper keeps a bounded latest
   in-memory job snapshot while it runs. This is not durable storage. After restart,
   an interrupted journal may need operator review against the actual applied
   revision. Existing files are not deleted to force recovery.
4. Correct permissions/free space/connectivity through a separately authorized
   host procedure. Do not run `init`, delete a volume, rotate a secret or change a
   firewall policy to make an error disappear.
5. Missing/failed collection stays unknown. A healthy process or successful API
   request does not establish complete source traffic or billing accuracy.

## Verification

Failure-injection tests use synthetic errors (including secret-bearing messages),
temporary databases/configurations, mocked network/process boundaries and the
actual browser transport functions under Node. Existing accounting, auth,
dashboard, portable packaging and integration tests remain required.

Run the complete development suite and checks described in [QA.md](QA.md).
Linux socket/Docker/systemd scenarios belong to disposable test environments;
never use a company installation to inject failures. Browser transport tests do
not replace actual rendering/entry/reload acceptance. See the source-specific
validation record for executed checks and remaining limits.

## Validation record — 2026-11-02

Baseline: repository-engineering branch `3fc21b37535e3e91e3c4b938e077f2e2c0765210`;
implementation branch: `codex/production-error-handling`. No installation was
updated. The runtime layout and existing dependency/schema/deployment contracts
remain; failure paths and internal recovery boundaries changed. No files moved
or were removed.

Executed on Windows with Python 3.12.14 and the pinned development environment:

| Check | Observed result |
|---|---|
| Complete unit suite with branch coverage | 260 source tests passed before the packaging follow-up; all 261 passed from the corrected, actually extracted Full archive, with no skips and combined line/branch coverage 73.58%. |
| Dependency-free portable environment | 197 tests, 6 explicit optional-dependency skips; passed before the final test-only cleanup synchronization correction. |
| Failure regressions | 82 new tests across management, deployment/configuration, services/storage, CLI/tools and browser transport. |
| Ruff correctness/imports and scoped formatting | Passed. Existing runtime-wide formatting remains separate. |
| Repository checker | Python grammar, local Markdown links/anchors, source filename hygiene and pinned workflow YAML passed. |
| Shipped frontend syntax | All three scripts passed Node compilation. |
| Workflow actionlint | Both workflows passed, with external shellcheck/pyflakes integrations disabled. |
| Dependency advisory audit | `pip_audit --strict -r requirements-dev.txt` reported no known vulnerabilities at execution time. |
| Candidate archive build/verify | Both distributions and SHA256 verified: 122 Full files and the existing 23 Portable files. |
| Real extraction and portable rebuild | Passed, including the added packaging regression; validated source payload tree `22dee490ac4825174514eb1ac85a4b257e295c2e`. |

Regression evidence includes auth/error headers, secret-bearing synthetic
exceptions, disconnects, worker checkpoint preservation, secondary storage/log
failures, partial-write recovery, lock release, committed-state preservation,
malformed upstream data, browser deadlines/session rejection and control cleanup.
A rejected historical APPLIED plan must not restore a previous configuration
unless this operation actually attempted installation. Threaded HTTP cleanup
tests synchronize on completion rather than assume response receipt means that
the server's `finally` block already ran.

The release tests exercise manifest completeness, byte reproducibility,
checksums and extracted CLI/accounting behavior. Actual extraction exposed a
pre-1980 ZIP timestamp failure that byte-only extraction had missed. Full archive
member times now use 1980-01-02 UTC; the unchanged portable bundle selector works
from the extracted source, and all 261 tests pass there. No tag or release was
published. Packaged Ubuntu/systemd installation still requires the separate
disposable acceptance procedure in [RELEASING.md](RELEASING.md).

Docker, Unix peer/socket permissions and real Ubuntu/systemd installation cannot
run on this Windows host without a disposable Linux environment. Use the exact
branch head's [GitHub Actions runs](https://github.com/Rafattitas/fortilens/actions)
for that evidence; an earlier passing source revision does not validate this one.
Actual browser rendering/navigation/reload, live upgrades, real FortiOS traffic
completeness, production capacity and sustained fault/load testing remain
unverified here. Unit coverage is not a zero-regression guarantee.

## Review summary

Operational and malformed-input failures previously could escape HTTP/CLI
boundaries, leave queued operations locked, obscure partial configuration writes
or expose raw upstream error content. This change keeps the existing architecture
and successful interfaces while making those failure results safe and recoverable.
It adds no automatic mutation retries, deployment operations or runtime packages.

Review management/configuration recovery separately from portable transport and
browser handling. Preserve existing accounting/WAN fixtures and the real isolated
integration gates. Follow-up work includes sustained fault/load tests, actual
browser acceptance and separately authorized live-install upgrade validation;
none is implied by passing the local suite.

## Pre-merge replay review — 2026-11-02

An additional review of candidate `ae886eef6abf650c2caf2c8fb5b766670b64a843`
found that replaying an already-started plan at the same saved revision could
mistake its existing recovery marker for a partial write from the new request.
Deployment now requires the plan to remain PREPARED under the configuration lock
before entering the installation/recovery boundary. The existing `stale_plan`
rejection is retained; explicit rollback remains a separate confirmed operation.

A regression replays APPLIED, APPLYING and FAILED markers at the unchanged
revision and verifies no automatic restore, revision/file changes or service
start. Existing partial-write recovery remains covered. All 14 runtime failure
tests and the complete 262-test suite passed locally on Python 3.12.14, with
combined line/branch coverage of 73.58%. Ruff, scoped formatting, repository
grammar/links/YAML checks and actionlint also passed. PR acceptance must validate
the final candidate's packaged Linux/Docker/Ubuntu paths before the authorized
merge; source merging does not authorize a production rollout or release.
