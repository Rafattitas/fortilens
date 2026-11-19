"""Alerts: settings, the open/close lifecycle, signals from metrics and delivery."""

from datetime import datetime
import http.client
import json
from pathlib import Path
import smtplib
import sys
import tempfile
import unittest
import urllib.error
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import notifications as n  # noqa: E402

TOKEN = "123456789:" + "A" * 35
try:
    CAIRO = ZoneInfo("Africa/Cairo")
except ZoneInfoNotFoundError:
    # Windows Python ships no IANA data unless tzdata is installed.
    raise unittest.SkipTest("IANA timezone database unavailable") from None


def settings(**parts):
    return n.validate(parts)


def vector(*rows):
    return [{"metric": metric, "value": [0, str(value)]} for metric, value in rows]


class SettingsTests(unittest.TestCase):
    def test_defaults_are_off_and_complete(self):
        c = settings()
        self.assertFalse(n.configured(c))
        self.assertEqual(set(c["rules"]), set(n.RULES))
        self.assertTrue(all(c["rules"].values()))
        self.assertEqual(c["work_hours"]["days"], ["mon", "tue", "wed", "thu", "sun"])

    def test_channels_need_their_destination(self):
        self.assertTrue(n.configured(settings(telegram={"enabled": True, "chat_id": "-1001234"})))
        for bad in (
            {"telegram": {"enabled": True}},
            {"telegram": {"chat_id": "not a chat"}},
            {"email": {"enabled": True, "host": "smtp.example.com"}},
            {"email": {"to": ["not-an-address"]}},
            {"email": {"security": "plain"}},
            {"rules": {"made_up": True}},
            {"work_hours": {"start": "18:00", "end": "08:00"}},
            {"work_hours": {"days": ["someday"]}},
            {"extra": {}},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                n.validate(bad)

    def test_working_hours_follow_the_given_timezone(self):
        c = settings()
        sunday_morning = datetime(2026, 11, 15, 9, 0, tzinfo=CAIRO).timestamp()
        friday_morning = datetime(2026, 11, 20, 9, 0, tzinfo=CAIRO).timestamp()
        sunday_night = datetime(2026, 11, 15, 22, 0, tzinfo=CAIRO).timestamp()
        self.assertTrue(n.working(c, sunday_morning, CAIRO))
        self.assertFalse(n.working(c, friday_morning, CAIRO))
        self.assertFalse(n.working(c, sunday_night, CAIRO))


class LifecycleTests(unittest.TestCase):
    def signal(self, bad, rule="line_slow", subject="internal3", text="Packet loss 24%."):
        return {"rule": rule, "subject": subject, "bad": bad, "text": text}

    def test_opens_after_hold_reminds_and_closes_after_recovery(self):
        alerts, c = n.Alerts(None), settings()
        kinds = []
        for minute in range(0, 10 * 60 + 1, 60):
            kinds += [m["kind"] for m in alerts.step(minute, [self.signal(True)], c)]
        self.assertEqual(kinds, ["start"])  # held 10 minutes before opening
        self.assertEqual(len(alerts.view()["open"]), 1)
        # No evidence keeps the alert as it is; it neither closes nor reminds.
        self.assertEqual(alerts.step(700, [self.signal(None)], c), [])
        reminder = alerts.step(600 + n.REMINDER_SECONDS, [self.signal(True)], c)
        self.assertEqual([m["kind"] for m in reminder], ["reminder"])
        self.assertIn("2 h 10 min so far", reminder[0]["text"])  # since the trouble began
        t = 600 + n.REMINDER_SECONDS + 60
        self.assertEqual(alerts.step(t, [self.signal(False)], c), [])
        end = alerts.step(t + n.RECOVERY_SECONDS, [self.signal(False)], c)
        self.assertEqual([m["kind"] for m in end], ["end"])
        self.assertEqual(end[0]["title"], "Recovered · internal3")
        self.assertEqual(alerts.view()["open"], [])

    def test_short_trouble_and_flapping_do_not_alert(self):
        alerts, c = n.Alerts(None), settings()
        out = []
        for minute, bad in enumerate([True] * 5 + [False] + [True] * 5):
            out += alerts.step(minute * 60, [self.signal(bad)], c)
        self.assertEqual(out, [])

    def test_quiet_outside_working_hours_but_critical_alerts_still_go(self):
        alerts, c = n.Alerts(None), settings()
        slow, down = [], []
        for t in range(0, 601, 60):
            slow += alerts.step(t, [self.signal(True)], c, at_work=False)
            down += alerts.step(t, [self.signal(True, "line_down")], c, at_work=False)
        self.assertEqual([(m["kind"], m["held"]) for m in slow], [("start", True)])
        self.assertEqual([(m["kind"], m["held"]) for m in down], [("start", False)])

    def test_a_disabled_rule_is_dropped(self):
        alerts = n.Alerts(None)
        c = settings(rules={"line_slow": False})
        for t in (0, 600, 1200):
            self.assertEqual(alerts.step(t, [self.signal(True)], c), [])
        self.assertEqual(alerts.view()["open"], [])

    def test_state_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "alerts.json"
            alerts, c = n.Alerts(path), settings()
            alerts.step(0, [self.signal(True, "line_down")], c)
            alerts.step(120, [self.signal(True, "line_down")], c)
            alerts.save()
            again = n.Alerts(path)
            self.assertEqual(len(again.view()["open"]), 1)
            self.assertEqual(again.step(180, [self.signal(True, "line_down")], c), [])


class DeliveryTests(unittest.TestCase):
    def test_deliver_records_each_channel_and_email_only_for_critical(self):
        c = settings(
            telegram={"enabled": True, "chat_id": "-1001234"},
            email={
                "enabled": True,
                "host": "smtp.example.com",
                "sender": "a@example.com",
                "to": ["b@example.com"],
            },
        )
        sent = []

        def send(c, secrets, channel, subject, text, **kwargs):
            sent.append((channel, subject))
            if channel == "email":
                raise n.DeliveryError("email_auth")

        alerts = n.Alerts(None)
        messages = [
            {
                "at": 1,
                "key": "line_down:wan2",
                "kind": "start",
                "title": "Line down · wan2",
                "text": "x",
                "held": False,
            },
            {
                "at": 2,
                "key": "line_slow:wan2",
                "kind": "start",
                "title": "Slow",
                "text": "y",
                "held": False,
            },
            {
                "at": 3,
                "key": "line_slow:wan1",
                "kind": "start",
                "title": "Held",
                "text": "z",
                "held": True,
            },
        ]
        n.deliver(c, {}, messages, alerts, send=send)
        self.assertEqual([s[0] for s in sent], ["telegram", "email", "telegram"])
        history = alerts.view()["history"]
        self.assertEqual(history[0]["deliveries"], [])  # held: recorded, not sent
        self.assertEqual(
            history[2]["deliveries"][1],
            {"channel": "email", "status": "failed", "error": "email_auth"},
        )

    def test_alerts_no_channel_carries_are_held_and_every_alert_reaches_the_summary(self):
        email_only = settings(
            email={
                "enabled": True,
                "host": "smtp.example.com",
                "sender": "a@example.com",
                "to": ["b@example.com"],
            }
        )
        sent, alerts = [], n.Alerts(None)
        messages = [
            {
                "at": 1,
                "key": "line_down:wan2",
                "kind": "start",
                "title": "Down",
                "text": "x",
                "held": False,
            },
            {
                "at": 2,
                "key": "line_slow:wan2",
                "kind": "start",
                "title": "Slow",
                "text": "y",
                "held": False,
            },
        ]
        n.deliver(email_only, {}, messages, alerts, send=lambda c, s, ch, *a, **k: sent.append(ch))
        self.assertEqual(sent, ["email"])  # email carries the critical alert only
        history = alerts.view()["history"]
        self.assertEqual((history[0]["held"], history[0]["deliveries"]), (True, []))
        self.assertFalse(history[1]["held"])
        # The daily summary lists both: the one sent and the one held.
        self.assertEqual(
            [d["key"] for d in alerts.digest(0, 10)], ["line_down:wan2", "line_slow:wan2"]
        )

    def test_an_unexpected_failure_on_one_channel_is_recorded_and_others_still_go(self):
        c = settings(
            telegram={"enabled": True, "chat_id": "-1001234"},
            email={
                "enabled": True,
                "host": "smtp.example.com",
                "sender": "a@example.com",
                "to": ["b@example.com"],
            },
        )
        sent = []

        def send(c, secrets, channel, *args, **kwargs):
            if channel == "telegram":
                raise http.client.IncompleteRead(b"")
            sent.append(channel)

        alerts = n.Alerts(None)
        message = {
            "at": 1,
            "key": "line_down:wan2",
            "kind": "start",
            "title": "Down",
            "text": "x",
            "held": False,
        }
        n.deliver(c, {}, [message], alerts, send=send)
        self.assertEqual(sent, ["email"])
        self.assertEqual(
            alerts.view()["history"][0]["deliveries"][0],
            {"channel": "telegram", "status": "failed", "error": "telegram_unreachable"},
        )

    def test_telegram_errors_never_carry_the_token(self):
        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, limit):
                return self.body

        class Opener:
            def __init__(self, result):
                self.result, self.requests = result, []

            def open(self, request, timeout):
                self.requests.append(request)
                if isinstance(self.result, Exception):
                    raise self.result
                return Response(self.result)

        ok = Opener(json.dumps({"ok": True}).encode())
        n.send_telegram(TOKEN, "-1001234", "hello", opener=ok)
        with self.assertRaises(n.DeliveryError) as broken:
            n.send_telegram(
                TOKEN, "-1001234", "hello", opener=Opener(http.client.IncompleteRead(b""))
            )
        self.assertEqual(broken.exception.code, "telegram_unreachable")
        self.assertIn(b"chat_id=-1001234", ok.requests[0].data)
        for result, code in (
            (
                urllib.error.HTTPError("https://api.telegram.org", 401, "Unauthorized", {}, None),
                "telegram_rejected",
            ),
            (OSError("network down"), "telegram_unreachable"),
            (json.dumps({"ok": False}).encode(), "telegram_rejected"),
        ):
            with self.subTest(code=code), self.assertRaises(n.DeliveryError) as raised:
                n.send_telegram(TOKEN, "-1001234", "hello", opener=Opener(result))
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn(TOKEN, str(raised.exception))
        with self.assertRaises(n.DeliveryError) as raised:
            n.send_telegram("", "-1001234", "hello")
        self.assertEqual(raised.exception.code, "not_configured")

    def test_email_maps_server_answers_to_fixed_codes(self):
        c = settings(
            email={
                "enabled": True,
                "host": "smtp.example.com",
                "user": "fl",
                "sender": "a@example.com",
                "to": ["b@example.com"],
            }
        )

        class Client:
            fail = None

            def __init__(self, *args, **kwargs):
                self.sent = []

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def starttls(self, context):
                pass

            def login(self, user, password):
                if Client.fail:
                    raise Client.fail

            def send_message(self, message):
                Client.message = message

        class Smtp:
            SMTP = Client
            SMTP_SSL = Client

        n.send_email(c, "secret", "Subject", "Body", smtp=Smtp)
        self.assertEqual(Client.message["To"], "b@example.com")
        Client.fail = smtplib.SMTPAuthenticationError(535, b"no")
        with self.assertRaises(n.DeliveryError) as raised:
            n.send_email(c, "secret", "Subject", "Body", smtp=Smtp)
        self.assertEqual(raised.exception.code, "email_auth")

    def test_secret_file_keeps_only_known_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "notify"
            path.write_text(json.dumps({"telegram_bot_token": TOKEN, "other": "x"}))
            self.assertEqual(n.read_secrets(path), {"telegram_bot_token": TOKEN})
            self.assertEqual(n.read_secrets(Path(folder) / "missing"), {})


