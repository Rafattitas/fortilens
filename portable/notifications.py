"""Alerts: rules over FortiLens evidence, their state, and delivery by Telegram or email.

Nothing here changes the FortiGate. Missing evidence leaves an alert as it is:
it never opens or closes one, and is never read as down or as zero traffic.
Secrets (the Telegram bot token, the mail password) arrive as arguments and are
never logged, stored with the alert history, or returned.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import html
from email.message import EmailMessage
import json
import math
import os
from pathlib import Path
import re
import smtplib
import ssl
import statistics
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# name: (label, critical, hold seconds). Critical alerts are sent outside working
# hours too; the others wait for working hours (and, later, the daily summary).
# The fourth value marks alerts that are never sent on their own: they are
# listed in the next daily summary instead.
RULES = {
    "line_down": ("Line down", True, 120, False),
    "line_slow": ("Internet slow on a line", False, 600, False),
    "line_starved": ("Line carries little traffic", False, 900, False),
    "heavy_device": ("One device fills a line", False, 1200, False),
    "logs_stopped": ("Logs stopped arriving", True, 0, False),
    "fortigate_unreachable": ("FortiGate unreachable", True, 300, False),
    "accounting_behind": ("Accounting behind", False, 0, False),
    "storage_full": ("Storage nearly full", True, 0, False),
    "sdwan_changed": ("SD-WAN rules changed", False, 0, False),
    "line_loss": ("Line losing packets", False, 1200, True),
    "line_full": ("Line nearly full", False, 1200, True),
    "checks_disagree": ("Health checks disagree", False, 3600, True),
    "new_device": ("New device on the network", False, 0, True),
}
HEAVY_SHARE = 0.5
FULL_SHARE = 0.9
DIGEST_SECONDS = 4 * 86400
RECOVERY_SECONDS = 120
REMINDER_SECONDS = 2 * 3600
HISTORY = 300
SLOW_LOSS_PCT = 20
SLOW_DELAY_FACTOR = 3
SLOW_DELAY_FLOOR_MS = 150
STARVED_SHARE = 0.25
STARVED_SESSIONS = 0.7
STARVED_MIN_MBPS = 2
LOG_SILENCE_SECONDS = 900
ACCOUNTING_LAG_SECONDS = 1800
STORAGE_FREE_SHARE = 0.10
STORAGE_DB_SHARE = 0.90
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

DEFAULT = {
    "telegram": {"enabled": False, "chat_id": ""},
    "email": {
        "enabled": False,
        "host": "",
        "port": 587,
        "security": "starttls",
        "user": "",
        "sender": "",
        "to": [],
    },
    "rules": {name: True for name in RULES},
    "work_hours": {"start": "08:00", "end": "18:00", "days": ["sun", "mon", "tue", "wed", "thu"]},
    # The daily summary goes on working days, to Telegram (or email without it);
    # the weekly and monthly reports go by email (or Telegram without it).
    "reports": {
        "daily": {"enabled": True, "time": "08:00"},
        "weekly": {"enabled": True, "day": "sun", "time": "08:00"},
        "monthly": {"enabled": False, "time": "08:00"},
    },
}
REPORT_WINDOW_SECONDS = 3600
_ADDRESS = re.compile(r"^[^@\s,;<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,63}$")
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_CHAT = re.compile(r"^(-?[0-9]{1,20}|@[A-Za-z0-9_]{5,32})$")
_CLOCK = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def validate(value):
    """A complete, checked notifications block; raises ValueError on anything else."""
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise ValueError("notifications has unknown fields")
    c = deepcopy(DEFAULT)
    for key, part in value.items():
        if not isinstance(part, dict) or set(part) - set(DEFAULT[key]):
            raise ValueError("notifications." + key + " has unknown fields")
        if key == "reports":
            for kind, report in part.items():
                if not isinstance(report, dict) or set(report) - set(DEFAULT["reports"][kind]):
                    raise ValueError("notifications.reports." + kind + " has unknown fields")
                c["reports"][kind].update(report)
            continue
        c[key].update(part)
    for kind, report in c["reports"].items():
        if type(report["enabled"]) is not bool or not _CLOCK.match(str(report["time"])):
            raise ValueError("Reports need a switch and an HH:MM time")
        if kind == "weekly" and report["day"] not in DAYS:
            raise ValueError("The weekly report day is mon to sun")
    t, e, w = c["telegram"], c["email"], c["work_hours"]
    if type(t["enabled"]) is not bool or not isinstance(t["chat_id"], str):
        raise ValueError("Invalid Telegram settings")
    if t["chat_id"] and not _CHAT.match(t["chat_id"]):
        raise ValueError("Telegram chat ID must be a number or an @channel name")
    if t["enabled"] and not t["chat_id"]:
        raise ValueError("Telegram needs a chat ID")
    if type(e["enabled"]) is not bool or type(e["port"]) is not int or not 1 <= e["port"] <= 65535:
        raise ValueError("Invalid email settings")
    if e["security"] not in ("starttls", "tls"):
        raise ValueError("Email security must be starttls or tls")
    for key in ("host", "user", "sender"):
        if not isinstance(e[key], str) or len(e[key]) > 254 or any(ord(x) < 33 for x in e[key]):
            raise ValueError("Invalid email " + key)
    if e["host"] and not _HOST.match(e["host"]):
        raise ValueError("Invalid mail server")
    if e["sender"] and not _ADDRESS.match(e["sender"]):
        raise ValueError("Invalid From address")
    if not isinstance(e["to"], list) or len(e["to"]) > 20:
        raise ValueError("Email recipients must be a list of up to 20 addresses")
    for address in e["to"]:
        if not isinstance(address, str) or not _ADDRESS.match(address):
            raise ValueError("Invalid recipient address")
    if e["enabled"] and not (e["host"] and e["sender"] and e["to"]):
        raise ValueError("Email needs a mail server, a From address and recipients")
    rules = c["rules"]
    if set(rules) - set(RULES) or any(type(v) is not bool for v in rules.values()):
        raise ValueError("Unknown alert rule")
    if not all(isinstance(w[k], str) and _CLOCK.match(w[k]) for k in ("start", "end")):
        raise ValueError("Working hours use HH:MM")
    if w["start"] >= w["end"]:
        raise ValueError("Working hours must end after they start")
    if (
        not isinstance(w["days"], list)
        or len(set(w["days"])) != len(w["days"])
        or set(w["days"]) - set(DAYS)
    ):
        raise ValueError("Working days are mon to sun")
    w["days"] = [d for d in DAYS if d in w["days"]]
    return c


def configured(c):
    return bool(c) and (c["telegram"]["enabled"] or c["email"]["enabled"])


def working(c, moment, zone):
    """Whether a moment falls in the working hours, in the accounting timezone."""
    local = datetime.fromtimestamp(moment, zone)
    w = c["work_hours"]
    return DAYS[local.weekday()] in w["days"] and w["start"] <= local.strftime("%H:%M") < w["end"]


# Delivery. Errors carry a fixed code, never the upstream text or a secret.


class DeliveryError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


MESSAGES = {
    "not_configured": "This channel is not set up.",
    "telegram_rejected": "Telegram refused the message: check the bot token and the chat ID, and that the bot was added to the chat.",
    "telegram_unreachable": "api.telegram.org could not be reached from the server.",
    "email_auth": "The mail server refused the user name or password.",
    "email_rejected": "The mail server refused the sender or a recipient.",
    "email_unreachable": "The mail server could not be reached, or TLS failed.",
}


def send_telegram(token, chat_id, text, *, opener=None, timeout=15):
    if not token or not chat_id:
        raise DeliveryError("not_configured")
    if not re.fullmatch(r"[0-9]{3,20}:[A-Za-z0-9_-]{20,80}", token):
        raise DeliveryError("telegram_rejected")
    body = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": "true"}
    ).encode()
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage", data=body, method="POST"
    )
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            result = json.loads(response.read(65536) or b"{}")
    except urllib.error.HTTPError as exc:
        raise DeliveryError(
            "telegram_rejected" if 400 <= exc.code < 500 else "telegram_unreachable"
        ) from None
    except (OSError, ValueError):
        raise DeliveryError("telegram_unreachable") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise DeliveryError("telegram_rejected")


def send_email(c, password, subject, text, *, smtp=smtplib, timeout=20, html_body=None):
    e = c["email"]
    if not (e["host"] and e["sender"] and e["to"]):
        raise DeliveryError("not_configured")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = e["sender"], ", ".join(e["to"]), subject
    message.set_content(text)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    context = ssl.create_default_context()
    try:
        if e["security"] == "tls":
            client = smtp.SMTP_SSL(e["host"], e["port"], timeout=timeout, context=context)
        else:
            client = smtp.SMTP(e["host"], e["port"], timeout=timeout)
        with client:
            if e["security"] == "starttls":
                client.starttls(context=context)
            if e["user"]:
                client.login(e["user"], password or "")
            client.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise DeliveryError("email_auth") from None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise DeliveryError("email_rejected") from None
    except (smtplib.SMTPException, OSError, ssl.SSLError, ValueError):
        raise DeliveryError("email_unreachable") from None


def send(c, secrets, channel, subject, text, **kwargs):
    """Send one message on one channel; returns None or raises DeliveryError."""
    secrets = secrets or {}
    if channel == "telegram":
        return send_telegram(
            secrets.get("telegram_bot_token"),
            c["telegram"]["chat_id"],
            subject + "\n" + text,
            opener=kwargs.get("opener"),
        )
    if channel == "email":
        return send_email(
            c,
            secrets.get("smtp_password"),
            subject,
            text,
            smtp=kwargs.get("smtp", smtplib),
            html_body=kwargs.get("html"),
        )
    raise DeliveryError("not_configured")


def read_secrets(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return (
        {
            k: v
            for k, v in value.items()
            if k in ("telegram_bot_token", "smtp_password") and isinstance(v, str)
        }
        if isinstance(value, dict)
        else {}
    )


# State: which alerts are open, and what was sent.


def duration(seconds):
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


class Alerts:
    """Open alerts and the sent history, kept in one small JSON file."""

    def __init__(self, path):
        self.path = Path(path) if path else None
        self.state = {
            "pending": {},
            "open": {},
            "history": [],
            "sdwan": None,
            "digest": [],
            "reports": {},
        }
        if self.path:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.state.update({k: loaded[k] for k in self.state if k in loaded})
            except (OSError, ValueError):
                pass

    def save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".alerts-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(self.state, stream)
            os.replace(temporary, self.path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass

    def step(self, now, signals, c, *, at_work=True):
        """Advance every alert with this minute's signals; return messages to send.

        A signal is ``{"rule", "subject", "bad": True|False|None, "text"}``, or an
        event (``"event": True``) that is sent once. ``bad`` None is no evidence:
        the alert keeps its state. An alert opens after its rule's hold, sends a
        reminder every two hours, and closes after two minutes back to normal.
        """
        pending, active, out = self.state["pending"], self.state["open"], []
        rules = (c or DEFAULT)["rules"]
        for s in signals:
            rule, key = s["rule"], s["rule"] + ":" + s.get("subject", "")
            label, critical, hold, summary = RULES[rule]
            if not rules.get(rule, True):
                pending.pop(key, None)
                active.pop(key, None)
                continue
            quiet = summary or not critical and not at_work
            title = label + (" · " + s["subject"] if s.get("subject") else "")
            if s.get("event"):
                out.append(self._message(now, key, "event", title, s["text"], quiet))
                continue
            if s["bad"] is None:
                continue
            if s["bad"]:
                # A trouble seen again after a gap without evidence starts over.
                if key in pending and now - pending[key].get("last", now) > 180:
                    del pending[key]
                pending.setdefault(key, {"since": now})["last"] = now
                if key in active:
                    alert = active[key]
                    alert.update(text=s["text"], clear_since=None)
                    if not summary and now - alert["last_sent"] >= REMINDER_SECONDS:
                        alert["last_sent"] = now
                        out.append(
                            self._message(
                                now,
                                key,
                                "reminder",
                                "Still: " + title,
                                duration(now - alert["opened"]) + " so far. " + s["text"],
                                quiet,
                            )
                        )
                elif now - pending[key]["since"] >= hold:
                    active[key] = {
                        "rule": rule,
                        "subject": s.get("subject", ""),
                        "title": title,
                        "text": s["text"],
                        "opened": pending[key]["since"],
                        "last_sent": now,
                        "clear_since": None,
                    }
                    out.append(self._message(now, key, "start", title, s["text"], quiet))
            else:
                pending.pop(key, None)
                alert = active.get(key)
                if alert:
                    alert["clear_since"] = alert.get("clear_since") or now
                    if now - alert["clear_since"] >= RECOVERY_SECONDS:
                        del active[key]
                        out.append(
                            self._message(
                                now,
                                key,
                                "end",
                                "Recovered · " + (s.get("subject") or label),
                                label
                                + " ended after "
                                + duration(alert["clear_since"] - alert["opened"])
                                + ".",
                                quiet,
                            )
                        )
        return out

    def _message(self, now, key, kind, title, text, quiet):
        return {"at": now, "key": key, "kind": kind, "title": title, "text": text, "held": quiet}

    def record(self, message, deliveries):
        entry = {k: message[k] for k in ("at", "key", "kind", "title", "text", "held")}
        entry["deliveries"] = deliveries
        history = self.state["history"]
        history.insert(0, entry)
        del history[HISTORY:]
        # What was not sent waits for the daily summary; keep a few days of it.
        if message["held"]:
            digest = self.state.setdefault("digest", [])
            digest.append({k: message[k] for k in ("at", "key", "kind", "title", "text")})
            digest[:] = [d for d in digest if d["at"] >= message["at"] - DIGEST_SECONDS]

    def digest(self, since, until):
        return [d for d in self.state.get("digest", []) if since <= d["at"] < until]

    def view(self):
        return {
            "open": sorted(self.state["open"].values(), key=lambda a: a["opened"]),
            "history": self.state["history"][:100],
        }


def deliver(c, secrets, messages, alerts, *, send=None, **kwargs):
    """Send each message on every enabled channel and record the outcome."""
    send = send or globals()["send"]
    for m in messages:
        deliveries = []
        if not m["held"] and configured(c):
            for channel in ("telegram", "email"):
                if not c[channel]["enabled"]:
                    continue
                # Email carries critical alerts only; Telegram carries every alert.
                if channel == "email" and not RULES[m["key"].split(":")[0]][1]:
                    continue
                try:
                    send(c, secrets, channel, "FortiLens · " + m["title"], m["text"], **kwargs)
                    deliveries.append({"channel": channel, "status": "delivered"})
                except DeliveryError as exc:
                    deliveries.append({"channel": channel, "status": "failed", "error": exc.code})
        alerts.record(m, deliveries)


# Signals from Prometheus series. ``query(expr)`` returns Prometheus vector results.


def _by(results, *labels):
    out = {}
    for r in results or []:
        try:
            value = float(r["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if math.isfinite(value):
            out[tuple(r.get("metric", {}).get(k, "") for k in labels)] = value
    return out


def _judged(values, iface, preferred):
    """The check that judges a line: the configured one when it covers it, else the best."""
    own = {sla: v for (i, sla), v in values.items() if i == iface}
    if not own:
        return None, None
    if preferred in own:
        return preferred, own[preferred]
    sla = min(own, key=own.get)
    return sla, own[sla]


def line_signals(query, site, *, delay_baseline, traffic_baseline, at_work):
    """Line down, Internet slow and little traffic, one signal per line per rule."""
    names = site.get("confirmed_wan_interfaces") or site.get("wan_interfaces") or []
    collected = _by(query('up{job="fortigate"}'), "job")
    if not collected or min(collected.values()) < 1:
        return [
            {"rule": r, "subject": n, "bad": None, "text": ""}
            for n in names
            for r in ("line_down", "line_slow", "line_starved")
        ]
    status = _by(query('fortigate_virtual_wan_status{state="up"}'), "interface", "sla")
    loss = _by(
        query("avg_over_time(fortigate_virtual_wan_packet_loss_ratio[10m]) * 100"),
        "interface",
        "sla",
    )
    delay = _by(
        query("avg_over_time(fortigate_virtual_wan_latency_seconds[10m]) * 1000"),
        "interface",
        "sla",
    )
    rx = _by(
        query(
            "max by (interface) (avg_over_time(fortigate_virtual_wan_bandwidth_rx_byte_per_second[10m])) * 8 / 1e6"
        ),
        "interface",
    )
    tx = _by(
        query(
            "max by (interface) (avg_over_time(fortigate_virtual_wan_bandwidth_tx_byte_per_second[10m])) * 8 / 1e6"
        ),
        "interface",
    )
    sessions = _by(
        query("max by (interface) (avg_over_time(fortigate_virtual_wan_active_sessions[10m]))"),
        "interface",
    )
    speeds = site.get("wan_speeds") or {}
    preferred = site.get("sla", "")
    out = []
    for name in names:
        checks = {sla: v for (i, sla), v in status.items() if i == name}
        down = None if not checks else max(checks.values()) < 1
        out.append(
            {
                "rule": "line_down",
                "subject": name,
                "bad": down,
                "text": "No health check has reported " + name + " alive for 2 minutes.",
            }
        )
        full = ""
        speed = speeds.get(name)
        if speed and (name,) in tx and tx[(name,)] >= 0.9 * speed["up_mbps"]:
            full = f" Upload {tx[(name,)]:.1f} of {speed['up_mbps']:g} Mbps: the line is full."
        elif speed and (name,) in rx and rx[(name,)] >= 0.9 * speed["down_mbps"]:
            full = f" Download {rx[(name,)]:.1f} of {speed['down_mbps']:g} Mbps: the line is full."
        sla, lost = _judged(loss, name, preferred)
        _, late = _judged(delay, name, sla)
        usual = delay_baseline.get((name, sla))
        slow = None
        text = ""
        if down is False and lost is not None:
            slow = False
            if lost >= SLOW_LOSS_PCT:
                slow, text = True, f"Packet loss {lost:.0f}% for 10 minutes on health check {sla}."
            elif (
                late is not None
                and usual
                and late >= max(SLOW_DELAY_FACTOR * usual, SLOW_DELAY_FLOOR_MS)
            ):
                slow, text = (
                    True,
                    f"Delay {late:.0f} ms on health check {sla}, {late / usual:.1f} times its usual {usual:.0f} ms.",
                )
        out.append(
            {"rule": "line_slow", "subject": name, "bad": slow, "text": (text + full).strip()}
        )
        base = traffic_baseline.get(name)
        starved = None
        if at_work and down is False and base and (name,) in rx and (name,) in sessions:
            usual_rx, usual_sessions = base
            if usual_rx >= STARVED_MIN_MBPS:
                starved = (
                    rx[(name,)] <= STARVED_SHARE * usual_rx
                    and sessions[(name,)] >= STARVED_SESSIONS * usual_sessions
                )
        out.append(
            {
                "rule": "line_starved",
                "subject": name,
                "bad": starved,
                "text": (
                    f"Download {rx.get((name,), 0):.1f} Mbps, usually {base[0]:.1f} at this hour, "
                    f"while {sessions.get((name,), 0):,.0f} sessions are open (usually {base[1]:,.0f}). "
                    "People are trying; the line is not delivering."
                )
                if base
                else "",
            }
        )
        # Summary-only: listed in the next daily summary, never sent on their own.
        warn = site.get("wan_loss_warning_pct", 5)
        out.append(
            {
                "rule": "line_loss",
                "subject": name,
                "bad": None if down is not False or lost is None else lost >= warn,
                "text": f"Packet loss {lost or 0:.1f}% on health check {sla}.",
            }
        )
        busy = None
        if speed and down is False and (name,) in rx and (name,) in tx:
            busy = rx[(name,)] >= FULL_SHARE * speed["down_mbps"] or (
                tx[(name,)] >= FULL_SHARE * speed["up_mbps"]
            )
        out.append(
            {
                "rule": "line_full",
                "subject": name,
                "bad": busy,
                "text": full.strip()
                or f"Download {rx.get((name,), 0):.1f}, upload {tx.get((name,), 0):.1f} Mbps.",
            }
        )
        states = set(v >= 1 for v in checks.values())
        out.append(
            {
                "rule": "checks_disagree",
                "subject": name,
                "bad": None if len(checks) < 2 else len(states) > 1,
                "text": "Its health checks give opposite answers: "
                + ", ".join(f"{k} {'up' if v >= 1 else 'down'}" for k, v in sorted(checks.items()))
                + ". This is a check problem, not an outage.",
            }
        )
    return out


def device_signals(live, site, open_subjects):
    """One device using half of a line's speed or more, from FortiGate live estimates.

    Devices that stop qualifying, or vanish from the live list, are signalled
    back to normal so their alerts close. No live reading leaves them unknown.
    """
    speeds = site.get("wan_speeds") or {}
    if not isinstance(live, dict) or not isinstance(live.get("devices"), list) or not speeds:
        return []
    out, seen = [], set()
    for device in live["devices"]:
        name = device.get("hostname") or device.get("ip") or device.get("mac") or "unknown device"
        for line, rate in (device.get("lines") or {}).items():
            speed = speeds.get(line)
            if not speed:
                continue
            down = rate.get("download_bps", 0) / 1e6
            up = rate.get("upload_bps", 0) / 1e6
            share = max(down / speed["down_mbps"], up / speed["up_mbps"])
            if share < HEAVY_SHARE:
                continue
            subject = line + " · " + name
            seen.add(subject)
            way = "upload" if up / speed["up_mbps"] >= down / speed["down_mbps"] else "download"
            used = up if way == "upload" else down
            limit = speed["up_mbps"] if way == "upload" else speed["down_mbps"]
            out.append(
                {
                    "rule": "heavy_device",
                    "subject": subject,
                    "bad": True,
                    "text": f"{name} uses {used:.1f} of {limit:g} Mbps {way} on {line} "
                    f"({100 * used / limit:.0f}%). FortiGate live estimate.",
                }
            )
    for subject in open_subjects - seen:
        out.append({"rule": "heavy_device", "subject": subject, "bad": False, "text": ""})
    return out


def delay_baselines(query):
    """Each check's usual delay on each line: the median of the last 7 days."""
    return _by(
        query("quantile_over_time(0.5, fortigate_virtual_wan_latency_seconds[7d]) * 1000"),
        "interface",
        "sla",
    )


