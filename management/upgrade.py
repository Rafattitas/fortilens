"""In-place upgrade of a web-managed installation, with a backup and rollback.

Run from the new reviewed source as root:

    sudo ./install.sh --upgrade
    sudo ./install.sh --rollback-upgrade

An upgrade replaces the allowlisted management source, rebuilds the management
virtual environment only when its requirements change, restarts the management
services, then refreshes the code of the existing accounting and native syslog
services and rebuilds and restarts only those that are running. A stopped
service gets the new code and stays stopped. It never initializes monitoring,
changes settings, rotates secrets, adds or starts services or touches data
volumes.

Every replaced file is saved first. A failure at any step restores the previous
source, environment, service code and images and restarts the previous version.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time

from . import install

# Code copied into an existing stack. Keys are stack paths, values source paths;
# the same mapping portable/setup.py uses when it first renders the stack.
STACK_CODE = {
    **{
        "usage/" + name: "portable/" + name
        for name in (
            "accounting.py",
            "accounting_storage.py",
            "interfaces.py",
            "wan_health.py",
            "usage_service.py",
            "portal.py",
            "reporting.html",
        )
    },
    "usage/Dockerfile": "portable/templates/Dockerfile.usage",
    "syslog/syslog_receiver.py": "portable/syslog_receiver.py",
    "syslog/Dockerfile": "portable/templates/Dockerfile.syslog",
}
# Stack directory -> Compose services built from it. The portal shares the
# accounting image, so it is recreated (not rebuilt) after the worker.
SERVICES = {"usage": ("fortilens-usage", "fortilens-portal"), "syslog": ("fortilens-syslog",)}
IMAGES = {"usage": "fortilens-usage", "syslog": "fortilens-syslog"}
REQUIREMENTS = "management/requirements.txt"
MIN_FREE = 1024**3
REGISTRY = "/etc/fortilens/registry.json"
WEB_UNIT = "/etc/systemd/system/fortilens-web.service"


class UpgradeError(SystemExit):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def stack_changes(source, stack):
    """Stack files whose code differs from the new source.

    Only files that already exist are refreshed: an upgrade never enables a
    service the operator has not deployed.
    """
    changes = []
    for relative, origin in sorted(STACK_CODE.items()):
        target = Path(stack) / relative
        if target.is_symlink():
            raise UpgradeError("Refusing to replace a symbolic link in the monitoring stack.")
        if target.is_file() and target.read_bytes() != (Path(source) / origin).read_bytes():
            changes.append(relative)
    return changes


def affected(changes):
    return sorted({relative.split("/", 1)[0] for relative in changes})


def journal_path(control):
    return Path(control) / "upgrades" / "current.json"


def read_journal(control):
    path = journal_path(control)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise UpgradeError(
            "The upgrade record is unreadable. Preserve the installation and review it manually."
        ) from None
    if not isinstance(value, dict) or value.get("status") not in (
        "APPLYING",
        "COMPLETE",
        "FAILED",
        "ROLLED_BACK",
    ):
        raise UpgradeError(
            "The upgrade record is incomplete. Preserve the installation and review it manually."
        )
    if not re.fullmatch(r"\d{8}T\d{6}Z", str(value.get("id", ""))):
        raise UpgradeError("The upgrade record names an invalid backup.")
    return value


def write_journal(control, value):
    install.put(journal_path(control), json.dumps(value, indent=2))


def deployment_running(control):
    """True while a console deployment is replacing configuration files."""
    for plan in (Path(control) / "plans").glob("*/plan.json"):
        try:
            if json.loads(plan.read_text(encoding="utf-8")).get("status") == "APPLYING":
                return True
        except (OSError, ValueError, UnicodeError):
            return True
    return False


def web_access(unit, origin, port):
    """Listener used by the installed web unit, for the readiness check."""
    text = Path(unit).read_text(encoding="utf-8")
    match = re.search(r"--host (\S+)", text)
    return (
        (match.group(1) if match else "127.0.0.1"),
        int(port),
        origin,
        origin.startswith("https:"),
    )


class Host:
    """Host operations, kept apart so tests can replace them."""

    def __init__(self, registry):
        self.registry = registry
        control = Path(registry["control"])
        client = control / "docker-client"
        client.mkdir(exist_ok=True, mode=0o700)
        self.env = {
            k: v for k, v in os.environ.items() if k not in ("DOCKER_HOST", "DOCKER_CONTEXT")
        }
        self.env.update(DOCKER_CONFIG=str(client), BUILDX_CONFIG=str(client / "buildx"))

    def run(self, *args, timeout=900, check=True):
        try:
            result = subprocess.run(
                list(args),
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                env=self.env,
            )
        except (OSError, UnicodeError, subprocess.SubprocessError):
            raise UpgradeError(
                "A host operation could not run or timed out: " + Path(str(args[0])).name + "."
            ) from None
        if check and result.returncode:
            raise UpgradeError(
                "A host operation failed: "
                + " ".join(Path(str(a)).name if i == 0 else str(a) for i, a in enumerate(args[:3]))
                + "."
            )
        return result

    def compose(self, *args, timeout=900):
        r = self.registry
        stack = Path(r["installation"])
        return self.run(
            "docker",
            "compose",
            "-p",
            r["project"],
            "--project-directory",
            str(stack),
            "-f",
            str(stack / r["compose_file"]),
            *args,
            timeout=timeout,
        )

    def services(self):
        return set(self.compose("config", "--services", timeout=60).stdout.split())

    def running(self):
        # A disabled collector keeps its Compose definition but is stopped.
        result = self.compose(
            "ps", "--services", "--status", "running", "--status", "restarting", timeout=60
        )
        return set(result.stdout.split())

    def image_exists(self, image):
        return (
            self.run("docker", "image", "inspect", image, check=False, timeout=60).returncode == 0
        )

    def tag(self, source, target):
        self.run("docker", "image", "tag", source, target, timeout=60)

    def image_tags(self, repository):
        result = self.run(
            "docker",
            "image",
            "ls",
            "--format",
            "{{.Repository}}:{{.Tag}}",
            repository,
            check=False,
            timeout=60,
        )
        return result.stdout.split() if result.returncode == 0 else []

    def untag(self, image):
        # Removes the name only; Docker keeps any layer another image still uses.
        self.run("docker", "image", "rm", image, check=False, timeout=120)

    def wait_healthy(self, service):
        name = self.registry["project"] + "-" + service + "-1"
        state = "missing"
        for _ in range(72):
            result = self.run(
                "docker",
                "inspect",
                "-f",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                name,
                check=False,
                timeout=30,
            )
            state = result.stdout.strip() or "missing"
            if state in ("healthy", "running") and result.returncode == 0:
                return
            if state == "unhealthy":
                break
            time.sleep(5)
        raise UpgradeError("The " + service + " service did not become healthy (" + state + ").")

    def systemctl(self, *args):
        self.run("systemctl", *args, timeout=120)

    def wait_web(self, access):
        install.wait_web(*access)

    def validate(self, prefix):
        code = (
            "from management.configuration import Configuration; import json; c=json.load(open('/etc/fortilens/registry.json')); "
            "Configuration(c['installation'],c['control'],project=c['project'],compose_file=c['compose_file'])"
        )
        env = dict(self.env, PYTHONPATH=str(Path(prefix) / "src"))
        result = subprocess.run(
            [str(Path(prefix) / "venv/bin/python"), "-c", code],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            raise UpgradeError("The new version could not read the registered configuration.")

    def build_venv(self, prefix, requirements):
        target = Path(prefix) / "venv.new"
        if target.exists():
            shutil.rmtree(target)
        self.run("python3", "-m", "venv", str(target))
        self.run(
            str(target / "bin/python"),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(requirements),
        )
        return target


def backup_source(prefix, folder):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        archive.add(Path(prefix) / "src", arcname="src")
    install.put(Path(folder) / "src.tar", data.getvalue())


def restore_source(prefix, folder):
    src = Path(prefix) / "src"
    with tarfile.open(Path(folder) / "src.tar") as archive:
        members = archive.getmembers()
        if any(
            m.issym()
            or m.islnk()
            or not (m.name == "src" or m.name.startswith("src/"))
            or ".." in Path(m.name).parts
            for m in members
        ):
            raise UpgradeError("The source backup is not a plain source tree. Restore it manually.")
        if src.exists():
            shutil.rmtree(src)
        archive.extractall(Path(prefix), members=members, filter="data") if hasattr(
            tarfile, "data_filter"
        ) else archive.extractall(Path(prefix), members=members)


def copy_like(target, data):
    """Replace a stack file keeping its existing owner and mode."""
    info = Path(target).stat()
    install.put(target, data, mode=info.st_mode & 0o777, uid=info.st_uid, gid=info.st_gid)


def refresh_stack(host, source, stack, changes, project, label, running):
    """Copy new code into the stack, then rebuild only affected running services.

    A stopped service (a disabled collector keeps its definition) receives the
    new code but stays stopped; enabling it later in the console builds it.
    Returns the stack directories whose services were rebuilt.
    """
    available = host.services()
    refreshed = []
    for directory in affected(changes):
        image = IMAGES[directory] + ":" + project
        if host.image_exists(image):
            host.tag(image, image + "-pre-" + label)
    for relative in changes:
        copy_like(Path(stack) / relative, (Path(source) / STACK_CODE[relative]).read_bytes())
    for directory in affected(changes):
        built, *shared = [s for s in SERVICES[directory] if s in available] or [None]
        if built is None or built not in running:
            continue
        host.compose("up", "-d", "--no-deps", "--build", "--force-recreate", built)
        host.wait_healthy(built)
        for service in shared:
            if service not in running:
                continue
            host.compose("up", "-d", "--no-deps", "--force-recreate", service, timeout=300)
            host.wait_healthy(service)
        refreshed.append(directory)
    return refreshed


def prune_previous_images(host, project, keep):
    """Remove the image copies kept for upgrades before the one named `keep`.

    Every upgrade tags the image it replaces as `-pre-<time>`. Only the latest
    can be rolled back to, so older tags are dead weight that grows with every
    upgrade. Best effort: a failure here never fails a finished upgrade.
    """
    removed = []
    for repository in sorted(set(IMAGES.values())):
        pattern = re.compile(re.escape(repository + ":" + project) + r"-pre-(\d{8}T\d{6}Z)")
        for image in host.image_tags(repository):
            match = pattern.fullmatch(image)
            if match and match.group(1) != keep:
                host.untag(image)
                removed.append(image)
    return removed


def restore_stack(host, stack, folder, journal):
    available = host.services()
    # Recreate only what ran before the upgrade. A journal written before this
    # was recorded falls back to what runs now, so a stopped service stays stopped.
    running = set(journal["running"]) if "running" in journal else host.running()
    for relative in journal.get("stack", []):
        saved = Path(folder) / "stack" / relative
        if saved.is_file():
            copy_like(Path(stack) / relative, saved.read_bytes())
    project = host.registry["project"]
    for directory in affected(journal.get("stack", [])):
        image = IMAGES[directory] + ":" + project
        previous = image + "-pre-" + journal["id"]
        if host.image_exists(previous):
            host.tag(previous, image)
        for service in [s for s in SERVICES[directory] if s in available and s in running]:
            host.compose("up", "-d", "--no-deps", "--force-recreate", service, timeout=300)
            host.wait_healthy(service)


def restore(host, prefix, journal, access):
    """Return management and service code to the saved previous version."""
    control = Path(host.registry["control"])
    stack = Path(host.registry["installation"])
    folder = control / "upgrades" / journal["id"]
    host.run("systemctl", "stop", "fortilens-web", "fortilens-helper", check=False, timeout=120)
    restore_source(prefix, folder)
    install.put(Path(prefix) / "installation.json", (folder / "installation.json").read_bytes())
    shutil.rmtree(Path(prefix) / "venv.new", ignore_errors=True)
    previous_venv = Path(prefix) / "venv.previous"
    if journal.get("venv_swapped") and previous_venv.is_dir():
        current = Path(prefix) / "venv"
        if current.exists():
            shutil.rmtree(current)
        previous_venv.rename(current)
    restore_stack(host, stack, folder, journal)
    host.systemctl("start", "fortilens-helper", "fortilens-web")
    host.wait_web(access)


def preflight(prefix):
    if os.geteuid() != 0:
        raise UpgradeError("Run the upgrade as root on the server that hosts the installation.")
    meta_path = Path(prefix) / "installation.json"
    if not meta_path.exists():
        raise UpgradeError(
            "No web-managed installation was found. Use ./install.sh for a new installation."
        )
    meta = install.saved_metadata(meta_path, ("data", "origin", "source"))
    registry = install.saved_metadata(
        REGISTRY, ("installation", "control", "project", "compose_file")
    )
    if (Path(prefix) / "installing.json").exists():
        raise UpgradeError(
            "An installation is unfinished. Resume it with the original options before upgrading."
        )
    return meta, registry


def upgrade(source, prefix, host=None, now=None):
    source, prefix = Path(source).resolve(), Path(prefix)
    meta, registry = preflight(prefix)
    host = host or Host(registry)
    control, stack = Path(registry["control"]), Path(registry["installation"])
    previous = read_journal(control)
    if previous and previous["status"] == "APPLYING":
        raise UpgradeError(
            "An earlier upgrade was interrupted. Run ./install.sh --rollback-upgrade first."
        )
    if deployment_running(control):
        raise UpgradeError(
            "A configuration deployment is running. Wait for it to finish, then upgrade."
        )
    new = install.source_files(source)
    changes = stack_changes(source, stack)
    if new == meta["source"] and not changes:
        print("FortiLens is already up to date. Nothing was changed.")
        return None
    for path in (prefix, control):
        if shutil.disk_usage(path).free < MIN_FREE:
            raise UpgradeError(
                "Less than 1 GiB is free for the backup and image build. Free space first; nothing was changed."
            )
    label = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    folder = control / "upgrades" / label
    if folder.exists():
        raise UpgradeError("An upgrade backup with this time already exists. Retry in a moment.")
    folder.mkdir(parents=True, mode=0o700)
    os.chmod(folder.parent, 0o700)
    requirements_changed = (prefix / "src" / REQUIREMENTS).read_bytes() != (
        source / REQUIREMENTS
    ).read_bytes()
    access = web_access(WEB_UNIT, meta["origin"], registry.get("management_port", 3030))
    # Back up everything that can change before the first replacement.
    backup_source(prefix, folder)
    install.put(folder / "installation.json", (prefix / "installation.json").read_bytes())
    for relative in changes:
        install.put(folder / "stack" / relative, (stack / relative).read_bytes())
    journal = {
        "id": label,
        "status": "APPLYING",
        "from": digest(json.dumps(meta["source"], sort_keys=True).encode()),
        "to": digest(json.dumps(new, sort_keys=True).encode()),
        "requirements_changed": requirements_changed,
        "stack": changes,
        "running": sorted(host.running()),
        "step": "prepare",
    }
    write_journal(control, journal)
    try:
        venv = host.build_venv(prefix, source / REQUIREMENTS) if requirements_changed else None
        journal["step"] = "management"
        write_journal(control, journal)
        host.systemctl("stop", "fortilens-web", "fortilens-helper")
        for name in new:
            install.put(prefix / "src" / name, (source / name).read_bytes(), mode=0o644)
        if venv:
            old = prefix / "venv.previous"
            if old.exists():
                shutil.rmtree(old)
            (prefix / "venv").rename(old)
            journal["venv_swapped"] = True
            write_journal(control, journal)
            venv.rename(prefix / "venv")
        host.validate(prefix)
        host.systemctl("start", "fortilens-helper", "fortilens-web")
        host.wait_web(access)
        journal["step"] = "services"
        write_journal(control, journal)
        refreshed = refresh_stack(
            host, source, stack, changes, registry["project"], label, set(journal["running"])
        )
        install.put(prefix / "installation.json", json.dumps({**meta, "source": new}, indent=2))
    except BaseException as failure:
        journal["failed_step"] = journal["step"]
        try:
            restore(host, prefix, journal, access)
            journal["status"] = "FAILED"
            write_journal(control, journal)
        except BaseException:
            write_journal(control, journal)
            raise UpgradeError(
                "The upgrade failed and the automatic restore did not finish. The backup is in "
                + str(folder)
                + ". Run ./install.sh --rollback-upgrade from this source."
            ) from None
        if isinstance(failure, KeyboardInterrupt):
            raise UpgradeError("Upgrade cancelled. The previous version was restored.") from None
        message = str(failure) if isinstance(failure, SystemExit) else "A host operation failed."
        raise UpgradeError(
            message + " The previous version was restored; no data volume was changed."
        ) from None
    journal["status"] = "COMPLETE"
    journal["step"] = "done"
    write_journal(control, journal)
    try:
        prune_previous_images(host, registry["project"], label)
    except (Exception, UpgradeError):
        # The upgrade is complete; a host error (UpgradeError is a SystemExit)
        # while tidying old image names must not turn it into a failure.
        pass
    stopped = [d for d in affected(changes) if d not in refreshed]
    print(
        "FortiLens upgraded. Management restarted; refreshed services: "
        + (", ".join(refreshed) or "none")
        + "."
        + (" New code only, still stopped: " + ", ".join(stopped) + "." if stopped else "")
    )
    print(
        "Backup: "
        + str(folder)
        + ". To return to the previous version: sudo ./install.sh --rollback-upgrade"
    )
    return journal


def rollback(prefix, host=None):
    prefix = Path(prefix)
    meta, registry = preflight(prefix)
    host = host or Host(registry)
    control = Path(registry["control"])
    journal = read_journal(control)
    if not journal or journal["status"] not in ("APPLYING", "COMPLETE"):
        raise UpgradeError("There is no completed or interrupted upgrade to roll back.")
    access = web_access(WEB_UNIT, meta["origin"], registry.get("management_port", 3030))
    restore(host, prefix, journal, access)
    journal["status"] = "ROLLED_BACK"
    write_journal(control, journal)
    print(
        "The previous FortiLens version was restored from "
        + str(control / "upgrades" / journal["id"])
        + ". Data volumes were not changed."
    )
    return journal
