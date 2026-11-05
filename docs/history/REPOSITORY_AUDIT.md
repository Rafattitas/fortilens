# Repository engineering audit

Audit date: November 2, 2026. Baseline: `main` commit
`da3013685e48943e122f4394fd0e6181e279b462`. Work branch:
`codex/repository-professionalization`. This is a source-repository review;
no running installation or firewall was used as a test environment.

## Baseline and scope

The review covered the agent instructions, every application Python module,
frontend assets, Docker/installer inputs, tests, dependency declarations,
workflows, packaging allowlists, ignore rules and public documentation. Actual
source and executed checks take precedence over historical documentation counts.

Before edits, Windows/Python 3.12.14 produced:

- Standard-library environment: **132 tests, OK, one management-module skip**.
- Isolated environment with the exact `management/requirements-test.txt` pins:
  **163 tests, OK, no skips**.
- `python tests/check_frontend.py`: all three shipped scripts passed syntax checks.
- Narrow Ruff correctness rules (`E9,F63,F7,F82`) passed the unmodified source.

The existing CI already covers four Python/OS combinations, actual isolated
Docker pipelines, nonroot management/helper integration, and a disposable Ubuntu
systemd installer. The local Windows environment has no Docker executable or
usable Linux subsystem, so those Linux scenarios were not executed locally.
Current validation and remaining limits are recorded in
[CLEANUP_VALIDATION.md](CLEANUP_VALIDATION.md).

## Strengths to preserve

- `management/` and `portable/` already have meaningful responsibilities; a
  directory rewrite would add compatibility risk without a demonstrated benefit.
- A restricted Unix-socket helper fixes the Compose project and filesystem roots;
  the independent nonroot web process has no Docker socket or Docker-group grant.
- Revision checks, file fingerprints, private candidates and conservative recovery
  protect operator changes. Accounting contract and retention restrictions are
  explicit. Database/history recovery is correctly separate from file rollback.
- Received-log accounting has session/replay/late-arrival tests, transactional
  SQLite summaries and visible uncertainty. WAN data gaps remain unknown.
- Portable and installer source allowlists are deliberate. Docker contexts deny
  unlisted files. Docker images and existing third-party Actions are SHA pinned.
- Native dashboard queries, service/volume identities and runtime dependency
  versions can remain unchanged throughout this cleanup.

## Findings and treatment

No Critical finding was demonstrated in this bounded review. One High finding
concerns release completeness, not a confirmed exploitable vulnerability. The
review is not a penetration test, historical-secret certification, real-device
traffic reconciliation or production capacity assessment.

### A1 — High: full installation has no complete release artifact

- **Problem/evidence:** `.github/workflows/release.yml` builds only the portable
  `forti-monitor/` ZIP. `portable/setup.py:bundle` deliberately omits `install.sh`,
  `management/`, its dependencies and static assets, although current source
  documents Web Setup and the Ubuntu installer.
- **Impact:** users can download a valid portable artifact and mistake it for a
  complete distribution of current-source functionality.
- **Safest improvement:** add a separate explicitly allowlisted full distribution,
  checksums and provenance manifest; retain the portable entry point and layout.
- **Compatibility risk:** low for additive tooling; high if the existing bundle
  were repurposed. Existing releases must remain immutable.
- **Verification:** compare portable payload bytes and paths with the existing
  bundler; validate full runtime/installer allowlists; test extracted artifacts,
  reproducibility and exclusions; run packaged installation only on disposable CI.

### A2 — Medium: release validation omits management and packaged installation

- **Problem/evidence:** baseline release CI runs unittest without management test
  dependencies and exercises integration from the checkout rather than an extracted
  full artifact. Management tests explicitly skip without their dependencies.
- **Affected files:** `.github/workflows/release.yml`,
  `management/requirements-test.txt`, `tests/integration_management.py`.
- **Safest improvement:** install existing test pins, test both extracted products,
  and add full-artifact Ubuntu/systemd acceptance in an ephemeral runner.
- **Compatibility risk:** no runtime impact; CI will become more demanding and
  expose packaging omissions instead of silently passing them.
