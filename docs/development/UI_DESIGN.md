# FortiLens console design

FortiLens is a network operations workspace. Give monitoring and evidence the
usable screen area, keep navigation predictable and use restrained surfaces.
The management layer and existing dark Grafana/report views share one visual
language and login. A setup/settings update must preserve that continuity.

## Visual system

The approved direction is A Precision: a bench instrument with colour reserved for
state and hatching for missing evidence. The source-only prototype at
`docs/design/a-precision-prototype.html` records the design direction;
it is not the application, is not part of release packages and is never served by
the product.

The [canonical screenshot gallery](../design/screenshots/README.md) shows the shipped
management and reporting interfaces rendered with synthetic fixtures. It is
separate from the prototype. The gallery records capture provenance and
regeneration instructions; public screenshots contain no customer data and do
not establish real-device collection health or accounting completeness.

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

Overview is the configured default and first destination. Keep an explicitly
saved valid route; use Settings for an unconfigured installation. Overview brings
link timelines, current observations, recorded consumers and collection evidence
together. The Firewall dashboard opens the real native Grafana dashboard within
the same workspace.

The sidebar is grouped by the question a person is asking, and every page is one
top-level item with its own icon:

```text
FortiLens            Site                                  Account
Overview             Overview                         Reload view
RIGHT NOW            +-------------------------------------------+
  Live usage      •  | Shared link timelines and observed states |
  Internet links     | Current link measurements                 |
  Firewall dashboard | Recorded consumers and segments           |
RECORDED USAGE       | Collection and installation evidence      |
  Devices            +-------------------------------------------+
  Segments
  Applications
  Websites
  Reports
SYSTEM
  Status
  Data quality
  Diagnostics
  Settings
```

- Right now: current state (realtime sessions, link health, Grafana). Recorded
  usage: views of accounting history, with Reports last because it spans them.
  System: FortiLens itself. Add a new page to the group whose question it answers,
  as a top-level item with an icon; do not nest pages under another page.
- Group captions are small uppercase labels. The active item has a tinted
  background and a 3px accent bar, so it does not rely on colour alone.
- Page names stay plain. URL fragments (`#devices`, `#quality`, …) are stable
  even when a visible name changes.

- A compact persistent sidebar and 56px header on desktop. One main scroll region
  for overview/settings/diagnostics; consistent content spacing.
- The Firewall dashboard and hosted reports fill the remaining viewport with a borderless document.
  The shell does not add an outer scrolling page around that embedded viewport.
  Retain native Grafana panels, selectors, query groups and saved layout.
- Native Grafana remains at `/grafana/`. Never globally replace Grafana URLs,
  plugin identifiers or boot settings. Hosted report styling is scoped to
  `/console/`; standalone reporting/dashboard configuration is preserved.
- Below 900px, navigation becomes a labelled drawer with background isolation,
  focus containment, Escape/scrim/close controls and focus restoration. Selecting
  a route, including the current route, closes it. Settings use a native section
  picker on these widths rather than a wall of wrapped tabs.
- Devices and Reports expose their own period controls. The last 7 days are today
  plus the six previous whole days; month uses the accounting timezone. Devices offers endpoint search and a
  docked device-history inspector; Reports summarizes period totals, largest
  consumers and segment distribution from the same accounting engine. Segments
  groups exact VLAN/interface mappings. Data quality opens the existing accounting
  evidence page and respects accounting enablement.
- Status describes service, storage and configuration state separately from
  source collection. Diagnostics retains the existing read-only checks; Settings
  preserves the reviewed configuration workflow.

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
Devices/Segments/Internet links/Data quality pages, masked settings and diagnostic loading/errors. Test first
setup/login through the existing isolated scenario; do not save/apply production
settings or inject fake traffic for visual QA.

Measured primary/secondary/action/link/status text pairs exceed WCAG AA 4.5:1;
input boundaries use a 3:1 contrast target. Keep public examples synthetic and
private site screenshots out of Git. A visual QA pass cannot certify live traffic
accuracy when firewall telemetry is unavailable.
