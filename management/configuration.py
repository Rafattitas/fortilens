"""Revisioned candidate generation and guarded configuration-only recovery.

This module never starts Docker and never opens a monitoring database. File roots
and project identity are supplied by the host installer, never by HTTP clients.
"""

from copy import deepcopy
import json
from pathlib import Path
import re
import ssl
import threading
import time
import urllib.parse
import yaml
from .common import Problem, atomic, guarded, load, report_failure, setup
import notifications  # noqa: E402  (portable/, on the path through .common)
from .models import Change, Settings

from .candidate import CandidateMixin
from .plans import PlansMixin

# Re-exported: callers and tests import these names from configuration.
from .compose_policy import (  # noqa: F401
    ACCOUNTING_CPUS,
    NOTIFY_KEYS,
    NOTIFY_SECRET,
    SERVICES,
    _compose_changes,
    accounting_cpus,
    optional_probe_policy,
    prometheus_policy,
    set_accounting_cpus,
)
from .settings_changes import SETTING_LABELS, _with_defaults, setting_changes  # noqa: F401


# Secrets sent to a server the settings name: a saved one goes only to the
# server it was entered for. (The Telegram token only ever goes to Telegram.)
SECRET_DESTINATIONS = {
    "token": ("token_reentry_required", "FortiGate"),
    "smtp_password": ("password_reentry_required", "mail"),
}


def destination(settings, key):
    """The server a secret is sent to under ``settings``: [host, port]."""
    settings = settings or {}
    if key == "token":
        return [settings.get("fortigate_host", ""), settings.get("api_port", 443)]
    email = (settings.get("notifications") or {}).get("email") or {}
    return [email.get("host", ""), email.get("port", 587)]


