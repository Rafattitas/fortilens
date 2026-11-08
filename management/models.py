"""Browser/helper contracts. Secret input is write-only and extra fields fail closed."""
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from .common import Problem, setup


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Monitoring(Strict):
    wan: bool = True
    sla: bool = True
    interfaces: bool = True
    syslog: bool = True
    accounting: bool = True
    vlans: bool = True
    server: bool = True
    # Opt-in: per-device website history (docs/operations/WEBSITES.md).
    websites: bool = False


class Retention(Strict):
    loki_days: int = Field(default=60, ge=1, le=3650)
    prometheus_days: int = Field(default=90, ge=1, le=3650)
    ledger_days: int = Field(default=3, ge=1, le=365)
    summary_days: int = Field(default=400, ge=31, le=3650)
    database_bytes: int = Field(default=4294967296, ge=67108864, le=1099511627776)


class Settings(Strict):
    site_name: str = Field(default='My company', min_length=1, max_length=120)
    installation_name: str = Field(default='FortiLens', pattern=r'^[A-Za-z0-9_. -]{1,80}$')
    # 'browser' shows every time in each viewer's own device timezone.
    timezone: str = 'browser'
    date_format: Literal['YYYY-MM-DD', 'DD/MM/YYYY', 'MM/DD/YYYY'] = 'YYYY-MM-DD'
    fortigate_host: str = ''
    api_port: int = Field(default=443, ge=1, le=65535)
    grafana_port: int = Field(default=3001, ge=1024, le=65535)
    usage_port: int = Field(default=3002, ge=1024, le=65535)
    vdom: str = 'root'
    api_tls_verify: bool = True
    sla: str = ''
    wan_interfaces: list[str] = Field(default_factory=list, max_length=64)
    wan_confirmed: bool = False
    syslog_protocol: Literal['tcp', 'udp'] = 'tcp'
    syslog_port: int = Field(default=1514, ge=1, le=65535)
    syslog_format: Literal['rfc5424', 'rfc3164', 'fortigate'] = 'rfc5424'
    syslog_network: Literal['bridge', 'host'] = 'bridge'
    optional_exporter_exclusions: list[Literal[
        'Log/Fortianalyzer/Status', 'System/Interface/Transceivers', 'System/SensorInfo',
    ]] | None = Field(default=None, max_length=3)
    monitoring_interval: int = Field(default=60, ge=15, le=3600)
    # 'fortigate' cuts accounting days at midnight on the FortiGate's own clock.
    accounting_timezone: str = 'fortigate'
    expected_devid: str = ''
    monitoring: Monitoring = Field(default_factory=Monitoring)
    retention: Retention = Field(default_factory=Retention)
    # Compose CPU limit of the single accounting writer; see docs/operations/CAPACITY.md.
    accounting_cpus: Literal['0.5', '1', '2'] = '0.5'

    @model_validator(mode='after')
    def supported(self):
        try:
            if self.timezone != 'browser':
                ZoneInfo(self.timezone)
            if self.accounting_timezone not in ('fortigate', 'UTC'):
                ZoneInfo(self.accounting_timezone)
            if self.retention.summary_days < self.retention.ledger_days:
                raise ValueError()
            if self.monitoring.accounting and not self.monitoring.syslog or (self.monitoring.vlans or self.monitoring.websites) and not self.monitoring.accounting:
                raise ValueError()
            if self.monitoring.accounting and self.grafana_port==self.usage_port:
                raise ValueError()
            if self.monitoring.syslog and self.syslog_protocol=='tcp' and self.syslog_port in [self.grafana_port]+([self.usage_port] if self.monitoring.accounting else []):
                raise ValueError()
            self.portable(validate=True)
        except (ValueError, setup.SetupError, ZoneInfoNotFoundError):
            raise ValueError('Review host, timezone, WAN/SLA names and monitoring/retention dependencies.') from None
        if self.wan_interfaces and not self.wan_confirmed:
            raise ValueError('Explicitly confirm the selected Internet interfaces.')
        return self

    def portable(self, baseline=None, *, validate=False):
        c = {**setup.DEFAULTS, **(baseline or {})}
        c.update({key: getattr(self, key) for key in ('site_name', 'fortigate_host', 'api_port', 'grafana_port', 'usage_port', 'vdom', 'sla', 'api_tls_verify', 'syslog_protocol', 'syslog_port', 'syslog_format', 'accounting_timezone', 'expected_devid')})
        c.update(timezone=self.timezone, confirmed_wan_interfaces=self.wan_interfaces,
                 syslog_network=self.syslog_network,
                 wan_interfaces=self.wan_interfaces if self.sla else [],
                 accounting_enabled=self.monitoring.accounting, portal_enabled=self.monitoring.accounting,
                 accounting_websites=self.monitoring.websites and self.monitoring.accounting,
                 accounting_ledger_days=self.retention.ledger_days, accounting_summary_days=self.retention.summary_days,
                 accounting_max_bytes=self.retention.database_bytes)
        if validate:
            # A pending custom CA is handled by the helper, never as a browser path.
            c['api_ca_file'] = ''
        return setup.validate_config(c)


class Change(Strict):
    revision: int = Field(ge=0)
    settings: Settings
    api_token: SecretStr | None = None
    replace_token: bool = False
    custom_ca: SecretStr | None = None
    replace_ca: bool = False
    acknowledge_insecure_tls: bool = False


class Revision(Strict):
    revision: int = Field(ge=0)


class Apply(Revision):
    plan_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    confirm: bool


class Job(Strict):
    job_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class Console(Strict):
    path: str = Field(max_length=16384)
    method: Literal['GET', 'POST'] = 'GET'
    body: str = Field(default='', max_length=3000000)
    content_type: str = Field(default='application/json', max_length=100)


def safe_settings(value):
    try:
        return Settings.model_validate(value)
    except ValueError:
        raise Problem('invalid_settings', 'Review the highlighted settings and supported dependencies.') from None