def traffic_baselines(query_range, now, zone, c, names):
    """Per line, the median download and sessions at this hour on past working days."""
    hour = datetime.fromtimestamp(now, zone).hour
    out = {}
    start, end = now - 7 * 86400, now - 3600
    for name in names:
        series = {}
        for metric in ("bandwidth_rx_byte_per_second", "active_sessions"):
            expr = f'max(fortigate_virtual_wan_{metric}{{interface="{name}"}})'
            points = query_range(expr, start, end, 300)
            series[metric] = {int(t): float(v) for t, v in points}
        picks = [
            t
            for t in series["bandwidth_rx_byte_per_second"]
            if datetime.fromtimestamp(t, zone).hour == hour and working(c, t, zone)
        ]
        if len(picks) >= 12:
            out[name] = (
                statistics.median(series["bandwidth_rx_byte_per_second"][t] for t in picks)
                * 8
                / 1e6,
                statistics.median(series["active_sessions"].get(t, 0) for t in picks),
            )
    return out


def service_signals(*, fortigate_up, log_age, lag, disk_free_share, db_share):
    """Collection, logs, accounting and storage, each None when unknown."""
    return [
        {
            "rule": "fortigate_unreachable",
            "subject": "",
            "bad": None if fortigate_up is None else not fortigate_up,
            "text": "The FortiGate API has not answered for 5 minutes. This is not reported as a line outage.",
        },
        {
            "rule": "logs_stopped",
            "subject": "",
            "bad": None if log_age is None else log_age > LOG_SILENCE_SECONDS,
            "text": f"No FortiGate log reached the server for {duration(log_age or 0)}.",
        },
        {
            "rule": "accounting_behind",
            "subject": "",
            "bad": None if lag is None else lag > ACCOUNTING_LAG_SECONDS,
            "text": f"Usage processing trails the clock by {duration(lag or 0)}. Logs are kept; reports read low until it catches up.",
        },
        {
            "rule": "storage_full",
            "subject": "",
            "bad": None
            if disk_free_share is None and db_share is None
            else bool(
                (disk_free_share is not None and disk_free_share < STORAGE_FREE_SHARE)
                or (db_share is not None and db_share > STORAGE_DB_SHARE)
            ),
            "text": (
                f"Disk {100 * (1 - (disk_free_share or 0)):.0f}% used; accounting database at "
                f"{100 * (db_share or 0):.0f}% of its limit."
            ),
        },
    ]


