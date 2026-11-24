"""Rules for the installation's Compose, Prometheus and exporter files.

Pure functions over parsed files: which services exist, CPU limits, exporter
probe exclusions, scrape intervals and how a service definition compares.
"""

from dataclasses import dataclass, field
from pathlib import Path
import re
import yaml
from .common import Problem, setup
from .models import Settings

SERVICES = (
    "fortigate-exporter",
    "prometheus",
    "grafana",
    "loki",
    "alloy",
    "node-exporter",
    "fortilens-usage",
    "fortilens-portal",
    "fortilens-syslog",
)

# Telegram bot token and mail password for alerts, as one write-only secret file.
NOTIFY_SECRET = "secrets/notify_credentials"
NOTIFY_KEYS = ("telegram_bot_token", "smtp_password")
ACCOUNTING_CPUS = ("0.5", "1", "2")


def accounting_cpus(compose):
    """The accounting writer's current CPU limit as a supported choice.

    An existing limit is read, not assumed, so a reviewed manual value is kept
    when it is one of the choices. Anything else shows the template default and
    is only replaced by an explicit Apply.
    """
    service = (compose or {}).get("services", {}).get("fortilens-usage") or {}
    try:
        value = "%g" % float(service.get("cpus", "0.5"))
    except (TypeError, ValueError):
        return "0.5"
    return value if value in ACCOUNTING_CPUS else "0.5"


def set_accounting_cpus(compose, value):
    if "fortilens-usage" in compose.get("services", {}):
        compose["services"]["fortilens-usage"]["cpus"] = value


def optional_probe_policy(record, selected):
    """Replace only explicitly reviewed optional omissions, retaining custom rules."""
    try:
        result = setup.exclude_optional_probes(record, [])
        profile = result.get("probes") or {}
        exclusions = profile.get("exclude", [])
        profile["exclude"] = [
            name for name in exclusions if name not in setup.OPTIONAL_EXPORTER_PROBES
        ]
        if not profile["exclude"]:
            profile.pop("exclude")
        if profile:
            result["probes"] = profile
        else:
            result.pop("probes", None)
        return setup.exclude_optional_probes(result, selected)
    except setup.SetupError:
        raise Problem(
            "exporter_policy_review",
            "Review the existing exporter profile before changing optional capabilities.",
        ) from None


def prometheus_policy(data, interval, *, update_global=True):
    if update_global:
        data.setdefault("global", {}).update(
            scrape_interval=str(interval) + "s", scrape_timeout=str(min(40, interval - 1)) + "s"
        )
    for job in data.get("scrape_configs", []):
        if job.get("scrape_interval") or "scrape_timeout" not in job:
            continue
        value = re.fullmatch(r"(\d+)([smh])", str(job["scrape_timeout"]))
        if not value:
            raise Problem(
                "scrape_policy_review",
                "Review a custom scrape timeout before changing the monitoring interval.",
            )
        seconds = int(value[1]) * {"s": 1, "m": 60, "h": 3600}[value[2]]
        if seconds > interval:
            if job.get("job_name") not in ("fortigate", "node-exporter", "fortilens-accounting"):
                raise Problem(
                    "scrape_policy_review",
                    "A custom scrape timeout exceeds the requested global interval. Review that job on the host.",
                )
            job["scrape_timeout"] = str(interval - 1) + "s"
    return data


def _compose_changes(before, after):
    """Service names whose Compose definition differs, or None if untraceable.

    A changed top-level secret or named volume counts for the services that
    use it. Any other top-level change (networks, project name, extensions)
    cannot be traced to particular services.
    """

    def definition(service):
        # An empty key (ports: []) means the same to Compose as a missing one.
        return {k: v for k, v in (service or {}).items() if v not in (None, [], {})}

    old, new = before.get("services") or {}, after["services"]
    changed = {
        name
        for name in set(old) | set(new)
        if definition(old.get(name)) != definition(new.get(name))
    }
    for key in set(before) | set(after):
        if key == "services" or before.get(key) == after.get(key):
            continue
        if key not in ("secrets", "volumes"):
            return None
        was, now = before.get(key) or {}, after.get(key) or {}
        names = {name for name in set(was) | set(now) if was.get(name) != now.get(name)}
        for service_name, service in new.items():
            for entry in service.get(key) or []:
                if isinstance(entry, str):
                    used = entry if key == "secrets" else entry.split(":")[0]
                else:
                    used = (entry or {}).get("source")
                if used in names:
                    changed.add(service_name)
    return changed


def _same_content(installed, candidate):
    """True when two YAML/JSON files hold the same data, however they are written."""
    if installed.suffix not in (".json", ".yaml", ".yml"):
        return False
    try:
        return yaml.safe_load(installed.read_text(encoding="utf-8-sig")) == yaml.safe_load(
            candidate.read_text(encoding="utf-8-sig")
        )
    except (OSError, UnicodeError, yaml.YAMLError):
        return False


def _container_port(binding):
    """The container side of a short-syntax Compose port, e.g. 1514 in 0.0.0.0:514:1514/udp."""
    return binding.rsplit(":", 1)[-1].split("/")[0]


def _syslog_bindings(ports):
    """The short-syntax bindings that publish the syslog listener's container port."""
    return [x for x in ports if isinstance(x, str) and _container_port(x) == "1514"]


@dataclass
class _Candidate:
    """One Prepare on a registered installation: its inputs and the files it changes.

    ``compose`` and ``c`` are edited in place. ``files`` maps installation paths
    to candidate bytes in the order they are written, which is the plan's order.
    """

    stage: Path
    settings: Settings
    c: dict
    compose: dict
    generated: dict
    active: dict
    site_file: str
    applied: dict
    endpoint_changed: bool
    files: dict = field(default_factory=dict)

    @property
    def native(self):
        return self.settings.syslog_format == "fortigate"

    @property
    def was_native(self):
        return self.active.get("syslog_format") == "fortigate"

    @property
    def host(self):
        return self.native and self.settings.syslog_network == "host"

    @property
    def was_host(self):
        return self.was_native and self.active.get("syslog_network", "bridge") == "host"

    @property
    def old_port(self):
        return self.active.get("syslog_port", 1514)
