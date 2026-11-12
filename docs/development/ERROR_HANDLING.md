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
synthetic reproduction following [SECURITY.md](../../SECURITY.md).

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

Dated validation records for this guide are kept in the [validation records](../history/VALIDATION_RECORDS.md#from-developmenterror_handlingmd). They describe the revision they tested, not the current source.