class Configuration(CandidateMixin, PlansMixin):
    def __init__(self, root, control, *, project=None, compose_file="compose.yaml"):
        self.root, self.control = Path(root).resolve(), Path(control).resolve()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,80}", project or ""):
            raise Problem(
                "project_required",
                "Register the existing Compose project explicitly; volume identity must not be guessed.",
            )
        self.project = project
        self.compose_name = guarded(self.root, compose_file).relative_to(self.root).as_posix()
        self.control.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.state_path = self.control / "configuration.json"
        self.secret_path = self.control / "pending-secrets.json"
        if not self.state_path.exists():
            self._import()

    def _import(self):
        existing = (self.root / self.compose_name).exists()
        cpath = "site.json" if (self.root / "site.json").exists() else ".fortilens/site.json"
        if existing and not (self.root / cpath).is_file():
            raise Problem(
                "site_config_missing",
                "Register a deployment with an existing FortiLens site configuration. No initialization was performed.",
            )
        self.site_name = cpath if existing else "site.json"
        c = load(self.root / self.site_name, {})
        d = {
            key: c[key]
            for key in Settings.model_fields
            if key in c and key not in ("wan_interfaces", "monitoring", "retention")
        }
        if existing:
            d["wan_interfaces"] = c.get("confirmed_wan_interfaces", [])
            d["wan_confirmed"] = bool(d["wan_interfaces"])
            d["monitoring"] = {
                "accounting": c.get("accounting_enabled", False),
                "vlans": c.get("accounting_enabled", False),
                "websites": c.get("accounting_websites", False) is True,
            }
            d["retention"] = {
                "ledger_days": c.get("accounting_ledger_days", 3),
                "summary_days": c.get("accounting_summary_days", 400),
                "database_bytes": c.get("accounting_max_bytes", 4294967296),
            }
            # Read actual policies, never assume that template defaults are live.
            compose = self.compose()
            d["accounting_cpus"] = accounting_cpus(compose)
            prom = self.bound_file(compose, "prometheus", "/etc/prometheus/prometheus.yml")
            global_config = (yaml.safe_load(prom.read_text()) or {}).get("global", {})
            interval = re.fullmatch(
                r"(\d+)([smh])", str(global_config.get("scrape_interval", "1m"))
            )
            if not interval:
                raise Problem(
                    "scrape_policy_review",
                    "Register a whole-second monitoring interval before adoption.",
                )
            d["monitoring_interval"] = int(interval[1]) * {"s": 1, "m": 60, "h": 3600}[interval[2]]
            loki = self.bound_file(compose, "loki", "/etc/loki/local-config.yaml", optional=True)
            if loki:
                value = yaml.safe_load(loki.read_text()) or {}
                retention = value.get("limits_config", {}).get("retention_period", "")
                match = re.fullmatch(r"(\d+)([hd])", str(retention))
                if match and int(match[1]) > 0 and (match[2] == "d" or int(match[1]) % 24 == 0):
                    d["retention"]["loki_days"] = int(match[1]) // (24 if match[2] == "h" else 1)
                else:
                    raise Problem(
                        "retention_policy_review",
                        "Register an explicit whole-day Loki retention policy before adoption. No default was substituted.",
                    )
            else:
                raise Problem(
                    "custom_mount_review",
                    "Register the actual Loki configuration mount before adoption.",
                )
            found = False
            for argument in compose["services"].get("prometheus", {}).get("command", []):
                m = re.fullmatch(r"--storage.tsdb.retention.time=(\d+)d", argument)
                if m:
                    d["retention"]["prometheus_days"] = int(m[1])
                    found = True
            if not found:
                raise Problem(
                    "retention_policy_review",
                    "Register an explicit whole-day Prometheus retention flag before adoption. No default was substituted.",
                )
        if not existing:
            d["fortigate_host"] = (
                "192.0.2.1"  # Draft-only placeholder, visibly not a connection result.
            )
        settings = Settings.model_validate(d).model_dump()
        atomic(
            self.state_path,
            {
                "revision": 0,
                "applied_revision": 0 if existing else None,
                "settings": settings,
                "applied": settings if existing else None,
                "configured": existing,
                "site_file": self.site_name,
            },
        )

    def state(self):
        try:
            data = load(self.state_path)
        except (OSError, ValueError) as exc:
            report_failure("configuration_state_read_failed", exc)
            raise Problem(
                "configuration_state_unavailable",
                "Saved configuration is unavailable. Review host storage; do not initialize over an existing installation.",
                503,
            ) from None
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("site_file"), str)
            or not isinstance(data.get("settings"), dict)
            or type(data.get("revision")) is not int
            or data["revision"] < 0
            or type(data.get("configured")) is not bool
        ):
            raise Problem(
                "configuration_state_invalid",
                "Saved configuration needs host-side review. Existing files and monitoring data were not reset.",
                503,
            )
        guarded(self.root, data["site_file"])
        self.site_name = data["site_file"]
        return data

    def compose(self):
        p = guarded(self.root, self.compose_name)
        try:
            if p.exists() and not p.is_file():
                raise ValueError("invalid_compose_file")
            value = yaml.safe_load(p.read_text(encoding="utf-8-sig")) if p.is_file() else None
        except OSError as exc:
            report_failure("compose_read_failed", exc)
            raise Problem(
                "configuration_unavailable",
                "The registered Compose file is unavailable. Review host storage and permissions.",
                503,
            ) from None
        except (ValueError, yaml.YAMLError) as exc:
            report_failure("compose_invalid", exc)
            raise Problem(
                "compose_unsupported", "The registered Compose file cannot be safely interpreted."
            ) from None
        if value is not None and (
            not isinstance(value, dict) or not isinstance(value.get("services"), dict)
        ):
            raise Problem(
                "compose_unsupported", "The registered Compose file cannot be safely interpreted."
            )
        return value

    def bound_file(self, compose, service, container_path, optional=False):
        matches = []
        for volume in compose["services"].get(service, {}).get("volumes", []):
            if isinstance(volume, str):
                parts = volume.split(":")
                source, target = parts[:2] if len(parts) >= 2 else ("", "")
            else:
                source, target = volume.get("source", ""), volume.get("target", "")
            if not source or not str(source).startswith((".", "/")):
                continue
            if container_path == target or container_path.startswith(target.rstrip("/") + "/"):
                base = Path(source) if Path(source).is_absolute() else self.root / source
                file = (
                    base / container_path[len(target) :].lstrip("/")
                    if target != container_path
                    else base
                )
                if not file.resolve().is_relative_to(self.root):
                    raise Problem(
                        "external_configuration",
                        "A service config is outside the registered directory; use the documented host migration.",
                    )
                matches.append(guarded(self.root, file.relative_to(self.root)))
        if len(matches) != 1:
            if optional:
                return None
            raise Problem(
                "custom_mount_review",
                "A custom service config mount needs a host-side registration review.",
            )
        return matches[0]

    def token(self):
        pending = load(self.secret_path, {})
        if pending.get("token"):
            return pending["token"]
        compose = self.compose()
        if not compose:
            return ""
        command = compose["services"]["fortigate-exporter"].get("command", [])
        destination = next(
            (command[i + 1] for i, x in enumerate(command[:-1]) if x == "-auth-file"),
            "/config/fortigate-key.yaml",
        )
        p = self.bound_file(compose, "fortigate-exporter", destination)
        auth = yaml.safe_load(p.read_text()) or {}
        old = load(self.root / self.state()["site_file"], {})
        record = auth[self.auth_key(auth, old)]
        if not isinstance(record, dict) or not isinstance(record.get("token"), str):
            raise Problem(
                "secret_unavailable", "The existing exporter credential could not be mapped safely."
            )
        return record["token"]

    @staticmethod
    def auth_key(auth, config, *, optional=False):
        """Recognize one HTTPS origin, including the legacy implicit443 form."""
        if not isinstance(auth, dict):
            raise Problem(
                "secret_unavailable", "The exporter credential mapping needs a host-side review."
            )
        wanted = urllib.parse.urlsplit(setup.target_url({**setup.DEFAULTS, **config}))
        matches = []
        for key in auth:
            try:
                item = urllib.parse.urlsplit(key)
                if (
                    item.scheme == "https"
                    and not item.username
                    and not item.password
                    and item.path in ("", "/")
                    and not item.query
                    and not item.fragment
                    and item.hostname == wanted.hostname
                    and (item.port or 443) == (wanted.port or 443)
                ):
                    matches.append(key)
            except (TypeError, ValueError):
                continue
        if not matches and optional:
            return None
        if len(matches) != 1:
            raise Problem(
                "secret_unavailable",
                "One unambiguous exporter credential must match the configured HTTPS origin; review its existing mapping.",
            )
        return matches[0]

    def secret_bound(self, key, settings, state=None, secret=None):
        """Whether the ``key`` secret these settings would use was entered for their server.

        A secret typed with the draft is bound to the server it was saved with,
        a stored one to the applied server. Without a secret, or without a
        server to send it to, there is nothing to protect.
        """
        state = self.state() if state is None else state
        secret = load(self.secret_path, {}) if secret is None else secret
        wanted = destination(settings, key)
        if not wanted[0]:
            return True
        if key == "smtp_password" and not settings["notifications"]["email"]["user"]:
            return True  # No login: the password is never sent.
        if secret.get(key):
            # Typed before destinations were recorded: bound to the draft it was saved with.
            bound = secret.get(key + "_for") or destination(state["settings"], key)
        else:
            try:
                present = bool(self.token() if key == "token" else self.notify_secrets()[key])
            except Problem:
                present = False
            if not present:
                return True
            applied = state.get("applied")
            bound = destination(applied, key) if applied else None
        return list(bound or []) == wanted

    def notify_secrets(self):
        """Alert channel secrets: the stored file, overlaid by values saved in the draft."""
        stored = notifications.read_secrets(self.root / NOTIFY_SECRET)
        pending = load(self.secret_path, {})
        return {key: pending.get(key) or stored.get(key) or "" for key in NOTIFY_KEYS}

    def view(self):
        state = self.state()
        try:
            present = bool(self.token())
        except Problem:
            present = False
        if "accounting_cpus" not in state["settings"]:
            # Settings saved before this choice existed: show the live limit, so
            # the next Save keeps it instead of resetting it to the default.
            state["settings"]["accounting_cpus"] = accounting_cpus(self.compose())
        changes = []
        if state["configured"] and state["applied_revision"] != state["revision"]:
            applied = deepcopy(state.get("applied") or {})
            if "accounting_cpus" not in applied:
                applied["accounting_cpus"] = accounting_cpus(self.compose())
            changes = setting_changes(
                _with_defaults(applied),
                _with_defaults(state["settings"]),
                load(self.secret_path, {}),
            )
        return {
            key: state[key] for key in ("revision", "applied_revision", "configured", "settings")
        } | {
            "secret_status": {
                "api_token_present": present,
                "custom_ca_present": bool(
                    load(self.secret_path, {}).get("ca")
                    or load(self.root / state["site_file"], {}).get("api_ca_file")
                ),
                **{key + "_present": bool(value) for key, value in self.notify_secrets().items()},
            },
            "pending_changes": state["applied_revision"] != state["revision"],
            # Saved but not applied, as a person reads them; empty before the first Apply.
            "changes": changes,
            "active_monitoring": (state.get("applied") or state["settings"])["monitoring"],
            "active_sla": (state.get("applied") or {}).get("sla", ""),
            "exporter_policy": self.exporter_policy(),
            "scope_locked": bool(
                (self.control / "accounting-contract.json").exists()
                or state["configured"]
                and self.compose()
                and "fortilens-usage" in self.compose()["services"]
            ),
            "accounting_scope": self.accounting_scope(state),
            "collector_note": "WAN, SLA and interface shortcuts share one exporter. It stops only when all three are disabled.",
        }

    def accounting_scope(self, state):
        """The recorded scope, so the form opens only the fields that may still move forward."""
        scope = load(self.control / "accounting-contract.json", state.get("applied"))
        return (
            {key: scope.get(key) for key in ("vdom", "accounting_timezone", "expected_devid")}
            if isinstance(scope, dict)
            else None
        )

    def exporter_policy(self):
        """Expose only reviewed capability names, never credentials or custom rules."""
        try:
            compose = self.compose()
            if not compose:
                return {"optional_exclusions": [], "custom_policy": False, "available": True}
            command = compose["services"]["fortigate-exporter"].get("command", [])
            destination = next(
                (command[i + 1] for i, x in enumerate(command[:-1]) if x == "-auth-file"),
                "/config/fortigate-key.yaml",
            )
            auth = yaml.safe_load(
                self.bound_file(compose, "fortigate-exporter", destination).read_text()
            )
            record = auth[self.auth_key(auth, load(self.root / self.state()["site_file"], {}))]
            validated = setup.exclude_optional_probes(record, [])
            profile = validated.get("probes") or {}
            excluded = profile.get("exclude", [])
            return {
                "optional_exclusions": [
                    name for name in setup.OPTIONAL_EXPORTER_PROBES if name in excluded
                ],
                "custom_policy": bool(
                    profile.get("include")
                    or any(name not in setup.OPTIONAL_EXPORTER_PROBES for name in excluded)
                ),
                "available": True,
            }
        except (
            Problem,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            yaml.YAMLError,
            setup.SetupError,
        ):
            return {"optional_exclusions": [], "custom_policy": False, "available": False}

    def audit(self, event, fields=(), outcome="success"):
        # This append follows committed state changes. Its failure must not claim
        # those changes were undone or trigger a configuration-only rollback.
        try:
            with (self.control / "audit.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "time": time.time(),
                            "event": event,
                            "fields": sorted(fields),
                            "outcome": outcome,
                        }
                    )
                    + "\n"
                )
        except (OSError, ValueError) as exc:
            report_failure("configuration_audit_write_failed", exc)

    def save(self, change):
        change = Change.model_validate(change)
        with self.lock:
            state = self.state()
            if change.revision != state["revision"]:
                raise Problem(
                    "revision_conflict",
                    "Settings changed in another window. Reload before saving.",
                    409,
                )
            new = change.settings.model_dump()
            previous = state.get("applied")
            scope = load(self.control / "accounting-contract.json", previous)
            if self.view()["scope_locked"] and scope:
                for field in ("vdom", "accounting_timezone", "expected_devid"):
                    # A fixed accounting zone may start following the FortiGate;
                    # days already counted keep their zone.
                    if (
                        field == "accounting_timezone"
                        and new[field] == "fortigate"
                        and scope[field] != "fortigate"
                    ):
                        continue
                    # An unbound source may bind its serial once; Apply checks
                    # it against Test Connection before anything is counted.
                    if field == "expected_devid" and not scope.get(field) and new[field]:
                        continue
                    if new[field] != scope.get(field):
                        raise Problem(
                            "accounting_migration_required",
                            "Changing historical accounting scope requires an explicit migration; no data was changed.",
                            409,
                        )
            if previous:
                for key, value in previous["retention"].items():
                    if new["retention"][key] < value:
                        raise Problem(
                            "retention_reduction_requires_review",
                            "Reducing retention or the database ceiling requires the documented data-preservation review.",
                            409,
                        )
            if (
                not change.settings.api_tls_verify
                and state["settings"]["api_tls_verify"]
                and not change.acknowledge_insecure_tls
            ):
                raise Problem(
                    "tls_ack_required",
                    "Explicitly acknowledge disabling FortiGate certificate verification.",
                )
            secret = load(self.secret_path, {})
            if change.api_token is not None:
                token = change.api_token.get_secret_value()
                if (
                    not change.replace_token
                    or not token
                    or len(token) > 4096
                    or any(ord(c) < 33 or ord(c) > 126 for c in token)
                ):
                    raise Problem(
                        "token_action_required",
                        "Select Replace token and provide a valid token, or leave the field empty.",
                    )
                secret["token"] = token
            if change.custom_ca is not None:
                if not change.replace_ca:
                    raise Problem(
                        "ca_action_required",
                        "Explicitly confirm replacing the custom CA certificate.",
                    )
                ca = change.custom_ca.get_secret_value()
                try:
                    if ca:
                        ssl.create_default_context(cadata=ca)
                except ssl.SSLError:
                    raise Problem(
                        "invalid_ca", "The custom CA is not a valid PEM trust bundle."
                    ) from None
                secret["ca"] = ca
            # Alert channel secrets: a non-empty value replaces the stored one.
            sent = []
            for key in NOTIFY_KEYS:
                value = getattr(change, key)
                if value is None or not value.get_secret_value():
                    continue
                text = value.get_secret_value()
                if len(text) > 512 or any(ord(c) < 32 for c in text):
                    raise Problem("invalid_secret", "Enter the bot token or password as one line.")
                secret[key] = text
                sent.append(key)
            entered = {
                "token": change.api_token is not None,
                "smtp_password": "smtp_password" in sent,
            }
            for key, (code, label) in SECRET_DESTINATIONS.items():
                if entered[key]:
                    secret[key + "_for"] = destination(new, key)
                elif not self.secret_bound(key, new, state, secret):
                    raise Problem(
                        code,
                        "The "
                        + label
                        + " server changed. Enter the "
                        + ("API token" if key == "token" else "mail password")
                        + " again: a saved one is only sent to the server it was entered for.",
                    )
            # One state transaction owns draft and write-only secret values.
            # Secret staging does not alter active service credentials.
            secret["revision"] = state["revision"] + 1
            atomic(self.secret_path, secret)
            changed = [key for key in new if new[key] != state["settings"].get(key)]
            state.update(settings=new, revision=state["revision"] + 1)
            atomic(self.state_path, state)
            self.audit(
                "settings_saved", changed + (["api_token"] if change.api_token else []) + sent
            )
            return self.view()