class SignalTests(unittest.TestCase):
    site = {
        "confirmed_wan_interfaces": ["internal3", "wan2"],
        "sla": "google",
        "wan_speeds": {"internal3": {"down_mbps": 35, "up_mbps": 5}},
    }

    def query(self, overrides=None):
        data = {
            'up{job="fortigate"}': vector(({"job": "fortigate"}, 1)),
            'fortigate_virtual_wan_status{state="up"}': vector(
                ({"interface": "internal3", "sla": "google"}, 1),
                ({"interface": "internal3", "sla": "ping1"}, 1),
                ({"interface": "wan2", "sla": "google"}, 0),
                ({"interface": "wan2", "sla": "ping1"}, 0),
            ),
            "loss": vector(
                ({"interface": "internal3", "sla": "google"}, 24),
                ({"interface": "internal3", "sla": "ping1"}, 0.5),
            ),
            "delay": vector(
                ({"interface": "internal3", "sla": "google"}, 200),
                ({"interface": "internal3", "sla": "ping1"}, 40),
            ),
            "rx": vector(({"interface": "internal3"}, 3.0)),
            "tx": vector(({"interface": "internal3"}, 5.0)),
            "sessions": vector(({"interface": "internal3"}, 2000)),
        }
        data.update(overrides or {})

        def query(expr):
            for key in ("loss", "delay", "rx", "tx", "sessions"):
                if {
                    "loss": "packet_loss",
                    "delay": "latency",
                    "rx": "_rx_",
                    "tx": "_tx_",
                    "sessions": "sessions",
                }[key] in expr:
                    return data[key]
            return data.get(expr, [])

        return query

    def signals(self, query, **kwargs):
        options = {"delay_baseline": {}, "traffic_baseline": {}, "at_work": True, **kwargs}
        return {(s["rule"], s["subject"]): s for s in n.line_signals(query, self.site, **options)}

    def test_down_only_when_no_check_reports_alive(self):
        s = self.signals(self.query())
        self.assertIs(s[("line_down", "wan2")]["bad"], True)
        self.assertIs(s[("line_down", "internal3")]["bad"], False)

    def test_failed_collection_is_unknown_not_down(self):
        s = self.signals(self.query({'up{job="fortigate"}': vector(({"job": "fortigate"}, 0))}))
        self.assertTrue(all(v["bad"] is None for v in s.values()))

    def test_slow_by_loss_on_the_configured_check_and_names_a_full_line(self):
        s = self.signals(self.query())[("line_slow", "internal3")]
        self.assertIs(s["bad"], True)
        self.assertIn("Packet loss 24%", s["text"])
        self.assertIn("Upload 5.0 of 5 Mbps", s["text"])

    def test_slow_by_delay_against_the_line_usual(self):
        q = self.query({"loss": vector(({"interface": "internal3", "sla": "google"}, 1))})
        calm = self.signals(q, delay_baseline={("internal3", "google"): 180})[
            ("line_slow", "internal3")
        ]
        self.assertIs(calm["bad"], False)  # 200 ms is not 3 times a usual 180 ms
        slow = self.signals(q, delay_baseline={("internal3", "google"): 50})[
            ("line_slow", "internal3")
        ]
        self.assertIs(slow["bad"], True)
        self.assertIn("4.0 times its usual 50 ms", slow["text"])

    def test_little_traffic_compares_with_the_line_usual_and_only_at_work(self):
        q = self.query()
        base = {"internal3": (20.0, 1800)}
        self.assertIs(
            self.signals(q, traffic_baseline=base)[("line_starved", "internal3")]["bad"], True
        )
        self.assertIsNone(
            self.signals(q, traffic_baseline=base, at_work=False)[("line_starved", "internal3")][
                "bad"
            ]
        )
        self.assertIs(
            self.signals(q, traffic_baseline={"internal3": (8.0, 1800)})[
                ("line_starved", "internal3")
            ]["bad"],
            False,
        )
        self.assertIsNone(self.signals(q)[("line_starved", "internal3")]["bad"])  # no usual yet

    def test_service_signals_are_unknown_without_evidence(self):
        unknown = n.service_signals(
            fortigate_up=None, log_age=None, lag=None, disk_free_share=None, db_share=None
        )
        self.assertTrue(all(s["bad"] is None for s in unknown))
        bad = {
            s["rule"]: s["bad"]
            for s in n.service_signals(
                fortigate_up=False, log_age=1200, lag=2000, disk_free_share=0.05, db_share=0.2
            )
        }
        self.assertEqual(
            bad,
            {
                "fortigate_unreachable": True,
                "logs_stopped": True,
                "accounting_behind": True,
                "storage_full": True,
            },
        )

    def test_sdwan_change_is_one_event_after_the_first_reading(self):
        alerts = n.Alerts(None)
        rules = {
            "status": "OBSERVED",
            "rules": [{"name": "geust", "mode": "manual", "lines": ["internal3"]}],
        }
        self.assertEqual(n.sdwan_signal(alerts, rules), [])
        self.assertEqual(n.sdwan_signal(alerts, rules), [])
        moved = {
            "status": "OBSERVED",
            "rules": [{"name": "geust", "mode": "manual", "lines": ["internal4"]}],
        }
        event = n.sdwan_signal(alerts, moved)
        self.assertEqual(len(event), 1)
        self.assertIn("geust now uses internal4", event[0]["text"])
        self.assertEqual(n.sdwan_signal(alerts, {"status": "UNAVAILABLE", "rules": []}), [])
        out = alerts.step(10, event, settings())
        self.assertEqual([m["kind"] for m in out], ["event"])


