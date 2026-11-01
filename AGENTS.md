# FortiLens agent entry point

Read [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) before changing this project.
For implementation, deployment or QA work, use the project skill at
[.agents/skills/fortilens-maintainer/SKILL.md](.agents/skills/fortilens-maintainer/SKILL.md).
It is stored with the repository so agents without skill discovery can read the
same instructions directly. Current user instructions and actual environment
evidence take priority over historical examples in documentation.

## Product and architecture

FortiLens monitors one FortiGate per independent installation. The primary web
console uses existing Grafana monitoring and protected accounting reports. Keep
product text, code comments and distribution documentation in English.

The nonroot management service must stay independent of Grafana/Loki/Docker.
Only the authenticated Unix-socket helper operates the fixed registered Compose
project. Do not give the web process a Docker socket, Docker-group membership,
arbitrary commands, writable monitoring roots or browser-selected filesystem paths.

## Preserve an existing installation

- Source checkouts and generated installations are different directories. Inspect
  the actual registry, project, Compose file, config mounts and dashboard UID.
- Do not run `init` over an existing stack or guess its Compose project name.
  Never delete its volumes, recreate its accounting DB, rotate secrets implicitly,
  reset native dashboards or change accounting source/timezone without migration.
- Existing alerts/notification routes are outside ordinary setup/upgrade work.
  Do not activate candidates or send notifications without the user's instruction.
- The monitoring gateway shares Grafana's network namespace. Recreate the two
  together if Grafana must be replaced. Preserve unrelated services.
- Keep production credentials, raw logs, customer exports, private operational
  notes and screenshots out of Git and container build contexts. The installer's
  source allowlist is deliberate; extend it only for reviewed application files.

## Evidence and testing

Running containers do not prove collection health or accurate usage. Missing
samples/logs remain unknown, never a confirmed WAN outage or zero traffic. Report
deployment success separately from real-device numeric/completeness verification.

The portable CLI is Python 3.10+ and dependency-free. Management requires
Python 3.11+ and `management/requirements-test.txt` for tests. Run relevant tests,
then the full suite for shared/runtime changes. Use isolated Docker projects,
volumes and synthetic inputs for failure, rollback, restart and accounting tests;
never inject test logs or stop production monitoring to simulate an outage.

Use the authorized deployment/release scope. Work on a `codex/` branch for changes;
publish or merge when the user requests it. Inspect current branch heads and CI,
and never force-push divergent history. Update the project context and validation
record after meaningful verified changes. See [docs/QA.md](docs/QA.md).
