# Repository cleanup validation

Date: November 2, 2026. Baseline source:
`da3013685e48943e122f4394fd0e6181e279b462`. Working branch:
`codex/repository-professionalization`. This record describes repository changes
only. No production service, installation, database, dashboard or firewall was
used for testing or modified.

## Before changes

Windows/Python 3.12.14, isolated temporary test environments:

| Check | Executed result |
|---|---|
| Standard-library unittest discovery | 132 tests, OK; one management-module skip because optional test dependencies were absent. |
| Exact management test requirements + unittest discovery | 163 tests, OK; no skips. |
| Shipped JavaScript compilation | Three scripts passed. |
| Protected-source baseline | SHA256 captured for 40 runtime/configuration/installer/build-context files. |
| Portable CLI baseline | Root and all 16 subcommand help outputs captured with successful exit codes. |

## Executed checks

| Check | Result |
|---|---|
| Full discovery under coverage, exact development environment | **178 tests, OK, no skips**, 43.949 seconds. |
| Standard-library environment after cleanup | **147 tests, OK, three expected skips**: management module and two optional PyYAML validator cases; 20.865 seconds. The full environment executes those cases. |
| Focused archive regression suite | **9 tests, OK**, after correcting a synthetic fixture marker that initially matched its own public test source; no runtime fix was needed. |
| Entire suite from the extracted full candidate | **178 tests, OK, no skips**, 37.823 seconds; synthetic temporary data only. |
| `python -m ruff check .` | Pass; narrow correctness rules on the entire repository. |
| New-tooling import order and `ruff format --check` | Pass for `tools/`, `tests/test_release.py`, `tests/test_repository_checks.py`; existing runtime source is not reformatted. |
| `python tools/check_repository.py` | Pass: supported Python grammar, local Markdown links/anchors, private filenames, YAML/duplicate keys and SHA-pinned Actions. |
| `python tests/check_frontend.py` | Three shipped scripts compile; also passes from the extracted full candidate. |
| `actionlint` v1.7.12 | Both workflows pass; official Windows binary checksum verified. Optional shellcheck/pyflakes integrations unavailable and disabled; no shell-lint coverage is claimed. |
| `pip_audit --strict -r requirements-dev.txt` | **45 resolved packages, zero known vulnerabilities, zero skips** at execution time; no runtime pin changed. |
| Protected runtime/configuration inputs | All **40 SHA256 values match baseline**, including application code, dependency pins, templates, Dockerfiles, installer, `.dockerignore` and `.gitattributes`. |
| Portable CLI | Root plus **all 16 subcommand** help outputs/exit codes match baseline. |
| Git ignore hygiene | Representative database/sidecar, generated installation, certificate/key, HAR, private/export and tool-output paths ignored; public examples/templates remain visible. |
| Bounded current tracked-source credential-pattern scan | No matching private-key/token patterns; not a full historical-secret or penetration audit. |
| Archive validation | Full: **116 files including manifest**; portable: **23 files**. Payload equality, manifest hashes, checksums, executable installer mode, exclusion, tamper/path/link rejection, reproducibility and non-overwrite behavior tested. |
| Extracted full prechecks | Its own release verifier, repository checks and frontend compilation pass; all installer-required inputs match. |

The extracted candidate was tested before these final documentation records were
completed. Final archives are rebuilt from the committed cleanup state and their
application/tool/test inputs are compared with that tested candidate. Checksums
are supplied beside the artifacts; putting the archive's own digest in an included
document would create a self-reference.

## Measured application coverage

Coverage 7.17.0 in branch mode includes every `management/` and `portable/` module,
with **zero excluded lines**. New tools/tests are outside the application
percentage. Subprocess/Linux integration execution is not included in this
local unit-test coverage measurement.

- Statements: **3,295 / 4,488 = 73.42%**.
- Branches: **1,153 / 1,884 = 61.20%**.
- Coverage.py combined statement/branch result: **69.81%**.

| Module | Statements | Branches |
|---|---:|---:|
| `management/app.py` | 88.24% | 81.71% |
| `management/auth.py` | 86.73% | 70.83% |
| `management/configuration.py` | 81.14% | 64.22% |
| `management/runtime.py` | 60.96% | 44.21% |
| `management/helper.py` | 28.57% | 0.00% |
| `management/install.py` | 25.38% | 6.12% |
| `management/recovery.py` | 0.00% | 0.00% |
| `portable/accounting.py` | 82.78% | 73.44% |
| `portable/usage_service.py` | 50.30% | 36.84% |
| `portable/portal.py` | 89.10% | 82.46% |
| `portable/setup.py` | 62.37% | 50.00% |
| `portable/upgrade.py` | 88.11% | 73.53% |
| `portable/wan_health.py` | 86.96% | 76.67% |

Separate follow-up priorities are real Unix peer/socket handling, privileged
installer paths, recovery CLI orchestration, Docker readiness/failure and rollback
combinations, worker polling/network failures and retained history. Existing
management tests exercise `Auth.recover` and session revocation; the separate
recovery command entry point is not covered by this run. Do not exclude these
modules or infer their behavior from the aggregate percentage.

## CI and unexecuted validation