class SummaryAndDeviceTests(unittest.TestCase):
    def test_summary_only_alerts_are_recorded_for_the_daily_summary_never_sent(self):
        alerts, c = n.Alerts(None), settings()
        loss = {"rule": "line_loss", "subject": "wan2", "bad": True, "text": "Packet loss 9%."}
        out = []
        for t in range(0, 1200 + n.REMINDER_SECONDS + 61, 60):
            out += alerts.step(t, [loss], c)
        self.assertEqual([(m["kind"], m["held"]) for m in out], [("start", True)])  # no reminder
        sent = []
        n.deliver(
            settings(telegram={"enabled": True, "chat_id": "-1001234"}),
            {},
            out,
            alerts,
            send=lambda *a, **k: sent.append(a),
        )
        self.assertEqual(sent, [])
        self.assertEqual([d["key"] for d in alerts.digest(0, 10**9)], ["line_loss:wan2"])

    def test_trouble_seen_again_after_a_gap_starts_its_hold_over(self):
        alerts, c = n.Alerts(None), settings()
        slow = {"rule": "line_slow", "subject": "wan1", "bad": True, "text": ""}
        alerts.step(0, [slow], c)
        # Ten minutes without evidence, then trouble again: no instant alert.
        self.assertEqual(alerts.step(600, [slow], c), [])
        self.assertEqual(alerts.state["pending"]["line_slow:wan1"]["since"], 600)
        out = []
        for t in range(660, 1201, 60):
            out += alerts.step(t, [slow], c)
        self.assertEqual([m["kind"] for m in out], ["start"])  # a full hold after the gap
        self.assertEqual(alerts.view()["open"][0]["opened"], 600)

    def test_summary_signals_for_loss_full_lines_and_disagreeing_checks(self):
        site = {
            "confirmed_wan_interfaces": ["internal3"],
            "sla": "google",
            "wan_speeds": {"internal3": {"down_mbps": 35, "up_mbps": 5}},
        }
        data = {
            'up{job="fortigate"}': vector(({"job": "fortigate"}, 1)),
            'fortigate_virtual_wan_status{state="up"}': vector(
                ({"interface": "internal3", "sla": "google"}, 1),
                ({"interface": "internal3", "sla": "ping"}, 0),
            ),
        }

        def query(expr):
            if "packet_loss" in expr:
                return vector(({"interface": "internal3", "sla": "google"}, 7))
            if "_tx_" in expr:
                return vector(({"interface": "internal3"}, 4.8))
            if "_rx_" in expr:
                return vector(({"interface": "internal3"}, 10))
            if "sessions" in expr or "latency" in expr:
                return []
            return data.get(expr, [])

        s = {
            x["rule"]: x
            for x in n.line_signals(
                query, site, delay_baseline={}, traffic_baseline={}, at_work=True
            )
        }
        self.assertIs(s["line_loss"]["bad"], True)
        self.assertIs(s["line_full"]["bad"], True)
        self.assertIn("Upload 4.8 of 5 Mbps", s["line_full"]["text"])
        self.assertIs(s["checks_disagree"]["bad"], True)
        self.assertIn("google up, ping down", s["checks_disagree"]["text"])
        self.assertIs(s["line_down"]["bad"], False)

    def test_a_device_filling_a_line_is_named_and_closes_when_it_stops(self):
        site = {"wan_speeds": {"internal3": {"down_mbps": 35, "up_mbps": 5}}}
        live = {
            "devices": [
                {
                    "hostname": "MICHEAL",
                    "lines": {"internal3": {"download_bps": 1e6, "upload_bps": 3e6}},
                },
                {
                    "hostname": "quiet",
                    "lines": {"internal3": {"download_bps": 1e6, "upload_bps": 1e5}},
                },
            ]
        }
        out = n.device_signals(live, site, set())
        self.assertEqual([(s["subject"], s["bad"]) for s in out], [("internal3 · MICHEAL", True)])
        self.assertIn("uses 3.0 of 5 Mbps upload on internal3 (60%)", out[0]["text"])
        gone = n.device_signals({"devices": []}, site, {"internal3 · MICHEAL"})
        self.assertEqual([(s["subject"], s["bad"]) for s in gone], [("internal3 · MICHEAL", False)])
        self.assertEqual(n.device_signals(None, site, {"internal3 · MICHEAL"}), [])
        # Interface counters without FortiView rows say nothing about devices.
        blind = {"devices": [], "compatibility": {"fortiview": "UNAVAILABLE"}}
        self.assertEqual(n.device_signals(blind, site, {"internal3 · MICHEAL"}), [])
        self.assertEqual(n.device_signals(live, {}, set()), [])


