"""The fixed Docker Compose operations for the one registered project.

No browser command, image, root or project input reaches these commands.
"""

import http.client
import json
import os
import platform
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import yaml
from .common import Problem, load, report_failure, setup
from .compose_policy import SERVICES
from .models import (
    Settings,
)


class Docker:
    def __init__(self, configuration):
        self.c = configuration

    def run(self, args, timeout=180):
        # Compose/buildx need writable client state even though systemd makes
        # /root read-only. Keep it inside the helper's private registered root;
        # never broaden the web process or helper's filesystem permissions.
        client = self.c.control / "docker-client"
        client.mkdir(exist_ok=True, mode=0o700)
        env = os.environ.copy()
        env.update(DOCKER_CONFIG=str(client), BUILDX_CONFIG=str(client / "buildx"))
        env.pop("DOCKER_HOST", None)
        env.pop("DOCKER_CONTEXT", None)
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                shell=False,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            report_failure("docker_operation_unavailable", exc)
            raise Problem(
                "docker_unavailable", "Docker is unavailable or the approved operation timed out."
            ) from None
        if result.returncode:
            if "read-only file system" in result.stderr.lower():
                raise Problem(
                    "docker_state_read_only",
                    "Docker could not write its client/build state. Review the helper private state directory and systemd permissions.",
                )
            raise Problem(
                "docker_operation_failed",
                "Docker rejected the operation. Review service readiness, host permissions, storage and port availability.",
            )
        return result.stdout

    def command(self, file=None, directory=None):
        return [
            "docker",
            "compose",
            "-p",
            self.c.project,
            "--project-directory",
            str(directory or self.c.root),
            "-f",
            str(file or self.c.root / self.c.compose_name),
        ]

    def validate(self, plan):
        folder, _ = self.c.plan(plan)
        # File binds are resolved against candidate files; volume identity still
        # uses the original fixed project. config --quiet never starts a service.
        self.run(
            self.command(folder / "candidate" / self.c.compose_name, folder / "candidate")
            + ["config", "--quiet"],
            30,
        )
        self.check_ports(yaml.safe_load((folder / "candidate" / self.c.compose_name).read_text()))

    def check_ports(self, candidate):
        own = set()
        for row in self.statuses():
            if not row.get("id") or row["status"] != "running":
                continue
            info = json.loads(self.run(["docker", "inspect", row["id"]], 15))[0]
            if (
                info.get("Config", {}).get("Labels", {}).get("com.docker.compose.project")
                != self.c.project
            ):
                raise Problem(
                    "project_mismatch", "Port ownership did not match the registered project."
                )
            for target, bindings in (info.get("NetworkSettings", {}).get("Ports") or {}).items():
                for binding in bindings or []:
                    own.add(
                        (
                            row["service"],
                            binding["HostIp"],
                            int(binding["HostPort"]),
                            target.split("/")[-1],
                        )
                    )
            if (
                row["service"] == "fortilens-syslog"
                and info.get("HostConfig", {}).get("NetworkMode") == "host"
            ):
                active = setup.validate_config(load(self.c.root / self.c.state()["site_file"]))
                if active["syslog_network"] == "host":
                    own.update(
                        {
                            (
                                "fortilens-syslog",
                                active["syslog_bind"],
                                active["syslog_port"],
                                "udp",
                            ),
                            ("fortilens-syslog", "127.0.0.1", active["syslog_port"] + 1, "tcp"),
                        }
                    )
        settings = Settings.model_validate(self.c.state()["settings"])
        enabled, _ = enabled_services(settings, candidate)
        checked = set()
        for name, service in candidate["services"].items():
            if name not in enabled:
                continue
            bindings = list(service.get("ports", []))
            if name == "fortilens-syslog" and service.get("network_mode") == "host":
                if platform.system() != "Linux":
                    raise Problem(
                        "native_host_unsupported",
                        "Native host reception requires Linux Docker Engine.",
                    )
                engine = json.loads(self.run(["docker", "info", "--format", "{{json .}}"], 15))
                if (
                    engine.get("OSType") != "linux"
                    or "docker desktop" in engine.get("OperatingSystem", "").lower()
                    or any(
                        "rootless" in option.lower() for option in engine.get("SecurityOptions", [])
                    )
                ):
                    raise Problem(
                        "native_host_unsupported",
                        "Native host reception requires rootful Linux Docker Engine; Desktop and rootless networking are not supported.",
                    )
                config = settings.portable(load(self.c.root / self.c.state()["site_file"], {}))
                bindings.extend(
                    [
                        f"{config['syslog_bind']}:{settings.syslog_port}:{settings.syslog_port}/udp",
                        f"127.0.0.1:{settings.syslog_port + 1}:{settings.syslog_port + 1}/tcp",
                    ]
                )
            for binding in bindings:
                if not isinstance(binding, str):
                    raise Problem(
                        "custom_ports_review",
                        "Register custom published-port syntax on the host before managing it.",
                    )
                address, protocol = binding.rsplit("/", 1) if "/" in binding else (binding, "tcp")
                parts = address.rsplit(":", 2)
                if len(parts) == 3:
                    host, port = parts[0], int(parts[1])
                elif len(parts) == 2:
                    host, port = "0.0.0.0", int(parts[0])
                else:
                    raise Problem(
                        "custom_ports_review",
                        "Use explicit single-port bindings for managed services.",
                    )
                host = host.strip("[]")
                key = (host, port, protocol)
                if key in checked:
                    raise Problem(
                        "port_conflict", "Two enabled services request the same published port."
                    )
                checked.add(key)
                if (name, host, port, protocol) in own:
                    continue
                # A reviewed profile can transfer this exact listener between
                # the two fixed syslog services in the registered project.
                if name in ("alloy", "fortilens-syslog") and any(
                    (other, host, port, protocol) in own for other in ("alloy", "fortilens-syslog")
                ):
                    continue
                sock = socket.socket(
                    socket.AF_INET6 if ":" in host else socket.AF_INET,
                    socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM,
                )
                try:
                    sock.bind((host, port))
                except OSError:
                    raise Problem(
                        "port_in_use",
                        "A requested monitoring port is already in use. Review service port settings before applying.",
                    ) from None
                finally:
                    sock.close()

    def statuses(self):
        if not (self.c.root / self.c.compose_name).exists():
            return []
        text = self.run(self.command() + ["ps", "--all", "--format", "json"], 30).strip()
        try:
            rows = (
                json.loads(text)
                if text.startswith("[")
                else [json.loads(x) for x in text.splitlines() if x]
            )
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("invalid_container_status")
            for row in rows:
                if (
                    not isinstance(row.get("Service"), str)
                    or not isinstance(row.get("State"), str)
                    or not isinstance(row.get("Name", ""), str)
                ):
                    raise ValueError("invalid_container_status")
        except ValueError as exc:
            report_failure("docker_status_invalid", exc)
            raise Problem(
                "docker_response_invalid",
                "Docker returned an unreadable service status. Review the local engine.",
                503,
            ) from None
        return [
            {
                "service": r.get("Service"),
                "status": r.get("State"),
                "health": r.get("Health") or None,
                "id": r.get("ID"),
            }
            for r in rows
            if r.get("Service") in SERVICES and "-run-" not in r.get("Name", "")
        ]

    def address(self, service, port):
        ids = self.run(self.command() + ["ps", "--all", "-q", service], 15).strip().splitlines()
        if len(ids) != 1:
            raise Problem("service_unavailable", "A required service is not running.")
        try:
            inspected = json.loads(self.run(["docker", "inspect", ids[0]], 15))
            if (
                not isinstance(inspected, list)
                or len(inspected) != 1
                or not isinstance(inspected[0], dict)
            ):
                raise ValueError("invalid_inspection")
            row = inspected[0]
            if not isinstance(row.get("Config"), dict) or not isinstance(
                row["Config"].get("Labels"), dict
            ):
                raise ValueError("invalid_container_identity")
        except (ValueError, TypeError) as exc:
            report_failure("docker_inspect_invalid", exc)
            raise Problem(
                "docker_response_invalid",
                "Docker returned unreadable service identity. No operation was redirected.",
                503,
            ) from None
        if (
            row.get("Config", {}).get("Labels", {}).get("com.docker.compose.project")
            != self.c.project
        ):
            raise Problem(
                "project_mismatch", "Service identity did not match the registered project."
            )
        if (
            service == "fortilens-syslog"
            and port == 8080
            and row.get("HostConfig", {}).get("NetworkMode") == "host"
        ):
            active = setup.validate_config(load(self.c.root / self.c.state()["site_file"]))
            if active["syslog_network"] != "host":
                raise Problem(
                    "service_network_unavailable",
                    "Review the applied native receiver network profile.",
                )
            return "127.0.0.1", active["syslog_port"] + 1
        networks = (
            row.get("NetworkSettings", {}).get("Networks")
            if isinstance(row.get("NetworkSettings"), dict)
            else None
        )
        if not isinstance(networks, dict) or any(
            not isinstance(v, dict) for v in networks.values()
        ):
            raise Problem(
                "service_network_unavailable",
                "This service uses an unsupported host/network layout.",
            )
        addresses = [v.get("IPAddress") for v in networks.values() if v.get("IPAddress")]
        if any(not isinstance(address, str) for address in addresses):
            raise Problem(
                "service_network_unavailable",
                "This service uses an unsupported host/network layout.",
            )
        if not addresses:
            raise Problem(
                "service_network_unavailable",
                "This service uses an unsupported host/network layout.",
            )
        return addresses[0], port

    def http(self, service, port, path, *, method="GET", body=None, headers=None, timeout=5):
        host, port = self.address(service, port)
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            r = conn.getresponse()
            data = r.read(16 * 1024 * 1024 + 1)
            if r.status != 200 or len(data) > 16 * 1024 * 1024:
                raise Problem(
                    "service_api_unavailable",
                    "A monitoring service did not return a readable bounded response.",
                )
            value = json.loads(data)
            if not isinstance(value, (dict, list)):
                raise ValueError("invalid_service_payload")
            return value
        except (OSError, http.client.HTTPException, ValueError) as exc:
            report_failure("service_request_failed", exc)
            raise Problem(
                "service_api_unavailable",
                "A monitoring service did not return a readable bounded response.",
                503,
            ) from None
        finally:
            conn.close()

    def start(self, enabled, disabled, recreate=None):
        """Stop disabled services and recreate ``recreate`` (all enabled if None)."""
        services = self.c.compose()["services"]
        # Stop disabled collectors; volumes, images, secrets and definitions stay.
        stopped = [x for x in disabled if x in services]
        if stopped:
            self.run(self.command() + ["stop", *stopped])
        selected = [
            x
            for x in recreated(enabled, enabled if recreate is None else recreate)
            if x in services
        ]
        if not selected:
            return
        if "fortilens-syslog" in selected and "alloy" in selected:
            # Release an old Alloy-owned UDP binding before the bridge claims
            # it. Compose otherwise schedules replacements concurrently. When
            # only the receiver is recreated, Alloy holds no UDP binding.
            self.run(self.command() + ["stop", "alloy", "fortilens-syslog"])
        # Always recreate the sidecar when its Grafana namespace is replaced.
        if "grafana" in selected and "fortilens-portal" in services:
            self.run(self.command() + ["stop", "fortilens-portal"])
        self.run(
            self.command() + ["up", "-d", "--no-deps", "--build", "--force-recreate", *selected],
            600,
        )
        if "grafana" in selected and "fortilens-portal" in selected:
            self.run(
                self.command() + ["up", "-d", "--no-deps", "--force-recreate", "fortilens-portal"],
                120,
            )

    def ready(self, enabled):
        last = []
        for _ in range(45):
            last = self.statuses()
            byname = {r["service"]: r for r in last}
            running = all(
                byname.get(n, {}).get("status") == "running"
                and byname[n]["health"] not in ("unhealthy", "starting")
                for n in enabled
                if n in self.c.compose()["services"]
            )
            if running:
                try:
                    environment = self.c.compose()["services"]["grafana"].get("environment") or {}
                    subpath = (
                        environment.get("GF_SERVER_SERVE_FROM_SUB_PATH") == "true"
                        if isinstance(environment, dict)
                        else "GF_SERVER_SERVE_FROM_SUB_PATH=true" in environment
                    )
                    self.http("grafana", 3000, "/grafana/api/health" if subpath else "/api/health")
                    self.http("prometheus", 9090, "/api/v1/query?query=up")
                    # Loki ready is text, so its query API confirms readiness here.
                    self.http(
                        "loki",
                        3100,
                        "/loki/api/v1/query?"
                        + urllib.parse.urlencode(
                            {"query": 'sum(count_over_time({job="fortigate-syslog"}[1m]))'}
                        ),
                    )
                    return last
                except (Problem, OSError, ValueError, http.client.HTTPException):
                    pass
            time.sleep(2)
        raise Problem(
            "readiness_failed",
            "A required service did not become ready. Configuration rollback is available.",
        )


def recreated(enabled, chosen):
    """The enabled services Docker.start recreates for ``chosen``, in order.

    The console gateway shares Grafana's network namespace, so it is added
    whenever Grafana is. The native receiver can be recreated alone: moving the
    UDP listener between it and Alloy changes both definitions, so both are
    chosen then, and Docker.start stops them together.
    """
    chosen = set(chosen)
    if "grafana" in chosen:
        chosen.add("fortilens-portal")
    return [name for name in enabled if name in chosen]


def enabled_services(settings, compose):
    f = settings.monitoring
    active = ["prometheus", "grafana", "loki"]
    if f.wan or f.sla or f.interfaces:
        active.append("fortigate-exporter")
    if f.server:
        active.append("node-exporter")
    if f.syslog:
        active.append("alloy")
    if f.syslog and settings.syslog_format == "fortigate":
        active.append("fortilens-syslog")
    if f.accounting:
        active.append("fortilens-usage")
    if "fortilens-portal" in compose["services"]:
        active.append("fortilens-portal")
    return active, [n for n in SERVICES if n not in active]
