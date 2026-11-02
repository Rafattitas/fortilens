# Repository governance recommendations

These are maintainer recommendations. Repository cleanup does not change access,
branch protection, GitHub features or publishing permissions.

## Observed configuration

Read-only inspection on November 2, 2026 found:

| Setting | Observed state |
|---|---|
| Repository | Public; default branch `main` |
| Issues | Enabled |
| Discussions | Disabled |
| Wiki | Enabled; wiki content was not audited |
| Repository rulesets | The accessible ruleset list was empty |
| Classic `main` branch protection | Unknown: GitHub returned HTTP 403, `Resource not accessible by integration` |
| Private vulnerability reporting | Not established by the available metadata; verify in repository settings |

An empty ruleset list does not establish that classic branch protection is
absent. Permissions shown for repository content do not prove administration
access. No repository settings were changed.

## Protect `main`

In **Settings → Rules → Rulesets**, create or review an active branch ruleset for
`refs/heads/main` (or use equivalent classic protection):

1. Require a pull request before merging; resolve review conversations before
   merge. Require one approval once an independent maintainer/reviewer is
   available. A sole-maintainer repository should avoid an approval requirement
   that leaves every PR permanently unmergeable.
2. Require successful CI for the exact proposed head and require the branch to
   be up to date before merging. Select the actual GitHub check contexts after
   the updated workflow has run; do not type guessed context IDs.
3. Block force pushes and branch deletion. Do not add routine bypasses for
   automation tokens. Document any tightly limited maintainer emergency bypass.
4. Keep release tags and publication under explicit maintainer control. Retain
   existing tags and published files unchanged.

The existing required behavior checks should include all four portable matrix
checks (`Python 3.10 / ubuntu-latest`, `Python 3.12 / ubuntu-latest`,
`Python 3.13 / ubuntu-latest`, `Python 3.12 / windows-latest`),
`Isolated Docker pipeline`, `Web management / Python 3.12` and
`Real Ubuntu systemd installer`. Add the quality/coverage, dependency audit and
distribution checks using their exact observed final workflow contexts. The
[QA guide](QA.md) and [release guide](RELEASING.md) describe what these checks
validate. A historical green run is not validation of a different source head.

Do not automatically merge dependency updates. Python versions, action pins,
container digests and management pins each need compatibility/security review.
Do not reduce test scope merely to obtain green checks.

## Small label set

Reuse existing labels where possible; add only labels the maintainers will use:

| Label | Meaning |
|---|---|
| `bug` | Reproducible current behavior that needs correction |
| `enhancement` | Scoped future capability |
| `documentation` | User/contributor guide or example improvement |
| `compatibility` | FortiOS, Python, installation or deployment compatibility |
| `testing` | Test fixtures, verification evidence or coverage |
| `devops` | CI, packaging and release engineering |
| `performance` | Measured capacity, storage or query-cost work |
| `good first issue` | Bounded task with enough context for a new contributor |
| `help wanted` | Maintainer-confirmed contribution opportunity |
| `security` | Public, non-sensitive hardening work only |

Vulnerability details belong in private reporting, not a public issue carrying a
`security` label. The templates do not assume these labels already exist.

## Community channels

- Keep version-controlled `docs/` as the primary documentation so changes receive
  code review and stay associated with a source revision. Preserve existing wiki
  material until it is inventoried; migrate useful content through normal PRs
  before deciding whether the wiki is still needed.
- Keep Discussions optional while support volume is small. If maintainers later
  enable it, start with **Q&A** and **Ideas**, and link it from issue guidance.
  Avoid duplicating actionable defects across issues and Discussions.
- Use the existing contribution/security guides and focused issue forms. Do not
  require customers to publish a network topology, address, serial number,
  credential or raw traffic capture to ask for help.
- Verify and enable private vulnerability reporting if the repository's policy
  permits it. Until confirmed, follow [SECURITY.md](../SECURITY.md): request a
  private contact route without disclosing exploit or customer details publicly.

This guide recommends policy; applying it requires a separate authorized
repository-settings action by an account with the necessary permissions.