class ReportTests(unittest.TestCase):
    def at(self, *args):
        return datetime(*args, tzinfo=CAIRO).timestamp()

    def test_reports_are_due_once_in_their_hour_on_their_days(self):
        c = settings(reports={"monthly": {"enabled": True}})
        sunday = self.at(2026, 11, 15, 8, 30)
        self.assertEqual(n.due_reports(c, sunday, CAIRO, {}), ["daily", "weekly"])
        sent = n.report_markers(sunday, CAIRO)
        self.assertEqual(n.due_reports(c, sunday + 60, CAIRO, sent), [])
        self.assertEqual(
            n.due_reports(c, self.at(2026, 11, 15, 9, 30), CAIRO, {}), []
        )  # missed hour
        self.assertEqual(n.due_reports(c, self.at(2026, 11, 20, 8, 30), CAIRO, {}), [])  # Friday
        self.assertEqual(
            n.due_reports(c, self.at(2026, 12, 1, 8, 5), CAIRO, {}), ["daily", "monthly"]
        )
        off = settings(reports={"daily": {"enabled": False}, "weekly": {"enabled": False}})
        self.assertEqual(n.due_reports(off, sunday, CAIRO, {}), [])

    def test_windows_are_whole_local_days(self):
        sunday = self.at(2026, 11, 15, 8, 0)
        start, end = n.report_window("daily", sunday, CAIRO, self.at(2026, 11, 12, 8, 0))
        self.assertEqual(
            (start.date().isoformat(), end.date().isoformat()), ("2026-11-12", "2026-11-15")
        )
        start, end = n.report_window("weekly", sunday, CAIRO)
        self.assertEqual((start.day, end.day), (8, 15))
        start, end = n.report_window("monthly", self.at(2026, 12, 1, 8, 0), CAIRO)
        self.assertEqual(
            (start.isoformat()[:10], end.isoformat()[:10]), ("2026-11-01", "2026-12-01")
        )

    def test_provider_report_finds_outages_and_degraded_periods(self):
        step, start = 300, 1_000_000_200
        alive = [[start + i * step, "0" if 10 <= i < 13 else "1"] for i in range(100)]
        loss = [[start + i * step, "12" if 40 <= i < 46 else "0.5"] for i in range(100)]
        delay = [[start + i * step, "40"] for i in range(100)]

        def query_range(expr, a, b, s):
            if "status" in expr:
                return alive
            if "packet_loss" in expr:
                return loss if 'sla="google"' in expr else []
            return delay

        line = n.isp_lines(query_range, ["wan2"], start, start + 100 * step, "google")[0]
        self.assertEqual(len(line["outages"]), 1)
        self.assertEqual(line["outages"][0]["end"] - line["outages"][0]["start"], 3 * step)
        self.assertEqual(len(line["degraded"]), 1)
        self.assertEqual(line["degraded"][0]["worst_loss_pct"], 12)
        self.assertAlmostEqual(line["up_pct"], 97.0)
        self.assertAlmostEqual(line["observed_pct"], 100.0)

    def test_messages_say_what_is_missing_and_escape_names(self):
        start = datetime(2026, 11, 14, tzinfo=CAIRO)
        end = datetime(2026, 11, 15, tzinfo=CAIRO)
        daily = n.render_report(
            "daily",
            {
                "start": start,
                "end": end,
                "site": "Site",
                "line_names": ["wan1", "wan2"],
                "digest": [
                    {"at": 1, "key": "line_loss:wan2", "kind": "start", "title": "", "text": ""},
                    {"at": 2, "key": "line_loss:wan2", "kind": "start", "title": "", "text": ""},
                    {"at": 3, "key": "line_loss:wan2", "kind": "end", "title": "", "text": ""},
                ],
                "alerts_sent": 3,
                "usage": {
                    "totals": {"download_bytes": 5e9, "upload_bytes": 1e9},
                    "devices": [
                        {"hostname": "<b>pc</b>", "download_bytes": 2e9, "upload_bytes": 0}
                    ],
                    "observed_endpoint_identities": 1,
                },
                "new_devices": [],
            },
            CAIRO,
        )
        self.assertEqual(daily["subject"], "FortiLens · Daily summary · Sat 14 Nov")
        self.assertIn("wan2: line losing packets 2×", daily["text"])
        self.assertIn("wan1: fine", daily["text"])
        self.assertIn("5.0 GB down, 1.0 GB up, 1 devices.", daily["text"])
        self.assertIn("&lt;b&gt;pc&lt;/b&gt;", daily["html"])
        self.assertNotIn("<b>pc</b>", daily["html"])
        weekly = n.render_report(
            "weekly",
            {
                "start": start,
                "end": end,
                "site": "Site",
                "usage": None,
                "lines": [],
                "applications": None,
            },
            CAIRO,
        )
        self.assertIn("Recorded usage is unavailable for this period.", weekly["text"])
        self.assertIn("Line health is unavailable for this period.", weekly["text"])
        # Found on real data: busy use is already a percentage, no records is not
        # zero usage, segments read as VLAN and name, and a line without samples
        # gets no "no outage" claim.
        monthly = n.render_report(
            "monthly",
            {
                "start": start,
                "end": end,
                "site": "Site",
                "usage": {"totals": {"download_bytes": 0}, "devices": [], "vlans": []},
                "lines": [
                    {
                        "interface": "internal3",
                        "avg_loss_pct": 1.9,
                        "incidents": 2,
                        "avg_sessions": 241,
                        "speed": {"busy_down_pct": 49.1, "busy_up_pct": 93.2},
                    }
                ],
                "isp": [
                    {
                        "interface": "wan2",
                        "observed_pct": 0,
                        "up_pct": None,
                        "avg_loss_pct": None,
                        "avg_delay_ms": None,
                        "outages": [],
                        "degraded": [],
                    }
                ],
            },
            CAIRO,
        )
        self.assertIn("internal3 · 1.9% · 2 · 49% / 93% · 241", monthly["text"])
        unmeasured = n._line_rows([{"interface": "wan2", "avg_loss_pct": None, "incidents": None}])
        self.assertEqual(unmeasured, [["wan2", "unknown", "unknown", "not measured", "unknown"]])
        self.assertIn("unknown, not zero", monthly["text"])
        self.assertNotIn("0.0 GB", monthly["text"])
        self.assertIn("no samples were kept", monthly["text"])
        self.assertNotIn("No outage", monthly["text"])
        segment = {"vlan": {"vlan_id": 20, "interface": "users", "key": "vlan:20:users"}}
        self.assertEqual(n._segment(segment), "VLAN 20 · users")

    def test_reports_prefer_their_channel(self):
        both = settings(
            telegram={"enabled": True, "chat_id": "-1001234"},
            email={
                "enabled": True,
                "host": "smtp.example.com",
                "sender": "a@example.com",
                "to": ["b@example.com"],
            },
        )
        report = {"subject": "S", "text": "T", "html": "<p>T</p>"}
        sent = []

        def send(c, secrets, channel, subject, text, **kwargs):
            sent.append((channel, kwargs.get("html")))

        self.assertEqual(
            n.deliver_report(both, {}, "daily", report, send=send)["channel"], "telegram"
        )
        self.assertEqual(
            n.deliver_report(both, {}, "weekly", report, send=send)["channel"], "email"
        )
        self.assertEqual(sent, [("telegram", None), ("email", "<p>T</p>")])
        self.assertEqual(
            n.deliver_report(settings(), {}, "weekly", report, send=send)["error"], "not_configured"
        )


