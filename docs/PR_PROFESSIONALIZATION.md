# Professionalize repository quality, documentation and release packaging

## Motivation

FortiLens's current source supports Web Setup, management and the Ubuntu installer,
but release automation packages only the older portable layout. Contributor checks
also lack consistent static analysis, coverage measurement, dependency auditing
and a navigable documentation entry point.

## Scope and changes

- Preserve application architecture and all runtime files; document module
  ownership and defer risky package/import refactoring.
- Add a reproducible, explicitly allowlisted full distribution alongside the
  compatible portable ZIP, with provenance, SHA256 checksums and extracted-product
  validation. Keep existing release assets immutable.
- Add development-only Ruff, EditorConfig, branch coverage, dependency auditing,
  update automation and repository consistency checks; retain the Python/OS matrix
  and real isolated integrations.
- Improve documentation navigation, supported installation/distribution guidance,
  contribution and issue/PR forms, roadmap and maintainer recommendations.
- Expand private-data/tool-output ignore patterns; retain fail-closed Docker and
  installer source allowlists.

## Compatibility

No accounting or WAN algorithm, authentication/authorization behavior, API,
portable CLI, installer path, Compose project/volume identity, database schema,
retention default, dashboard query, runtime dependency or container image changes
are intended. No file movement or production deployment is needed. Portable
Python 3.10+ and management Python 3.11+ remain distinct supported contracts.

Published releases are not changed. Repository settings and access policies are
recommendations only. Formatting/import checks are incremental for new tooling;
existing runtime code is not reformatted in this change.

## Validation and remaining limits

Local Python 3.12.14 validation passed 178 tests with all management dependencies
and 178 tests from the extracted full candidate. The dependency-free environment
passed 147 tests with three documented optional-dependency skips. Ruff, new-tool
formatting/import checks, repository/Markdown/YAML checks, three shipped browser
script compilations and actionlint passed. Dependency auditing resolved 45 packages
with no known vulnerabilities. Application coverage measured 73.42% of statements
and 61.20% of branches; 40 protected source files and 17 portable CLI help contracts
match the baseline. Archive tests verify completeness, exclusions, checksums,
tamper rejection and reproducibility.

See [CLEANUP_VALIDATION.md](CLEANUP_VALIDATION.md) for the executed baseline, final
tests, coverage and preservation checks. The local Windows host cannot execute
Docker/Linux/systemd acceptance; the release workflow prepares disposable
extracted-artifact jobs for that evidence. No CI success is claimed for an
unpublished branch. Real-device traffic completeness, browser rendering and
production capacity remain separate validation activities.

See [REPOSITORY_AUDIT.md](REPOSITORY_AUDIT.md) for severity, affected files,
compatibility risks and verification of every finding.
