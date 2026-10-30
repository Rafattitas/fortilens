# One FortiLens console

The unified console is an opt-in source feature after v0.2.0. Published v0.2.0 ZIPs
retain the standalone reporting interface. Do not expect the new flag in those ZIPs.

FortiLens provides one browser origin, one sign-in and shared navigation:

- **Monitoring** embeds the existing native Grafana dashboard and its selectors.
- **Usage overview** and **Devices & VLANs** use the persistent accounting engine.
- **WAN health** retains physical/SLA/route distinctions and observed history.
- **Data quality** retains gaps, pending counters and source/metadata uncertainty.

The integration does not change byte accounting, historical data, alert rules,
notifications or the meaning of unavailable telemetry.

## Fresh installation

From the current source's `portable` directory:

```bash
python3 setup.py init --enable-portal
python3 setup.py start
python3 setup.py doctor
```

`--enable-portal` enables accounting too. Interactive setup offers the console
when accounting is enabled. JSON configurations can explicitly set both
`accounting_enabled` and `portal_enabled` to `true`. Defaults retain compatibility
with existing configurations that do not contain the new setting.

Open the configured `usage_bind`/`usage_port` (loopback port 3002 by default).
Sign in as `admin` with the existing `secrets/usage_admin_password` file. Grafana's
native administrator password is separate and stays inside its existing secret.
For a remote loopback installation, forward server port 3002 using SSH. The
console and embedded monitoring both travel through that one forward.

`start` imports the generated dashboard, verifies the portal's Viewer identity,
backs up the current dashboard ACL before any change, and adds only that user's
dashboard read permission. Other explicit permissions remain intact. To repeat
only that connection step on a generated installation:

```bash
python3 setup.py connect-portal
```

An already-used dashboard UID or a custom installation requires its actual UID;
do not import the generated dashboard over customer edits as a migration shortcut.

## Components and authentication

The existing six monitoring services and optional accounting worker remain.
One lightweight gateway container, `fortilens-portal`, serves the console and
forwards authenticated requests to the existing services. Its limits are 96 MiB
and 0.25 CPU. The accounting worker retains its original volume and limits.

The gateway shares Grafana's network namespace. Grafana trusts auth-proxy headers
only from `127.0.0.1`. The browser's Cookie, Authorization, forwarding and identity
headers are discarded before constructing upstream requests. The gateway supplies
a fixed `fortilens-portal` identity with the **Viewer** role; an Editor or Grafana
administrator identity fails verification. Grafana's upstream session cookies
remain in server memory, scoped to each FortiLens session, and never reach the
browser. This also supports Grafana's native session rotation.

The browser receives only an opaque HttpOnly, SameSite=Strict FortiLens cookie.
Sessions expire after eight hours; logout and gateway restart revoke them. No
password/token is stored in browser storage or URLs. Login is rate limited;
session actions and monitoring POST requests have same-origin checks. The gateway
allows query/feature-evaluation/session-maintenance POSTs and blocks dashboard,
user and administrative writes. Native Grafana permissions remain an additional
authorization boundary.

Read-only report APIs retain Basic authentication for existing automation and
`doctor`. A Basic header alone cannot access embedded Grafana. Prometheus continues
scraping the private worker directly with its existing credential.

## Exposure and lifecycle

Loopback is the default. Before serving the console over a network, terminate
HTTPS at a trusted reverse proxy, preserve its original Host header, and set
`portal_secure_cookie=true`. Keep the
Grafana subpath `/grafana/` on the same origin. Do not publish anonymous dashboards
or use browser-visible Grafana administrator/service tokens for embedding.

The Grafana container publishes the console's host port to the shared namespace's
8081 listener. The worker's separate host port is removed. Grafana retains its
advanced administration listener and native login at `/grafana/`; administrators
can forward its configured port separately when needed. This is an administration
path, while routine users need only FortiLens.

Use Compose to recreate Grafana **and its portal sidecar together** when changing
Grafana's container/network namespace. Restarting only the portal signs users out
but preserves all monitoring/accounting data. Sessions are intentionally local to
one gateway; multi-replica deployments are outside the current single-site scope.

Grafana Live WebSocket streaming is not proxied in this version. Existing dashboard
query/refresh, time selectors and history work through ordinary HTTP requests.
No Grafana Live setting or existing alert configuration is disabled.

## Existing installations

`prepare-upgrade` remains the earlier six-service-to-accounting migration. It is
not a portal migration for an already-running accounting installation. Review a
separate Compose change with backups of its real configuration and Grafana DB:

1. Add a separately built gateway using `portal.py` and `reporting.html`, the
   existing reporting secret, the actual dashboard UID and private worker URL.
2. Apply the auth-proxy/subpath settings from `portal.configure`, with loopback-only
   trust and the shared Grafana network namespace. Preserve existing images,
   secrets, volumes, selectors, queries, data sources and alert/notification settings.
3. Move the existing reporting host port to Grafana's 8081 mapping; keep the worker
   on its existing private network. Bootstrap and grant the dedicated Viewer only
   read permission to the existing dashboard. Back up its previous ACL.
4. Verify native admin access, report totals, actual dashboard queries, selectors,
   logout/replay rejection, private upstreams and unchanged database integrity.

Rollback restores the prior Compose file and dashboard ACL, removes only the new
portal service, and recreates the affected listeners. Never delete data volumes or
restore an old accounting database merely to undo presentation/auth routing.

## Verification limits

Portal access and unchanged report totals can be tested independently of FortiGate
connectivity. They do not establish complete traffic collection or billing accuracy.
When source data is absent, the unified console retains unavailable/NO DATA states.
