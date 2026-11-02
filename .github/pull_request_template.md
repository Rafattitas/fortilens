## Summary and motivation

Describe the concrete problem, why this change is needed and the resulting
behavior. Link the related issue, or explain why this is a standalone change.

## Scope

List the meaningful changes. Separate runtime behavior changes, formatting,
release tooling and documentation; keep unrelated work in another PR.

## Compatibility

Explain any impact on CLI arguments/API contracts, configuration formats,
installation paths, Compose project/volume identity, database schemas,
accounting/WAN semantics, dashboards/queries and existing deployments.
If none, state why the touched scope preserves those contracts.

## Validation

List exact commands, environments and results; link CI for the final source head.
Include skipped/unexecuted checks and remaining limits. Archive changes need
extracted-artifact checks; fresh-install tests alone do not prove adoption safety.

## Security and documentation

- [ ] Authentication, authorization, helper isolation and secret-handling impact reviewed.
- [ ] Relevant documentation/changelog updated, or no update needed with explanation.
- [ ] Fixtures/examples are synthetic or sanitized; no secrets, generated installation, production configuration, raw customer data or private QA artifacts are included.

## Deployment and publication

Describe any separately required rollout, migration or release review. This PR
does not authorize production Apply, firewall changes, credential rotation,
notification tests, data deletion, release publication or a merge by automation.
