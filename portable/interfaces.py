"""Read-only interface inventory. Save only normalized, allowlisted fields."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

NAME = re.compile(r"[A-Za-z0-9_.:@+-]{1,80}")
ROLES = {"lan", "wan", "dmz", "undefined"}
TYPES = {"physical", "vlan", "aggregate", "redundant", "tunnel", "loopback", "switch", "hard-switch", "soft-switch", "vap-switch", "wl-mesh"}
FIELDS = ("type", "role", "parent", "vlan_id")


class InventoryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def text(value):
    if isinstance(value, dict):
        value = value.get("name")
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 250 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return value


def vlan_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,4}", value):
        value = int(value)
    return value if type(value) is int and 1 <= value <= 4094 else None


def row(name, vdom, source):
    return {"name": name, "vdom": vdom, "type": None, "role": None, "parent": None,
            "vlan_id": None, "sources": [source], "field_sources": {}, "issues": []}


def finish(item):
    item = dict(item)
    issues = list(item["issues"])
    if not item["vdom"]:
        issues.append("VDOM is not reported")
    if not NAME.fullmatch(item["name"]):
        issues.append("Name is not supported by the current dashboard query formatter")
    if item["type"] is None:
        issues.append("Interface type is not reported")
    if item["role"] not in {"lan", "wan", "dmz"}:
        issues.append("LAN/WAN/DMZ role requires review")
    if item["type"] == "vlan" and (not item["parent"] or item["vlan_id"] is None):
        issues.append("VLAN parent or VLAN ID is missing")
    item["issues"] = sorted(set(issues))
    item["review_status"] = "Needs review" if issues else "Reported"
    return item


def from_metrics(response, now=None, freshness=300):
    now = time.time() if now is None else now
    if not isinstance(response, dict) or response.get("status") != "success":
        raise InventoryError("metrics_invalid", "Exporter inventory metrics could not be read.")
    data = response.get("data", {})
    if data.get("resultType") != "matrix" or not isinstance(data.get("result"), list):
        raise InventoryError("metrics_invalid", "Exporter inventory returned an unsupported response.")
    result = []
    for series in data["result"]:
        labels = series.get("metric", {})
        name = text(labels.get("name"))
        if not name:
            continue
        stamps = [float(p[0]) for p in series.get("values", []) if len(p) == 2 and math.isfinite(float(p[0]))]
        if not stamps or not -30 <= now - max(stamps) <= freshness:
            continue
        item = row(name, text(labels.get("vdom")), "exporter")
        item["parent"] = text(labels.get("parent"))
        item["vlan_id"] = vlan_id(labels.get("vlanid"))
        # A positive VLAN ID is explicit VLAN evidence. VLAN ID zero does not
        # distinguish physical ports, aggregates, switches, or tunnels.
        item["type"] = "vlan" if item["vlan_id"] is not None else None
        for field in ("type", "parent", "vlan_id"):
            if item[field] is not None:
                item["field_sources"][field] = "exporter"
        result.append(item)
    return result


def from_cmdb(response):
    envelopes = response if isinstance(response, list) else [response]
    result, partial = [], False
    if not envelopes:
        raise InventoryError("api_invalid", "The interface API returned no response envelope.")
    for envelope in envelopes:
        if not isinstance(envelope, dict) or envelope.get("status") != "success" or not isinstance(envelope.get("results"), list):
            raise InventoryError("api_invalid", "The interface API did not return a successful inventory.")
        if envelope.get("limit_reached") is True:
            partial = True
        for raw in envelope["results"]:
            if not isinstance(raw, dict):
                raise InventoryError("api_invalid", "The interface API contained an invalid row.")
            name = text(raw.get("name"))
            if not name:
                partial = True
                continue
            item = row(name, text(raw.get("vdom")) or text(envelope.get("vdom")), "cmdb")
            kind, role = text(raw.get("type")), text(raw.get("role"))
            item["type"] = kind if kind in TYPES else None
            item["role"] = role if role in ROLES else None
            item["parent"] = text(raw.get("interface"))
            item["vlan_id"] = vlan_id(raw.get("vlanid"))
            if raw.get("q_limited_permission") is True:
                item["issues"].append("API permissions limit this interface's fields")
                partial = True
            if kind and kind not in TYPES:
                item["issues"].append("Interface type is not recognized by this version")
            for field in FIELDS:
                if item[field] is not None:
                    item["field_sources"][field] = "cmdb"
            result.append(item)
    return result, partial


def merge(metric_rows, api_rows):
    combined = {}
    for item in metric_rows + api_rows:
        key = (item["vdom"], item["name"])
        if key not in combined:
            combined[key] = {**item, "field_sources": dict(item["field_sources"]),
                             "sources": list(item["sources"]), "issues": list(item["issues"])}
            continue
        current = combined[key]
        for source in item["sources"]:
            if source not in current["sources"]:
                current["sources"].append(source)
        current["issues"].extend(item["issues"])
        for field in FIELDS:
            if item[field] is None:
                continue
            if current[field] is not None and current[field] != item[field]:
                current["issues"].append("Sources disagree about " + field)
            if current[field] is None or item["field_sources"].get(field) == "cmdb":
                current[field] = item[field]
                current["field_sources"][field] = item["field_sources"][field]
    return [finish(item) for _, item in sorted(combined.items(), key=lambda pair: (pair[0][0] or "", pair[0][1]))]


def cmdb_get(c, token, *, start=0, base=None):
    host = c["fortigate_host"]
    if ":" in host:
        host = "[" + host + "]"
    ca = Path(c["api_ca_file"]) if c["api_ca_file"] else None
    if ca and not ca.is_absolute() and base is not None:
        ca = base / ca
    try:
        context = ssl.create_default_context(cafile=str(ca) if ca else None)
        if not c["api_tls_verify"]:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        params = {"vdom": c["vdom"], "format": "name|vdom|type|role|interface|vlanid", "count": 500, "start": start}
        url = f"https://{host}:{c['api_port']}/api/v2/cmdb/system/interface?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"}, method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), NoRedirect())
        with opener.open(req, timeout=15) as response:
            payload = response.read(8 * 1024 * 1024 + 1)
        if len(payload) > 8 * 1024 * 1024:
            raise InventoryError("api_too_large", "The interface API response exceeded the safe inventory size.")
        return json.loads(payload)
    except urllib.error.HTTPError as exc:
        message = {401: "Interface API authentication was rejected (401).",
                   403: "Interface API access was denied (403); review read permissions and VDOM scope."}.get(exc.code,
                   "The interface API request failed. Review endpoint compatibility; redirects are not followed.")
        raise InventoryError("api_http_" + str(exc.code), message) from None
    except (OSError, ValueError, urllib.error.URLError):
        raise InventoryError("api_unavailable", "Interface API unavailable. Review connectivity, TLS, and local credentials; response contents are hidden.") from None


def collect(c, token, *, request=None, installation=None, prom_uid="fm-prometheus", use_api=True, response_file=None, api_get=None):
    notes, metrics, api_rows = [], [], []
    coverage = "Not established"
    if response_file is not None:
        try:
            data = json.loads(Path(response_file).read_text(encoding="utf-8-sig"))
            api_rows, partial = from_cmdb(data)
            for item in api_rows:
                item["sources"] = ["offline_file"]
                item["field_sources"] = {k: "offline_file" for k in item["field_sources"]}
            coverage = "Offline file; live freshness not verified" + ("; partial response" if partial else "")
        except (OSError, ValueError):
            raise InventoryError("file_invalid", "The inventory file could not be read as a FortiGate interface API response.") from None
    else:
        if request is not None and installation is not None:
            try:
                expr = 'fortigate_interface_link_up{job="fortigate",instance=' + json.dumps(c["fortigate_host"]) + '}[600s]'
                path = f"/api/datasources/proxy/uid/{prom_uid}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
                metrics = from_metrics(request(installation, c, path, timeout=15))
                if not metrics:
                    notes.append("No recent exporter interface samples were received.")
            except (OSError, ValueError, KeyError, TypeError, InventoryError):
                notes.append("Exporter inventory unavailable; no interface state is inferred.")
        else:
            notes.append("Exporter metrics are not yet available before installation startup.")
        # The current exporter reports parent/VLAN labels but not interface roles.
        incomplete = not metrics or any(finish(x)["issues"] for x in metrics)
        if use_api and incomplete:
            getter = api_get or (lambda start: cmdb_get(c, token, start=start, base=installation))
            start, partial = 0, False
            try:
                for page in range(20):
                    payload = getter(start)
                    rows, limited = from_cmdb(payload)
                    api_rows.extend(rows)
                    envelopes = payload if isinstance(payload, list) else [payload]
                    more = [x for x in envelopes if x.get("limit_reached") is True]
                    partial = partial or any("API permissions limit this interface's fields" in x["issues"] for x in rows)
                    partial = partial or len(rows) < sum(len(x["results"]) for x in envelopes)
                    if not more:
                        partial = partial or limited
                        break
                    if len(envelopes) != 1 or type(more[0].get("next_idx")) is not int or more[0]["next_idx"] <= start:
                        partial = True
                        break
                    start = more[0]["next_idx"]
                else:
                    partial = True
                coverage = "Accessible API response for the requested VDOM" + ("; partial response" if partial else "")
            except InventoryError as exc:
                notes.append(exc.code + ": " + str(exc))
                coverage = "Exporter evidence only; API inventory unavailable or incomplete"
        elif incomplete:
            notes.append("API inventory was skipped. Missing roles and types require review.")
            coverage = "Exporter evidence only"
        else:
            coverage = "Exporter evidence"
    result = merge(metrics, api_rows)
    return {"schema_version": 1, "collected_at": datetime.now(timezone.utc).isoformat(),
            "scope": {"host": c["fortigate_host"], "vdom": c["vdom"]}, "coverage": coverage,
            "read_only_device": True, "notes": notes, "interfaces": result}


def selected_names(report, names, vdom):
    selected = list(dict.fromkeys(names))
    if not selected or any(not isinstance(n, str) or not NAME.fullmatch(n) for n in selected):
        raise InventoryError("selection_invalid", "Select one or more supported interface names, separated by commas.")
    by_name = {x["name"]: x for x in report["interfaces"] if x["vdom"] == vdom}
    if any(n not in by_name for n in selected):
        raise InventoryError("selection_outside_scope", "A selected interface was not discovered in the configured VDOM. Nothing was saved.")
    return selected


def print_table(report):
    headers = ("VDOM", "Interface", "Type", "Role", "Parent", "VLAN ID", "Review")
    values = [headers]
    for item in report["interfaces"]:
        values.append(tuple(str(v) for v in [item["vdom"] or "Unknown", item["name"], item["type"] or "Unknown",
            item["role"] or "Unknown", item["parent"] or "-", item["vlan_id"] or "-", item["review_status"]]))
    widths = [max(len(r[i]) for r in values) for i in range(len(headers))]
    for i, cells in enumerate(values):
        print(" | ".join(cells[j].ljust(widths[j]) for j in range(len(headers))))
        if i == 0:
            print("-+-".join("-" * n for n in widths))
    if not report["interfaces"]:
        print("No interfaces were discovered. Manual setup remains available.")
    print("Coverage: " + report["coverage"])
    for note in report["notes"]:
        print("Note: " + note)
    for item in report["interfaces"]:
        if item["issues"]:
            print((item["vdom"] or "Unknown") + "/" + item["name"] + ": " + "; ".join(item["issues"]))
    print("Reported roles are device configuration, not proof of Internet access. No interface is selected automatically.")


def confirm_interactive(report, c, ask):
    print_table(report)
    if not report["interfaces"]:
        return c
    suggested = [x["name"] for x in report["interfaces"] if x["vdom"] == c["vdom"] and x["role"] == "wan"]
    if suggested:
        print("Reported WAN candidates (not selected): " + ", ".join(suggested))
    raw = ask("Internet interfaces in " + c["vdom"] + " (comma-separated; blank = leave unconfirmed)")
    if not raw:
        return c
    selected = selected_names(report, [x.strip() for x in raw.split(",") if x.strip()], c["vdom"])
    if ask("Confirm these interfaces for this project's monitoring only? yes/no", "no").lower() != "yes":
        print("Selection not confirmed; configuration preserved.")
        return c
    return {**c, "confirmed_wan_interfaces": selected}
