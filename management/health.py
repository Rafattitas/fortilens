"""Service state, storage use and the read-only diagnostics the Health page shows."""

import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import yaml
from .common import Problem, atomic, load, report_failure, setup
from .models import (
    Job,
    Retention,
    Settings,
)
from .storage import observe
import doctor
from .compose_engine import enabled_services


class HealthMixin:
    """Status, storage and diagnostics. Nothing here changes the installation."""

    def status(self):
        try:
            usage = shutil.disk_usage(self.c.control)
            disk = {"total": usage.total, "used": usage.used, "available": usage.free}
        except OSError as exc:
            report_failure("storage_probe_failed", exc)
            disk = {"total": None, "used": None, "available": None}
        try:
            self.docker.run(["docker", "version", "--format", "{{.Server.Version}}"], 5)
            services = self.docker.statuses()
            docker = "Available"
        except (Problem, OSError, ValueError):
            services = []
            docker = "Unavailable"
        ram = None
        try:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, AttributeError, OSError):
            pass
        if self.c.compose():
            _, disabled = enabled_services(
                Settings.model_validate(self.c.state()["settings"]), self.c.compose()
            )
            for row in services:
                if row["service"] in disabled:
                    row.update(status="disabled", health=None)
        identities = {p.stem for p in (self.c.control / "jobs").glob("*.json")}
        with self.jobs_lock:
            identities.update(self.jobs)
        jobs = []
        for identity in identities:
            try:
                Job(job_id=identity)
                jobs.append(self.read_job(identity))
            except (Problem, ValueError) as exc:
                report_failure("job_status_unavailable", exc)

        def last_event(job):
            event = (job.get("events") or [{}])[-1]
            stamp = event.get("at", 0) if isinstance(event, dict) else 0
            return stamp if isinstance(stamp, (int, float)) else 0

        latest = max(jobs, key=last_event) if jobs else None
        return {
            "server_time_seconds": time.time(),
            "server_clock_supported": True,
            "hostname": socket.gethostname(),
            "os": platform.platform(),
            "architecture": platform.machine(),
            "cpu_count": os.cpu_count(),
            "ram_bytes": ram,
            "disk": disk,
            "docker": docker,
            "services": services,
            "installation": {"configured": self.c.state()["configured"], "project": self.c.project},
            "storage_projection": self.storage(),
            "latest_job": latest,
            "links": {
                "console": "fortilens-portal" in (self.c.compose() or {"services": {}})["services"],
                "dashboard": "/grafana/d/"
                + self.dashboard_uid
                + "/?kiosk&theme=dark"
                + (
                    "&"
                    + urllib.parse.urlencode(
                        {"var-sla": (self.c.state().get("applied") or {}).get("sla")}
                    )
                    if (self.c.state().get("applied") or {}).get("sla")
                    else ""
                ),
                "grafana_port": load(self.c.root / self.c.state()["site_file"], {}).get(
                    "grafana_port", 3001
                ),
                "grafana_prefix": "/grafana/"
                if "fortilens-portal" in (self.c.compose() or {"services": {}})["services"]
                else "/",
            },
        }

    def storage(self):
        # Status requests share one persisted observation window. A refresh must
        # not overwrite another request's baseline or hide a valid trend.
        with self.storage_lock:
            return self._storage()

    def _storage(self):
        result = {"available": False, "horizon_seconds": 86400, "components": {}}
        now = time.time()
        history = {}
        try:
            path = self.c.control / "storage-samples.json"
            if path.exists() and path.stat().st_size > 256 * 1024:
                raise ValueError("invalid_storage_history")
            saved = load(path, {})
            if not isinstance(saved, dict):
                raise ValueError("invalid_storage_history")
            if saved.get("version") == 2 and isinstance(saved.get("components"), dict):
                history = saved["components"]
        except (OSError, ValueError) as exc:
            report_failure("storage_history_unavailable", exc)
        state = self.c.state()
        observed = (state.get("applied") if state.get("configured") else None) or state["settings"]
        # First-run settings can have an empty device address. Storage only
        # needs retention and must remain available before a connection exists.
        retention = Retention.model_validate(observed.get("retention", {}))
        poll_seconds = 30
        try:
            configured_poll = load(self.c.root / state["site_file"], {}).get(
                "accounting_poll_seconds", 30
            )
            if type(configured_poll) is int and 5 <= configured_poll <= 3600:
                poll_seconds = configured_poll
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        max_accounting_lag = max(120, poll_seconds * 2)
        updated = {}
        filesystems = {}
        measured_volumes = set()
        for service in ("loki", "prometheus", "fortilens-usage"):
            try:
                ids = (
                    self.docker.run(self.docker.command() + ["ps", "--all", "-q", service], 10)
                    .strip()
                    .splitlines()
                )
                if len(ids) != 1:
                    continue
                item = json.loads(self.docker.run(["docker", "inspect", ids[0]], 10))[0]
                if item["Config"]["Labels"].get("com.docker.compose.project") != self.c.project:
                    continue
                targets = (
                    ("/loki", "/tmp/loki")
                    if service == "loki"
                    else ("/prometheus",)
                    if service == "prometheus"
                    else ("/data",)
                )
                mounts = [
                    m
                    for m in item["Mounts"]
                    if m["Destination"] in targets and m.get("Type") == "volume"
                ]
                if len(mounts) != 1:
                    continue
                path = Path(mounts[0]["Source"])
                mount_stat = path.stat()
                device = mount_stat.st_dev
                total = 0
                count = 0
                deadline = time.monotonic() + 0.25
                for base, dirs, files in os.walk(path, followlinks=False):
                    dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
                    for name in files:
                        p = Path(base) / name
                        if not p.is_symlink():
                            total += p.stat().st_size
                        count += 1
                    if count > 100000 or time.monotonic() > deadline:
                        raise ValueError()
                value = {"bytes": total, "complete_measurement": True}
                context = retention.loki_days if service == "loki" else retention.prometheus_days
                eligible = item.get("State", {}).get("Running") is True
                reason = (
                    None
                    if eligible
                    else "The service is not running; a live growth trend is unavailable."
                )
                if service == "fortilens-usage":
                    value.update(
                        capacity_limit_bytes=retention.database_bytes,
                        ledger_days=retention.ledger_days,
                        summary_days=retention.summary_days,
                    )
                    context = [
                        retention.ledger_days,
                        retention.summary_days,
                        retention.database_bytes,
                        *[
                            observed.get(k)
                            for k in (
                                "fortigate_host",
                                "vdom",
                                "expected_devid",
                                "accounting_timezone",
                            )
                        ],
                    ]
                    if not observed.get("monitoring", {}).get("accounting", True):
                        eligible = False
                        reason = "Accounting is disabled; a live growth trend is unavailable."
                    if eligible:
                        try:
                            details = self.accounting_diagnostics({})
                            health_at = time.time()
                            context.append(details.get("last_failure_seconds"))
                            checkpoint = details.get("checkpoint_ns")
                            if (
                                details.get("last_poll_ok") is not True
                                or details.get("last_error") is not None
                            ):
                                eligible = False
                                reason = "Accounting is not processing successfully; its growth trend is unavailable."
                            elif type(checkpoint) is not int or not 0 < checkpoint <= int(
                                health_at * 1e9
                            ):
                                eligible = False
                                reason = "A recent accounting checkpoint has not been verified."
                            elif health_at - checkpoint / 1e9 > max_accounting_lag:
                                eligible = False
                                reason = "Accounting is processing a backlog; collect a new growth history after it catches up."
                        except (Problem, OSError, ValueError, TypeError, AttributeError):
                            eligible = False
                            reason = "Accounting processing health is unavailable; its growth trend cannot be verified."
                identity = hashlib.sha256(
                    json.dumps(
                        [
                            self.c.project,
                            str(path),
                            mounts[0].get("Name"),
                            device,
                            mount_stat.st_ino,
                            context,
                        ]
                    ).encode()
                ).hexdigest()
                trend, updated[service] = observe(
                    history.get(service), identity, now, total, eligible=eligible, reason=reason
                )
                value.update(trend)
                result["components"][service] = value
                if "projection_bytes" in value:
                    result["available"] = True
                    # Existing bytes already occupy the filesystem. Compare only
                    # additional growth, once per mounted volume, on its device.
                    volume = str(path.resolve())
                    if volume not in measured_volumes:
                        measured_volumes.add(volume)
                        if device not in filesystems:
                            filesystems[device] = [0, shutil.disk_usage(path).free]
                        filesystems[device][0] += value["projection_bytes"]
                    if (
                        service == "fortilens-usage"
                        and total + value["projection_bytes"] > retention.database_bytes
                    ):
                        value["capacity_warning"] = (
                            "This trend may reach the configured accounting ceiling; the ceiling stops ingestion rather than reducing storage demand."
                        )
            except (Problem, OSError, ValueError, KeyError, TypeError, IndexError):
                continue
        result["partial"] = len(result["components"]) < 3 or any(
            "projection_bytes" not in v for v in result["components"].values()
        )
        result["message"] = (
            "Conditional additional file-size growth over the next 24 hours, based on the observed window. "
            "Compaction, retention and future traffic can change actual growth. "
            "This is not a full-retention capacity estimate; OS files, backups and other storage are excluded."
        )
        if result["partial"]:
            result["message"] += " Some service trends are unavailable; see the individual reasons."
        if not result["components"]:
            result["message"] = (
                "Storage measurements are unavailable. Review service and host storage access."
            )
        if any(growth > free for growth, free in filesystems.values()):
            result["message"] += (
                " Warning: the available trends exceed free space on a measured volume filesystem."
            )
        try:
            atomic(self.c.control / "storage-samples.json", {"version": 2, "components": updated})
        except (Problem, OSError, ValueError) as exc:
            report_failure("storage_history_write_failed", exc)
            result["message"] += (
                " The latest storage sample could not be saved; review host storage."
            )
        return result

    def diagnostics(self):
        state = self.c.state()
        # Collection diagnostics describe the running installation, not an
        # unapplied connection draft. Draft readiness is shown by Settings.
        observed = state.get("applied") if state["configured"] else state["settings"]
        settings = Settings.model_validate(observed or state["settings"])
        c = settings.portable(load(self.c.root / state["site_file"], {}))
        report = self._monitoring_report(settings, c, state["revision"])
        collection = self._collection(report, c, settings)
        return {
            "monitoring": report,
            "system": self.status(),
            "collection": collection,
            "read_only": True,
            "observed_revision": state.get("applied_revision") if state["configured"] else None,
            "saved_revision": state["revision"],
        }

    def _doctor_request(self, p, config, path, method="GET", body=None, timeout=15):
        """Answer one of doctor's Grafana-style requests from the services themselves."""
        try:
            return self._service_answer(path, body)
        except Problem:
            raise ValueError("monitoring_service_unavailable") from None

    def _service_answer(self, path, body):
        if path == "/api/health":
            return self.docker.http(
                "grafana",
                3000,
                (
                    "/grafana"
                    if "fortilens-portal" in (self.c.compose() or {"services": {}})["services"]
                    else ""
                )
                + "/api/health",
            )
        for uid, service, port in (
            (setup.PROM_UID, "prometheus", 9090),
            (setup.LOKI_UID, "loki", 3100),
        ):
            if path == f"/api/datasources/uid/{uid}/health":
                self.docker.http(
                    service,
                    port,
                    "/api/v1/query?query=up"
                    if service == "prometheus"
                    else "/loki/api/v1/query?"
                    + urllib.parse.urlencode(
                        {"query": 'sum(count_over_time({job="fortigate-syslog"}[1m]))'}
                    ),
                )
                return {"status": "OK"}
        if "/api/v1/query?" in path:
            return self.docker.http("prometheus", 9090, "/api/v1/query?" + path.split("?", 1)[1])
        if path == "/api/ds/query":
            query = body["queries"][0]
            kind = query["datasource"]["type"]
            native = self.docker.http(
                "prometheus" if kind == "prometheus" else "loki",
                9090 if kind == "prometheus" else 3100,
                ("/api/v1/query?" if kind == "prometheus" else "/loki/api/v1/query?")
                + urllib.parse.urlencode({"query": query["expr"]}),
            )
            values = [float(row["value"][1]) for row in native.get("data", {}).get("result", [])]
            return {
                "results": {
                    "A": {
                        "frames": [
                            {
                                "schema": {"fields": [{"type": "number"}]},
                                "data": {"values": [values]},
                            }
                        ]
                    }
                }
            }
        raise ValueError("unsupported_diagnostic")

    def _exporter_exclusions(self, c):
        """Optional exporter probes the registered credential record excludes."""
        compose = self.c.compose() or {}
        exporter = compose.get("services", {}).get("fortigate-exporter", {})
        command = exporter.get("command", [])
        destination = next(
            (command[i + 1] for i, x in enumerate(command[:-1]) if x == "-auth-file"),
            "/config/fortigate-key.yaml",
        )
        auth = (
            yaml.safe_load(
                self.c.bound_file(compose, "fortigate-exporter", destination).read_text()
            )
            if exporter
            else {}
        )
        record = auth.get(self.c.auth_key(auth, c), {}) if auth else {}
        return (record.get("probes") or {}).get("exclude", [])

    def _monitoring_report(self, settings, c, revision):
        """doctor's checks on the running installation, read through the helper."""
        try:
            # doctor is the same callable used by setup.py doctor --json; its
            # subprocess-based status reader is replaced with approved helper data.
            rows = self.docker.statuses()
            if settings.syslog_format != "fortigate" or not settings.monitoring.syslog:
                rows = [row for row in rows if row["service"] != "fortilens-syslog"]
            checks = [
                doctor.check(
                    "service:" + r["service"],
                    "PASS" if r["status"] == "running" else "WARN",
                    r["service"] + ": " + str(r["status"]),
                    "service_state",
                )
                for r in rows
            ]
            exclusions = self._exporter_exclusions(c)

            def native_receiver_request(*args):
                try:
                    return self.docker.http("fortilens-syslog", 8080, "/health")
                except Problem:
                    raise ValueError("native_receiver_unavailable") from None

            report = doctor.run(
                self.c.root,
                c,
                self._doctor_request,
                lambda *a: [],
                setup.PROM_UID,
                setup.LOKI_UID,
                service_reader=lambda *a, **k: checks,
                usage_request=lambda *a: self.accounting_diagnostics(c),
                probe_exclusions=exclusions,
                native_receiver_request=native_receiver_request,
            )
            report["checks"].append(self.connection_test_check(settings, revision))
            report["status"] = next(
                (
                    v
                    for v in ("FAIL", "WARN", "UNKNOWN")
                    if any(x["status"] == v for x in report["checks"])
                ),
                "PASS",
            )
        except (Problem, OSError, ValueError, TypeError):
            report = {
                "status": "UNKNOWN",
                "checks": [
                    {
                        "id": "monitoring",
                        "status": "UNKNOWN",
                        "message": "Monitoring diagnostics are unavailable. The management portal is still running.",
                        "action": "Review service status, Docker permissions and disk capacity.",
                    }
                ],
            }
        return report

    def _collection(self, report, c, settings):
        """When the newest metric and log arrived, and the accounting lag; null when unknown."""
        collection = {
            "latest_log_at": None,
            "latest_metric_at": None,
            "processing_lag_seconds": None,
            "log_timestamp_kind": "receipt"
            if c.get("loki_receive_timestamps")
            else "source or unconfirmed",
        }
        for check in report.get("checks", []):
            age = check.get("evidence", {}).get("newest_sample_age_seconds")
            if isinstance(age, (int, float)):
                collection["latest_metric_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age)
                )
        try:
            now = time.time()
            result = self.docker.http(
                "loki",
                3100,
                "/loki/api/v1/query_range?"
                + urllib.parse.urlencode(
                    {
                        "query": '{job="fortigate-syslog"}',
                        "start": str(int((now - 86400) * 1e9)),
                        "end": str(int(now * 1e9)),
                        "direction": "backward",
                        "limit": 1,
                    }
                ),
            )
            stamps = [
                int(v[0]) / 1e9
                for row in result.get("data", {}).get("result", [])
                for v in row.get("values", [])
            ]
            if stamps:
                collection["latest_log_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(max(stamps))
                )
        except (Problem, OSError, ValueError, KeyError, TypeError):
            pass
        if settings.monitoring.accounting:
            try:
                collection["processing_lag_seconds"] = self.accounting_diagnostics(c).get(
                    "processing_lag_seconds"
                )
            except (Problem, OSError, ValueError, TypeError):
                pass
        return collection

    def usage_get(self, route, *, timeout=5):
        """GET a route of the accounting service with its own report credential."""
        compose = self.c.compose() or {}
        source = compose.get("secrets", {}).get("usage_admin_password", {}).get("file")
        if not source:
            raise ValueError("report_secret_unavailable")
        from .common import guarded

        path = Path(source)
        path = path if path.is_absolute() else self.c.root / path
        password = guarded(self.c.root, path.relative_to(self.c.root)).read_text().strip()
        auth = "Basic " + base64.b64encode(("admin:" + password).encode()).decode()
        try:
            return self.docker.http(
                "fortilens-usage", 8080, route, headers={"Authorization": auth}, timeout=timeout
            )
        except Problem:
            raise ValueError("accounting_service_unavailable") from None

    def accounting_diagnostics(self, c):
        compose = self.c.compose() or {}
        source = compose.get("secrets", {}).get("usage_admin_password", {}).get("file")
        if not source:
            raise ValueError("report_secret_unavailable")
        from .common import guarded

        path = Path(source)
        path = path if path.is_absolute() else self.c.root / path
        password = guarded(self.c.root, path.relative_to(self.c.root)).read_text().strip()
        auth = "Basic " + base64.b64encode(("admin:" + password).encode()).decode()
        try:
            return self.docker.http(
                "fortilens-usage", 8080, "/api/diagnostics", headers={"Authorization": auth}
            )
        except Problem:
            raise ValueError("accounting_service_unavailable") from None