- **Verification:** require successful management tests and package checks; retain
  existing isolated integrations. Unexecuted runner jobs remain unverified here.

### A3 — Medium: contributors lack reproducible static/style tooling

- **Problem/evidence:** no `pyproject.toml`, EditorConfig or lint gate exists;
  semicolon-heavy existing style is widespread, including security-sensitive code.
- **Affected files:** application/test Python source and contributor instructions.
- **Safest improvement:** a small repository-wide correctness rule set; formatting
  and import ordering for new development tooling; document gradual adoption.
- **Compatibility risk:** mass formatting/refactoring would obscure security and
  accounting review. It is deliberately excluded from this change.
- **Verification:** run Ruff against all Python files, check the documented formatter
  scope and retain Python 3.10 portable/Python 3.11 management syntax compatibility.

### A4 — Medium: test coverage and environment gaps are not measured

- **Problem/evidence:** good scenario tests exist, but no coverage configuration or
  report distinguishes exercised lines/branches from root-only installer paths.
- **Affected files:** `tests/`, `.github/workflows/ci.yml`, critical management and
  portable modules.
- **Safest improvement:** measure branch coverage with management dependencies;
  report per-module gaps and preserve real Docker/Ubuntu acceptance.
- **Compatibility risk:** no production impact. An arbitrary coverage threshold
  would encourage shallow tests; no invented threshold or passing percentage is used.
- **Verification:** execute coverage and publish its measured output. Local unit
  coverage never substitutes for packaged Linux installation or browser acceptance.

### A5 — Medium: pinned dependencies lack advisory/update automation

- **Problem/evidence:** runtime pins and Action/image digests are explicit, but no
  automated dependency advisory audit or scheduled update configuration exists.
- **Affected files:** `management/requirements*.txt`, GitHub workflows and Dockerfiles.
- **Safest improvement:** development-only dependency auditing and modest Dependabot
  update groups; retain actual runtime pins until separately tested updates.
- **Compatibility risk:** changing runtime versions during cleanup is excluded.
  Advisory/network failures must be reported as failures, not suppressed.
- **Verification:** execute the audit against the declared runtime dependencies;
  review update configuration and pin all newly introduced third-party Actions.

### A6 — Medium: ignore rules omit common private runtime artifacts

- **Problem/evidence:** `.gitignore` excludes secrets and portable/local but does
  not exclude SQLite databases/sidecars, HAR captures, exports or private root-level
  workspaces. No matching committed credential pattern was found by the bounded
  current-tree scan; that does not certify every historical revision.
- **Affected files:** `.gitignore`; `.dockerignore` is already fail-closed.
- **Safest improvement:** add targeted private-state and tool-output patterns;
  preserve explicit public example/template files and Docker source allowlists.
- **Compatibility risk:** source-control hygiene only; intentional future sanitized
  fixtures require review. Existing tracked files remain tracked.
- **Verification:** `git check-ignore` representative sensitive paths and confirm
  public examples/templates remain visible; exercise archive exclusion tests.

### A7 — Medium: documentation facts obscure supported installation paths

- **Problem/evidence:** architecture counts omit the optional eighth gateway;
  README emphasizes an older portable release beside newer source-only web setup;
  test-count wording and portable allowlist count are stale. One installation
  statement does not distinguish reviewed CLI upgrades from automatic migration.
- **Affected files:** `README.md`, `portable/README.md`, `docs/ARCHITECTURE.md`,
  `docs/INSTALLATION.md`, `docs/VERIFICATION.md`, `docs/MANAGEMENT_VERIFICATION.md`.
- **Safest improvement:** correct facts against rendering/installer source,
  distinguish distributions and retain dated historical validation as historical.
- **Compatibility risk:** documentation only; installation commands must retain
  existing paths, options and exact-project adoption requirements.
- **Verification:** compare commands with `--help`, validate relative links and
  packaged guides, and use exact-head test evidence rather than old counts.

### A8 — Low: navigation and community guidance need a small contributor entry point

