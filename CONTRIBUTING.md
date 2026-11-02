# Contributing to FortiLens

Thank you for helping make FortiGate observability easier to deploy and understand.

Useful first contributions include documentation fixes, FortiOS compatibility reports, sanitized inventory fixtures, and accounting edge cases. Discuss broad architecture or new service dependencies in an issue before implementing them.

Start with the [documentation index](docs/README.md), [source map](docs/PROJECT_CONTEXT.md)
and [maintainer instructions](AGENTS.md). The [roadmap](ROADMAP.md) distinguishes
existing capabilities from planned work. Use the issue forms for bugs,
documentation and sanitized FortiOS compatibility reports; sensitive findings
follow [SECURITY.md](SECURITY.md).

## Development environment

Python 3.10+ is enough for the portable CLI tests; those remain dependency-free.
Management requires Python 3.11+. The complete development environment uses
Python 3.11+ in a virtual environment; its quality tools are not runtime dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

On Windows, activate `.venv\Scripts\Activate.ps1` in PowerShell instead. Linux
remains the deployment target; Windows test/configuration support does not make
the systemd installer or host monitoring portable to Windows.

For management-only dependencies, `management/requirements-test.txt` remains
supported. To exercise the dependency-free portable path, use a separate Python
3.10+ environment without those packages; management tests then explicitly skip.

## Local checks

Run from the repository root:

```bash
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff check tools tests/test_repository_checks.py tests/test_release.py tests/test_cli_errors.py tests/test_frontend_errors.py tests/test_management_errors.py tests/test_runtime_errors.py tests/test_service_errors.py tests/test_configuration_ca.py tests/test_probe_diagnostics.py tests/test_repair_runtime.py --extend-select I
python -m ruff format --check tools tests/test_repository_checks.py tests/test_release.py tests/test_cli_errors.py tests/test_frontend_errors.py tests/test_management_errors.py tests/test_runtime_errors.py tests/test_service_errors.py tests/test_configuration_ca.py tests/test_probe_diagnostics.py tests/test_repair_runtime.py
python tools/check_repository.py
python tests/check_frontend.py
python -m coverage run -m unittest discover -s tests -v
python -m coverage report -m
python -m coverage xml
python -m pip_audit --strict -r requirements-dev.txt
```

The frontend syntax check needs a development Node.js executable; Node is not
added to the production installation. Ruff uses a deliberately small correctness
ruleset. Formatting initially covers the new development/release tooling and its
tests so cleanup does not mix a repository-wide runtime reformat with engineering
changes. Import-order/style modernization of runtime modules is separate reviewed
work. Do not ignore a real correctness/security finding just to pass CI.

Coverage measures executed source paths, not traffic completeness or deployment
compatibility. Report the actual run, environment, skipped tests and unexecuted
checks; do not copy historical counts from documentation. Dependency-audit
findings need compatibility review rather than automatic runtime pin changes.

Run the existing integration scenarios only as a nonroot test user on a
verified disposable Linux host with Docker Compose v2 and OpenSSL:

```bash
python tests/integration_stack.py
python tests/integration_management.py
```

CI also tests the real Ubuntu/systemd installer twice on an ephemeral VM. Never
run root `--installed` integration mode against a personal or company installation;
it is restricted to a disposable CI VM. Review [management verification](docs/MANAGEMENT_VERIFICATION.md)
and the full [QA procedure](docs/QA.md).

Use synthetic fixtures and separate directories, ports, Compose projects, and volumes for integration work. Never use a client's production environment as a test fixture. Include meaningful regression coverage for behavior changes and document what you actually verified.

## Distribution checks

The existing portable command and layout remain supported:

```bash
python portable/setup.py bundle --destination dist/fortilens-single-device.zip
```

For a full/portable release-candidate pair, use a fresh output directory and
follow [release preparation](docs/RELEASING.md). The example version below is not
an announcement or authorization to tag/publish:

```bash
python tools/release.py build --version v0.3.0 --output dist/review
python tools/release.py verify --directory dist/review --version v0.3.0
```

Verify extracted artifacts as well as the checkout. Full installation acceptance
belongs on an isolated Ubuntu VM, never a company monitoring host. Building an
archive is separate from publishing it; published releases remain immutable.

## Design principles

- Keep missing evidence visible; do not turn absent samples into UP/DOWN or zero usage.
- Prefer existing exporter metadata over adding another collector.
- Separate reported role, interface type, explicit monitoring choices, and accounting filters.
- Keep FortiGate operations read-only and explicit; never silently weaken TLS or overwrite dashboard edits.
- Keep shipped messages and documentation in English, with plain wording and actionable errors.
- Keep secrets, generated installations, raw logs, and customer screenshots out of commits and issues.

Open a focused pull request explaining the trigger, resulting behavior, tests, and remaining limits. Contributions are distributed under the repository's MIT license. Follow the [security policy](SECURITY.md) for sensitive findings.

## Pull request scope

- Start a dedicated branch, such as `codex/your-change`, from the current target
  branch. Keep documentation, formatting, release engineering and functional
  changes independently reviewable; do not force-push divergent history.
- Preserve CLI/API contracts, existing installation paths, Compose project and
  named-volume identity, database schemas, dashboards/queries and auth boundaries.
- Refactor only when there is a concrete maintenance benefit and regression
  evidence. Large modules alone are not evidence that a rewrite is needed.
- Explain changed files, compatibility impact, tests actually executed and
  remaining limits using the PR template. Update relevant guides/changelog when
  implemented behavior changes.
- Keep generated installations, credentials, databases, raw logs, customer
  exports and private QA artifacts out of commits and build contexts.
- Publication, production rollout, policy changes and merging require their own
  authorized maintainer action. Repository cleanup does not authorize them.

See [repository governance](docs/REPOSITORY_GOVERNANCE.md) for recommended labels,
protection and community channels; these recommendations do not change settings.
