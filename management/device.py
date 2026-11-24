"""Read-only FortiGate access: credentials, Test Connection and discovery."""

import http.client
import hashlib
import ipaddress
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from .common import Problem, atomic, load
from .models import (
    Settings,
)
import doctor
import interfaces


def fortigate(settings, token, path, params=None, ca=None):
    """Pinned-DNS, bounded, GET-only API connection; no proxies or redirects."""
    if path not in ("/api/v2/monitor/system/status", "/api/v2/cmdb/system/interface"):
        raise Problem(
            "device_operation_rejected",
            "Only supported read-only FortiGate operations are permitted.",
        )
    try:
        addresses = list(
            dict.fromkeys(
                row[4][0]
                for row in socket.getaddrinfo(
                    settings.fortigate_host, settings.api_port, type=socket.SOCK_STREAM
                )
            )
        )
        if not addresses or any(
            (ip := ipaddress.ip_address(a)).is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or ip.is_reserved
            or (
                ip.version == 6
                and ip.ipv4_mapped
                and (ip.ipv4_mapped.is_loopback or ip.ipv4_mapped.is_link_local)
            )
            for a in addresses
        ):
            raise Problem(
                "device_address_rejected",
                "Choose the actual FortiGate address; local, metadata and reserved service addresses are not supported.",
            )
        context = ssl.create_default_context(cadata=ca or None)
        if not settings.api_tls_verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        class Pinned(http.client.HTTPSConnection):
            def connect(self):
                sock = socket.create_connection((addresses[0], settings.api_port), timeout=10)
                self.sock = context.wrap_socket(sock, server_hostname=settings.fortigate_host)

        connection = Pinned(settings.fortigate_host, settings.api_port, timeout=15, context=context)
        try:
            connection.request(
                "GET",
                path + ("?" + urllib.parse.urlencode(params) if params else ""),
                headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
            )
            response = connection.getresponse()
            data = response.read(8 * 1024 * 1024 + 1)
            if response.status in (401, 403):
                raise Problem(
                    "api_authentication_failed"
                    if response.status == 401
                    else "api_permission_denied",
                    "Review the API token, read permissions, VDOM scope and Trusted Hosts restrictions.",
                    400,
                )
            if response.status != 200:
                raise Problem(
                    "api_http_failed",
                    "The read-only API request failed; redirects are not followed.",
                )
            if len(data) > 8 * 1024 * 1024:
                raise Problem(
                    "api_response_too_large", "The interface response exceeded the safe size limit."
                )
            value = json.loads(data)
            if not isinstance(value, (dict, list)):
                raise ValueError()
            return value
        finally:
            connection.close()
    except Problem:
        raise
    except ssl.SSLError:
        raise Problem(
            "api_tls_failed",
            "The FortiGate certificate could not be verified. Review its hostname, validity and custom CA.",
        ) from None
    except (OSError, ValueError, http.client.HTTPException):
        raise Problem(
            "api_connection_failed",
            "Cannot reach the FortiGate HTTPS API. Review the address, port, routing and API access.",
        ) from None


