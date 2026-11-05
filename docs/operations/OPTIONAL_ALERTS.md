# Optional Grafana alerts

Existing alerts and notification policies are never changed by setup, upgrade preparation, or doctor. The new command creates **paused review candidates outside active provisioning**:

```bash
python3 setup.py prepare-alerts
python3 setup.py prepare-alerts --notification email
python3 setup.py prepare-alerts --notification telegram
```

Candidates live under the installation's `alert-candidates/<timestamp>/`. No API write, service restart, rule activation, contact test or notification is sent. WAN candidates require a configured SLA and explicit interface names, with literal regex escaping. Review those choices and thresholds first.

- Explicit WAN DOWN for 2 minutes, gated by successful scrape and API probe.
- High latency or packet loss for 5 minutes while Internet SLA is explicitly UP.
- FortiGate collection unavailable for 5 minutes, reported separately from a WAN outage.
- Optional usage processing failure/lag for 15 minutes. Initial backfill can legitimately trigger the lag condition after activation.

Missing data stays Grafana `NoData`; execution failures stay `Error`. They are not transformed into a WAN outage. Review how your notification routing handles datasource health alerts before enabling anything.

Quiet syslog alone cannot establish a failure. Only if you have an independently established expectation of continuous logs, explicitly generate an expected-delivery candidate with `--expected-log-interval-seconds 600`. Receipt-time stream configuration is required. This rule is absent by default and does not certify dropped traffic.

Email/Telegram files contain environment references only: `FORTILENS_ALERT_EMAIL`, or `FORTILENS_TELEGRAM_BOT_TOKEN` and `FORTILENS_TELEGRAM_CHAT_ID`. Set these privately in Grafana's environment when explicitly provisioning. Email also requires your existing Grafana SMTP configuration. No destination, secret or global notification policy is shipped; select the new contact point and narrowly route its rules in your own reviewed configuration.

Validate/import the rule file in an isolated Grafana first, confirm it remains paused, then explicitly configure routing and enable only the intended rules. The integration test loads rules paused with no external contact point and validates their PromQL; it never sends a notification. Real destination delivery is an operator check requiring an intended recipient.

Reference: [Grafana file provisioning](https://grafana.com/docs/grafana/latest/alerting/set-up/provision-alerting-resources/file-provisioning/).
