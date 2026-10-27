#!/usr/bin/env python3
"""FortiLens single-device setup. Standard library only; secrets stay in local/."""
import argparse
import base64
from copy import deepcopy
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings

ROOT = Path(__file__).resolve().parent
UID = "forti-monitor"
PROM_UID = "fm-prometheus"
LOKI_UID = "fm-loki"
DEFAULTS = {
    "schema_version": 1, "site_name": "My company", "fortigate_host": "",
    "api_port": 443, "api_tls_verify": True, "api_ca_file": "",
    "vdom": "root", "sla": "", "wan_interfaces": [],
    "confirmed_wan_interfaces": [],
    "syslog_port": 1514, "syslog_protocol": "tcp", "syslog_format": "rfc5424",
    "syslog_bind": "0.0.0.0", "grafana_port": 3001,
    "grafana_bind": "127.0.0.1", "timezone": "browser",
}


class SetupError(Exception):
    pass


def dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def port(value, name):
    if type(value) is not int or not 1 <= value <= 65535:
        raise SetupError(f"{name} must be an integer from 1 to 65535")
    return value


def validate_config(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise SetupError("Unknown configuration fields; use site.example.json (no tokens in JSON)")
    c = {**deepcopy(DEFAULTS), **raw}
    if type(c["schema_version"]) is not int or c["schema_version"] != 1:
        raise SetupError("Unsupported schema_version")
    for key in ["site_name", "fortigate_host", "vdom", "sla", "timezone", "api_ca_file"]:
        if not isinstance(c[key], str) or len(c[key]) > 250 or any(ord(x) < 32 for x in c[key]):
            raise SetupError(f"Invalid {key}")
    host = c["fortigate_host"].strip()
    try:
        host = str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
            raise SetupError("fortigate_host must be an IP or hostname, without https:// or a port")
        if any(not part or len(part) > 63 or part.startswith("-") or part.endswith("-") for part in host.split(".")):
            raise SetupError("Invalid hostname")
    c["fortigate_host"] = host
    for key in ["api_port", "syslog_port", "grafana_port"]:
        port(c[key], key)
    if type(c["api_tls_verify"]) is not bool:
        raise SetupError("api_tls_verify must be true or false")
    if c["syslog_protocol"] not in ["tcp", "udp"] or c["syslog_format"] not in ["rfc5424", "rfc3164"]:
        raise SetupError("Supported syslog: tcp/udp and rfc5424/rfc3164")
    for key in ["grafana_bind", "syslog_bind"]:
        try:
            ipaddress.IPv4Address(c[key])
        except (ValueError, TypeError):
            raise SetupError(f"{key} must be an IPv4 listen address") from None
    if c["syslog_protocol"] == "tcp" and c["syslog_port"] == c["grafana_port"]:
        raise SetupError("Grafana and TCP syslog must have different host ports")
    if not c["site_name"].strip() or not c["vdom"]:
        raise SetupError("site_name and vdom cannot be empty")
    # Values enter PromQL strings and Go label_replace replacements. Keep the
    # supported name alphabet explicit rather than permitting query injection.
    for key in ["vdom", "sla"]:
        if not re.fullmatch(r"[A-Za-z0-9_.:@ /+-]*", c[key]):
            raise SetupError(f"Unsupported characters in {key}")
    for key in ["wan_interfaces", "confirmed_wan_interfaces"]:
        if not isinstance(c[key], list) or len(c[key]) > 64:
            raise SetupError(key + " must be a list (maximum 64)")
        if any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.:@+-]{1,80}", v) for v in c[key]):
            raise SetupError("Invalid WAN interface name")
        c[key] = list(dict.fromkeys(c[key]))
    if c["wan_interfaces"] and not c["sla"]:
        raise SetupError("Set the SLA name when supplying fallback WAN interfaces")
    if c["api_ca_file"] and not c["api_tls_verify"]:
        raise SetupError("A custom CA requires api_tls_verify=true")
    return c


def target_url(c):
    host = c["fortigate_host"]
    if ":" in host:
        host = "[" + host + "]"
    return f"https://{host}:{c['api_port']}"


