# Security policy

This is an early-access independent project without a guaranteed response SLA. Only the current main branch is maintained; there is no long-term support release line yet.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/Rafattitas/fortilens/security/advisories/new) when it is available. If it is unavailable, open an issue asking for a private contact route **without publishing exploit details, secrets, or affected customer information**. Do not post tokens, passwords, unredacted logs, or complete device configurations in public issues.

Include the affected revision, prerequisites, impact, and a minimal synthetic reproduction. Rotate any exposed credentials in the affected environment.

## Deployment boundaries

- API tokens must use suitable read permissions and restricted trusted hosts. FortiLens does not create or rotate them.
- HTTPS verification is enabled by default. Use a trusted private CA instead of disabling verification where possible.
- Generated secrets live in the local installation directory. Linux uses a private 0700 parent; Windows generation does not establish private NTFS ACLs. Protect backups too.
- Grafana defaults to localhost. Any wider LAN access, TLS termination, firewall policy, and authentication hardening are the operator's responsibility.
- Syslog is a dedicated single-device input. Its default TCP transport is not encrypted or sender-authenticated. Restrict ingress to the intended FortiGate and use a trusted network path.
- The Node Exporter mounts host paths for host monitoring. Review Compose mounts and service privileges before deployment.
- Bundles use a source allowlist. Generated local directories and secrets must never be added to source control.

The optional reports service has a separate generated password and transient browser authorization. Use loopback/SSH tunnelling or a reviewed HTTPS reverse proxy; plain HTTP Basic authentication is not encrypted. SQLite stores endpoint identity/usage metadata and allowlisted fields: protect its volume and backups. An expected device-ID filter is not sender authentication.

Upgrade plans are private and may contain draft credentials. Mounted files must be readable by nonroot containers; protect the 0700 installation parent. Optional notification candidates contain environment references only; never publish real tokens or recipients.

Container image digests pin a reviewed version; they do not guarantee freedom from vulnerabilities. Review upstream advisories and revalidate compatibility when updating pins.
