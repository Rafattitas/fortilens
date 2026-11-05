# Module ownership and safe maintenance

The existing architecture remains in place. Python 3.10+ portable scripts are
usable without installing a Python package; management requires Python 3.11+
and its separately pinned dependencies. A `src/` migration or relative-import
rewrite would affect shipped scripts, worker copies, installer allowlists and
Docker contexts. This cleanup deliberately adds development tooling around those
contracts instead of changing them.

## Existing seams

| Module | Responsibility and contract | Future extraction prerequisite |
|---|---|---|
| `management/app.py` | HTTP/auth boundaries, fixed routes, trusted initial-view launch and hosted-report presentation. | Separate presentation constants only after native mount, cache policy, frame entry/reload and packaging tests; preserve import names/routes. |
| `management/auth.py`, `models.py` | Private auth state, session/CSRF contracts and strict validated browser/helper inputs. | Keep management and gateway/service authentication separate; characterize errors and session persistence first. |
| `management/configuration.py` | Import existing layout, revisions, candidate merge, fingerprints and file-only recovery. | Characterize every accepted existing mount/config form before splitting candidate serialization from transactions. |
| `management/runtime.py` | Fixed Docker operations, readiness, diagnostics, apply jobs and read-only console bridge. | Separate gateway/device adapters behind existing injected interfaces; retain peer/helper registry and fixed command boundaries. |
| `management/common.py` | Safety primitives and deliberate loading of portable setup. | Preserve standard-library CLI paths and copied-worker imports; test both installed and source contexts before changing loading. |
| `portable/setup.py` | Configuration validation, rendering, CLI, dashboard import and portable bundle. | Keep command/argument/error compatibility and install output unchanged; isolated extraction needs explicit installer/bundle/Docker file updates. |
| `portable/accounting.py` | Normalization, source/session identity, conservative byte contributions, transactional ledger and summaries. | Require independently known fixtures, late-arrival/replay/restart/retention equivalence and database compatibility; no algorithm changes in organizational work. |
| `portable/usage_service.py` | Bounded Loki ingestion, engine lifecycle, WAN/report access and authenticated HTTP serving. | Preserve query budgets/checkpoint transactions; any HTTP adapter extraction must preserve response/error and auth behavior. |
| `portable/portal.py` | Protected native Viewer/report gateway and read-only route allowlists. | Preserve its independent session/service credentials and query boundary; never merge it with management auth for code reuse. |
| `portable/interfaces.py`, `wan_health.py` | Exact scoped interface evidence and bounded observed WAN interval math. | Preserve historical mapping, missing-data semantics and destination-specific route scope. |
| `portable/doctor.py`, `upgrade.py` | Reusable read-only diagnostics and separately reviewed CLI upgrade/recovery. | Retain callback injection, exact project identity, file hashes and no-volume-deletion behavior. |

Repeated hash helpers, no-redirect handlers and authentication wrappers are small
and operate in different deployment/trust contexts. Similar spelling alone is
insufficient reason to introduce a shared runtime package.

## Development standards

Use the commands in [CONTRIBUTING.md](../../CONTRIBUTING.md). Correctness lint applies
across the repository; new development tools have conventional formatting/import
ordering. Existing dense runtime formatting is intentionally deferred to a
dedicated mechanical change with source/AST and behavior validation. No broad
type-checking claim is made for existing dynamically loaded runtime modules.
Typed internal APIs can be introduced one characterized seam at a time.

Use annotations and short docstrings where they describe a stable contract; avoid
inventing abstraction layers around fixed single-host operations. A new Python
module is not complete until every affected source/install/build allowlist and
extracted distribution has been tested.

## Verification before a future refactor

1. Capture accepted inputs, outputs, observed errors and CLI help/arguments.
2. Include existing-layout adoption, external edits, interrupted Apply/recovery,
   database restart/replay and saved dashboard preservation as applicable.
3. Demonstrate extracted portable and full installation behavior on their supported
   environments; keep the old import/entry point where downstream use is possible.
4. Run focused tests, the full suite, isolated Docker/systemd acceptance and actual
   browser entry/reload for UI-related changes.
5. Measure coverage and remaining gaps; disclose unverified Linux/device behavior.

Formatting, runtime extraction and release-system changes should remain separate
logical commits or pull requests. The cleanup's new packaging/development tools
have no production imports or runtime dependency role.
