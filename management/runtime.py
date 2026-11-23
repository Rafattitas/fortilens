"""Host-only approved operations. No browser command, image, root or project input."""

import base64
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from .common import Problem, atomic, load, report_failure
from .models import (
    Apply,
    Console,
    Job,
    NotifyReport,
    NotifyTest,
    Revision,
    Settings,
)
import notifications  # noqa: E402  (portable/, on the path through .common)
import interfaces
import portal
from .deployment import DeploymentMixin
from .device import DeviceMixin, fortigate
from .health import HealthMixin

# Re-exported: callers and tests import these names from runtime.
from .compose_engine import Docker, enabled_services, recreated  # noqa: F401


class Operations(DeviceMixin, HealthMixin, DeploymentMixin):
    """The helper's fixed operations. The mixins hold the FortiGate (device), status
    and diagnostics (health) and Apply/rollback (deployment) parts; this class
    holds the dispatcher, alert tests and the console bridge."""

    def __init__(
        self, configuration, docker=None, device_get=fortigate, dashboard_uid="forti-monitor"
    ):
        self.c = configuration
        self.docker = docker or Docker(configuration)
        self.device_get = device_get
        self.jobs = {}
        self.jobs_lock = threading.Lock()
        self.busy = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.gateway_cookie = ""
        self.gateway_lock = threading.Lock()
        self.storage_lock = threading.Lock()
        if not interfaces.NAME.fullmatch(dashboard_uid):
            raise ValueError("Invalid registered dashboard UID")
        self.dashboard_uid = dashboard_uid
        for p in (self.c.control / "jobs").glob("*.json"):
            try:
                Job(job_id=p.stem)
                job = self.read_job(p.stem)
            except (Problem, ValueError) as exc:
                report_failure("job_recovery_unavailable", exc)
                continue
            if job.get("status") == "starting":
                self._safe_progress(
                    job,
                    "failed",
                    "The helper restarted during deployment. Review a controlled configuration rollback.",
                    status="failed",
                    rollback_available=True,
                )

    def dispatch(self, operation, data):
        if operation in ("view", "status", "diagnostics") and data:
            raise Problem("unexpected_fields", "This operation accepts no additional fields.")
        if operation == "view":
            return self.view()
        if operation == "status":
            return self.status()
        if operation == "diagnostics":
            return self.diagnostics()
        if operation == "save":
            if self.busy.locked():
                raise Problem(
                    "deployment_busy", "A deployment is running. Wait before saving.", 409
                )
            result = self.c.save(data)
            result["discovery"] = self.discovery_readiness()
            return result
        if operation == "prepare":
            rev = Revision.model_validate(data)
            if self.busy.locked():
                raise Problem("deployment_busy", "A deployment is already running.", 409)
            plan = self.c.prepare(rev.revision)
            self.docker.validate(plan["plan_id"])
            plan["discovery"] = self.discovery_readiness()
            plan.update(self.service_effects(plan["plan_id"]))
            plan["changes"] = self.c.view()["changes"]
            return plan
        if operation in ("test", "discover"):
            Revision.model_validate(data)
            try:
                result = self.device(operation, data["revision"])
                if operation == "test":
                    atomic(
                        self.c.control / "connection-test.json",
                        {
                            "at": time.time(),
                            "scope": self.inventory_scope(
                                Settings.model_validate(self.c.state()["settings"])
                            ),
                            "result": result,
                        },
                    )
                if operation == "discover":
                    result["discovery"] = self.discovery_readiness()
                return result
            except Problem as e:
                if operation == "test":
                    atomic(
                        self.c.control / "connection-test.json",
                        {
                            "at": time.time(),
                            "revision": data["revision"],
                            "result": {"status": "failed", "message": e.message, "code": e.code},
                        },
                    )
                raise
        if operation == "alerts":
            if data:
                raise Problem("unexpected_fields", "This operation accepts no additional fields.")
            try:
                return self.usage_get("/api/alerts")
            except ValueError:
                raise Problem(
                    "alerts_unavailable",
                    "Alerts are unavailable while accounting is not running.",
                    503,
                ) from None
        if operation == "notify_test":
            return self.notify_test(NotifyTest.model_validate(data))
        if operation in ("report_preview", "notify_report"):
            request = NotifyReport.model_validate(data)
            try:
                # Building a report reads raw records and Prometheus ranges (up to 15 s
                # and 25 s); sending it can take another 20 s.
                report = self.usage_get("/api/alerts/report?kind=" + request.kind, timeout=50)
            except ValueError:
                raise Problem(
                    "alerts_unavailable",
                    "Reports are unavailable while accounting is not running.",
                    503,
                ) from None
            if operation == "report_preview":
                return report
            return self.notify_report(request, report)
        if operation == "apply":
            return self.apply(Apply.model_validate(data))
        if operation == "rollback":
            return self.rollback(Apply.model_validate(data))
        if operation == "job":
            request = Job.model_validate(data)
            return self.read_job(request.job_id)
        if operation == "console":
            return self.console(Console.model_validate(data))
        raise Problem("operation_rejected", "This management operation is not supported.", 403)

    def view(self):
        result = self.c.view()
        result["discovery"] = self.discovery_readiness()
        return result

    def notify_test(self, request):
        """Send one test message with the saved draft's channel and secrets."""
        state = self.c.state()
        if request.revision != state["revision"]:
            raise Problem("revision_conflict", "Save the draft, then send the test.", 409)
        settings = Settings.model_validate(state["settings"])
        c = notifications.validate(settings.notifications.model_dump(mode="json"))
        try:
            notifications.send(
                c,
                self.c.notify_secrets(),
                request.channel,
                "FortiLens · Test message",
                "A test from " + settings.installation_name + ". Alerts will arrive here.",
            )
        except notifications.DeliveryError as exc:
            # A failed test is recorded with its fixed code, never the server's reply.
            self.c.audit("notification_test", [request.channel, exc.code], outcome="failed")
            raise Problem(exc.code, notifications.MESSAGES[exc.code], 502) from None
        self.c.audit("notification_test", [request.channel])
        return {"status": "delivered", "channel": request.channel, "at": time.time()}

    def notify_report(self, request, report):
        """Send one report now as a sample, with the saved draft's channels."""
        state = self.c.state()
        if request.revision != state["revision"]:
            raise Problem("revision_conflict", "Save the draft, then send the sample.", 409)
        if not isinstance(report, dict) or not all(
            isinstance(report.get(k), str) for k in ("subject", "text", "html")
        ):
            raise Problem("report_unavailable", "The report could not be built.", 503)
        settings = Settings.model_validate(state["settings"])
        c = notifications.validate(settings.notifications.model_dump(mode="json"))
        result = notifications.deliver_report(c, self.c.notify_secrets(), request.kind, report)
        if result["status"] != "delivered":
            code = result.get("error", "not_configured")
            self.c.audit("notification_report_sample", [request.kind, code], outcome="failed")
            raise Problem(code, notifications.MESSAGES.get(code, "The report was not sent."), 502)
        self.c.audit("notification_report_sample", [request.kind])
        return {"status": "delivered", "channel": result["channel"], "at": time.time()}

    def console(self, request):
        # Existing unified console retains its own auth boundary. The helper
        # signs in server-side and never sends that credential/cookie to the UI.
        parsed = urllib.parse.urlsplit(request.path)
        decoded = urllib.parse.unquote(parsed.path)
        if (
            parsed.scheme
            or parsed.netloc
            or ".." in decoded.split("/")
            or "\\" in decoded
            or any(ord(c) < 32 for c in decoded)
        ):
            raise Problem("console_path_rejected", "Invalid console resource.")
        allowed = parsed.path in (
            "/",
            "/api/portal",
            "/api/session",
            "/api/monitoring-status",
            *portal.REPORT_ROUTES,
        ) or parsed.path.startswith("/grafana/")
        if not allowed or request.method == "POST" and not portal.query_post(parsed.path):
            raise Problem(
                "console_operation_rejected",
                "Only existing read-only console operations are proxied.",
                403,
            )
        c = load(self.c.root / self.c.state()["site_file"], {})
        if "fortilens-portal" not in (self.c.compose() or {"services": {}})["services"]:
            raise Problem(
                "console_not_integrated",
                "This deployment retains its native Grafana/report login. Use the existing service links.",
                503,
            )
        source = self.c.compose()["secrets"]["usage_admin_password"]["file"]
        path = Path(source)
        from .common import guarded

        password = (
            guarded(self.c.root, path.relative_to(self.c.root) if path.is_absolute() else path)
            .read_text()
            .strip()
        )

        def connect():
            return http.client.HTTPConnection("127.0.0.1", c.get("usage_port", 3002), timeout=30)

        try:
            for attempt in range(2):
                with self.gateway_lock:
                    if not self.gateway_cookie:
                        conn = connect()
                        try:
                            conn.request(
                                "POST",
                                "/api/session",
                                json.dumps({"username": "admin", "password": password}),
                                {"X-FortiLens-Request": "1", "Content-Type": "application/json"},
                            )
                            response = conn.getresponse()
                            response.read(65536)
                            if response.status != 200:
                                raise Problem(
                                    "console_signin_failed",
                                    "The protected monitoring console is not ready.",
                                    503,
                                )
                            self.gateway_cookie = response.getheader("Set-Cookie", "").split(";")[0]
                        finally:
                            conn.close()
                    cookie = self.gateway_cookie
                conn = connect()
                try:
                    headers = {
                        "Cookie": cookie,
                        "Content-Type": request.content_type,
                        "Accept-Encoding": "identity",
                        "Origin": f"http://127.0.0.1:{c.get('usage_port', 3002)}",
                    }
                    conn.request(
                        request.method,
                        request.path,
                        base64.b64decode(request.body) if request.body else None,
                        headers,
                    )
                    response = conn.getresponse()
                    data = response.read(32 * 1024 * 1024 + 1)
                    if response.status == 401 and not attempt:
                        with self.gateway_lock:
                            if self.gateway_cookie == cookie:
                                self.gateway_cookie = ""
                        continue
                    if len(data) > 32 * 1024 * 1024:
                        raise Problem(
                            "console_too_large", "This console resource exceeded the proxy limit."
                        )
                    # A short body must not reach the browser as a complete, cacheable asset.
                    if isinstance(response.length, int) and response.length > 0:
                        raise Problem(
                            "console_unavailable",
                            "Monitoring is unavailable; Settings and Diagnostics remain accessible.",
                            503,
                        )
                    return {
                        "status": response.status,
                        "content_type": response.getheader(
                            "Content-Type", "application/octet-stream"
                        ),
                        "location": response.getheader("Location"),
                        "body": base64.b64encode(data).decode(),
                    }
                finally:
                    conn.close()
        except TimeoutError:
            # Distinct from a stopped service: the reader should look for what keeps it busy.
            raise Problem(
                "console_timeout",
                "The report service did not answer within 30 seconds. Open Diagnostics for the cause; "
                "a clock difference between this server and the FortiGate is a common one. Settings and Diagnostics remain accessible.",
                504,
            ) from None
        except (OSError, http.client.HTTPException):
            raise Problem(
                "console_unavailable",
                "Monitoring is unavailable; Settings and Diagnostics remain accessible.",
                503,
            ) from None