- **Problem/evidence:** flat technical guides have no index; contribution/PR forms
  do not collect all compatibility evidence; no clear planned-vs-implemented roadmap
  or maintainer settings guide exists.
- **Affected files:** `docs/`, `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/`,
  `.github/pull_request_template.md`.
- **Safest improvement:** categorized index without moving guides, focused sanitized
  forms, realistic roadmap and governance recommendations only.
- **Compatibility risk:** none for runtime; moving existing documentation URLs is
  unnecessary. Repository access policies require separate authorization.
- **Verification:** link/form/YAML checks; compare advertised features with source.
  Observed settings and unavailable protection APIs are distinguished in
  [REPOSITORY_GOVERNANCE.md](../REPOSITORY_GOVERNANCE.md).

### A9 — Low: dense modules and implicit portable imports raise maintenance cost

- **Problem/evidence:** `management/common.py` deliberately loads portable setup
  and exposes the portable path; `configuration._patch` merges many preserved
  configuration forms; runtime orchestrates device/Docker/console operations.
  These are coupled contracts, not evidence that their working logic is incorrect.
- **Affected files:** `management/{common,app,configuration,runtime}.py`,
  `portable/{setup,accounting,usage_service,portal,doctor}.py`.
- **Safest improvement:** document the actual seams, ownership and extraction
  prerequisites in [MAINTAINABILITY.md](../MAINTAINABILITY.md). Preserve code locations
  and internal/public entry points in this cleanup.
- **Compatibility risk:** meaningful for package imports, copied worker files,
  installer/Docker allowlists, errors and transactional accounting. Defer extraction.
- **Verification:** baseline/current runtime byte comparison, complete existing
  tests and independent future characterization tests before any extraction.

### A10 — Low: repository metadata has no automated consistency check

- **Problem/evidence:** relative Markdown links and workflow/template YAML are
  reviewed manually; development syntax/allowlist compatibility has no common gate.
- **Affected files:** documentation, `.github/`, installer/build inputs.
- **Safest improvement:** a small development-only repository validator and explicit
  frontend syntax invocation; no production Node dependency or new framework.
- **Compatibility risk:** checks must understand local anchors, HTML links and
  GitHub expression syntax without claiming to execute Actions.
- **Verification:** validator regression tests and a successful run over the source
  and extracted full distribution. GitHub workflow execution remains separate.

## Assessment of the requested engineering areas

| Area | Assessment / treatment |
|---|---|
| Directory structure | Preserve meaningful top-level components; add only tools and documentation entry points. |
| Module responsibilities | Existing ownership is coherent; document orchestration seams (A9). |
| Code organization | Incremental tooling standards; no cosmetic package migration (A3/A9). |
| Separation of concerns | Preserve web/helper and metric/accounting boundaries. |
| Duplicate code | Separate authentication/hash/redirect wrappers have differing trust/deployment contexts; no unsafe deduplication. |
| Module coupling | Portable dynamic imports and copied worker inputs are compatibility contracts (A9). |
| Dependency management | Preserve pins, add advisory/update tooling (A5). |
| Testing quality | Strong behavioral scenarios; add coverage and artifact tests (A2/A4). |
| CI/CD | Preserve seven existing jobs; add repository-quality and package gates. |
| Security practices | Preserve strong boundaries; improve private-artifact hygiene (A6). |
| Installation compatibility | Keep paths, installer, project identities and adoption unchanged. |
| Documentation quality | Correct observed inconsistencies and add navigation (A7/A8). |
| Release packaging | Add full distribution beside preserved portable payload (A1/A2). |
| Contributor experience | Reproducible checks, focused forms, roadmap and governance (A3/A8). |
| Maintainability | Narrow, reviewable changes; defer risky refactors with concrete prerequisites. |

## Explicit exclusions

No accounting/WAN/auth/API/CLI algorithm, database schema, Docker image, retention
default, Compose identity, dashboard query, installer behavior or production
configuration is changed. No release/tag, push, merge, repository policy change,
credential rotation or live integration experiment is authorized by this audit.