def sdwan_signal(alerts, routing):
    """One event when the SD-WAN rules differ from the last ones read."""
    if not isinstance(routing, dict) or routing.get("status") != "OBSERVED":
        return []
    rules = [
        (r.get("name"), r.get("mode"), tuple(r.get("lines") or []))
        for r in routing.get("rules", [])
    ]
    previous = alerts.state.get("sdwan")
    alerts.state["sdwan"] = [list(r[:2]) + [list(r[2])] for r in rules]
    if previous is None:
        return []
    before = {r[0]: (r[1], tuple(r[2])) for r in previous}
    after = {r[0]: (r[1], r[2]) for r in rules}
    changes = [f"added {n}" for n in after if n not in before]
    changes += [f"removed {n}" for n in before if n not in after]
    changes += [
        f"{n} now uses {', '.join(after[n][1]) or 'no line'}"
        for n in after
        if n in before and after[n][1] != before[n][1]
    ]
    changes += [
        f"{n} mode {before[n][0]} → {after[n][0]}"
        for n in after
        if n in before and after[n][0] != before[n][0]
    ]
    if not changes and [r[0] for r in previous] != [r[0] for r in rules]:
        changes = ["rules were reordered"]
    if not changes:
        return []
    return [
        {
            "rule": "sdwan_changed",
            "subject": "",
            "event": True,
            "bad": True,
            "text": "; ".join(changes)[:600]
            + ". Internet links compares the lines before and after.",
        }
    ]