class ServiceReportTests(unittest.TestCase):
    def test_reports_build_without_metrics_and_new_devices_wait_for_history(self):
        import accounting as a
        import usage_service as u
        from test_accounting import CONFIG, INVENTORY

        store = a.Store(
            ":memory:", {**CONFIG, "fortigate_host": "fw:443"}, INVENTORY, storage_format="compact"
        )
        self.addCleanup(store.close)

        def fetch(*args, **kwargs):
            raise OSError("metrics unavailable")

        engine = u.Engine(store, fetch=fetch)
        for kind in ("daily", "weekly", "monthly"):
            with self.subTest(kind=kind):
                report = engine.build_report(
                    kind, now=datetime(2026, 12, 1, 9, tzinfo=CAIRO).timestamp()
                )
                self.assertTrue(report["subject"].startswith("FortiLens · "))
                self.assertIn("never as zero", report["text"])
        self.assertIsNone(engine.new_devices("2026-11-01", "2026-11-02"))
        with self.assertRaises(u.ServiceError):
            engine.build_report("yearly")

    def engine(self):
        import accounting as a
        import usage_service as u
        from test_accounting import CONFIG, INVENTORY

        store = a.Store(
            ":memory:", {**CONFIG, "fortigate_host": "fw:443"}, INVENTORY, storage_format="compact"
        )
        self.addCleanup(store.close)

        def fetch(*args, **kwargs):
            raise OSError("metrics unavailable")

        return u, u.Engine(store, fetch=fetch)

    def test_the_daily_summary_covers_whole_days_and_lists_sent_alerts(self):
        u, engine = self.engine()
        now = datetime(2026, 12, 1, 9, tzinfo=CAIRO).timestamp()  # Tuesday
        for hour, line in ((10, "wan8"), (31, "wan9")):  # Monday 10:00, Tuesday 07:00
            message = {
                "at": datetime(2026, 11, 30, tzinfo=CAIRO).timestamp() + hour * 3600,
                "key": "line_down:" + line,
                "kind": "start",
                "title": "Line down · " + line,
                "text": "x",
                "held": False,
            }
            engine.alerts.record(message, [{"channel": "telegram", "status": "delivered"}])
        text = engine.build_report("daily", now=now)["text"]
        self.assertIn("wan8: line down 1×", text)
        self.assertNotIn("wan9", text)  # after midnight: the next summary's
        self.assertIn("Alerts sent: 1", text)

    def test_a_failed_report_is_retried_in_its_window_and_alerts_of_unwatched_lines_drop(self):
        u, engine = self.engine()
        c = settings(telegram={"enabled": True, "chat_id": "-1001234"})
        now = datetime(2026, 12, 1, 8, 5, tzinfo=CAIRO).timestamp()
        engine.send_report("daily", c, {}, now)  # no bot token: not delivered
        self.assertNotIn("daily", engine.alerts.state["reports"])
        self.assertEqual(engine._report_retry["daily"], now + u.REPORT_RETRY_SECONDS)
        self.assertEqual(engine.alerts.view()["history"][0]["kind"], "report")
        self.assertTrue(u.watched("line_down:wan1", {"wan1"}, {}))
        self.assertFalse(u.watched("line_down:wan3", {"wan1"}, {}))
        self.assertFalse(u.watched("heavy_device:wan1 · pc", {"wan1"}, {}))
        self.assertTrue(u.watched("logs_stopped:", set(), {}))
        # A whole step without any metrics or logs runs and opens nothing.
        self.assertEqual(engine.alert_step(now), [])


if __name__ == "__main__":
    unittest.main()
