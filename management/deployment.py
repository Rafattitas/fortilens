"""Apply and rollback jobs: which services restart, progress and recovery."""

from copy import deepcopy
import os
import subprocess
import time
import yaml
from .common import Problem, atomic, load, report_failure, setup
from .compose_policy import SERVICES
from .models import (
    Settings,
)
import interfaces
from .compose_engine import enabled_services, recreated


class DeploymentMixin:
    """Apply and rollback, one job at a time, with progress kept on disk."""

    def running_services(self):
        """Names of running services, or None when Docker status cannot be read."""
        try:
            return {r["service"] for r in self.docker.statuses() if r["status"] == "running"}
        except (Problem, OSError, ValueError, subprocess.SubprocessError):
            return None

    def recreate_plan(self, plan, compose, running):
        """(enabled, disabled, recreate) for Apply of ``plan`` over ``compose``.

        Apply recreates only the services that read a changed file (the plan's
        affected_services), services this plan enables and enabled services that
        are not running, plus the services recreated with them (recreated()). A
        first installation, or a plan whose changes could not be traced to
        particular services, recreates every enabled service. ``running`` is None
        when Docker status is unknown; then no service is added for not running.
        """
        services = compose.get("services") or {}
        enabled, disabled = enabled_services(Settings.model_validate(plan["settings"]), compose)
        enabled = [name for name in enabled if name in services]
        affected = plan.get("affected_services")
        if plan.get("fresh") or not isinstance(affected, list):
            return enabled, disabled, enabled
        try:
            previous = plan["previous"].get("applied")
            before = enabled_services(Settings.model_validate(previous), compose)[0]
        except (TypeError, ValueError):
            # The previous enabled set cannot be read: treat everything as being enabled.
            before = []
        chosen = set(affected) | (set(enabled) - set(before))
        if running is not None:
            chosen |= set(enabled) - running
        return enabled, disabled, recreated(enabled, chosen)

    def service_effects(self, plan_id):
        """Which services Apply recreates and which running ones it stops.

        The restart list comes from recreate_plan(), the same rule _deploy uses,
        so the review names what Apply actually recreates. A disabled service is
        listed only when it runs now; if the status cannot be read, stopping is
        reported as unknown rather than guessed.
        """
        folder, plan = self.c.plan(plan_id)
        candidate = folder / "candidate" / self.c.compose_name
        compose = (
            yaml.safe_load(candidate.read_text(encoding="utf-8-sig"))
            if candidate.is_file()
            else self.c.compose()
        ) or {}
        services = compose.get("services") or {}
        if not services:
            return {"restarts": [], "stops": [], "stops_known": True}
        running = self.running_services()
        _, disabled, recreate = self.recreate_plan(plan, compose, running)
        return {
            "restarts": recreate,
            "stops": [name for name in disabled if name in services and name in (running or ())],
            "stops_known": running is not None,
        }

    def read_job(self, identity):
        # The current process knows the newest state even when disk-full prevents
        # recording it. The bounded cache is not a durable recovery guarantee.
        with self.jobs_lock:
            cached = self.jobs.get(identity)
            if cached is not None:
                return deepcopy(cached)
        try:
            job = load(self.c.control / "jobs" / (identity + ".json"))
        except (OSError, ValueError) as exc:
            report_failure("job_read_failed", exc)
            raise Problem(
                "job_state_unavailable",
                "Deployment history is unavailable. Review host storage and service logs.",
                503,
            ) from None
        if not job:
            raise Problem("job_missing", "Deployment job not found.", 404)
        if (
            not isinstance(job, dict)
            or job.get("job_id") != identity
            or not isinstance(job.get("events", []), list)
        ):
            raise Problem(
                "job_state_invalid",
                "Deployment history needs host-side review. Check Settings and the registered installation before retrying.",
                503,
            )
        return job

    def _progress(self, job, phase, message, **values):
        job.update(phase=phase, message=message, **values)
        job.setdefault("events", []).append({"at": time.time(), "phase": phase, "message": message})
        with self.jobs_lock:
            self.jobs[job["job_id"]] = deepcopy(job)
            while len(self.jobs) > 128:
                self.jobs.pop(next(iter(self.jobs)))
        atomic(self.c.control / "jobs" / (job["job_id"] + ".json"), job)

    def _safe_progress(self, job, phase, message, **values):
        try:
            self._progress(job, phase, message, **values)
        except (OSError, ValueError, TypeError, Problem) as exc:
            report_failure("job_write_failed", exc)

    def _failed_job(self, job, message, **values):
        self._safe_progress(job, "failed", message, status="failed", **values)
        try:
            self.c.audit("deployment_failed", outcome="failed")
        except (OSError, ValueError, Problem) as exc:
            report_failure("deployment_audit_failed", exc)

    def apply(self, request):
        if not request.confirm:
            raise Problem("confirmation_required", "Review the candidate before applying it.")
        if not self.busy.acquire(blocking=False):
            raise Problem("deployment_busy", "A deployment is already running.", 409)
        identity = os.urandom(16).hex()
        job = {
            "job_id": identity,
            "plan_id": request.plan_id,
            "requested_revision": request.revision,
            "status": "starting",
            "phase": "queued",
            "events": [],
        }
        queued = False
        try:
            self._progress(job, "queued", "Preparing the approved configuration.")
            self.executor.submit(self._deploy, request, job)
            queued = True
        except (OSError, ValueError, RuntimeError, Problem) as exc:
            report_failure("deployment_queue_failed", exc)
            self._failed_job(job, "Deployment could not be queued. No services were started.")
            raise Problem(
                "deployment_queue_failed",
                "Deployment could not be queued. Review host storage and the helper service.",
                503,
            ) from None
        finally:
            if not queued:
                self.busy.release()
        return job

    def _deploy(self, request, job):
        installed = False
        install_attempted = False
        try:
            with self.c.lock:
                if request.revision != self.c.state()["revision"]:
                    raise Problem(
                        "revision_conflict", "The saved configuration revision changed.", 409
                    )
                self._progress(
                    job, "validating", "Validating Docker Compose and reviewed file fingerprints."
                )
                self.docker.validate(request.plan_id)
                folder, plan = self.c.plan(request.plan_id)
                if plan["status"] != "PREPARED":
                    raise Problem("stale_plan", "Prepare and review the latest revision.", 409)
                previous = plan["previous"].get("applied") or {}
                changed_scope = any(
                    previous.get(k) != plan["settings"][k]
                    for k in (
                        "wan_interfaces",
                        "fortigate_host",
                        "api_port",
                        "vdom",
                        "api_tls_verify",
                    )
                )
                if plan["settings"]["wan_interfaces"] and (plan["fresh"] or changed_scope):
                    inventory = load(self.c.control / "inventory.json")
                    if not inventory or inventory.get("scope") != self.inventory_scope(
                        Settings.model_validate(plan["settings"])
                    ):
                        raise Problem(
                            "discovery_required",
                            "Discover interfaces for this saved revision before deployment.",
                        )
                    interfaces.selected_names(
                        inventory["report"],
                        plan["settings"]["wan_interfaces"],
                        plan["settings"]["vdom"],
                    )
                if (
                    plan["settings"]["monitoring"]["accounting"]
                    and not plan["fresh"]
                    and not previous.get("expected_devid")
                    and plan["settings"]["expected_devid"]
                ):
                    self.confirm_serial(Settings.model_validate(plan["settings"]))
                self._progress(
                    job,
                    "backup",
                    "Saving a private configuration backup; monitoring data is not copied or migrated.",
                )
                install_attempted = True
                self.c.install_files(request.plan_id)
                installed = True
                enabled, disabled, recreate = self.recreate_plan(
                    plan, self.c.compose(), self.running_services()
                )
                self._progress(
                    job,
                    "starting",
                    (
                        "Starting the enabled services"
                        if plan["fresh"]
                        else "Restarting the services this change affects"
                        if recreate
                        else "No service needs a restart"
                    )
                    + "; all data volumes are kept.",
                    restarted=recreate,
                )
                self.docker.start(enabled, disabled, recreate)
                self._progress(job, "readiness", "Waiting for monitoring service readiness.")
                services = self.docker.ready(enabled)
                if plan["fresh"]:
                    c = setup.validate_config(load(self.c.root / self.c.state()["site_file"]))
                    setup.import_dashboard(self.c.root, c)
                    if c.get("portal_enabled"):
                        setup.connect_portal(self.c.root, c)
                else:
                    # Saved dashboards are kept. Only what Settings own (the
                    # line and device selectors and the generated title)
                    # is updated, and the former raw-log usage panels removed.
                    # Services are already running, so a failure here must not
                    # undo the deployment.
                    self._progress(
                        job,
                        "dashboard",
                        "Updating the saved Grafana dashboard to match Settings; other panels are kept.",
                    )
                    try:
                        setup.update_saved_dashboard(
                            self.c.root,
                            setup.validate_config(load(self.c.root / self.c.state()["site_file"])),
                        )
                    except Exception as exc:
                        report_failure("dashboard_update_failed", exc)
                self._progress(
                    job,
                    "diagnostics",
                    "Checking collection evidence. Missing FortiGate data is not a deployment failure.",
                )
                # Services are already running, so failed evidence must not undo the deployment.
                try:
                    diagnostic = self.diagnostics()
                except Exception as exc:
                    report_failure("deployment_diagnostics_failed", exc)
                    diagnostic = None
                self.c.complete(request.plan_id)
                self._safe_progress(
                    job,
                    "complete",
                    "Configuration applied. Review any collection warnings in Health.",
                    status="complete",
                    services=services,
                    diagnostics=diagnostic,
                    applied_revision=self.c.state().get("applied_revision"),
                )
        except Exception as e:
            report_failure("deployment_failed", e)
            restored = False
            # install_files can fail after replacing only some files. Its
            # durable APPLYING marker is written before the first target write.
            if install_attempted and not installed:
                try:
                    _, current = self.c.plan(request.plan_id)
                    installed = current.get("status") in ("APPLYING", "APPLIED", "FAILED")
                except (Problem, OSError, ValueError):
                    pass
            if installed:
                try:
                    self.c.restore(request.plan_id)
                    restored = True
                except (Problem, OSError, ValueError) as exc:
                    report_failure("configuration_restore_failed", exc)
            message = (
                e.message
                if isinstance(e, Problem)
                else "Deployment could not be completed. Review the recorded step, configuration recovery and service status before retrying. Monitoring data was not deleted."
            )
            self._failed_job(
                job,
                message,
                failed_step=job["phase"],
                configuration_restored=restored,
                rollback_available=installed,
            )
        finally:
            self.busy.release()

    def rollback(self, request):
        if not request.confirm:
            raise Problem("confirmation_required", "Confirm the configuration rollback.")
        if request.revision != self.c.state()["revision"]:
            raise Problem(
                "revision_conflict",
                "Reload the latest configuration before choosing rollback.",
                409,
            )
        if not self.busy.acquire(blocking=False):
            raise Problem("deployment_busy", "Wait for the running operation.", 409)
        identity = os.urandom(16).hex()
        job = {
            "job_id": identity,
            "plan_id": request.plan_id,
            "requested_revision": request.revision,
            "status": "starting",
            "events": [],
        }

        def run():
            try:
                _, plan = self.c.plan(request.plan_id)
                if plan["status"] != "ROLLED_BACK":
                    self.c.restore(request.plan_id)
                if not plan["previous"]["configured"]:
                    # No original stack exists on a failed first install: stop
                    # only this registered project's services; retain all files/data.
                    self.docker.run(
                        self.docker.command()
                        + ["stop", *[n for n in SERVICES if n in self.c.compose()["services"]]]
                    )
                    self._safe_progress(
                        job,
                        "complete",
                        "Fresh-install services stopped; all data retained.",
                        status="complete",
                        applied_revision=self.c.state().get("applied_revision"),
                    )
                    return
                s = Settings.model_validate(plan["previous"]["settings"])
                enabled, disabled = enabled_services(s, self.c.compose())
                self.docker.start(enabled, disabled)
                self.docker.ready(enabled)
                self._safe_progress(
                    job,
                    "complete",
                    "Previous configuration restored; no application-data rollback was attempted.",
                    status="complete",
                    applied_revision=self.c.state().get("applied_revision"),
                )
            except Exception as e:
                report_failure("rollback_failed", e)
                self._failed_job(
                    job,
                    e.message
                    if isinstance(e, Problem)
                    else "Rollback could not be completed. Retained data was not deleted; review diagnostics.",
                )
            finally:
                self.busy.release()

        queued = False
        try:
            self._progress(
                job,
                "rollback",
                "Restoring the previous configuration. Data volumes remain untouched.",
            )
            self.executor.submit(run)
            queued = True
        except (OSError, ValueError, RuntimeError, Problem) as exc:
            report_failure("rollback_queue_failed", exc)
            self._failed_job(job, "Rollback could not be queued. No services were restarted.")
            raise Problem(
                "rollback_queue_failed",
                "Rollback could not be queued. Review host storage and the helper service.",
                503,
            ) from None
        finally:
            if not queued:
                self.busy.release()
        return job
