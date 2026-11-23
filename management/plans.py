"""Reviewed plans: prepare, review, install, complete, restore and prune."""

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import time
import yaml
from .common import Problem, atomic, fingerprint, guarded, load, report_failure, setup
from .models import Settings
from .compose_policy import NOTIFY_KEYS, NOTIFY_SECRET, SERVICES, _compose_changes, _same_content

# Reviewed plans kept after an Apply, newest first, for rollback.
PLANS_KEPT = 5


class PlansMixin:
    """The plan lifecycle. Each plan is a private folder under control/plans."""

    def prepare(self, revision):
        with self.lock:
            state = self.state()
            if revision != state["revision"]:
                raise Problem("revision_conflict", "Reload the latest saved revision.", 409)
            settings = Settings.model_validate(state["settings"])
            token = self.token()
            if not token:
                raise Problem(
                    "token_required", "Save the FortiGate API token before preparing deployment."
                )
            baseline = load(self.root / state["site_file"], {})
            pending = load(self.secret_path, {})
            if pending and pending.get("revision") != state["revision"]:
                raise Problem(
                    "interrupted_draft",
                    "The last draft save was interrupted. Save the form again before preparing.",
                )
            # Resolve write-only CA operations before validating the effective
            # candidate. An applied CA must not invalidate its pending removal.
            baseline = deepcopy(baseline)
            if "ca" in pending:
                baseline["api_ca_file"] = "secrets/web-fortigate-ca.pem" if pending["ca"] else ""
            if not settings.api_tls_verify and baseline.get("api_ca_file"):
                raise Problem(
                    "tls_ca_conflict",
                    "A custom CA cannot be actively used while TLS verification is disabled. Explicitly remove the custom CA or enable certificate verification.",
                )
            c = settings.portable(baseline)
            identity = secrets.token_hex(16)
            folder = self.control / "plans" / identity
            folder.mkdir(parents=True, mode=0o700)
            # render reads CA files but accepts no browser-supplied paths.
            render_config = deepcopy(c)
            if c["api_ca_file"]:
                path = (
                    folder / "ca.pem" if "ca" in pending else guarded(self.root, c["api_ca_file"])
                )
                if "ca" in pending:
                    atomic(path, pending["ca"].encode())
                render_config["api_ca_file"] = str(path)
            stage = folder / "generated"
            setup.render(render_config, stage, token)
            files = self._patch(stage, settings, c)
            if not self.compose():
                c = self._fresh_candidate(stage, settings, files)
            if NOTIFY_SECRET in files or any(pending.get(k) for k in NOTIFY_KEYS):
                merged = {k: v for k, v in self.notify_secrets().items() if v}
                files[NOTIFY_SECRET] = (json.dumps(merged) + "\n").encode()
            if self.compose() and "ca" in pending and pending["ca"]:
                files["secrets/web-fortigate-ca.pem"] = pending["ca"].encode()
            files[state["site_file"]] = setup.dumps(c).encode()
            records = []
            for name, data in files.items():
                target = guarded(self.root, name)
                candidate = guarded(folder / "candidate", name)
                atomic(candidate, data, mode=0o644)
                meta = target.stat() if target.exists() else None
                records.append(
                    {
                        "path": name,
                        "before": fingerprint(target),
                        "after": fingerprint(candidate),
                        "mode": stat.S_IMODE(meta.st_mode) if meta else 0o644,
                        "uid": meta.st_uid if meta else None,
                        "gid": meta.st_gid if meta else None,
                    }
                )
            plan = {
                "id": identity,
                "revision": revision,
                "status": "PREPARED",
                "files": records,
                "fresh": not state["configured"],
                "settings": state["settings"],
                "previous": state,
                "created": time.time(),
                "secret_fingerprint": fingerprint(self.secret_path),
                # A first installation starts every enabled service.
                "affected_services": (
                    self.affected_services(folder, records) if state["configured"] else None
                ),
            }
            atomic(folder / "plan.json", plan)
            return self.review(plan)

    def affected_services(self, folder, records):
        """Services that read a file this candidate changes, in SERVICES order.

        Readers come from the installed and candidate Compose files: bind
        mounts, file-backed secrets and build contexts. A Compose change counts
        only for the service definitions that differ, and a YAML or JSON file
        whose content is unchanged (only rewritten) affects nothing. Returns
        None when a change cannot be traced to particular services; Apply then
        recreates every enabled service, as before.
        """
        candidate = folder / "candidate"
        before = self.compose() or {"services": {}}
        after = before
        if guarded(candidate, self.compose_name).is_file():
            after = yaml.safe_load(
                guarded(candidate, self.compose_name).read_text(encoding="utf-8-sig")
            )
            if not isinstance(after, dict) or not isinstance(after.get("services"), dict):
                return None
        affected = set()
        for row in records:
            path = row["path"]
            if row["before"] == row["after"]:
                continue
            if path == self.compose_name:
                changed = _compose_changes(before, after)
                if changed is None:
                    return None
                affected |= changed
            elif row["before"] is None or not _same_content(
                guarded(self.root, path), guarded(candidate, path)
            ):
                readers = self._readers(path, before) | self._readers(path, after)
                # The site file reaches services only through mounts; the console
                # reads it directly. Any other file nothing mounts is untraceable.
                if not readers and path != self.state()["site_file"]:
                    return None
                affected |= readers
        return [name for name in SERVICES if name in affected]

    def _readers(self, path, compose):
        """Services in ``compose`` that read the installation file ``path``."""
        target = Path(os.path.normpath(self.root / path))

        def covers(source):
            if not isinstance(source, str) or not source.startswith((".", "/")):
                return False  # A named volume, not a host path.
            base = Path(os.path.normpath(self.root / source))
            # A mount outside the installation (node-exporter reads /) is not a reader.
            if base != self.root and self.root not in base.parents:
                return False
            return target == base or base in target.parents

        secrets = {
            name
            for name, value in (compose.get("secrets") or {}).items()
            if isinstance(value, dict) and covers(value.get("file"))
        }
        readers = set()
        for name, service in (compose.get("services") or {}).items():
            for volume in service.get("volumes") or []:
                source = (
                    volume.split(":")[0]
                    if isinstance(volume, str)
                    else (volume or {}).get("source")
                )
                if covers(source):
                    readers.add(name)
            for secret in service.get("secrets") or []:
                if (secret if isinstance(secret, str) else (secret or {}).get("source")) in secrets:
                    readers.add(name)
            build = service.get("build")
            context = build if isinstance(build, str) else (build or {}).get("context")
            if covers(context):
                readers.add(name)
            env_files = service.get("env_file") or []
            for env_file in [env_files] if isinstance(env_files, str) else env_files:
                if covers(env_file if isinstance(env_file, str) else (env_file or {}).get("path")):
                    readers.add(name)
        return readers

    def review(self, plan):
        return {
            "plan_id": plan["id"],
            "revision": plan["revision"],
            "status": plan["status"],
            "fresh_installation": plan["fresh"],
            "changed_files": [r["path"] for r in plan["files"] if r["before"] != r["after"]],
            "services_requiring_review": list(SERVICES),
            "settings": plan["settings"],
            "warnings": [
                "Service restarts may create collection gaps. Existing alert rules are not changed.",
                *(
                    [
                        "Choose an actual SLA check name before relying on Internet health or SLA measurements. A blank selection remains unknown."
                    ]
                    if not plan["settings"].get("sla")
                    else []
                ),
                *(
                    [
                        "Native UDP is best effort. The receiver restricts source addresses but does not authenticate the sender or prove complete delivery."
                    ]
                    if plan["settings"].get("syslog_format") == "fortigate"
                    else []
                ),
                *(
                    [
                        "The native receiver shares the Linux host network namespace. A bind-only root bootstrap permanently drops to UID/GID 10001 with no usable capabilities before processing. TCP handoff and health remain loopback-only; receiver metrics are local and are not scraped by bridge Prometheus."
                    ]
                    if plan["settings"].get("syslog_network") == "host"
                    else []
                ),
                "Syslog sender configuration and FortiGate read permissions must be set by its administrator.",
                "WAN/SLA/interface views share the existing exporter.",
                "Missing telemetry remains unknown; a completed deployment does not certify traffic accuracy.",
            ],
        }

    def plan(self, identity):
        if not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise Problem("invalid_plan", "Choose a server-generated configuration plan.")
        folder = self.control / "plans" / identity
        try:
            plan = load(folder / "plan.json")
        except (OSError, ValueError) as exc:
            report_failure("configuration_plan_read_failed", exc)
            raise Problem(
                "plan_unavailable",
                "The reviewed plan is unreadable. Review host storage and prepare a new candidate.",
                503,
            ) from None
        if not plan:
            raise Problem("plan_missing", "This reviewed plan is not available.", 404)
        if (
            not isinstance(plan, dict)
            or plan.get("id") != identity
            or type(plan.get("revision")) is not int
            or plan.get("status")
            not in ("PREPARED", "APPLYING", "APPLIED", "FAILED", "ROLLED_BACK")
            or not isinstance(plan.get("files"), list)
            or not isinstance(plan.get("settings"), dict)
            or not isinstance(plan.get("previous"), dict)
        ):
            raise Problem(
                "plan_invalid",
                "The reviewed plan is incomplete. Review the current installation and prepare a new candidate before applying.",
                503,
            )
        for row in plan["files"]:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("path"), str)
                or type(row.get("mode")) is not int
                or not 0 <= row["mode"] <= 0o7777
                or not all(key in row for key in ("before", "after", "uid", "gid"))
                or row["before"] is not None
                and not re.fullmatch(r"[a-f0-9]{64}", str(row["before"]))
                or not re.fullmatch(r"[a-f0-9]{64}", str(row["after"]))
            ):
                raise Problem(
                    "plan_invalid",
                    "The reviewed plan is incomplete. Review the current installation and prepare a new candidate before applying.",
                    503,
                )
            guarded(self.root, row["path"])
        return folder, plan

    def install_files(self, identity):
        with self.lock:
            folder, plan = self.plan(identity)
            if plan["status"] != "PREPARED" or plan["revision"] != self.state()["revision"]:
                raise Problem("stale_plan", "Prepare and review the latest revision.", 409)
            if fingerprint(self.secret_path) != plan["secret_fingerprint"]:
                raise Problem(
                    "draft_changed", "The write-only credential draft changed after review.", 409
                )
            for row in plan["files"]:
                if (
                    fingerprint(guarded(self.root, row["path"])) != row["before"]
                    or fingerprint(guarded(folder / "candidate", row["path"])) != row["after"]
                ):
                    raise Problem(
                        "external_edit",
                        "Files changed after review. Prepare a new candidate; nothing was overwritten.",
                        409,
                    )
            for row in plan["files"]:
                if row["before"] is not None:
                    atomic(
                        guarded(folder / "before", row["path"]),
                        guarded(self.root, row["path"]).read_bytes(),
                    )
            plan["status"] = "APPLYING"
            atomic(folder / "plan.json", plan)
            # Save already refused every change except the forward-only ones,
            # so the recorded scope follows them and cannot be undone later.
            if plan["settings"]["monitoring"]["accounting"]:
                atomic(
                    self.control / "accounting-contract.json",
                    {
                        k: plan["settings"][k]
                        for k in ("vdom", "accounting_timezone", "expected_devid")
                    },
                )
            for row in plan["files"]:
                self.write_target(row, guarded(folder / "candidate", row["path"]).read_bytes())
            return plan

    def restore(self, identity):
        with self.lock:
            folder, plan = self.plan(identity)
            if plan["status"] not in ("APPLYING", "APPLIED", "FAILED"):
                raise Problem(
                    "rollback_unavailable",
                    "This plan has no applied configuration to restore.",
                    409,
                )
            for row in plan["files"]:
                if fingerprint(guarded(self.root, row["path"])) not in (
                    row["before"],
                    row["after"],
                ):
                    raise Problem(
                        "rollback_external_edit",
                        "An operator changed a managed file. Rollback will not overwrite it.",
                        409,
                    )
            for row in plan["files"]:
                if row["before"] is not None:
                    self.write_target(row, guarded(folder / "before", row["path"]).read_bytes())
                # Newly created files/volumes are retained for recovery, never deleted.
            previous = plan["previous"]
            previous["revision"] = self.state()["revision"] + 1
            previous["applied_revision"] = previous["revision"] if previous["configured"] else None
            atomic(self.state_path, previous)
            # Do not record a completed rollback before its configuration state
            # is durable. A failed state write keeps the plan recoverable.
            plan["status"] = "ROLLED_BACK"
            atomic(folder / "plan.json", plan)
            self.audit("configuration_rollback")
            return previous

    def prune_plans(self, keep):
        """Delete all but the newest reviewed plans; ``keep`` and running ones stay.

        Each plan holds copies of the configuration and its secrets, so old
        ones must not pile up. The newest few remain for rollback.
        """
        root = self.control / "plans"
        folders = [
            p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"[a-f0-9]{32}", p.name)
        ]
        folders.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for folder in folders[PLANS_KEPT:]:
            if folder.name == keep or load(folder / "plan.json", {}).get("status") == "APPLYING":
                continue
            shutil.rmtree(folder)

    def write_target(self, row, data):
        target = guarded(self.root, row["path"])
        # Directory mounts must be traversable by the existing nonroot service
        # users. The installation/control roots and host secret directory stay
        # private; individual secret binds retain their container-readable mode.
        missing = []
        parent = target.parent
        while not parent.exists() and parent != self.root:
            missing.append(parent)
            parent = parent.parent
        for parent in reversed(missing):
            parent.mkdir(mode=0o700 if parent.name == "secrets" else 0o755)
        atomic(target, data, mode=row["mode"])
        if hasattr(os, "geteuid") and os.geteuid() == 0 and row["uid"] is not None:
            os.chown(target, row["uid"], row["gid"])

    def complete(self, identity):
        with self.lock:
            folder, plan = self.plan(identity)
            state = self.state()
            state.update(
                configured=True, applied_revision=plan["revision"], applied=plan["settings"]
            )
            plan["status"] = "APPLIED"
            atomic(folder / "plan.json", plan)
            atomic(self.state_path, state)
            # The apply has committed; a failed cleanup must not look like a failed apply.
            try:
                atomic(self.secret_path, {})
            except OSError as exc:
                report_failure("pending_secret_clear_failed", exc)
            try:
                self.prune_plans(identity)
            except OSError as exc:
                report_failure("plan_prune_failed", exc)
            self.audit(
                "configuration_applied",
                [row["path"] for row in plan["files"] if row["before"] != row["after"]],
            )
