# Upgrade from v0.1.0

Download the new source/bundle into a separate code directory. Keep the existing installation at its **original absolute path**: the path determines its Compose project and therefore its volume names. Never run `init` over it or move it to start an upgrade.

This migration adds a seventh optional service with its own SQLite volume. It does not delete data, rotate secrets, change FortiGate configuration, provision alerts, or import/overwrite the running dashboard. Existing retention and custom service settings are preserved. The one known unavailable v0.1.0 default exporter digest is repaired to the published v1.25.0 linux/amd64 manifest; custom image references are preserved. The private plan lists every change.

Back up the installation and take application-consistent volume snapshots using your normal backup procedure first. Keep the old release and exported live Grafana dashboard. An upgrade plan backs up changed configuration files, not the contents of monitoring databases.

From the **new** `portable/` code directory (or extracted `forti-monitor/`):

```bash
python3 setup.py prepare-upgrade --output /absolute/path/to/existing/local --enable-accounting --accounting-timezone UTC --expected-devid YOUR_FORTIGATE_SERIAL
```

Review `.upgrades/usage-<ID>/plan.json`, the private `candidate/` files and `dashboard.visual-candidate.json`. They contain local configuration and private draft credentials; never publish the folder. Preparation makes no runtime changes. Custom non-JSON YAML, multiple syslog listeners, occupied new file names and later configuration edits are refused rather than overwritten. Merge such installations manually after reviewing the generated candidate in a separate directory.

The plan changes **newly received** Loki timestamps to receipt time, retaining the FortiGate `eventtime` field inside each log line. This lets short incremental polls find late deliveries. It does not rewrite old Loki timestamps or restore missing historical logs. Historical VLAN mappings cannot be established by today's inventory.

After approval in your environment, apply the reviewed file changes:

```bash
python3 setup.py apply-upgrade --output /absolute/path/to/existing/local --plan usage-<ID> --accept-config-changes
python3 setup.py start --output /absolute/path/to/existing/local
python3 setup.py doctor --output /absolute/path/to/existing/local
```

`apply-upgrade` verifies input and candidate hashes, saves the original files, and replaces only the allowlisted files. It does not start services. `start` is the explicit runtime action: it builds the worker and may recreate Prometheus/Alloy for the reviewed configuration changes. Existing Grafana edits are preserved. Review/import the visual candidate separately if wanted.

Initial backfill is bounded and may take time. The protected reports show processing lag and unknown traffic coverage. A fast empty query does not prove a complete month. Do not claim historical VLAN totals where the engine labels mappings unknown.

## Rollback

Stop the new writer **before** restoring the original Compose configuration:

```bash
python3 setup.py stop-accounting --output /absolute/path/to/existing/local
python3 setup.py rollback-upgrade --output /absolute/path/to/existing/local --plan usage-<ID> --accept-config-changes
python3 setup.py start --output /absolute/path/to/existing/local
```

Rollback checks backup/current hashes and refuses to overwrite subsequent operator edits. It restores original configuration and leaves new worker files, private credentials and `usage-data` intact for recovery. No volume is deleted. A stopped orphan worker can remain listed in Docker; do not remove its volume. Rolling back the known bad exporter reference may depend on the previously cached image; if unavailable, retain the reviewed repaired image in a manual merge.

If a file replacement was interrupted, the private manifest records `APPLYING`; rollback can restore the saved originals after hash checks. Do not edit or delete the plan before recovery. A second migration into an already enabled engine is deliberately refused: preserve the database and review future schema changes separately.