class DeviceMixin:
    """Operations on the FortiGate. Every request is a bounded, read-only GET."""

    def discovery_readiness(self):
        state = self.c.state()
        settings = Settings.model_validate(state["settings"])
        previous = state.get("applied") or {}
        fields = ("wan_interfaces", "fortigate_host", "api_port", "vdom", "api_tls_verify")
        changed = [key for key in fields if previous.get(key) != state["settings"].get(key)]
        required = bool(settings.wan_interfaces) and (not state["configured"] or bool(changed))
        if not required:
            return {
                "required": False,
                "current": True,
                "changed_fields": changed,
                "reason": "No new interface discovery is required for this draft.",
            }
        try:
            inventory = load(self.c.control / "inventory.json", {})
            current = isinstance(inventory, dict) and inventory.get(
                "scope"
            ) == self.inventory_scope(settings)
            if current:
                interfaces.selected_names(
                    inventory["report"], settings.wan_interfaces, settings.vdom
                )
        except (Problem, OSError, ValueError, TypeError, KeyError):
            current = False
        return {
            "required": True,
            "current": bool(current),
            "changed_fields": changed,
            "reason": "Interface discovery matches this connection and WAN selection."
            if current
            else "Connection, trust, credentials or WAN selection changed. Save, discover interfaces and confirm the Internet interfaces before Apply.",
        }

    def credentials(self, settings):
        pending = load(self.c.secret_path, {})
        baseline = load(self.c.root / self.c.state()["site_file"], {})
        ca = pending.get("ca")
        if ca is None and baseline.get("api_ca_file"):
            from .common import guarded

            ca = guarded(self.c.root, baseline["api_ca_file"]).read_text()
        return self.c.token(), ca

    def inventory_scope(self, settings):
        token, ca = self.credentials(settings)
        fields = [
            settings.fortigate_host,
            settings.api_port,
            settings.vdom,
            settings.api_tls_verify,
            hashlib.sha256(token.encode()).hexdigest(),
            hashlib.sha256((ca or "").encode()).hexdigest(),
        ]
        return hashlib.sha256(json.dumps(fields).encode()).hexdigest()

    def device(self, operation, revision):
        state = self.c.state()
        if revision != state["revision"]:
            raise Problem("revision_conflict", "Save and use the latest settings revision.", 409)
        s = Settings.model_validate(state["settings"])
        token, ca = self.credentials(s)
        if not token:
            raise Problem("token_required", "Save the FortiGate API token first.")
        if operation == "test":
            data = self.device_get(s, token, "/api/v2/monitor/system/status", {"vdom": s.vdom}, ca)
            if not isinstance(data, dict) or data.get("status") != "success":
                raise Problem(
                    "api_status_unconfirmed",
                    "The API did not confirm a successful read-only request.",
                )
            # Never return arbitrary device payloads or echo a token in a name.
            info = {}
            for key in ("version", "serial", "model"):
                details = data.get("results") if isinstance(data.get("results"), dict) else {}
                value = data.get(key, details.get(key))
                if isinstance(value, str) and len(value) <= 120 and token not in value:
                    info[key] = value
            if s.expected_devid and info.get("serial") and info["serial"] != s.expected_devid:
                raise Problem(
                    "device_identity_mismatch",
                    "The API device ID differs from the configured accounting source. Review the address and expected device ID.",
                )
            return {
                "status": "success",
                "tls_verified": s.api_tls_verify,
                "device": info,
                "message": "Read-only system/status succeeded. Interface and SLA permissions may differ.",
            }
        metadata = {}

        def getter(start):
            try:
                data = self.device_get(
                    s,
                    token,
                    "/api/v2/cmdb/system/interface",
                    {
                        "vdom": s.vdom,
                        "format": "name|vdom|type|role|interface|vlanid|ip|status",
                        "count": 500,
                        "start": start,
                    },
                    ca,
                )
                for envelope in data if isinstance(data, list) else [data]:
                    for raw in envelope.get("results", []):
                        if isinstance(raw, dict):
                            metadata[(envelope.get("vdom", s.vdom), raw.get("name"))] = raw
                return data
            except Problem as e:
                raise interfaces.InventoryError(e.code, e.message) from None

        result = interfaces.collect(s.portable(), token, api_get=getter)
        for row in result["interfaces"]:
            raw = metadata.get((row["vdom"], row["name"]), {})
            address = None
            try:
                if isinstance(raw.get("ip"), str):
                    address = str(ipaddress.ip_interface(raw["ip"]))
            except ValueError:
                pass
            row.update(
                ip_address=address,
                status="Configured enabled"
                if raw.get("status") == "up"
                else "Configured disabled"
                if raw.get("status") == "down"
                else "Unknown",
                discovery_confidence=row.get("review_status", "Review required"),
            )
        result["interfaces"] = [row for row in result["interfaces"] if token not in json.dumps(row)]
        atomic(
            self.c.control / "inventory.json", {"scope": self.inventory_scope(s), "report": result}
        )
        return result

    def confirm_serial(self, settings):
        """Binding existing usage to a serial cannot be undone, so the FortiGate must report it first."""
        test = load(self.c.control / "connection-test.json", {})
        test = test if isinstance(test, dict) else {}
        result = test.get("result")
        device = result.get("device") if isinstance(result, dict) else None
        reported = device.get("serial") if isinstance(device, dict) else None
        if (
            test.get("scope") != self.inventory_scope(settings)
            or not reported
            or reported != settings.expected_devid
        ):
            raise Problem(
                "serial_confirmation_required",
                "Run Test Connection with these saved settings first. Binding cannot be undone, so the FortiGate must report the serial number you entered.",
            )

    def connection_test_check(self, settings, revision, now=None):
        """The last Test Connection result for these connection settings, with its age.

        A success stays valid until the address, VDOM, TLS choice, token or CA
        change; the exporter and usage-engine checks show whether the API is
        answering right now. A failure is current only for a few minutes."""
        now = time.time() if now is None else now
        previous = load(self.c.control / "connection-test.json", {})
        previous = previous if isinstance(previous, dict) else {}
        test = previous.get("result", {})
        at = previous.get("at")
        action = "Use Settings > FortiGate Connection > Test Connection. A system/status read does not verify all API permissions."
        if not isinstance(at, (int, float)) or not isinstance(test, dict):
            return doctor.check(
                "direct_api",
                "INFO",
                "Test Connection has not been run for this installation yet.",
                "connection_test_not_run",
                action,
            )
        tested = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))
        if test.get("status") == "success":
            if previous.get("scope") != self.inventory_scope(settings):
                return doctor.check(
                    "direct_api",
                    "INFO",
                    "The connection settings changed after the last successful Test Connection.",
                    "connection_test_outdated",
                    action,
                    tested_at=tested,
                )
            return doctor.check(
                "direct_api",
                "PASS",
                "The last Test Connection succeeded. It reads system/status only; interface and SLA permissions may differ.",
                "last_read_only_api_test",
                action,
                tested_at=tested,
                age_seconds=round(now - at),
            )
        if (
            test.get("status") == "failed"
            and now - at < 300
            and previous.get("revision") == revision
        ):
            return doctor.check(
                "direct_api",
                "FAIL",
                test.get("message", "The last Test Connection failed."),
                "last_read_only_api_test",
                action,
                tested_at=tested,
            )
        return doctor.check(
            "direct_api",
            "INFO",
            "The last Test Connection did not succeed. Run it again to see the current result.",
            "connection_test_failed_earlier",
            action,
            tested_at=tested,
        )