Baseline main had successful
[CI run 41368828573](https://github.com/Rafattitas/fortilens/actions/runs/41368828573).
Remote `main` was checked again and still matched the baseline. No push, tag,
merge, publication or repository-policy change was performed. **There is no
GitHub CI result for this unpublished cleanup branch.**

The local Windows host has no Docker executable or usable Linux subsystem.
These checks were **not executed locally**:

- `python tests/integration_stack.py` with real isolated Docker services.
- `python tests/integration_management.py` with Linux Unix sockets/nonroot web.
- Actual Ubuntu/systemd installation, including from the packaged full artifact.
- Python 3.10/3.13 interpreter matrix, actual browser rendering/accessibility,
  real FortiGate numeric completeness, container advisory scans and load/capacity.
  Grammar validation is not execution under another interpreter version.

All existing CI jobs remain. Release CI adds extracted-artifact acceptance on
disposable VMs; those jobs must pass on the final pushed head before merge/release.
No destructive or root installation test is run against a developer/company host
to replace that evidence.

## Architecture and compatibility

Application architecture is unchanged. No runtime module moves, package conversion,
algorithm/API/CLI/schema changes or new runtime dependencies are introduced.
The only portable payload documentation edit corrects its stale file count from
22 to 23. Portable code/layout, accounting/auth/WAN logic, defaults, image/mount/
volume templates, installer paths and dashboard definitions remain byte-identical.

The two new Python tools are development-only. Broad formatting, shared auth
helpers, dynamic-import restructuring, migrations, CodeQL and whole-runtime type
checking are deferred to independently scoped changes. Current static/dependency/
boundary checks are not substitutes for those reviews. See
[MAINTAINABILITY.md](../MAINTAINABILITY.md).

## Changed files and reasons

No file is moved or removed; existing technical guide paths remain.

| Status | Files | Reason |
|---|---|---|
| Created | `tools/release.py`, `tests/test_release.py`, `docs/RELEASING.md` | Full/portable packaging, provenance/checksums and artifact regression tests. |
| Modified | `.github/workflows/release.yml` | PR/manual verification; packaged acceptance before non-clobbering attachment to an explicitly published release. |
| Created | `pyproject.toml`, `.editorconfig`, `requirements-dev.txt` | Reproducible development standards and coverage configuration. |
| Created | `tools/check_repository.py`, `tests/test_repository_checks.py` | Grammar/link/YAML/Action/private-file consistency checks. |
| Created / modified | `.github/dependabot.yml` (new), `.github/workflows/ci.yml` | Dependency updates, lint/advisory checks, explicit development Node and coverage; all original jobs preserved. |
| Modified | `.gitignore` | Exclude private runtime/customer data and tool output. |
| Created | `ROADMAP.md`, `docs/README.md`, `docs/REPOSITORY_GOVERNANCE.md` | Supported/planned scope, navigation and recommendations without policy changes. |
| Created | `.github/ISSUE_TEMPLATE/{config,documentation,fortios_compatibility}.yml` | Sanitized documentation/compatibility reporting and security routing. |
| Modified | `.github/ISSUE_TEMPLATE/{bug_report,feature_request}.yml`, `.github/pull_request_template.md` | Expected/actual behavior, compatibility, validation/security/docs and privacy evidence. |
| Modified | `README.md`, `CONTRIBUTING.md` | Distribution/runtime requirements, navigation and contributor commands. |
| Modified | `docs/{ARCHITECTURE,INSTALLATION,WEB_SETUP,QA,VERIFICATION,MANAGEMENT_VERIFICATION}.md`, `portable/README.md` | Correct observed facts, preserve historical evidence and clarify procedures/checks. |
| Created | `docs/{REPOSITORY_AUDIT,MAINTAINABILITY,CLEANUP_VALIDATION,PR_PROFESSIONALIZATION}.md` | Audit, module rationale, validation/limitations and review-ready PR description. |
| Modified | `docs/PROJECT_CONTEXT.md`, `CHANGELOG.md` | Current source map and additive engineering change record. |

## Before and after

Before:

```text
fortilens/
├── .agents/skills/fortilens-maintainer/
├── .github/{ISSUE_TEMPLATE/,workflows/,pull_request_template.md}
├── assets/, examples/
├── docs/                 existing technical guides
├── management/           management/helper and static assets
├── portable/             CLI, worker, reports and templates
├── tests/                unit/frontend/integration checks
└── install.sh, AGENTS.md, README.md, CONTRIBUTING.md, SECURITY.md, CHANGELOG.md
```

After (existing directories retained):

```text
fortilens/
├── .agents/              unchanged
├── .github/              improved forms, CI/release checks, Dependabot
├── assets/, examples/    unchanged
├── docs/                 old paths + index/release/audit/evidence/guidance
├── management/           unchanged
├── portable/             unchanged runtime; one factual README correction
├── tests/                old checks + release/repository checks
├── tools/                check_repository.py, release.py
├── pyproject.toml, .editorconfig, requirements-dev.txt, ROADMAP.md
└── existing root guides/installer retained
```

## Next maintainer actions

1. Review focused local commits and the [PR description](PR_PROFESSIONALIZATION.md).
   Push/create a PR only after authorization; run final-head CI and packaged Linux
   acceptance before deciding to merge or publish.
2. Apply chosen [governance recommendations](../REPOSITORY_GOVERNANCE.md) only through
   separately authorized settings changes. Classic protection is unknown to the
   connector; private reporting needs maintainer verification.
3. Prioritize measured test gaps and staged formatting/types before structural
   extraction. Do not chase coverage by excluding real application code.
4. Follow the [roadmap](../../ROADMAP.md) for planned completeness, per-WAN,
   analytics/export and capacity work; none is implemented by this cleanup.

Coverage and passing tests are evidence for their executed scope, not a guarantee
of zero regressions or complete real-device traffic capture.