# Reports: when each is due, the per-line provider report, and the messages.


def report_markers(now, zone):
    local = datetime.fromtimestamp(now, zone)
    return {
        "daily": local.date().isoformat(),
        "weekly": local.date().isoformat(),
        "monthly": local.strftime("%Y-%m"),
    }


def due_reports(c, now, zone, sent):
    """Reports whose time arrived within the last hour and that were not sent yet.

    The daily summary goes on working days only. A report missed for longer
    than the window (the service was down) is skipped, not sent late.
    """
    local = datetime.fromtimestamp(now, zone)
    markers, due = report_markers(now, zone), []
    for kind, r in c["reports"].items():
        if not r["enabled"] or sent.get(kind) == markers[kind]:
            continue
        if kind == "daily" and DAYS[local.weekday()] not in c["work_hours"]["days"]:
            continue
        if kind == "weekly" and DAYS[local.weekday()] != r["day"]:
            continue
        if kind == "monthly" and local.day != 1:
            continue
        hour, minute = map(int, r["time"].split(":"))
        at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if 0 <= (local - at).total_seconds() < REPORT_WINDOW_SECONDS:
            due.append(kind)
    return due


def report_window(kind, now, zone, last_daily=None):
    """Whole local days the report covers, as ``(start, end)`` datetimes."""
    today = datetime.fromtimestamp(now, zone).replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "daily":
        start = today - timedelta(days=1)
        if last_daily:
            previous = datetime.fromtimestamp(last_daily, zone).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            # After a weekend the summary covers every day since the last one.
            start = min(start, max(previous, today - timedelta(days=4)))
        return start, today
    if kind == "weekly":
        return today - timedelta(days=7), today
    end = today.replace(day=1)
    return (end - timedelta(days=1)).replace(day=1), end


