"""Read-only readiness checks; never print credentials, API bodies or log lines."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import base64

SERVICES = ("fortigate-exporter", "prometheus", "grafana", "loki", "alloy", "node-exporter")
LABELS = {"PASS": "Ready", "WARN": "Needs review", "FAIL": "Problem", "UNKNOWN": "Unavailable"}


def check(name, status, message, code, action="", **evidence):
    return {"id": name, "status": status, "message": message, "code": code,
            "action": action, "evidence": evidence}


def service_checks(p, compose_command, services=SERVICES):
    try:
        r = subprocess.run(compose_command(p, "ps", "--all", "--format", "json"),
                           capture_output=True, text=True, timeout=10)
        if r.returncode:
            raise ValueError("docker_unavailable")
        text = r.stdout.strip()
        rows = json.loads(text) if text.startswith("[") else [json.loads(x) for x in text.splitlines()]
        if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
            raise ValueError("docker_response_invalid")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return [check("service:" + name, "UNKNOWN", name + ": Docker status could not be read.",
                      "docker_unavailable", "Check Docker availability and your account permissions. This does not prove the service is stopped.") for name in services]
    result = []
    for name in services:
        matches = [x for x in rows if x.get("Service") == name and "-run-" not in x.get("Name", "")]
        running = [x for x in matches if x.get("State") == "running"]
        if not matches:
            result.append(check("service:" + name, "FAIL", name + ": no container found for this installation.",
                                "service_missing", "Review the configuration, then run start for this installation."))
        elif not running or any(x.get("Health") == "unhealthy" for x in running):
            result.append(check("service:" + name, "FAIL", name + ": stopped, restarting, or unhealthy.",
                                "service_not_ready", "Review this service's logs in this installation's Compose project."))
        elif len(running) != len(matches) or any(x.get("Health") == "starting" for x in running):
            result.append(check("service:" + name, "WARN", name + ": running, but readiness is incomplete.",
                                "service_starting", "Wait briefly, then run the check again."))
        else:
            result.append(check("service:" + name, "PASS", name + ": running.", "service_running"))
    return result


def request_failure(name, exc):
    # HTTP status belongs to Grafana or its datasource path here, NOT FortiGate.
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return check(name, "FAIL", "Grafana rejected the readiness tool's login. This is not a FortiGate token result.",
                         "grafana_auth_rejected", "Check the local Grafana credentials and whether the admin password has changed.", http_status=exc.code)
        return check(name, "FAIL", "The Grafana or datasource request failed.",
                     "datasource_request_failed", "Review Grafana's connection to the datasource and its settings. The response body is hidden.", http_status=exc.code)
    return check(name, "UNKNOWN", "The service could not be read or returned an invalid response.",
                 "request_unavailable", "Check the Grafana address and service availability. The cause has not been established.")


def metric_snapshot(p, c, request, prom_uid, now, freshness):
    # Range vectors expose actual sample timestamps; an instant-query evaluation
    # timestamp alone does not prove that an exporter sample is fresh.
    selector = ('{__name__=~"up|probe_success|fortigate_virtual_wan_status|fortigate_interface_link_up",'
                'job="fortigate",instance=' + json.dumps(c["fortigate_host"]) + '}')
    expr = selector + f"[{2 * freshness}s]"
    path = f"/api/datasources/proxy/uid/{prom_uid}/api/v1/query?" + urllib.parse.urlencode({"query": expr, "time": str(now)})
    response = request(p, c, path, timeout=15)
    if not isinstance(response, dict):
        raise ValueError("Invalid Prometheus response")
    data = response.get("data", {})
    if response.get("status") != "success" or data.get("resultType") != "matrix":
        raise ValueError("Unexpected Prometheus response")
    result = []
    for row in data["result"]:
        points = []
        for stamp, value in row.get("values", []):
            stamp, value = float(stamp), float(value)
            if math.isfinite(stamp) and math.isfinite(value):
                points.append((stamp, value))
        if points:
            stamp, value = max(points)
            result.append({"labels": row["metric"], "age": now - stamp, "value": value})
    return result


def metrics_checks(samples, c, freshness):
    def select(metric):
        return [x for x in samples if x["labels"].get("__name__") == metric]

    def fresh(rows):
        return [x for x in rows if -30 <= x["age"] <= freshness]

    def absent(name, rows):
        if any(x["age"] < -30 for x in rows):
            return check(name, "WARN", "Some samples have future timestamps; their freshness cannot be confirmed.",
                         "clock_skew", "Check time synchronization on the server and device.")
        return check(name, "UNKNOWN", "No recent metrics are available for this check.",
                     "metrics_stale" if rows else "metrics_missing", "Wait for collection or review its configuration. Missing metrics do not mean a WAN is down.")

    result = []
    rows = select("up")
    latest = fresh(rows)
    scrape_failed = bool(latest) and any(x["value"] != 1 for x in latest)
    if latest:
        ok = all(x["value"] == 1 for x in latest)
        result.append(check("fortigate_scrape", "PASS" if ok else "FAIL",
            "Prometheus recently scraped the exporter successfully." if ok else "The latest exporter scrape failed; an API failure cause has not been established.",
            "scrape_ok" if ok else "scrape_failed",
            "" if ok else "Review the exporter and collection path. Use --api-check for a separate API check.",
            newest_sample_age_seconds=round(min(x["age"] for x in latest), 1)))
    else:
        result.append(absent("fortigate_scrape", rows))
    rows = select("probe_success")
    latest = fresh(rows)
    if latest and all(x["value"] == 1 for x in latest) and scrape_failed:
        result.append(check("fortigate_api", "UNKNOWN", "An earlier successful probe does not confirm readiness after a newer failed scrape.",
                            "probe_not_current", "Wait for a new successful sample or use --api-check."))
    elif latest and all(x["value"] == 1 for x in latest):
        result.append(check("fortigate_api", "PASS", "Recent exporter probes succeeded. This does not verify every API permission.", "probe_ok"))
    elif latest:
        result.append(check("fortigate_api", "FAIL", "The exporter recorded a failed FortiGate probe; this metric alone does not identify the cause.",
                            "probe_failed", "Use --api-check to inspect API connectivity, authentication, and TLS. This does not establish a WAN outage."))
    else:
        result.append(absent("fortigate_api", rows))

    for name, metric, label in [("sla_metrics", "fortigate_virtual_wan_status", "interface"),
                                 ("interface_metrics", "fortigate_interface_link_up", "name")]:
        rows = select(metric)
        if name == "sla_metrics":
            rows = [x for x in rows if x["labels"].get("vdom") == c["vdom"]
                    and (not c["sla"] or x["labels"].get("sla") == c["sla"])]
        latest = fresh(rows)
        if not latest:
            result.append(absent(name, rows))
            continue
        names = {x["labels"].get(label) for x in latest} - {None, ""}
        expected = c.get("confirmed_wan_interfaces") or c["wan_interfaces"]
        missing = sorted(set(expected) - names)
        partial = bool(missing) or len(latest) < len(rows)
        result.append(check(name, "WARN" if partial else "PASS",
            ("SLA metrics" if name == "sla_metrics" else "Device interface metrics")
            + f" received recently for {len(names)} interface(s)." + (" Some configured choices or older series have no recent sample." if partial else ""),
            "metrics_partial" if partial else "metrics_received",
            "Review names, VDOM/SLA scope, and read permissions. A missing interface is not classified as DOWN." if partial else "",
            interfaces_received=len(names), configured_interfaces_without_recent_data=missing))
    return result


def logs_check(p, c, request, loki_uid, now, freshness):
    body = {"from": str(int((now - freshness) * 1000)), "to": str(int(now * 1000)),
        "queries": [{"refId": "A", "datasource": {"uid": loki_uid, "type": "loki"},
                     "expr": f'sum(count_over_time({{job="fortigate-syslog"}}[{freshness}s]))',
                     "queryType": "instant", "intervalMs": 60000, "maxDataPoints": 1}]}
    response = request(p, c, "/api/ds/query", "POST", body, timeout=20)
    if not isinstance(response, dict):
        raise ValueError("Invalid Loki response")
    result = response.get("results", {}).get("A")
    if not isinstance(result, dict) or result.get("error") or result.get("status", 200) >= 400:
        raise ValueError("Loki query failed")
    counts = []
    for frame in result.get("frames", []):
        for i, field in enumerate(frame.get("schema", {}).get("fields", [])):
            if field.get("type") == "number":
                for value in frame["data"]["values"][i]:
                    if value is not None and math.isfinite(float(value)):
                        counts.append(float(value))
    count = sum(counts)
    if count > 0:
        return check("recent_logs", "PASS", f"Found {int(count)} log entries with timestamps in the last {freshness} seconds.",
                     "logs_received", "This confirms recent logs, not complete traffic coverage or verified sender identity.", count=int(count), window_seconds=freshness)
    return check("recent_logs", "UNKNOWN", f"No logs observed in the last {freshness} seconds. A delivery failure has not been established.",
                 "logs_not_observed", "There may be no traffic. Review the syslog destination, port, protocol, format, clocks, and filters.", window_seconds=freshness)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_check(p, c):
    try:
        host = c["fortigate_host"]
        if ":" in host:
            host = "[" + host + "]"
        target = f"https://{host}:{c['api_port']}"
        auth = json.loads((p / "secrets/fortigate-key.yaml").read_text(encoding="utf-8"))
        token = auth[target]["token"]
        if not isinstance(token, str) or not token or any(ord(x) < 33 for x in token):
            raise ValueError("Invalid token file")
        ca = Path(c["api_ca_file"]) if c["api_ca_file"] else None
        if ca and not ca.is_absolute():
            ca = p / ca
        context = ssl.create_default_context(cafile=str(ca) if ca else None)
        if not c["api_tls_verify"]:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        request = urllib.request.Request(target + "/api/v2/monitor/system/status",
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                    urllib.request.HTTPSHandler(context=context), NoRedirect())
        with opener.open(request, timeout=10) as response:
            # Bound the response and discard it; never return status payloads.
            data = json.loads(response.read(1024 * 1024))
        if not isinstance(data, dict) or data.get("status") != "success":
            return check("direct_api", "UNKNOWN", "The API response did not confirm a successful system/status check.",
                         "api_response_unconfirmed", "Review version compatibility, endpoint, and permissions. The response body is hidden.")
        return check("direct_api", "PASS", "The direct API check succeeded for system/status only.",
                     "api_status_ok", "This does not verify SLA permissions or access to every VDOM.", tls_verified=c["api_tls_verify"])
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return check("direct_api", "FAIL", "The API rejected authentication (401).", "api_auth_rejected",
                         "Review token validity and source access restrictions. The token alone is not a confirmed cause.", http_status=401)
        if exc.code == 403:
            return check("direct_api", "FAIL", "The API denied access (403).", "api_access_forbidden",
                         "Review API permissions, VDOM scope, and trusted hosts. This does not prove the token is invalid.", http_status=403)
        return check("direct_api", "FAIL", "The API returned an HTTP status that does not confirm readiness.",
                     "api_redirect_rejected" if 300 <= exc.code < 400 else "api_http_error",
                     "Review the API address, port, and endpoint. Redirects were not followed and the response body is hidden.", http_status=exc.code)
    except (ssl.SSLError, urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLError):
            code, msg = "api_tls_error", "HTTPS negotiation or certificate validation failed."
        elif isinstance(reason, socket.gaierror):
            code, msg = "api_dns_error", "The FortiGate hostname could not be resolved."
        elif isinstance(reason, (TimeoutError, socket.timeout)):
            code, msg = "api_timeout", "The API check timed out without a response."
        elif isinstance(reason, ConnectionRefusedError):
            code, msg = "api_connection_refused", "The API port refused the connection."
        else:
            code, msg = "api_connection_unavailable", "The API could not be reached or a local connection file could not be read."
        return check("direct_api", "FAIL", msg, code, "Review this installation's connection settings, certificate, and files. Nothing was changed automatically.")
    except (ValueError, KeyError, TypeError):
        return check("direct_api", "UNKNOWN", "The connection file or API response could not be used for this check.",
                     "api_data_invalid", "Review configuration and compatibility. Contents are hidden to protect credentials.")


def accounting_request(p,c):
    host=c['usage_bind'] if c['usage_bind']!='0.0.0.0' else '127.0.0.1'
    password=(p/'secrets/usage_admin_password').read_text().strip()
    auth=base64.b64encode(('admin:'+password).encode()).decode()
    req=urllib.request.Request(f"http://{host}:{c['usage_port']}/api/diagnostics",headers={'Authorization':'Basic '+auth},method='GET')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(req,timeout=10) as response:
        body=response.read(1024*1024+1)
    if len(body)>1024*1024:raise ValueError('Invalid accounting diagnostics')
    return json.loads(body)


def accounting_checks(report,now,freshness):
    if not isinstance(report,dict):raise ValueError('Invalid accounting diagnostics')
    result=[]
    attempt=report.get('last_attempt')
    fresh=isinstance(attempt,(int,float)) and math.isfinite(attempt) and -30<=now-attempt<=freshness
    ready=report.get('last_poll_ok') is True and fresh
    result.append(check('accounting_poll','PASS' if ready else 'WARN',
        'The usage engine recently processed a Loki poll.' if ready else 'A recent successful usage-engine poll was not established.',
        'accounting_poll_ok' if ready else 'accounting_poll_unavailable',
        '' if ready else 'Review engine reachability and its bounded-query/storage status; processing success does not certify traffic completeness.'))
    lag=report.get('processing_lag_seconds')
    caught_up=isinstance(lag,(int,float)) and math.isfinite(lag) and 0<=lag<=freshness
    result.append(check('accounting_lag','PASS' if caught_up else 'WARN',
        'Usage processing is near the present.' if caught_up else 'Usage processing is catching up or its checkpoint is unavailable.',
        'accounting_caught_up' if caught_up else 'accounting_backfill_or_stale',
        '' if caught_up else 'Allow bounded backfill to finish and review storage limits. Older summaries are not current-period proof.'))
    for name,key,label in [('accounting_metadata','inventory_available','Interface metadata'),('accounting_source','source_identity_bound','Expected source-device filter')]:
        ok=report.get(key) is True
        result.append(check(name,'PASS' if ok else 'WARN',label+(' is configured/available.' if ok else ' is not established.'),
            name+'_available' if ok else name+'_unavailable',
            '' if ok else 'Review read permissions, exact interface/VDOM metadata and source binding; do not infer VLANs from IP subnets.'))
    if report.get('inventory_error') in {'api_http_401','api_http_403'}:
        result.append(check('accounting_inventory_permissions','FAIL','The interface inventory API rejected authentication or read permissions.',
            report['inventory_error'],'Review the existing API account and VDOM scope; response bodies and tokens are hidden.'))
    rejected=sum(report.get(key,0) for key in ('parser_rejections','late_rejections') if isinstance(report.get(key,0),int))
    result.append(check('accounting_rejections','WARN' if rejected else 'PASS',
        f'The engine has recorded {rejected} malformed/too-late input rejection(s).' if rejected else 'No input rejection has been recorded by this engine.',
        'accounting_rejections_seen' if rejected else 'accounting_no_rejections',
        'Review source formatting, timestamp synchronization and ledger retention; these counts are historical, not proof of a current outage.' if rejected else ''))
    receipt=report.get('loki_timestamps_at_receipt') is True
    result.append(check('accounting_delivery_timestamps','PASS' if receipt else 'WARN',
        'Receipt-time Loki timestamps are configured for late-delivery discovery.' if receipt else 'Receipt-time discovery is not confirmed; late source timestamps may fall outside a short poll overlap.',
        'receipt_timestamps_configured' if receipt else 'late_delivery_coverage_unverified',
        '' if receipt else 'Review the upgrade plan; source eventtime must remain in the raw log line.'))
    return result


def run(p, c, request, compose_command, prom_uid, loki_uid, *, freshness=300, direct_api=False, now=None, usage_request=None):
    if type(freshness) is not int or not 60 <= freshness <= 3600:
        raise ValueError("freshness must be between 60 and 3600 seconds")
    now = time.time() if now is None else now
    services = SERVICES + (('fortilens-usage',) if c.get('accounting_enabled') else ()) + (('fortilens-portal',) if c.get('portal_enabled') else ())
    checks = service_checks(p, compose_command, services)
    available = {}
    try:
        health = request(p, c, "/api/health", timeout=5)
        if not isinstance(health, dict):
            raise ValueError("Invalid Grafana health response")
        available["grafana"] = health.get("database") == "ok"
        checks.append(check("grafana", "PASS" if available["grafana"] else "FAIL",
                            "Grafana and its database are ready." if available["grafana"] else "Grafana did not confirm database readiness.", "grafana_ready" if available["grafana"] else "grafana_not_ready"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        checks.append(request_failure("grafana", exc))
    for name, uid in [("prometheus", prom_uid), ("loki", loki_uid)]:
        available[name] = False
        if available.get("grafana"):
            try:
                h = request(p, c, "/api/datasources/uid/" + uid + "/health", timeout=10)
                if not isinstance(h, dict):
                    raise ValueError("Invalid datasource health response")
                available[name] = h.get("status") == "OK"
                checks.append(check(name, "PASS" if available[name] else "FAIL",
                    name + (": connected through Grafana." if available[name] else ": datasource health did not confirm connectivity."), "datasource_ready" if available[name] else "datasource_not_ready"))
            except (OSError, ValueError, urllib.error.URLError) as exc:
                checks.append(request_failure(name, exc))
        else:
            checks.append(check(name, "UNKNOWN", name + ": cannot be checked through Grafana right now.", "grafana_prerequisite_unavailable"))
    metric_ids = ["fortigate_scrape", "fortigate_api", "sla_metrics", "interface_metrics"]
    if available["prometheus"]:
        try:
            checks.extend(metrics_checks(metric_snapshot(p, c, request, prom_uid, now, freshness), c, freshness))
        except (OSError, ValueError, KeyError, TypeError, IndexError, urllib.error.URLError) as exc:
            checks.append(request_failure("metric_query", exc))
            checks.extend(check(k, "UNKNOWN", "Metrics for this check could not be read.", "metrics_query_unavailable") for k in metric_ids)
    else:
        checks.extend(check(k, "UNKNOWN", "Metrics cannot be checked until Prometheus is readable.", "prometheus_prerequisite_unavailable") for k in metric_ids)
    if available["loki"]:
        try:
            checks.append(logs_check(p, c, request, loki_uid, now, freshness))
        except (OSError, ValueError, KeyError, TypeError, IndexError, urllib.error.URLError) as exc:
            checks.append(request_failure("recent_logs", exc))
    else:
        checks.append(check("recent_logs", "UNKNOWN", "Logs cannot be checked until Loki is readable.", "loki_prerequisite_unavailable"))
    if direct_api:
        checks.append(api_check(p, c))
    if c.get('accounting_enabled'):
        try:
            checks.extend(accounting_checks((usage_request or accounting_request)(p,c),now,freshness))
        except urllib.error.HTTPError as exc:
            checks.append(check('accounting_api','FAIL','The local reporting service rejected or failed its read-only diagnostic request.',
                'accounting_auth_rejected' if exc.code in (401,403) else 'accounting_http_failed',
                'Review the local reporting password and engine service. This is not a FortiGate token result.',http_status=exc.code))
        except (OSError,ValueError,TypeError,KeyError,urllib.error.URLError):
            checks.append(check('accounting_api','UNKNOWN','Usage-engine diagnostics are unavailable.',
                'accounting_diagnostics_unavailable','Review the optional service and its local reporting address. No source traffic or WAN state is inferred.'))
    status = next((s for s in ["FAIL", "WARN", "UNKNOWN"] if any(x["status"] == s for x in checks)), "PASS")
    return {"schema_version": 1, "checked_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "status": status, "read_only": True, "direct_api_requested": direct_api,
            "freshness_seconds": freshness, "checks": checks}


def print_report(report, as_json=False):
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("FortiLens readiness check — " + LABELS[report["status"]])
        for item in report["checks"]:
            print("[" + LABELS[item["status"]] + "] " + item["message"])
            if item["action"]:
                print("  " + item["action"])
        if not report["direct_api_requested"]:
            print("No direct API request was sent. Add --api-check for a separate read-only API check.")
    return 0 if report["status"] == "PASS" else 1 if report["status"] == "FAIL" else 2
