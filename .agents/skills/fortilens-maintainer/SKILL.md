---
name: fortilens-maintainer
description: "Understand, modify, deploy and verify the FortiLens single-FortiGate monitoring project while preserving existing configuration, secrets, dashboards and accounting evidence. Use for FortiLens repository work, installation/adoption, management UI, reports or QA; not for unrelated firewall administration."
---

# FortiLens Maintainer

Read [AGENTS.md](../../../AGENTS.md) and
[project context](../../../docs/PROJECT_CONTEXT.md) first. Locate the source root
containing `management/` and `portable/`; a generated company installation is a
different directory. These files guide implementation, not permission to deploy,
publish, change credentials or modify a firewall. Follow the current user's scope.

## Choose the relevant path

- **Code or UI:** use the source map in project context. Read existing behavior and
  tests before editing. Reuse `setup`, `interfaces`, `doctor`, accounting and the
  native Grafana/report gateway; avoid introducing a replacement collector or UI.
- **Installation/adoption:** read [Web Setup](../../../docs/WEB_SETUP.md) and
  [management architecture](../../../docs/MANAGEMENT_ARCHITECTURE.md).
- **Accounting/WAN accuracy:** read [accounting](../../../docs/ACCOUNTING.md),
  [engine](../../../docs/ACCOUNTING_ENGINE.md) and
  [verification](../../../docs/VERIFICATION.md). Distinguish received records from
  complete source traffic and physical link from Internet/route evidence.
- **QA/release:** read [QA](../../../docs/QA.md) and
  [management verification](../../../docs/MANAGEMENT_VERIFICATION.md). Use actual
  head-specific checks, not a historical passing count.

## Maintain the architecture

The management web service is nonroot, with its own auth SQLite and no Docker
socket/group or monitoring-root access. The root helper accepts fixed operations
over a key/peer-UID authenticated Unix socket; a root-owned registry fixes project,
Compose file, paths and dashboard UID. Browser input must never choose shell
commands, Docker arguments, filesystem roots, images or projects.

Keep management available when the monitoring stack fails. The existing protected
gateway provides native Grafana Viewer/report access; the management cookie is
not a native Grafana administration credential. Do not bypass separate auth
boundaries or broaden a loopback listener to LAN without the requested HTTPS/access
configuration. Preserve write-only tokens, explicit TLS acknowledgement, sessions,
CSRF, revision checks and edited-review invalidation.

All shipped text/docs remain English. Conversation can follow the user's language.

## Preserve the deployed installation

Before live work, inspect the actual registry, Compose labels, files/mounts and
health. Record private hashes and take appropriate consistent backups. Never copy
a documentation example over the real config. Template datasource/dashboard UIDs,
WAN/SLA names, retention and addresses are not site facts.

The portable CLI derives its Compose project from its installation directory.
Adoption instead requires the exact existing project. Do not use a generic CLI
start/init to replace a manually named deployment. Registration must not reset
dashboards, rotate service secrets or create a new set of empty data volumes.

The gateway shares Grafana's network namespace. Recreate those two together when
replacing Grafana; leave unrelated collectors alone when their configuration does
not need a change. Existing alert rules and notification routing are not an implicit
part of ordinary maintenance. Do not trigger live notification tests by default.

Keep source/build allowlists narrow. Exclude generated installations, customer
exports/logs, credentials, private operational notes and local QA screenshots from
Git/build contexts. A code rollout is not authorization to change source identity,
accounting timezone, storage scope or retention reductions. Configuration rollback
cannot reverse a database migration or recover deleted history.

## Keep the evidence honest

- Docker running, API authentication and complete traffic capture are different
  claims. Missing samples/logs remain unknown/null, not zero traffic or WAN DOWN.
- Inspect actual labels before changing PromQL/LogQL or selector interpolation.
  Preserve saved dashboard query groups, selectors, defaults and access controls.
- Keep VLAN mapping exact by VDOM/interface/parent/VLAN ID; never infer it from
  a subnet, interface name or current metadata for historical traffic.
- Preserve existing session/counter/replay and late-data rules. A UI/settings task
  does not request a new accounting algorithm or a 100% billing claim.

## Verify and deliver

Run focused checks for the changed behavior and full tests for shared/runtime
changes. Portable tests must remain dependency-free/Python 3.10 compatible;
management tests need Python 3.11+ and `management/requirements-test.txt`.
Use the real isolated Docker/Ubuntu scenarios for startup, failure, rollback and
SQLite preservation. Do not point their disposable modes at production.

Live QA is bounded and read-only except for the reviewed deployment changes.
Check auth rejection/revocation, secret masking, permitted Viewer/data queries,
service isolation and original dashboard/secret/image/mount/database preservation.
Do not stop live monitoring, inject records or modify FortiGate policies to force
test outcomes. Explicit real-device checks are optional and require an available,
authorized source; record unavailable numeric/completeness checks as pending.

Publish only the authorized source scope. Inspect remote heads, respect dependency
PRs and use expected-head merges; never force-push divergent history. After a
verified update, refresh project context and the delivery/QA record with source
head, tests, preservation evidence, access instructions and remaining limitations.
