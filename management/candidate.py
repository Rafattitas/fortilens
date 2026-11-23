"""Prepare's candidate files: one patch step per installed file, in plan order."""

from copy import deepcopy
import json
from pathlib import Path
import re
import urllib.parse
import yaml
from .common import Problem, guarded, load, setup
from .compose_policy import (
    NOTIFY_SECRET,
    _Candidate,
    _container_port,
    _syslog_bindings,
    accounting_cpus,
    optional_probe_policy,
    prometheus_policy,
    set_accounting_cpus,
)


class CandidateMixin:
    """Patch a rendered candidate onto the registered installation's own files."""

    def _patch(self, stage, settings, c):
        """Candidate files for this revision, keyed by installation path.

        A fresh installation takes the rendered template as it is. A registered
        one keeps its identity, images, data volumes and customizations; only
        the fields FortiLens manages are changed, and a file that does not look
        as FortiLens left it stops Prepare with a review code. The steps run in
        a fixed order, which is also the order of the plan's files.
        """
        compose = self.compose()
        if not compose:
            return {
                p.relative_to(stage).as_posix(): p.read_bytes()
                for p in stage.rglob("*")
                if p.is_file()
            }
        generated = setup.load(stage / "compose.yaml")
        state = self.state()
        active = load(self.root / state["site_file"], {})
        p = _Candidate(
            stage=stage,
            settings=settings,
            c=c,
            compose=compose,
            generated=generated,
            active=active,
            site_file=state["site_file"],
            applied=state.get("applied") or {},
            endpoint_changed=setup.target_url(c) != setup.target_url({**setup.DEFAULTS, **active}),
        )
        if p.native:
            self._patch_native_receiver(p)
        self._patch_accounting_services(p)
        self._patch_prometheus(p)
        set_accounting_cpus(compose, settings.accounting_cpus)
        self._patch_loki(p)
        self._patch_alloy(p)
        self._patch_syslog_listener(p)
        self._patch_exporter(p)
        self._patch_token_file(p)
        self._patch_published_ports(p)
        p.files[self.compose_name] = yaml.safe_dump(compose, sort_keys=False).encode()
        return p.files

    def _patch_native_receiver(self, p):
        """Use the rendered native receiver, if the installed one is unmodified."""
        receiver = deepcopy(p.generated["services"]["fortilens-syslog"])
        receiver["image"] = "fortilens-syslog:" + self.project
        receiver["volumes"] = ["./" + p.site_file + ":/config/site.json:ro"]
        existing = p.compose["services"].get("fortilens-syslog")
        if existing and existing.get("image") == "fortilens-syslog:local":
            receiver["image"] = existing["image"]
        # Compare against the old managed profile, not the requested one.
        # Only these fixed transport fields may change during a transition.
        expected = deepcopy(receiver)
        expected.pop("network_mode", None)
        expected.pop("cap_add", None)
        expected["user"] = "10001:10001"
        expected["command"] = [x for x in expected["command"] if x != "--host-network"]
        health_port = 8080
        if p.was_host:
            expected["network_mode"] = "host"
            expected["user"] = "0:0"
            expected["cap_add"] = ["NET_BIND_SERVICE", "SETUID", "SETGID"]
            expected["command"].append("--host-network")
            health_port = p.active["syslog_port"] + 1
        expected["healthcheck"]["test"][-1] = (
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:"
            + str(health_port)
            + "/health',timeout=3)"
        )
        if existing and (
            {k: v for k, v in existing.items() if k != "ports"}
            != {k: v for k, v in expected.items() if k != "ports"}
        ):
            raise Problem(
                "custom_syslog_review",
                "Review the existing native receiver service before replacing its runtime.",
            )
        if existing and p.was_host and existing.get("ports"):
            raise Problem(
                "custom_syslog_review",
                "Review unexpected published ports on the host receiver.",
            )
        p.compose["services"]["fortilens-syslog"] = receiver
        for path in (p.stage / "syslog").iterdir():
            p.files["syslog/" + path.name] = path.read_bytes()

    def _patch_accounting_services(self, p):
        """Add the accounting writer if it is missing; record which services exist."""
        compose, c = p.compose, p.c
        for service in ("fortilens-usage", "fortilens-portal"):
            if service in compose["services"] or service not in p.generated["services"]:
                continue
            if service == "fortilens-portal":
                # Existing Grafana authentication is not silently reconfigured.
                # The independent management UI can still link to native Grafana.
                continue
            compose["services"][service] = p.generated["services"][service]
            compose["services"][service]["image"] = "fortilens-usage:" + self.project
            volumes = compose["services"][service]["volumes"]
            volumes[:] = [
                ("./" + p.site_file + ":/config/site.json:ro")
                if v == "./site.json:/config/site.json:ro"
                else v
                for v in volumes
                if "fortigate-ca.pem:" not in v
            ]
            if c["api_ca_file"]:
                volumes.append("./" + c["api_ca_file"] + ":/config/" + c["api_ca_file"] + ":ro")
            compose.setdefault("volumes", {}).setdefault("usage-data", {})
            for key in ("usage_admin_password", "fortigate_api_token"):
                compose.setdefault("secrets", {}).setdefault(key, p.generated["secrets"][key])
                relative = "secrets/" + key
                if not (self.root / relative).exists():
                    p.files[relative] = (p.stage / relative).read_bytes()
            for path in (p.stage / "usage").iterdir():
                p.files["usage/" + path.name] = path.read_bytes()
        if "fortilens-usage" in compose["services"] and p.settings.notifications.used():
            # Telegram and mail secrets reach the writer as one secret file; the
            # console writes it, the browser never reads it back.
            service = compose["services"]["fortilens-usage"]
            if "notify_credentials" not in service.setdefault("secrets", []):
                service["secrets"].append("notify_credentials")
            compose.setdefault("secrets", {}).setdefault(
                "notify_credentials", {"file": "./" + NOTIFY_SECRET}
            )
            if not (self.root / NOTIFY_SECRET).exists():
                p.files[NOTIFY_SECRET] = b"{}\n"
        # Keep an existing source-contract identity, ports, data volumes and images.
        c["portal_enabled"] = p.active.get("portal_enabled", False)
        if "fortilens-portal" in compose["services"]:
            c["portal_enabled"] = True
        if "fortilens-usage" in compose["services"]:
            # Worker config keeps its source scope, even when the service is stopped.
            c["accounting_enabled"] = True

    def _patch_prometheus(self, p):
        """Scrape target, accounting and receiver jobs, interval and retention."""
        settings, compose = p.settings, p.compose
        prom = self.bound_file(compose, "prometheus", "/etc/prometheus/prometheus.yml")
        pdata = yaml.safe_load(prom.read_text())
        for job in pdata["scrape_configs"]:
            if job.get("job_name") == "fortigate":
                if p.endpoint_changed:
                    job["static_configs"][0]["targets"] = [setup.target_url(p.c)]
                for relabel in job.get("relabel_configs", []):
                    if relabel.get("target_label") == "instance":
                        relabel["replacement"] = p.c["fortigate_host"]
        if settings.monitoring.accounting and not any(
            j.get("job_name") == "fortilens-accounting" for j in pdata["scrape_configs"]
        ):
            generated_job = setup.load(p.stage / "prometheus/prometheus.yml")["scrape_configs"][-1]
            pdata["scrape_configs"].append(generated_job)
            if generated_job.get("job_name") != "fortilens-accounting":
                raise Problem(
                    "accounting_job_missing",
                    "The accounting scrape candidate could not be generated.",
                )
            compose["services"]["prometheus"].setdefault("secrets", []).append(
                "usage_admin_password"
            )
        native_jobs = [
            job for job in pdata["scrape_configs"] if job.get("job_name") == "fortilens-syslog"
        ]
        expected_job = {
            "job_name": "fortilens-syslog",
            "static_configs": [{"targets": ["fortilens-syslog:8080"]}],
        }
        if native_jobs and native_jobs != [expected_job]:
            raise Problem(
                "custom_syslog_review",
                "Review the existing native receiver scrape job before changing its collection profile.",
            )
        if p.native and settings.syslog_network == "bridge" and not native_jobs:
            pdata["scrape_configs"].append(expected_job)
        if not p.native or settings.syslog_network == "host":
            pdata["scrape_configs"] = [
                job for job in pdata["scrape_configs"] if job.get("job_name") != "fortilens-syslog"
            ]
        prometheus_policy(
            pdata,
            settings.monitoring_interval,
            update_global=settings.monitoring_interval != p.applied.get("monitoring_interval"),
        )
        p.files[str(prom.relative_to(self.root))] = yaml.safe_dump(pdata, sort_keys=False).encode()
        service = compose["services"]["prometheus"]
        service["command"] = [
            ("--storage.tsdb.retention.time=" + str(settings.retention.prometheus_days) + "d")
            if str(arg).startswith("--storage.tsdb.retention.time=")
            else arg
            for arg in service["command"]
        ]

    def _patch_loki(self, p):
        loki = self.bound_file(p.compose, "loki", "/etc/loki/local-config.yaml")
        ldata = yaml.safe_load(loki.read_text())
        ldata.setdefault("limits_config", {})["retention_period"] = (
            str(p.settings.retention.loki_days) + "d"
        )
        p.files[str(loki.relative_to(self.root))] = yaml.safe_dump(ldata, sort_keys=False).encode()

    def _patch_alloy(self, p):
        """Syslog protocol and format, the native message limit and log timestamps."""
        settings, native = p.settings, p.native
        alloy = self.bound_file(p.compose, "alloy", "/etc/alloy/config.alloy")
        text = alloy.read_text()
        for name, value in [
            ("protocol", "tcp" if native else settings.syslog_protocol),
            ("syslog_format", "rfc5424" if native else settings.syslog_format),
        ]:
            pattern = rf'(?m)^(\s*{name}\s*=\s*)"[^"]*"'
            if len(re.findall(pattern, text)) != 1:
                if native or value != p.active.get(name):
                    raise Problem(
                        "custom_alloy_review",
                        "Custom syslog syntax requires a controlled host-side migration.",
                    )
            else:
                text = re.sub(pattern, lambda m: m[1] + json.dumps(value), text)
        maximum = r"(?m)^(\s*max_message_length\s*=\s*)\d+(\s*\n)"
        if native:
            if re.search(maximum, text):
                # Only the number changes, so an unchanged file restarts nothing.
                text = re.sub(maximum, r"\g<1>65536\g<2>", text)
            else:
                text = re.sub(
                    r"(?m)^(\s*use_incoming_timestamp\s*=)",
                    r"    max_message_length = 65536\n\1",
                    text,
                    count=1,
                )
            if not re.search(r"(?m)^\s*max_message_length\s*=\s*65536\s*$", text):
                raise Problem(
                    "custom_alloy_review",
                    "Review the native receiver message limit in the existing Alloy configuration.",
                )
        elif p.was_native:
            text = re.sub(r"(?m)^\s*max_message_length\s*=\s*65536\s*\n", "", text)
        path = str(alloy.relative_to(self.root))
        p.files[path] = text.encode()
        if settings.monitoring.accounting and not p.active.get("accounting_enabled"):
            p.files[path] = re.sub(
                r"(?m)^(\s*use_incoming_timestamp\s*=\s*)true\s*$", lambda m: m[1] + "false", text
            ).encode()
            p.c["loki_receive_timestamps"] = True

    def _patch_syslog_listener(self, p):
        """Move the syslog listener between Alloy and the native receiver.

        The registered binding keeps its address; only its port and protocol
        change. A binding FortiLens did not create stops Prepare for review.
        """
        settings = p.settings
        native, was_native, was_host = p.native, p.was_native, p.was_host
        old_port = p.old_port
        port_owner = "fortilens-syslog" if was_native else "alloy"
        # The installed file, not the candidate: the receiver may already be replaced.
        ports = (
            [f"{p.active.get('syslog_bind', '0.0.0.0')}:{old_port}:1514/udp"]
            if was_host
            else (self.compose()["services"].get(port_owner) or {}).get("ports", [])
        )
        if ports and not all(isinstance(x, str) for x in ports):
            raise Problem(
                "custom_ports_review", "Custom published-port syntax requires a host-side review."
            )
        replaced = []
        receiver_found = False
        for binding in ports:
            address = binding.split("/")[0]
            parts = address.rsplit(":", 2)
            if parts[-1] != "1514":
                if was_native:
                    raise Problem(
                        "custom_syslog_review",
                        "Review unexpected published ports on the native receiver before applying changes.",
                    )
                replaced.append(binding)
                continue
            receiver_found = True
            parts[-2] = str(settings.syslog_port)
            replaced.append(":".join(parts) + "/" + settings.syslog_protocol)
        if not receiver_found and (
            native != was_native
            or settings.syslog_port != old_port
            or settings.syslog_protocol != p.active.get("syslog_protocol", "tcp")
        ):
            raise Problem(
                "custom_ports_review",
                "Register the actual syslog target port before changing its listener.",
            )
        if native:
            self._listen_on_receiver(p, replaced)
        else:
            self._listen_on_alloy(p, replaced)

    def _listen_on_receiver(self, p, replaced):
        """The native receiver takes the listener; Alloy keeps only a host handoff."""
        compose, old_port = p.compose, p.old_port
        if p.host:
            compose["services"]["fortilens-syslog"].pop("ports", None)
        else:
            compose["services"]["fortilens-syslog"]["ports"] = [
                binding for binding in replaced if _container_port(binding) == "1514"
            ]
        alloy_ports = compose["services"]["alloy"].get("ports", [])
        if p.was_host and _syslog_bindings(alloy_ports) != [f"127.0.0.1:{old_port}:1514/tcp"]:
            raise Problem(
                "custom_ports_review",
                "Review the host receiver loopback handoff before changing profiles.",
            )
        compose["services"]["alloy"]["ports"] = [
            binding
            for binding in alloy_ports
            if isinstance(binding, str) and _container_port(binding) != "1514"
        ]
        if any(not isinstance(binding, str) for binding in alloy_ports):
            raise Problem(
                "custom_ports_review",
                "Review custom Alloy port bindings before changing its receiver.",
            )
        if p.host:
            compose["services"]["alloy"]["ports"].append(
                f"127.0.0.1:{p.settings.syslog_port}:1514/tcp"
            )

    def _listen_on_alloy(self, p, replaced):
        """Alloy takes the listener; a native receiver definition stays, disabled."""
        compose, was_native = p.compose, p.was_native
        unrelated = compose["services"]["alloy"].get("ports", []) if was_native else []
        if p.was_host:
            expected_binding = f"127.0.0.1:{p.old_port}:1514/tcp"
            if _syslog_bindings(unrelated) != [expected_binding]:
                raise Problem(
                    "custom_ports_review",
                    "Review the host receiver loopback handoff before changing profiles.",
                )
            unrelated = [binding for binding in unrelated if binding != expected_binding]
        if was_native and any(
            not isinstance(binding, str) or _container_port(binding) == "1514"
            for binding in unrelated
        ):
            raise Problem(
                "custom_ports_review",
                "Review unexpected Alloy syslog bindings before leaving the native profile.",
            )
        compose["services"]["alloy"]["ports"] = unrelated + replaced
        if "fortilens-syslog" in compose["services"]:
            disabled = compose["services"]["fortilens-syslog"]
            disabled["ports"] = []
            disabled.pop("network_mode", None)
            disabled.pop("cap_add", None)
            disabled["user"] = "10001:10001"
            disabled["command"] = [x for x in disabled["command"] if x != "--host-network"]
            disabled["healthcheck"]["test"][-1] = (
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)"
            )

    def _patch_exporter(self, p):
        """Exporter credential record, TLS verification and the custom CA."""
        settings, c = p.settings, p.c
        exporter = p.compose["services"]["fortigate-exporter"]
        cmd = exporter.get("command", [])
        destination = next(
            (cmd[i + 1] for i, x in enumerate(cmd[:-1]) if x == "-auth-file"),
            "/config/fortigate-key.yaml",
        )
        authfile = self.bound_file(p.compose, "fortigate-exporter", destination)
        auth = yaml.safe_load(authfile.read_text())
        pending_token = load(self.secret_path, {}).get("token")
        if p.endpoint_changed or pending_token or settings.optional_exporter_exclusions is not None:
            old_key = self.auth_key(auth, p.active)
            key = setup.target_url(c) if p.endpoint_changed else old_key
            if p.endpoint_changed:
                existing_key = self.auth_key(auth, c, optional=True)
                if existing_key and not pending_token:
                    raise Problem(
                        "token_action_required",
                        "Changing to an existing credential target requires an explicit token replacement.",
                    )
                key = existing_key or key
            record = auth.get(key, auth[old_key])
            if not isinstance(record, dict):
                raise Problem(
                    "secret_unavailable",
                    "Review the existing exporter credential record before changing it.",
                )
            record = {**record, "token": self.token()}
            auth[key] = (
                optional_probe_policy(record, settings.optional_exporter_exclusions)
                if settings.optional_exporter_exclusions is not None
                else record
            )
            p.files[str(authfile.relative_to(self.root))] = yaml.safe_dump(auth).encode()
        exporter["command"] = [x for x in cmd if x != "-insecure"] + (
            [] if c["api_tls_verify"] else ["-insecure"]
        )
        pending = load(self.secret_path, {})
        if "ca" not in pending:
            return
        clean = []
        skip = False
        for arg in exporter["command"]:
            if skip:
                skip = False
                continue
            if arg == "-extra-ca-certs":
                skip = True
                continue
            if str(arg).startswith("-extra-ca-certs="):
                continue
            clean.append(arg)
        exporter["command"] = clean
        if not pending["ca"]:
            return
        exporter["command"] += ["-extra-ca-certs", "/config/web-fortigate-ca.pem"]
        mount = "./secrets/web-fortigate-ca.pem:/config/web-fortigate-ca.pem:ro"
        if mount not in exporter["volumes"]:
            exporter["volumes"].append(mount)
        if "fortilens-usage" in p.compose["services"]:
            mount = "./secrets/web-fortigate-ca.pem:/config/secrets/web-fortigate-ca.pem:ro"
            if mount not in p.compose["services"]["fortilens-usage"]["volumes"]:
                p.compose["services"]["fortilens-usage"]["volumes"].append(mount)

    def _patch_token_file(self, p):
        # Keep the actual worker's token file, rather than assuming a template path.
        secret = p.compose.get("secrets", {}).get("fortigate_api_token", {})
        if secret.get("file") and load(self.secret_path, {}).get("token"):
            path = Path(secret["file"])
            path = path if path.is_absolute() else self.root / path
            path = guarded(self.root, path.relative_to(self.root))
            p.files[str(path.relative_to(self.root))] = (self.token() + "\n").encode()

    def _patch_published_ports(self, p):
        """Grafana and accounting ports, and a Grafana root URL that names the old port."""
        settings = p.settings
        for name in ("grafana", "fortilens-usage"):
            service = p.compose["services"].get(name)
            if service is None:
                continue
            ports = service.get("ports", [])
            if not all(isinstance(x, str) for x in ports):
                raise Problem(
                    "custom_ports_review",
                    "A custom port binding requires host-side registration review.",
                )
            replacements = []
            for binding in ports:
                target = _container_port(binding)
                if target in ("3000", "8080", "8081"):
                    newport = settings.grafana_port if target == "3000" else settings.usage_port
                    parts = binding.rsplit(":", 2)
                    if len(parts) == 2:
                        parts[0] = str(newport)
                    else:
                        parts[1] = str(newport)
                    binding = ":".join(parts)
                replacements.append(binding)
            service["ports"] = replacements
        environment = p.compose["services"]["grafana"].get("environment", {})
        old_port = p.active.get("grafana_port", 3001)
        if (
            isinstance(environment, dict)
            and environment.get("GF_SERVER_ROOT_URL")
            and settings.grafana_port != old_port
        ):
            url = urllib.parse.urlsplit(environment["GF_SERVER_ROOT_URL"])
            if url.hostname and url.port == old_port:
                host = "[" + url.hostname + "]" if ":" in url.hostname else url.hostname
                environment["GF_SERVER_ROOT_URL"] = urllib.parse.urlunsplit(
                    (
                        url.scheme,
                        host + ":" + str(settings.grafana_port),
                        url.path,
                        url.query,
                        url.fragment,
                    )
                )

    def _fresh_candidate(self, stage, settings, files):
        """Settings the renderer does not take, applied to a first installation.

        Edits ``files`` in place and returns the rendered site configuration,
        which the plan records as the site file.
        """
        c = setup.load(stage / "site.json")
        files["compose.yaml"] = (stage / "compose.yaml").read_bytes()
        prom = json.loads(files["prometheus/prometheus.yml"])
        prometheus_policy(prom, settings.monitoring_interval)
        files["prometheus/prometheus.yml"] = setup.dumps(prom).encode()
        ldata = json.loads(files["loki/loki-config.yaml"])
        ldata["limits_config"]["retention_period"] = str(settings.retention.loki_days) + "d"
        files["loki/loki-config.yaml"] = setup.dumps(ldata).encode()
        comp = json.loads(files.pop("compose.yaml"))
        if "fortilens-syslog" in comp["services"]:
            comp["services"]["fortilens-syslog"]["image"] = "fortilens-syslog:" + self.project
        for name in ("fortilens-usage", "fortilens-portal"):
            if name in comp["services"]:
                comp["services"][name]["image"] = "fortilens-usage:" + self.project
        comp["services"]["prometheus"]["command"] = [
            ("--storage.tsdb.retention.time=" + str(settings.retention.prometheus_days) + "d")
            if x.startswith("--storage.tsdb.retention.time=")
            else x
            for x in comp["services"]["prometheus"]["command"]
        ]
        set_accounting_cpus(comp, settings.accounting_cpus)
        files[self.compose_name] = setup.dumps(comp).encode()
        if settings.optional_exporter_exclusions is not None:
            auth = json.loads(files["secrets/fortigate-key.yaml"])
            key = self.auth_key(auth, c)
            auth[key] = optional_probe_policy(auth[key], settings.optional_exporter_exclusions)
            files["secrets/fortigate-key.yaml"] = setup.dumps(auth).encode()
        return c