def discovery(label, selector, values, selection, scope, restrict_scope=False):
    live = f"count by ({label}) ({selector})"
    gate = 'label_replace(vector(0), "setup_scope", ' + json.dumps(selection) + ', "__name__", ".*")'
    if restrict_scope:
        fixed = 'label_replace(vector(0), "setup_scope", ' + json.dumps(scope) + ', "__name__", ".*")'
        live = f"(({live}) and on() ({gate} and on(setup_scope) {fixed}))"
    if values:
        choices = []
        for value in values:
            choices.append(
                'label_replace(label_replace(vector(0), "setup_scope", '
                + json.dumps(scope) + ', "__name__", ".*"), '
                + json.dumps(label) + ', ' + json.dumps(value)
                + ', "__name__", ".*")'
            )
        fallback = " or ".join(choices)
        live += f" or (max by ({label}) (({fallback}) and on(setup_scope) {gate}) unless on() count({selector}))"
    return "query_result(" + live + ")"


def make_dashboard(c):
    result = load(ROOT / "templates/dashboard.json")
    spec = result["spec"]
    spec["title"] = "FortiLens — " + c["site_name"]
    spec["timeSettings"]["timezone"] = c["timezone"]
    base = 'fortigate_virtual_wan_status{job="fortigate",instance="${fortigate}"'
    confirmed = c.get("confirmed_wan_interfaces", [])
    wan_selector = base + ',vdom="${vdom}",sla="${sla}"'
    if confirmed:
        wan_selector += ",interface=~" + json.dumps("|".join(re.escape(x) for x in confirmed))
    wan_selector += "}"
    wan_query = discovery("interface", wan_selector, confirmed or c["wan_interfaces"],
        "${fortigate}/${vdom}" if confirmed else "${fortigate}/${vdom}/${sla}",
        c["fortigate_host"] + "/" + c["vdom"] + ("" if confirmed else "/" + c["sla"]), restrict_scope=bool(confirmed))
    definitions = [
        ("fortigate", 'label_values(up{job="fortigate"}, instance)', c["fortigate_host"], [c["fortigate_host"]], ""),
        ("vdom", discovery("vdom", base + "}", [c["vdom"]], "${fortigate}", c["fortigate_host"]), c["vdom"], [c["vdom"]], '/vdom="([^"]+)"/'),
        ("sla", discovery("sla", base + ',vdom="${vdom}"}', [c["sla"]] if c["sla"] else [], "${fortigate}/${vdom}", c["fortigate_host"] + "/" + c["vdom"]), c["sla"], [c["sla"]] if c["sla"] else [], '/sla="([^"]+)"/'),
        ("wan", wan_query, "$__all", confirmed or c["wan_interfaces"], '/interface="([^"]+)"/'),
    ]
    for v, (name, query, current, values, regex) in zip(spec["variables"], definitions):
        s = v["spec"]
        if s["name"] != name:
            raise SetupError("Unexpected dashboard template variable order")
        s["query"]["spec"]["query"] = query
        s["definition"] = query
        s["regex"] = regex
        s["current"] = {"text": "All" if name == "wan" else current, "value": current}
        s["options"] = [{"text": x, "value": x, "selected": x == current} for x in values]
        if name == "wan":
            s["options"].insert(0, {"text": "All", "value": "$__all", "selected": True})
        s.pop("staticOptions", None)
        s.pop("staticOptionsOrder", None)
        s.pop("allValue", None)
        if name == "wan" and confirmed:
            s["description"] = (
                "Only user-confirmed Internet interfaces for the configured device/VDOM. "
                "Names may be available without SLA samples; choices never fabricate measurements. "
                "Other VDOMs require separate review. This does not change FortiGate roles or usage-accounting filters."
            )
    return result


def write_file(root, relative, value, mode=0o644):
    p = root / relative
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value, encoding="utf-8", newline="\n")
    p.chmod(mode)