def isp_lines(query_range, names, start, end, preferred, *, step=300):
    """Per line, for its provider: time it worked, outages and degraded periods.

    Down means no health check reported the line alive. Loss is the configured
    check when it covers the line, otherwise the line's best check, as on the
    Internet links page. Intervals without samples count as unobserved.
    """
    out = []
    window = f"[{step}s]"
    for name in names:
        where = 'interface="' + name + '"'
        alive = query_range(
            'max(fortigate_virtual_wan_status{state="up",' + where + "})", start, end, step
        )
        loss = query_range(
            "avg_over_time(fortigate_virtual_wan_packet_loss_ratio{"
            + where
            + ',sla="'
            + preferred
            + '"}'
            + window
            + ") * 100",
            start,
            end,
            step,
        ) or query_range(
            "min(avg_over_time(fortigate_virtual_wan_packet_loss_ratio{"
            + where
            + "}"
            + window
            + ")) * 100",
            start,
            end,
            step,
        )
        delay = query_range(
            "min(avg_over_time(fortigate_virtual_wan_latency_seconds{"
            + where
            + "}"
            + window
            + ")) * 1000",
            start,
            end,
            step,
        )
        values = {int(float(t)): float(v) for t, v in alive}
        lost = {int(float(t)): float(v) for t, v in loss}
        late = {int(float(t)): float(v) for t, v in delay}
        times = sorted(values)
        expected = max(1, int((end - start) // step))

        def runs(flag, minimum):
            found, first, last = [], None, None
            for t in times:
                if not flag(t):
                    continue
                if first is not None and t - last > step:
                    if last - first + step >= minimum:
                        found.append((first, last + step))
                    first = None
                if first is None:
                    first = t
                last = t
            if first is not None and last - first + step >= minimum:
                found.append((first, last + step))
            return found

        outages = runs(lambda t: values[t] < 1, 2 * 60)
        degraded = runs(lambda t: values[t] >= 1 and lost.get(t, 0) >= 5, 20 * 60)
        up = [t for t in times if values[t] >= 1]
        up_loss = [lost[t] for t in up if t in lost]
        up_delay = [late[t] for t in up if t in late]
        out.append(
            {
                "interface": name,
                "observed_pct": 100 * len(times) / expected,
                "up_pct": 100 * len(up) / len(times) if times else None,
                "avg_loss_pct": statistics.fmean(up_loss) if up_loss else None,
                "avg_delay_ms": statistics.fmean(up_delay) if up_delay else None,
                "outages": [{"start": a, "end": b} for a, b in outages],
                "degraded": [
                    {
                        "start": a,
                        "end": b,
                        "worst_loss_pct": max(lost.get(t, 0) for t in times if a <= t < b),
                        "avg_delay_ms": statistics.fmean(
                            [late[t] for t in times if a <= t < b and t in late] or [0]
                        ),
                    }
                    for a, b in degraded
                ],
            }
        )
    return out


def _gb(value):
    return f"{(value or 0) / 1e9:,.1f} GB"


def _when(stamp, zone):
    return datetime.fromtimestamp(stamp, zone).strftime("%a %d %b %H:%M")


def _segment(row):
    vlans = row.get("vlans") or []
    v = row.get("vlan") or (vlans[0] if vlans else {})
    name = v.get("name") or v.get("interface") or ""
    if v.get("vlan_id") is not None:
        return f"VLAN {v['vlan_id']} · {name}".strip(" ·")
    return name or v.get("key") or "segment"


def _device(row):
    return row.get("hostname") or row.get("ip") or row.get("mac") or row.get("device_id", "")[:12]


TITLES = {
    "daily": "Daily summary",
    "weekly": "Weekly network report",
    "monthly": "Monthly network report",
}


def _line_rows(lines):
    rows = []
    for line in lines:
        speed = line.get("speed") or {}
        if speed.get("busy_down_pct") is not None and speed.get("busy_up_pct") is not None:
            busy = f"{speed['busy_down_pct']:.0f}% / {speed['busy_up_pct']:.0f}%"  # already percent
        elif "speed" not in line:
            busy = "not measured"
        else:
            busy = "speed not set"
        loss = line.get("avg_loss_pct")
        sessions = line.get("avg_sessions")
        rows.append(
            [
                line["interface"],
                "unknown" if loss is None else f"{loss:.1f}%",
                "unknown" if line.get("incidents") is None else str(line["incidents"]),
                busy,
                "unknown" if sessions is None else f"{sessions:,.0f}",
            ]
        )
    return rows


def _daily_lines(data):
    per_line = {}
    for d in data.get("digest", []):
        if d["kind"] not in ("start", "event"):
            continue
        rule, _, subject = d["key"].partition(":")
        label = RULES[rule][0] if rule in RULES else rule
        line = subject.split(" · ")[0] or "Installation"
        found = per_line.setdefault(line, {})
        found[label] = found.get(label, 0) + 1
    text = [
        line + ": " + ", ".join(f"{label.lower()} {n}×" for label, n in sorted(found.items()))
        for line, found in sorted(per_line.items())
    ] or ["No line or service trouble was recorded."]
    fine = [n for n in data.get("line_names", []) if n not in per_line]
    if per_line and fine:
        text.append(", ".join(fine) + ": fine")
    return text


def _isp_section(line, zone):
    facts = [
        f"worked {line['up_pct']:.2f}% of observed time"
        if line["up_pct"] is not None
        else "no samples",
        f"observed {line['observed_pct']:.0f}% of the period",
    ]
    if line["avg_loss_pct"] is not None:
        facts.append(f"average loss {line['avg_loss_pct']:.2f}%")
    if line["avg_delay_ms"] is not None:
        facts.append(f"average delay {line['avg_delay_ms']:.0f} ms")
    rows = [
        ["Down", _when(o["start"], zone), duration(o["end"] - o["start"]), "", ""]
        for o in line["outages"]
    ]
    rows += [
        [
            "Degraded",
            _when(p["start"], zone),
            duration(p["end"] - p["start"]),
            f"{p['worst_loss_pct']:.0f}%",
            f"{p['avg_delay_ms']:.0f} ms",
        ]
        for p in line["degraded"]
    ]
    body = ["; ".join(facts).capitalize() + "."]
    if not line["observed_pct"]:
        body.append("Nothing can be said about this line for the period: no samples were kept.")
    elif not rows:
        body.append("No outage or degraded period.")
    table = [["", "From", "Lasted", "Worst loss", "Delay"]] + rows[:40] if rows else None
    return "Provider report · " + line["interface"], body, table


def render_report(kind, data, zone):
    """``{"subject", "text", "html"}`` for a report. Missing parts say so; nothing is invented."""
    start, end = data["start"], data["end"]
    days = start.strftime("%a %d %b")
    if (end - start).days > 1:
        days += " to " + (end - timedelta(days=1)).strftime("%a %d %b")
    title = TITLES[kind]
    subject = f"FortiLens · {title} · {days}"
    sections = []  # (heading or None, paragraphs, table rows with a header row, or None)
    if kind == "daily":
        sections.append(("Lines and services", _daily_lines(data), None))
        sections.append((None, [f"Alerts sent: {data.get('alerts_sent', 0)}"], None))
    else:
        rows = _line_rows(data.get("lines", []))
        note = (
            "Loss on the judging health check. Incidents: trouble for 20 minutes or longer. "
            "Busy: download / upload in the busiest 5% of the period, against the line's speed."
        )
        sections.append(
            (
                "Internet lines",
                [note] if rows else ["Line health is unavailable for this period."],
                [["Line", "Avg loss", "Incidents", "Busy use", "Sessions"]] + rows
                if rows
                else None,
            )
        )
    usage = data.get("usage")
    if usage is not None and not usage.get("devices"):
        # No records is not zero usage: the records may not reach back that far.
        sections.append(
            (
                "Usage",
                ["No usage was recorded in this period; it is unknown, not zero."],
                None,
            )
        )
    elif usage:
        totals = usage.get("totals") or {}
        devices = usage.get("devices") or []
        count = usage.get("observed_endpoint_identities", len(devices))
        sections.append(
            (
                "Usage",
                [
                    f"{_gb(totals.get('download_bytes'))} down, "
                    f"{_gb(totals.get('upload_bytes'))} up, {count} devices."
                ],
                None,
            )
        )
        top = devices[: 5 if kind == "daily" else 10]
        table = [["Device", "Download", "Upload"]] + [
            [_device(d), _gb(d.get("download_bytes")), _gb(d.get("upload_bytes"))] for d in top
        ]
        sections.append(
            ("Top devices", [] if top else ["No recorded usage."], table if top else None)
        )
        if kind != "daily" and usage.get("vlans"):
            table = [["Segment", "Download", "Upload"]] + [
                [_segment(v), _gb(v.get("download_bytes")), _gb(v.get("upload_bytes"))]
                for v in usage["vlans"][:8]
            ]
            sections.append(("Segments", [], table))
    else:
        sections.append(("Usage", ["Recorded usage is unavailable for this period."], None))
    apps = data.get("applications")
    if kind != "daily" and apps:
        named = [a for a in apps if a.get("app") and not a.get("generic")][:6]
        unnamed = sum(a.get("total_bytes", 0) for a in apps if not a.get("app") or a.get("generic"))
        rows = [[a["app"], _gb(a.get("total_bytes"))] for a in named]
        if unnamed:
            rows.append(["Encrypted or unnamed traffic", _gb(unnamed)])
        sections.append(("Applications", [], [["Application", "Total"]] + rows))
    new = data.get("new_devices")
    if new is not None:
        table = [["Device", "Segment", "First seen"]] + [
            [d["name"], d["segment"], d["day"]] for d in new[:20]
        ]
        sections.append(
            (
                "New devices",
                [f"{len(new)} seen for the first time."] if new else ["None."],
                table if new else None,
            )
        )
    for line in data.get("isp", []):
        sections.append(_isp_section(line, zone))
    footer = (
        "Usage counts only sessions the FortiGate logs. "
        "Missing data is shown as missing, never as zero."
    )

    text = [subject.removeprefix("FortiLens · "), data.get("site", "")]
    for heading, body, table in sections:
        text.append("")
        if heading:
            text.append(heading)
        text += body
        for row in (table or [])[1:]:
            text.append("- " + " · ".join(x for x in row if x))
    text += ["", footer]

    def cell(tag, value):
        style = "padding:6px 10px 6px 0;border-bottom:1px solid #dce1e0;text-align:left"
        return f'<{tag} style="{style}">{html.escape(str(value))}</{tag}>'

    parts = [
        '<div style="font-family:Segoe UI,Arial,sans-serif;color:#14191b;max-width:680px">',
        f'<h1 style="font-size:20px;margin:0 0 4px">{html.escape(title)}</h1>',
        '<p style="color:#4a5559;margin:0 0 16px">'
        + html.escape(data.get("site", "") + " · " + days)
        + "</p>",
    ]
    for heading, body, table in sections:
        if heading:
            parts.append(
                f'<h2 style="font-size:15px;margin:18px 0 6px">{html.escape(heading)}</h2>'
            )
        parts += [f'<p style="margin:0 0 6px">{html.escape(b)}</p>' for b in body]
        if table:
            parts.append('<table style="border-collapse:collapse;font-size:13px;width:100%">')
            parts.append("<tr>" + "".join(cell("th", h) for h in table[0]) + "</tr>")
            parts += ["<tr>" + "".join(cell("td", v) for v in row) + "</tr>" for row in table[1:]]
            parts.append("</table>")
    parts.append(
        f'<p style="color:#4a5559;font-size:12px;margin-top:20px">{html.escape(footer)}</p></div>'
    )
    return {"subject": subject, "text": "\n".join(text).strip(), "html": "".join(parts)}


def deliver_report(c, secrets, kind, report, *, send=None, **kwargs):
    """The daily summary prefers Telegram; the weekly and monthly reports prefer email."""
    send = send or globals()["send"]
    order = ("telegram", "email") if kind == "daily" else ("email", "telegram")
    channel = next((ch for ch in order if c[ch]["enabled"]), None)
    if channel is None:
        return {"channel": None, "status": "failed", "error": "not_configured"}
    text = report["text"]
    if channel == "telegram" and len(text) > 3900:
        text = text[:3900] + "\n…"
    body = report["html"] if channel == "email" else None
    try:
        send(c, secrets, channel, report["subject"], text, html=body, **kwargs)
        return {"channel": channel, "status": "delivered"}
    except DeliveryError as exc:
        return {"channel": channel, "status": "failed", "error": exc.code}
