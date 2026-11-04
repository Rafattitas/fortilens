# A Precision: backend and packaging follow-ups

The A Precision redesign changed only frontend files. These items need a change
outside the frontend. None of them has been implemented.

## Decide first

### Ship the font files with installed copies

`management/install.py` copies an explicit list of `management/static` files. It does
not include `static/fonts/`, so an installation built from it will not serve IBM Plex
Sans. Pages then fall back to the system font and still work.

Needed: add the four `static/fonts/IBMPlexSans-*-Latin1.woff2` files and `static/fonts/OFL.txt`
to that list, then confirm `tools/release.py` picks them up.

### Reports opened outside the console

`portable/portal.py` and `portable/usage_service.py` serve only `reporting.html`, so a
report opened directly, outside the management console, uses the system font.

Needed, if this matters: a route for the font files on those services.

## Data the prototype shows that the API does not provide

| Prototype element | Current state | Needed |
|---|---|---|
| Latency, loss and bandwidth traces in Network channels | Overview shows current values and observed state history. Traces stay in live monitoring. | A time series per WAN link from `/api/wan`. |
| Log rate channel | Not shown. | A received-log count over time. |
| Firewall CPU, memory and sessions | Not shown. Overview shows installation state instead. | A firewall health summary from the API. |
| Opening a device directly from Overview or the palette | Overview links to the Usage page. | A route that opens the report frame on one device. |

## Small efficiency item

`GET /` without a `view` parameter still starts the first frame on `monitoring`, while the
default page is now Overview. The frame is removed as soon as the app starts, so the
result is correct but makes an unnecessary request. Changing the default in
`management/app.py` would remove it.

## Styling kept out of the backend

`HOSTED_REPORT_CSS` in `management/app.py` still contains the previous dark colours. The
report page overrides those rules with more specific selectors. Removing the old rules
would simplify the report stylesheet.

The Grafana dashboard stays in its dark theme when the console is in light mode, because
its address is fixed by the server.
