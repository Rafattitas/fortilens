"""What a saved draft changes, in words a person reads on Review & Apply."""

import notifications  # noqa: E402  (portable/, on the path through .common)
from .models import Settings

# What a person sees for each saved setting: its label and the Settings section
# that holds it. Fields not listed still appear, under their own name.
SETTING_LABELS = {
    "site_name": ("Company / site name", "general"),
    "installation_name": ("Installation name", "general"),
    "timezone": ("Display timezone", "general"),
    "date_format": ("Date format", "general"),
    "fortigate_host": ("FortiGate address", "connection"),
    "api_port": ("HTTPS API port", "connection"),
    "vdom": ("VDOM", "connection"),
    "api_tls_verify": ("TLS certificate verification", "connection"),
    "wan_interfaces": ("Internet interfaces", "interfaces"),
    "wan_confirmed": ("Internet interfaces confirmed", "interfaces"),
    "wan_speeds": ("Line speeds", "interfaces"),
    "monitoring.wan": ("Collect WAN health", "monitoring"),
    "monitoring.sla": ("Collect SLA checks", "monitoring"),
    "monitoring.interfaces": ("Collect interface traffic", "monitoring"),
    "monitoring.syslog": ("Receive syslog", "monitoring"),
    "monitoring.accounting": ("Usage accounting", "monitoring"),
    "monitoring.vlans": ("Segments", "monitoring"),
    "monitoring.websites": ("Website history", "monitoring"),
    "monitoring.server": ("Monitoring host metrics", "monitoring"),
    "syslog_protocol": ("Syslog protocol", "monitoring"),
    "syslog_port": ("Syslog port", "monitoring"),
    "syslog_format": ("Syslog format", "monitoring"),
    "syslog_network": ("Native UDP reception", "monitoring"),
    "sla": ("SLA check name", "monitoring"),
    "grafana_port": ("Native Grafana port", "monitoring"),
    "usage_port": ("Protected reporting port", "monitoring"),
    "monitoring_interval": ("Monitoring interval", "monitoring"),
    "accounting_timezone": ("Accounting days", "monitoring"),
    "expected_devid": ("Expected FortiGate serial number", "monitoring"),
    "optional_exporter_exclusions": ("Exporter exclusions", "monitoring"),
    "retention.loki_days": ("Raw log retention", "storage"),
    "retention.prometheus_days": ("Metric retention", "storage"),
    "retention.ledger_days": ("Accounting ledger retention", "storage"),
    "retention.summary_days": ("Daily summary retention", "storage"),
    "retention.database_bytes": ("Accounting database limit", "storage"),
    "accounting_cpus": ("Accounting processing CPU", "storage"),
    "notifications.telegram.enabled": ("Send alerts to Telegram", "alerts"),
    "notifications.telegram.chat_id": ("Telegram chat ID", "alerts"),
    "notifications.email.enabled": ("Send alerts by email", "alerts"),
    "notifications.email.host": ("Mail server", "alerts"),
    "notifications.email.port": ("Mail server port", "alerts"),
    "notifications.email.security": ("Mail security", "alerts"),
    "notifications.email.user": ("Mail user", "alerts"),
    "notifications.email.sender": ("From address", "alerts"),
    "notifications.email.to": ("Email recipients", "alerts"),
    "notifications.work_hours.start": ("Working hours start", "alerts"),
    "notifications.work_hours.end": ("Working hours end", "alerts"),
    "notifications.work_hours.days": ("Working days", "alerts"),
    "notifications.reports.daily.enabled": ("Daily summary", "alerts"),
    "notifications.reports.daily.time": ("Daily summary time", "alerts"),
    "notifications.reports.weekly.enabled": ("Weekly report", "alerts"),
    "notifications.reports.weekly.day": ("Weekly report day", "alerts"),
    "notifications.reports.weekly.time": ("Weekly report time", "alerts"),
    "notifications.reports.monthly.enabled": ("Monthly report", "alerts"),
    "notifications.reports.monthly.time": ("Monthly report time", "alerts"),
}


def _setting_text(field, value):
    """One saved value as a person reads it; an absent value is "Not set"."""
    if value is None or value in ("", [], {}):
        return "Not set"
    if field == "wan_speeds":
        return ", ".join(
            f"{name} {speed['down_mbps']:g} / {speed['up_mbps']:g} Mbps"
            for name, speed in sorted(value.items())
        )
    if field == "accounting_cpus":
        return f"{value} core" if value in ("0.5", "1") else f"{value} cores"
    if field == "retention.database_bytes":
        return f"{value / 1073741824:g} GiB"
    if field.startswith("retention.") and field.endswith("_days"):
        return f"{value} days"
    if field == "monitoring_interval":
        return f"{value} seconds"
    if field == "accounting_timezone" and value == "fortigate":
        return "FortiGate clock"
    if field == "timezone" and value == "browser":
        return "Each viewer's device"
    if isinstance(value, bool):
        return "On" if value else "Off"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _flatten(settings, prefix=""):
    flat = {}
    for key, value in (settings or {}).items():
        name = prefix + key
        if isinstance(value, dict) and (
            key in ("monitoring", "retention", "notifications")
            and not prefix
            or prefix.startswith("notifications.")
        ):
            flat.update(_flatten(value, name + "."))
        else:
            flat[name] = value
    return flat


def _with_defaults(settings):
    """Fill fields a newer version added, so they are not reported as changed."""
    try:
        return Settings.model_validate(settings).model_dump(mode="json")
    except ValueError:
        return settings


def setting_changes(applied, draft, pending=None):
    """Saved settings that differ from the running ones, in Settings order.

    Each change names its label, its Settings section and both values as text.
    Write-only credentials are never shown, only that a new one waits.
    """
    before, after = _flatten(applied), _flatten(draft)
    order = list(SETTING_LABELS) + sorted((set(before) | set(after)) - set(SETTING_LABELS))
    changes = []
    for field in order:
        old, new = before.get(field), after.get(field)
        if old == new or _setting_text(field, old) == _setting_text(field, new):
            continue
        rule = field.removeprefix("notifications.rules.")
        label, section = SETTING_LABELS.get(field) or (
            ("Alert: " + notifications.RULES[rule][0], "alerts")
            if rule != field and rule in notifications.RULES
            else (field, "general")
        )
        changes.append(
            {
                "field": field,
                "label": label,
                "section": section,
                "before": _setting_text(field, old),
                "after": _setting_text(field, new),
            }
        )
    pending = pending or {}
    if pending.get("token"):
        changes.append(
            {
                "field": "api_token",
                "label": "FortiGate API token",
                "section": "connection",
                "before": "Current token",
                "after": "New token",
            }
        )
    for key, label in (
        ("telegram_bot_token", "Telegram bot token"),
        ("smtp_password", "Mail password"),
    ):
        if pending.get(key):
            changes.append(
                {
                    "field": key,
                    "label": label,
                    "section": "alerts",
                    "before": "Current",
                    "after": "New",
                }
            )
    if "ca" in pending:
        changes.append(
            {
                "field": "custom_ca",
                "label": "Custom trust bundle",
                "section": "connection",
                "before": "Current",
                "after": "New bundle" if pending["ca"] else "Removed",
            }
        )
    return changes
