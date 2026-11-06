# FortiLens documentation

Choose a guide by task. Guides are grouped by purpose:

| Folder | Holds |
|---|---|
| `getting-started/` | Choosing and completing an installation. |
| `operations/` | Running, troubleshooting and upgrading a site. |
| `architecture/` | How the system and its accounting work. |
| `development/` | Testing, releasing and maintaining the source. |
| `design/`, `assets/` | Interface reference, screenshots and README artwork. |
| `history/` | Records of past work, not current guides. |

The short pages left directly in this folder are pointers. The published v0.2.0
package links to those addresses, so keep them.

## Installation and configuration

| Guide | Use it for |
|---|---|
| [Web Setup & Settings](getting-started/WEB_SETUP.md) | Ubuntu prerequisites, administrator bootstrap, trusted HTTPS, configuration, exact-project adoption and account recovery. |
| [Installation overview](getting-started/INSTALLATION.md) | Choosing the web or portable path and checking source prerequisites. |
| [Portable installation and operations](../portable/README.md) | The Python 3.10+ CLI, private generated installation, commands and operational examples. |
| [Unified console](getting-started/UNIFIED_CONSOLE.md) | Native Grafana/report gateway, authentication boundaries and explicit adoption limitations. |

The v0.3.0 release adds a full archive with web management and the unified console.
The portable ZIP, including the earlier v0.2.0 one, does not include web management.
Read [release preparation](development/RELEASING.md) before choosing or building an archive.

## Architecture and data contracts

| Guide | Use it for |
|---|---|
| [Architecture](architecture/ARCHITECTURE.md) | Collectors, storage, reporting, resource bounds and service relationships. |
| [Management architecture](architecture/MANAGEMENT_ARCHITECTURE.md) | Nonroot web process, authenticated helper, fixed Compose identity and configuration transaction boundaries. |
| [Accounting paths](architecture/ACCOUNTING.md) | Choosing persistent reports, legacy Loki diagnostics or offline sample auditing. |
| [Accounting engine contract](architecture/ACCOUNTING_ENGINE.md) | Identity, direction, counters, replay/late-data handling, exact VLAN evidence and calendar periods. |
| [Compact accounting storage](architecture/COMPACT_ACCOUNTING.md) | Optional lossless ledger layout, verified offline copy, acceptance checks and matched runtime/database rollback. |
| [Legacy usage diagnostics](architecture/USAGE_QUALITY.md) | Existing Loki query behavior and bounded offline normalized-sample auditing. |
| [Project context](development/PROJECT_CONTEXT.md) | Maintainer source map, supported defaults and invariants; not a deployment snapshot. |

## Operations and troubleshooting

| Guide | Use it for |
|---|---|
| [Troubleshooting](operations/TROUBLESHOOTING.md) | Read-only doctor evidence, missing telemetry, processing lag and storage failures. |
| [Native FortiGate UDP reception](operations/SYSLOG_NATIVE_UDP.md) | Explicit managed native UDP profile, bounded delivery evidence and the separate legacy rsyslog alternative. |
| [Upgrade from v0.1.0](operations/UPGRADE.md) | Reviewed accounting migration and hash-guarded configuration rollback. |
| [Optional alerts](operations/OPTIONAL_ALERTS.md) | Paused review candidates and explicit operator activation. |
| [Security policy](../SECURITY.md) | Private reporting and deployment/data-protection boundaries. |

## Development and maintenance

| Guide | Use it for |
|---|---|
| [Contributing](../CONTRIBUTING.md) | Development setup, quality checks, focused PRs and synthetic fixtures. |
| [QA](development/QA.md) | Source checks, isolated Docker/systemd scenarios and bounded live acceptance. |
| [Error handling and recovery](development/ERROR_HANDLING.md) | Safe failure responses, unknown mutation outcomes, retained recovery evidence and failure-injection validation. |
| [Verification and compatibility](development/VERIFICATION.md) | Accounting/pipeline evidence and remaining real-device limitations. |
| [Management verification](development/MANAGEMENT_VERIFICATION.md) | Management test history, installer checks and optional read-only FortiGate validation. |
| [Release preparation](development/RELEASING.md) | Full/portable archive manifests, checksums, extracted-artifact validation and publication review. |
| [Maintainability](development/MAINTAINABILITY.md) | Module boundaries and deliberately deferred structural work. |
| [Repository governance](development/REPOSITORY_GOVERNANCE.md) | Suggested labels, branch protection, community channels and private reporting. |
| [Roadmap](../ROADMAP.md) | Implemented baseline and planned milestones without release promises. |

## Interface design

- [Console visual contract](development/UI_DESIGN.md): current presentation, navigation and
  accessibility requirements while preserving native monitoring.
- [Current interface screenshots](design/screenshots/README.md): canonical
  gallery of the shipped Overview, Usage, Reports, Internet and installation
  screens, rendered with synthetic fixtures; capture provenance and regeneration
  instructions are included.
- A Precision prototype (`docs/design/a-precision-prototype.html` in the source
  checkout): design reference, separate from the shipped interface and its screenshot gallery; never
  served by the product or included in release packages.

## Engineering records

The [changelog](../CHANGELOG.md) holds implemented changes and release history. These
records in `history/` describe the revision they inspected and are not current guides:

- [Repository audit](history/REPOSITORY_AUDIT.md): findings, compatibility risks and
  safe follow-up recommendations for the inspected source revision.
- [Cleanup validation](history/CLEANUP_VALIDATION.md): commands actually executed,
  measured results and checks that still require a suitable environment.
- [Repository cleanup PR description](history/PR_PROFESSIONALIZATION.md): motivation,
  scope and validation of that change.
- [Reliability release plan](history/RELIABILITY_RELEASE_PLAN.md): accounting design
  rationale; current future work lives in the roadmap.
- [Frontend design review](history/FRONTEND_DESIGN_REVIEW.md): draft findings and
  follow-up evidence, not an open-defect list for every later revision.

Test counts and screenshots describe their recorded source revision. The current
interface gallery uses synthetic fixtures, not a live installation. Use the exact
current source head and executed checks for a new compatibility or collection
claim.
