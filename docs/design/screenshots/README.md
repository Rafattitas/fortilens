# Shipped interface screenshots

These images render the actual FortiLens management and hosted reporting
frontend with synthetic API fixtures. They contain no production traffic,
credentials or customer records. The site name in the interface identifies the
synthetic lab. Fixture measurements illustrate presentation, not real-device
accuracy or performance.

The HTML prototype at `docs/design/a-precision-prototype.html` remains a separate
historical design reference. It is not served by the product or included in
release archives. The captures here use `management/static/` and the actual
`management.app.hosted_report()` document transformation instead.

| Capture | State |
|---|---|
| [Overview, dark](01-overview-dark.png) | Link timelines, current observations and recorded consumers. |
| [Overview, light](02-overview-light.png) | The same fixtures in the light theme. |
| [Overview, degraded](03-overview-degraded.png) | Failed SLA observation, stale accounting and missing evidence. |
| [Devices with inspector](04-usage-docked.png) | Selected endpoint with daily and monthly history. |
| [Reports](05-reports.png) | Period totals, largest consumers and segment distribution. |
| [Data quality](06-evidence.png) | Coverage, pending counters and source classifications. |
| [Internet links](07-internet.png) | SLA, physical state, measurements and observed history. |
| [Status](08-system.png) | Service state and storage overview. |
| [Diagnostics](09-diagnostics.png) | Collection observations with explicit unknowns. |
| [Settings](10-settings.png) | Site settings without credentials. |
| [First run](11-first-run.png) | Administrator bootstrap form; no token or password filled. |
| [Sign in](12-sign-in.png) | Signed-out form with a synthetic username. |
| [Narrow Overview](14-overview-narrow.png) | 375px viewport. |
| [Narrow Devices](15-usage-narrow.png) | 375px viewport scrolled to the first endpoint. |
| [Applications](16-applications.png) | Labelled usage, largest named applications and filters, with generic example application names. |
| [Live usage](17-live.png) | Current download and upload per device, application, segment and link, refreshed every 10 seconds. |
| [Websites](18-websites.png) | Opt-in: recorded usage by website and the devices that used a selected site (synthetic data). |

## Regenerate

Use development Node.js, Python 3.11+ and a locally installed Playwright Chromium
browser. No Node dependency is added to the deployed application. For example,
from the source root:

```bash
npm install --prefix /tmp/fortilens-capture playwright
/tmp/fortilens-capture/node_modules/.bin/playwright install chromium
FORTILENS_PLAYWRIGHT_MODULE=/tmp/fortilens-capture/node_modules/playwright \
  node tools/capture_screenshots.cjs
```

When reusing an installed Chrome/Chromium, set `FORTILENS_BROWSER_EXECUTABLE`
to its executable path. `FORTILENS_PYTHON` selects Python when it is not available
as `python`. These environment variables also work in PowerShell. Avoid running
the tool in a generated installation or using a customer browser profile.

The script binds only an ephemeral loopback port, blocks requests outside that
origin, accepts only read-only synthetic API requests and uses temporary isolated
browser contexts. It freezes fixture time, waits for local fonts and rejects
JavaScript errors, unhandled routes and outer-page horizontal overflow. It
replaces the seventeen named PNGs and writes [manifest.json](manifest.json), plus
a contact sheet and capture results under ignored `qa-private/presentation/`.
Review every image before committing; automation does not replace visual review.

The manifest records fixture time, browser version, viewport/state and SHA256
hashes of source files and captures. The checked-in set was captured twice with
the recorded browser and all seventeen PNG hashes matched. Identical bytes across
browser, platform or font-rendering changes are not promised. Regenerate the set
and manifest together after frontend changes; do not reuse stale provenance.

These reviewed public assets belong only in the full source release. Runtime
installer and Docker build allowlists remain unchanged. Private QA screenshots
stay excluded from Git and releases.
