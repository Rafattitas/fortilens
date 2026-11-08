# Online demo

The online demo at <https://rafattitas.github.io/fortilens/> is the shipped
console UI running entirely in the visitor's browser with synthetic data. It
lets people click through every page without a FortiGate or a server.

## What it is

- `management/static/` and `portable/reporting.html`, copied by
  `tools/build_demo.cjs`. The only changes are path rewrites so the pages work
  under `/fortilens/` instead of `/`. Every rewrite must match, so a UI change
  that breaks one fails the build instead of shipping a broken demo.
- `demo/fixtures.js`: the synthetic API answers. The documentation screenshots
  (`tools/capture_screenshots.cjs`) use the same file, so the demo and the
  screenshots always show the same lab.
- `demo/demo.js`: replaces `fetch` for `/api/` paths with those answers and
  refuses every write with a "read-only demo" message. It also sets the first
  embedded view, which the management service normally writes into its HTML.

Nothing in `demo/` is installed, packaged into a container or loaded by a real
installation.

## What it is not

The demo has no server, so it cannot show native Grafana, sign-in, setup jobs
or any real device. All names are invented and every address is from the
RFC 5737 documentation ranges.

## Build and check locally

```bash
node tools/build_demo.cjs site
node tools/check_demo.cjs site   # needs Playwright, like the screenshot tool
```

`check_demo.cjs` serves `site/` under `/fortilens/`, opens every page in light
and dark themes, and fails on browser errors, missing files, external requests
or a write that is not refused.

## Publishing

`.github/workflows/demo.yml` builds and checks the demo on every pull request.
On `main` it also deploys to GitHub Pages. Pages must be enabled once in the
repository settings with **Source: GitHub Actions**.
