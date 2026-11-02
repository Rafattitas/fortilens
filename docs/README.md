# FortiLens documentation

Choose a guide by task. Existing guide paths are retained so external links,
installer references and the portable workflow continue to work.

## Installation and configuration

| Guide | Use it for |
|---|---|
| [Web Setup & Settings](WEB_SETUP.md) | Ubuntu prerequisites, administrator bootstrap, trusted HTTPS, configuration, exact-project adoption and account recovery. |
| [Installation overview](INSTALLATION.md) | Choosing the web or portable path and checking source prerequisites. |
| [Portable installation and operations](../portable/README.md) | The Python 3.10+ CLI, private generated installation, commands and operational examples. |
| [Unified console](UNIFIED_CONSOLE.md) | Native Grafana/report gateway, authentication boundaries and explicit adoption limitations. |

The published v0.2.0 portable ZIP predates web management and the unified console.
Read [release preparation](RELEASING.md) before choosing or building an archive.

## Architecture and data contracts

| Guide | Use it for |
|---|---|
| [Architecture](ARCHITECTURE.md) | Collectors, storage, reporting, resource bounds and service relationships. |
| [Management architecture](MANAGEMENT_ARCHITECTURE.md) | Nonroot web process, authenticated helper, fixed Compose identity and configuration transaction boundaries. |
| [Accounting paths](ACCOUNTING.md) | Choosing persistent reports, legacy Loki diagnostics or offline sample auditing. |
| [Accounting engine contract](ACCOUNTING_ENGINE.md) | Identity, direction, counters, replay/late-data handling, exact VLAN evidence and calendar periods. |
| [Legacy usage diagnostics](USAGE_QUALITY.md) | Existing Loki query behavior and bounded offline normalized-sample auditing. |
| [Project context](PROJECT_CONTEXT.md) | Maintainer source map, supported defaults and invariants; not a deployment snapshot. |

## Operations and troubleshooting

| Guide | Use it for |
|---|---|
| [Troubleshooting](TROUBLESHOOTING.md) | Read-only doctor evidence, missing telemetry, processing lag and storage failures. |
| [Upgrade from v0.1.0](UPGRADE.md) | Reviewed accounting migration and hash-guarded configuration rollback. |
| [Optional alerts](OPTIONAL_ALERTS.md) | Paused review candidates and explicit operator activation. |
| [Security policy](../SECURITY.md) | Private reporting and deployment/data-protection boundaries. |

## Development and maintenance

| Guide | Use it for |
|---|---|
| [Contributing](../CONTRIBUTING.md) | Development setup, quality checks, focused PRs and synthetic fixtures. |
| [QA](QA.md) | Source checks, isolated Docker/systemd scenarios and bounded live acceptance. |
| [Error handling and recovery](ERROR_HANDLING.md) | Safe failure responses, unknown mutation outcomes, retained recovery evidence and failure-injection validation. |
| [Verification and compatibility](VERIFICATION.md) | Accounting/pipeline evidence and remaining real-device limitations. |
| [Management verification](MANAGEMENT_VERIFICATION.md) | Management test history, installer checks and optional read-only FortiGate validation. |
| [Release preparation](RELEASING.md) | Full/portable archive manifests, checksums, extracted-artifact validation and publication review. |
| [Maintainability](MAINTAINABILITY.md) | Module boundaries and deliberately deferred structural work. |
| [Repository governance](REPOSITORY_GOVERNANCE.md) | Suggested labels, branch protection, community channels and private reporting. |
| [Roadmap](../ROADMAP.md) | Implemented baseline and planned milestones without release promises. |

## Interface design

- [Console visual contract](UI_DESIGN.md): current presentation, navigation and
  accessibility requirements while preserving native monitoring.
- [Frontend design review](FRONTEND_DESIGN_REVIEW.md): historical draft findings
  and follow-up evidence, not an open-defect list for every later revision.

## Engineering records

- [Repository audit](REPOSITORY_AUDIT.md): findings, compatibility risks and safe
  follow-up recommendations for the inspected source revision.
- [Cleanup validation](CLEANUP_VALIDATION.md): commands actually executed,
  measured results and checks that still require a suitable environment.
- [Reliability release plan](RELIABILITY_RELEASE_PLAN.md): historical accounting
  design rationale; current future work lives in the roadmap.
- [Changelog](../CHANGELOG.md): implemented changes and release history.

Historical test counts and screenshots describe their recorded revision. Use
the exact current source head and executed checks for a new compatibility claim.