def render(c, output, token, admin_password=None, inventory_report=None):
    c = validate_config(c)
    if not token or any(ord(x) < 33 for x in token) or len(token) > 4096:
        raise SetupError("API token must be non-empty and contain no whitespace")
    output = Path(output).resolve()
    if output.exists():
        raise SetupError("Output already exists; preserve it or choose a new directory")
    ca = None
    if c["api_ca_file"]:
        ca = Path(c["api_ca_file"]).read_text(encoding="utf-8")
        if "-----BEGIN CERTIFICATE-----" not in ca:
            raise SetupError("api_ca_file must be a PEM certificate bundle")
    # Build everything in memory before creating a private installation folder.
    compose = load(ROOT / "templates/compose.json")
    compose["services"]["grafana"]["ports"] = [f"{c['grafana_bind']}:{c['grafana_port']}:3000"]
    compose["services"]["alloy"]["ports"] = [f"{c['syslog_bind']}:{c['syslog_port']}:1514/{c['syslog_protocol']}"]
    exporter = compose["services"]["fortigate-exporter"]
    if not c["api_tls_verify"]:
        exporter["command"].append("-insecure")
    if ca:
        exporter["command"].extend(["-extra-ca-certs", "/config/fortigate-ca.pem"])
        exporter["volumes"].append("./secrets/fortigate-ca.pem:/config/fortigate-ca.pem:ro")
    prom = load(ROOT / "templates/prometheus.json")
    prom["scrape_configs"][0]["static_configs"][0]["targets"] = [target_url(c)]
    prom["scrape_configs"][0]["relabel_configs"][1]["replacement"] = c["fortigate_host"]
    alloy = (ROOT / "templates/config.alloy").read_text(encoding="utf-8")
    alloy = alloy.replace("__PROTOCOL__", c["syslog_protocol"]).replace("__FORMAT__", c["syslog_format"])
    dashboard = make_dashboard(c)
    public_c = {**c, "api_ca_file": "secrets/fortigate-ca.pem" if ca else ""}
    files = {
        "compose.yaml": dumps(compose), "site.json": dumps(public_c),
        "prometheus/prometheus.yml": dumps(prom), "alloy/config.alloy": alloy,
        "loki/loki-config.yaml": (ROOT / "templates/loki.json").read_text(encoding="utf-8"),
        "grafana/provisioning/datasources/sources.yaml": (ROOT / "templates/datasources.json").read_text(encoding="utf-8"),
        "grafana/dashboard.json": dumps(dashboard),
        "secrets/fortigate-key.yaml": dumps({target_url(c): {"token": token}}),
        "secrets/grafana_admin_password": (admin_password or secrets.token_urlsafe(24)) + "\n",
        ".gitignore": "*\n!.gitignore\n",
    }
    if ca:
        files["secrets/fortigate-ca.pem"] = ca
    if inventory_report is not None:
        files["interface-inventory.json"] = dumps(inventory_report)
    output.mkdir(parents=True, mode=0o700)
    output.chmod(0o700)
    (output / "secrets").mkdir(mode=0o700)
    for path, value in files.items():
        write_file(output, path, value)
    print(f"Prepared: {output}\nAPI token saved locally; never printed.")
    print("Next: python3 setup.py start --output " + str(output))
    return output


def ask(label, default=""):
    value = input(f"{label}" + (f" [{default}]" if default != "" else "") + ": ").strip()
    return value or default


def wizard():
    c = deepcopy(DEFAULTS)
    c["site_name"] = ask("Company / installation name", c["site_name"])
    c["fortigate_host"] = ask("FortiGate IP or hostname (without https://)")
    c["api_port"] = int(ask("HTTPS API port", "443"))
    c["api_ca_file"] = ask("Custom CA PEM path (blank = system trust)")
    verification = ask("Verify FortiGate HTTPS certificate? yes/no", "yes").lower()
    if verification not in ["yes", "no"]:
        raise SetupError("Answer yes or no for certificate verification")
    c["api_tls_verify"] = verification == "yes"
    c["syslog_port"] = int(ask("Syslog receive port on monitoring server", "1514"))
    c["syslog_protocol"] = ask("Syslog protocol tcp/udp", "tcp").lower()
    c["syslog_format"] = ask("Syslog format rfc5424/rfc3164", "rfc5424").lower()
    c["vdom"] = ask("Default VDOM (configuration choice until discovered)", "root")
    c["sla"] = ask("SLA check name (blank = discover when metrics arrive)")
    c["wan_interfaces"] = [v.strip() for v in ask("Fallback WAN names for the SLA above (blank = discover/review inventory later)").split(",") if v.strip()]
    c["grafana_bind"] = ask("Grafana listen IPv4 (127.0.0.1 = this server only; use server LAN IP for LAN access)", "127.0.0.1")
    c["grafana_port"] = int(ask("Grafana port", "3001"))
    return validate_config(c)


