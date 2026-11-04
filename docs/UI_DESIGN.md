# FortiLens console design

FortiLens is a network operations workspace. Give monitoring and evidence the
usable screen area, keep navigation predictable and use restrained surfaces.
The management layer and existing dark Grafana/report views share one visual
language and login. A setup/settings update must preserve that continuity.

## Visual system

The approved direction is A Precision: a bench instrument with colour reserved for
state and hatching for missing evidence. The approved prototype is kept in the source repository at
`docs/design/a-precision-prototype.html`. It is a design reference only, is not part of
release packages and is never served by the product.

Use IBM Plex Sans from `management/static/fonts/` with the system stack as its fallback,
and tabular numerals for measurements. All assets are local; no font service, CDN,
animation library or new build toolchain is required. Product text remains English.
Controls are compact for a mouse and keep 44px targets for touch and narrow screens.
The tokens below are superseded by the light and dark tokens at the top of `app.css`.

| Token | Value | Role |
|---|---|---|
| Background | `#111217` | Continuous workspace and hosted reporting surface. |
| Navigation | `#0d1017` | Persistent navigation, distinct without a floating card. |
| Surface | `#181c25` | Controls/loading feedback; avoid wrapping every section. |
| Text | `#e9edf5` | Primary labels and values. |
| Secondary | `#a5afc1` | Descriptions/context with readable contrast. |
| Accent | `#87b4ff` | Links, keyboard focus and selected navigation. |
| Primary action | `#376cda` | White text; reserve emphasis for the main action. |
| Positive / Warning / Failure | `#78d7b1` / `#efc276` / `#ffa1af` | Always paired with explicit state text. |

The summary is a divided strip, not a collection of floating cards. Use headings,
spacing and thin dividers for services, diagnostics and settings. Keep borders
where they identify an interactive input, state badge, warning or data structure.
Configured services do not prove source capture; preserve evidence notes and
distinguish pending drafts from the applied configuration.

## Layout and navigation

Overview is the configured default and first network destination, with live
monitoring one click away. Keep an explicitly saved valid route; use Settings
for an unconfigured installation. The real native dashboard is the main content,
with restrained navigation around it:

```text
FortiLens       Site                                      Account
Monitoring     Network monitoring                     Reload view
WAN health     +------------------------------------------------+
Device usage   | Native selectors, firewall state and WAN traffic |
VLAN analytics |                                                |
Reports        |               Remaining viewport               |
Data quality   |                                                |
Overview       +------------------------------------------------+
Settings
Diagnostics
```

- A compact persistent sidebar and 56px header on desktop. One main scroll region
  for overview/settings/diagnostics; consistent content spacing.
- Monitoring/reports fill the remaining viewport with a borderless document.
  The shell does not add an outer scrolling page around that embedded viewport.
  Retain native Grafana panels, selectors, query groups and saved layout.
- Native Grafana remains at `/grafana/`. Never globally replace Grafana URLs,
  plugin identifiers or boot settings. Hosted report styling is scoped to
  `/console/`; standalone reporting/dashboard configuration is preserved.
- Below 900px, navigation becomes a labelled drawer with background isolation,
  focus containment, Escape/scrim/close controls and focus restoration. Selecting
  a route, including the current route, closes it. Settings use a native section
  picker on these widths rather than a wall of wrapped tabs.
- Reports expose their own period controls. Week means rolling 168 hours; month
  uses the accounting timezone. The direct Data quality navigation reuses the
  existing accounting evidence page and respects accounting enablement.

## Interaction and accessibility

Visible labels, keyboard focus, a skip-to-content link, 44px primary touch targets,
state text and reduced-motion support are required. Do not disable browser zoom.
Tables may scroll horizontally inside their own region; the page must fit the
viewport. Inputs remain 16px on small screens to avoid unwanted mobile focus zoom.

Startup has explicit loading feedback rather than briefly showing unapplied
defaults. Authenticated initial HTML includes a parser-created protected launch
frame. This avoids the blank initial document observed with automatic JavaScript
frame navigation in Chrome. The root document never waits for the management
helper. The launch route checks configured/active features and accepts only fixed
report destinations or the validated canonical native dashboard path. It retains
the normal session boundary and `no-store` policy.

The selected view is also recorded in the URL query so a full-page reload starts
the correct document. Existing hash links remain valid; an initial hash-only link
to a different view is resolved with one same-origin page replacement. Sign-in
retains the same protected session through that replacement. Switching embedded
pages after entry creates a fresh browsing context so the old
page cannot remain visible under a new heading. The document can show its own
loader; bounded readiness checks do not wait for every Grafana subresource. Leaving an
embedded page unloads it to stop hidden refresh work. Preserve error recovery,
write-only secrets, edited-review invalidation and server-side authorization.
Hosted reports preserve the application's hidden state during their existing
authentication handshake. They show one managed loading/recovery message instead
of a second credential form. No API authorization is replaced by that styling.
Permanent settings use ordinary headings; numbered steps belong to first setup.

Only successful, authenticated fingerprinted native JavaScript/CSS responses can
use private immutable browser caching. HTML, API data, unsuccessful responses,
unfingerprinted files and unauthenticated requests retain `no-store` behavior.

## Design QA

Review actual rendering at 375px, tablet, landscape and desktop widths. Check main
and frame bounds, document overflow, drawer keyboard order/closure and settings
section changes. Inspect monitoring selectors and actual day/week/month reports,
VLAN/WAN/quality pages, masked settings and diagnostic loading/errors. Test first
setup/login through the existing isolated scenario; do not save/apply production
settings or inject fake traffic for visual QA.

Measured primary/secondary/action/link/status text pairs exceed WCAG AA 4.5:1;
input boundaries use a 3:1 contrast target. Keep public examples synthetic and
private site screenshots out of Git. A visual QA pass cannot certify live traffic
accuracy when firewall telemetry is unavailable.
