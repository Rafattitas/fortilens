# Frontend design review

Reviewed November 2, 2026 with the requested Anthropic `frontend-design` skill.
This is an independent review of the in-progress `codex/console-design-polish`
draft. The original design implementation chat is still active in the same source
directory. Findings below are a snapshot, not a declaration that the final design
is approved or deployed. No production configuration mutation was used here.

## Skill installation and application

The personal skill was installed from `anthropics/skills`, revision
`ca45fc496a3436251f191354091d557da31c15b5`, path `skills/frontend-design`.
Its installed SKILL.md Git blob is
`a5333457c414d20d625f307df945842c0952ecc3`, matching the fetched source exactly.
The upstream license file is present. The skill is available for automatic
discovery on subsequent turns; its full instructions were also read and applied
directly during this review. The skill itself is not bundled into FortiLens.

The review applies the subject-specific hierarchy, restrained structure,
intentional type/palette, plain interface copy and screenshot/self-critique
guidance. The subject is an operator's firewall/traffic workspace. It calls for
the real monitoring surface, data tables and honest evidence states, not a
marketing hero, repeated generic cards or decorative motion.

## Findings to resolve before final sign-off

| Priority | Finding | Evidence | Recommended correction |
|---|---|---|---|
| P2 | Network monitoring remains a secondary destination. | With no saved hash, `enter()` selects `overview`; Monitoring is the fifth navigation item. The first screen presents management/process status rather than the firewall workspace. | Make Monitoring the first workspace destination and default configured entry, retaining Overview for service/settings status. Respect explicitly saved pages and disabled monitoring. |
| P2 | Draft and applied configuration are visually ambiguous. | The preview heading showed `Draft revision 1`, while the summary said `Configuration: Configured / Revision 1`. `renderSystem()` uses the current draft revision for that summary. | Label applied and pending revisions separately. A saved draft must not appear applied. |
| P2 | Input boundary contrast narrowly misses the 3:1 non-text target. | `#56637a` input boundary against `#111722` measures 2.96:1. | Raise boundary contrast above 3:1; the report view already uses a stronger boundary. Keep visible labels and focus rings. |
| P2 | Report navigation briefly exposes a second sign-in prompt while loading. | The browser first showed the report sign-in form together with the report layout; both settled into the correct authenticated report afterward. The hosted CSS forces `.layout` to display even while it has `hidden`. | Preserve hidden-state semantics, scope layout styling to the visible application, and show a managed loading state while report authentication initializes. Do not ask for a second credential in the steady managed flow. |
| P3 | Generic labels and technical implementation copy remain. | `NETWORK OPERATIONS` above `Network overview`, uppercase navigation captions, `Independent control plane`, and decorative arrows on diagnostic links. | Remove repeated eyebrows, use sentence case, and describe what an operator can inspect or do. Keep actual technical terms only where they help a decision. |
| P3 | Permanent Settings still look like a numbered first-run sequence. | Section markers 02–07 are rendered for an already configured installation. | Show step numbering during first-run setup; use ordinary field-group headings for permanent settings. |

These findings were established from actual preview rendering plus the source,
not from a generic score. They are not claims that the backend accounting or
authentication is defective. The design implementation remains responsible for
the fixes and final regression/deployment acceptance.

## Improvements already verified in the draft

- The workspace and hosted content use a consistent dark base. Overview metrics
  form an unboxed strip, and service rows no longer sit in repeated rounded cards.
- Monitoring renders through the real protected Grafana route. At 1280×720, the
  frame had no border and 539px of usable height. The outer page did not scroll;
  the monitoring content has its own native scroll region.
- At 375×812 the document width was 375px: no outer horizontal overflow.
- Opening mobile navigation produced an expanded trigger and a modal Workspace
  dialog; the background received `inert`. Shift+Tab from its first close control
  wrapped to Diagnostics; Tab wrapped back to the close control. Escape closed
  the drawer, made the offscreen navigation inert and returned focus to its trigger.
- Primary text contrast is 15.94:1; secondary text 8.46:1; links 8.91:1; white text
  on the primary action 4.86:1. These are measured token pairs, not a complete
  accessibility certification.
- No source telemetry was invented. The embedded dashboard showed the existing
  scrape failure/NO DATA states. A healthy host or process remains a separate fact.
- Device reports completed their asynchronous load, showing the real accounting
  timezone and empty-evidence state with null-value dashes. The transient prompt
  above disappeared without entering another password. Permanent Settings loaded
  its existing site values; no field was saved or applied during review.

The mobile review viewport was reset afterward. No Apply, token replacement,
firewall write, alert activation or production restart was part of this review.
The locally running preview intentionally rejects configuration mutations.

## Source and verification limits

The draft is mutable while implementation continues. The reviewed snapshot hashes
were:

| Source | SHA256 |
|---|---|
| management/static/app.css | 2def427469615206fc204d62e3660872f213cf8ad5af2d5bb2bc55b7be403845 |
| management/static/index.html | b17d74d97f54eaedae02de5cc9ba1729469b7738aed470f9c911ddd59e596424 |
| management/static/app.js | c9a1d4a7973592d556df479629bbc831e2f316581ffde9203e4a127f1964300a |
| management/app.py | 0a438effed3b9bd69fa324db3b7ee0d158407bf74ac7e153b761effe91b3ae6f |

After the remaining corrections, rerun the relevant behavior tests and inspect
monitoring, reports, data quality, settings, sign-in/out, keyboard and responsive
layouts on the final source. Preserve production state as required by
[QA.md](QA.md). Do not infer final release readiness from this draft review.

Private browser evidence remains outside public Git, in the operational
workspace's output directory. Live FortiGate numeric/source completeness remains
pending. See [UI_DESIGN.md](UI_DESIGN.md) for the target design contract.

## Follow-up implementation

The implementation addresses the six draft findings above:

- Monitoring is first in network navigation and is the configured default when
  enabled. Explicit saved routes remain valid; service/configuration status lives
  under System overview. First setup still opens Settings.
- The summary distinguishes a pending draft and its applied revision. Input
  boundaries use the stronger `#63728b` token.
- Hosted reporting explicitly preserves `hidden` application state, suppresses
  its duplicate credential form and shows managed connection/recovery feedback.
  Existing server-side session/API authorization is unchanged.
- Redundant page eyebrows, uppercase navigation captions and decorative link
  arrows are removed. Labels describe operator tasks; native data tables and
  saved dashboard contents remain intact.
- Numbered settings markers appear only during first setup. Permanent settings
  have ordinary field-group headings and a compact section picker on mobile.

Local verification passed160 Python tests and a separate compilation check of
all three shipped browser scripts. Actual browser checks verified managed report
loading without a second sign-in prompt, first/last drawer focus wrapping, Escape
and focus restoration, configured Settings without step numbering, report periods,
and375px/tablet/landscape layouts without outer horizontal overflow. Final commit
CI and production preservation evidence are recorded in the delivery PR/private
operational report; the draft snapshot hashes above remain historical.