def installation(output):
    p = Path(output).resolve()
    if not (p / "compose.yaml").is_file():
        raise SetupError("No installation found; run init first")
    return p, validate_config(load(p / "site.json"))


def compose_command(p, *args):
    # A directory-derived project name isolates volumes/networks even if two
    # companies use the same human-readable company name.
    project = "forti-monitor-" + hashlib.sha256(str(p).encode()).hexdigest()[:10]
    return ["docker", "compose", "-p", project, "-f", str(p / "compose.yaml"), *args]


def grafana_request(p, c, path, method="GET", body=None, timeout=100):
    host = c["grafana_bind"] if c["grafana_bind"] != "0.0.0.0" else "127.0.0.1"
    password = (p / "secrets/grafana_admin_password").read_text(encoding="utf-8").strip()
    auth = base64.b64encode(("admin:" + password).encode()).decode()
    req = urllib.request.Request(f"http://{host}:{c['grafana_port']}" + path,
        data=dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"}, method=method)
    # Never send initial admin credentials through an environment HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as response:
        return json.load(response)


def import_dashboard(p, c):
    route = "/apis/dashboard.grafana.app/v2/namespaces/default/dashboards"
    try:
        existing = grafana_request(p, c, route + "/" + UID)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    else:
        if "forti-monitor" not in existing.get("spec", {}).get("tags", []):
            raise SetupError("Dashboard UID belongs to another dashboard; nothing overwritten")
        print("Existing dashboard preserved (no overwrite).")
        return
    result = grafana_request(p, c, route, "POST", load(p / "grafana/dashboard.json"))
    if result.get("metadata", {}).get("name") != UID:
        raise SetupError("Dashboard import returned an unexpected identifier")
    print("Dashboard imported: /d/" + UID)


def start(p, c):
    if sys.platform != "linux":
        raise SetupError("Run start on a Linux Docker host; init also works on Windows")
    subprocess.run(compose_command(p, "config", "--quiet"), check=True)
    subprocess.run(compose_command(p, "up", "-d"), check=True)
    for _ in range(60):
        try:
            health = grafana_request(p, c, "/api/health", timeout=5)
            if health.get("database") == "ok":
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(2)
    else:
        raise SetupError("Grafana did not become ready; containers preserved for diagnosis")
    for uid in [PROM_UID, LOKI_UID]:
        for _ in range(30):
            try:
                health = grafana_request(p, c, "/api/datasources/uid/" + uid + "/health", timeout=5)
                if health.get("status") == "OK":
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(2)
        else:
            raise SetupError(f"Datasource {uid} is not ready; inspect this installation's container logs")
    import_dashboard(p, c)
    print("Login: admin; password is in secrets/grafana_admin_password inside the output directory.")
    print("FortiGate syslog destination: this server's reachable LAN IP and configured syslog port/protocol.")


def discover(p, c):
    expr = 'count by (vdom, sla, interface) (fortigate_virtual_wan_status{job="fortigate"})'
    path = f"/api/datasources/proxy/uid/{PROM_UID}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    result = grafana_request(p, c, path)
    if result.get("status") != "success":
        raise SetupError("Discovery query failed")
    groups = {}
    for row in result["data"]["result"]:
        labels = row["metric"]
        key = (labels.get("vdom", ""), labels.get("sla", ""))
        if all(key) and labels.get("interface"):
            groups.setdefault(key, set()).add(labels["interface"])
    print(dumps({"source": "received SLA metric labels; not link state or VLAN classification",
        "sla_checks": [{"vdom": v, "sla": s, "interfaces": sorted(ports)} for (v, s), ports in sorted(groups.items())]}))
    if not groups:
        print("No current SLA labels received. Existing manual choices are configuration only; do not infer WAN DOWN.")
    return groups


def inventory_setup(c, token, args, interactive):
    import interfaces
    report = None
    requested = bool(args.inventory_file or args.discover_interfaces)
    if interactive and not args.skip_interface_discovery and not requested:
        requested = ask("Read interface inventory from FortiGate now? yes/no", "yes").lower() == "yes"
    if requested:
        report = interfaces.collect(c, token, response_file=args.inventory_file)
        if interactive and not args.select_wan:
            c = interfaces.confirm_interactive(report, c, ask)
        else:
            interfaces.print_table(report)
    if args.select_wan:
        if report is None or not args.confirm_wan:
            raise SetupError("Select from a discovered/offline inventory and add --confirm-wan to save the choice")
        c = {**c, "confirmed_wan_interfaces": interfaces.selected_names(report, split_names(args.select_wan), c["vdom"])}
    elif args.confirm_wan:
        raise SetupError("--confirm-wan requires --select-wan")
    return validate_config(c), report


def split_names(raw):
    return [x.strip() for x in raw.split(",") if x.strip()]


def interface_command(p, c, args):
    import interfaces
    token = ""
    if not args.metrics_only and not args.inventory_file:
        token = load(p / "secrets/fortigate-key.yaml")[target_url(c)]["token"]
        if not isinstance(token, str) or not token or any(ord(x) < 33 for x in token):
            raise SetupError("Invalid local API token file")
    report = interfaces.collect(c, token, request=grafana_request, installation=p, prom_uid=PROM_UID,
                                use_api=not args.metrics_only, response_file=args.inventory_file)
    if args.select_wan:
        if not args.confirm_wan:
            raise SetupError("Add --confirm-wan to save the selected interfaces for this project")
        names = interfaces.selected_names(report, split_names(args.select_wan), c["vdom"])
        updated = validate_config({**c, "confirmed_wan_interfaces": names})
        # Build the candidate before changing any project files. Keep the live
        # Grafana dashboard unchanged so existing user edits remain reviewable.
        candidate = dumps(make_dashboard(updated))
        import shutil
        backup = p / "backups" / ("interfaces-" + str(time.time_ns()))
        backup.mkdir(parents=True, mode=0o700)
        shutil.copy2(p / "site.json", backup / "site.json")
        for path, contents in [("interface-inventory.json", dumps(report)),
                               ("grafana/dashboard.interfaces-candidate.json", candidate), ("site.json", dumps(updated))]:
            dest = p / path
            if dest.exists() and path != "site.json":
                shutil.copy2(dest, backup / dest.name)
            tmp = dest.with_name(dest.name + ".pending")
            write_file(p, str(tmp.relative_to(p)), contents)
            tmp.replace(dest)
        report["selection"] = {"vdom": c["vdom"], "names": names, "confirmed": True,
                               "live_dashboard_changed": False,
                               "candidate": "grafana/dashboard.interfaces-candidate.json"}
    elif args.confirm_wan:
        raise SetupError("--confirm-wan requires --select-wan")
    if args.json:
        print(dumps(report), end="")
    else:
        interfaces.print_table(report)
        if args.select_wan:
            print("Selection saved. Review/import grafana/dashboard.interfaces-candidate.json to update an existing dashboard.")
    return report


def bundle(destination):
    # Deliberate allowlist: never zip the workspace, local installations, exports,
    # handover documents, screenshots or credentials.
    import zipfile
    allowed = [ROOT / "setup.py", ROOT / "doctor.py", ROOT / "interfaces.py", ROOT / "site.example.json", ROOT / "README.md", ROOT / ".gitignore", ROOT / "LICENSE"]
    allowed.extend(ROOT / "templates" / name for name in [
        "dashboard.json", "compose.json", "prometheus.json", "loki.json", "datasources.json", "config.alloy"])
    destination = Path(destination).resolve()
    if destination.exists():
        raise SetupError("Bundle destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for f in allowed:
            if not f.is_file() or f.is_symlink():
                raise SetupError("Invalid bundle input")
            archive.write(f, "forti-monitor/" + f.relative_to(ROOT).as_posix())
    print("Created portable-only bundle: " + str(destination))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Interactive setup or read a non-secret JSON config")
    init.add_argument("--config", type=Path)
    init.add_argument("--token-file", type=Path, help="Private file containing only the API token (otherwise hidden prompt)")
    init.add_argument("--output", type=Path, default=ROOT / "local")
    init.add_argument("--discover-interfaces", action="store_true", help="Read interface metadata during setup")
    init.add_argument("--skip-interface-discovery", action="store_true", help="Skip the interactive API inventory prompt")
    init.add_argument("--inventory-file", type=Path, help="Offline FortiGate interface API JSON response; no network requests")
    init.add_argument("--select-wan", help="Comma-separated names from the inventory in the configured VDOM")
    init.add_argument("--confirm-wan", action="store_true", help="Explicitly confirm selected Internet interfaces for this project")
    for name in ["start", "import-dashboard", "discover"]:
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path, default=ROOT / "local")
    doctor_parser = sub.add_parser("doctor", help="Read-only service, collection and recent-log readiness report")
    doctor_parser.add_argument("--output", type=Path, default=ROOT / "local")
    doctor_parser.add_argument("--json", action="store_true", help="Machine-readable report; no raw logs or credentials")
    doctor_parser.add_argument("--freshness-seconds", type=int, default=300)
    doctor_parser.add_argument("--api-check", action="store_true", help="Also send one HTTPS GET to FortiGate system/status")
    interfaces_parser = sub.add_parser("interfaces", help="Review exporter/API interface inventory; no device changes")
    interfaces_parser.add_argument("--output", type=Path, default=ROOT / "local")
    interfaces_parser.add_argument("--metrics-only", action="store_true", help="Read existing exporter metrics without direct API requests")
    interfaces_parser.add_argument("--inventory-file", type=Path, help="Offline FortiGate interface API JSON response")
    interfaces_parser.add_argument("--json", action="store_true")
    interfaces_parser.add_argument("--select-wan")
    interfaces_parser.add_argument("--confirm-wan", action="store_true")
    b = sub.add_parser("bundle")
    b.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            c = validate_config(load(args.config)) if args.config else wizard()
            if args.config and c["api_ca_file"] and not Path(c["api_ca_file"]).is_absolute():
                c["api_ca_file"] = str((args.config.parent / c["api_ca_file"]).resolve())
            if args.token_file:
                token = args.token_file.read_text(encoding="utf-8").strip()
            else:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    try:
                        token = getpass.getpass("FortiGate API token (hidden): ")
                    except getpass.GetPassWarning:
                        raise SetupError("Hidden input unavailable; use a terminal or --token-file") from None
            if args.skip_interface_discovery and args.discover_interfaces:
                raise SetupError("Choose either --discover-interfaces or --skip-interface-discovery")
            c, report = inventory_setup(c, token, args, interactive=args.config is None)
            render(c, args.output, token, inventory_report=report)
        elif args.command == "bundle":
            bundle(args.destination)
        else:
            p, c = installation(args.output)
            if args.command == "start":
                start(p, c)
            elif args.command == "discover":
                discover(p, c)
            elif args.command == "doctor":
                import doctor
                report = doctor.run(p, c, grafana_request, compose_command, PROM_UID, LOKI_UID,
                                    freshness=args.freshness_seconds, direct_api=args.api_check)
                return doctor.print_report(report, args.json)
            elif args.command == "interfaces":
                interface_command(p, c, args)
            else:
                import_dashboard(p, c)
        return 0
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: request failed; response body suppressed to protect credentials.", file=sys.stderr)
    except (SetupError, ValueError, OSError, subprocess.CalledProcessError, urllib.error.URLError) as e:
        print(f"Setup failed: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        print("Setup cancelled.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
