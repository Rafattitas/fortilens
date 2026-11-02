"""Revisioned candidate generation and guarded configuration-only recovery.

This module never starts Docker and never opens a monitoring database. File roots
and project identity are supplied by the host installer, never by HTTP clients.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import threading
import time
import urllib.parse
import yaml
from .common import Problem, atomic, fingerprint, guarded, load, report_failure, setup
from .models import Change, Settings

SERVICES = ('fortigate-exporter', 'prometheus', 'grafana', 'loki', 'alloy', 'node-exporter', 'fortilens-usage', 'fortilens-portal')


def prometheus_policy(data,interval,*,update_global=True):
    if update_global:data.setdefault('global',{}).update(scrape_interval=str(interval)+'s',scrape_timeout=str(min(40,interval-1))+'s')
    for job in data.get('scrape_configs',[]):
        if job.get('scrape_interval') or 'scrape_timeout' not in job:continue
        value=re.fullmatch(r'(\d+)([smh])',str(job['scrape_timeout']))
        if not value:raise Problem('scrape_policy_review','Review a custom scrape timeout before changing the monitoring interval.')
        seconds=int(value[1])*{'s':1,'m':60,'h':3600}[value[2]]
        if seconds>interval:
            if job.get('job_name') not in ('fortigate','node-exporter','fortilens-accounting'):
                raise Problem('scrape_policy_review','A custom scrape timeout exceeds the requested global interval. Review that job on the host.')
            job['scrape_timeout']=str(interval-1)+'s'
    return data


class Configuration:
    def __init__(self, root, control, *, project=None, compose_file='compose.yaml'):
        self.root, self.control = Path(root).resolve(), Path(control).resolve()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,80}', project or ''):
            raise Problem('project_required', 'Register the existing Compose project explicitly; volume identity must not be guessed.')
        self.project = project
        self.compose_name = guarded(self.root, compose_file).relative_to(self.root).as_posix()
        self.control.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.state_path = self.control / 'configuration.json'
        self.secret_path = self.control / 'pending-secrets.json'
        if not self.state_path.exists():
            self._import()

    def _import(self):
        existing = (self.root / self.compose_name).exists()
        cpath = 'site.json' if (self.root / 'site.json').exists() else '.fortilens/site.json'
        if existing and not (self.root / cpath).is_file():
            raise Problem('site_config_missing', 'Register a deployment with an existing FortiLens site configuration. No initialization was performed.')
        self.site_name = cpath if existing else 'site.json'
        c = load(self.root / self.site_name, {})
        d = {key: c[key] for key in Settings.model_fields if key in c and key not in ('wan_interfaces', 'monitoring', 'retention')}
        if existing:
            if d.get('timezone')=='browser':d['timezone']=c.get('accounting_timezone','UTC')
            d['wan_interfaces'] = c.get('confirmed_wan_interfaces', [])
            d['wan_confirmed'] = bool(d['wan_interfaces'])
            d['monitoring'] = {'accounting': c.get('accounting_enabled', False), 'vlans': c.get('accounting_enabled', False)}
            d['retention'] = {'ledger_days': c.get('accounting_ledger_days', 8), 'summary_days': c.get('accounting_summary_days', 400), 'database_bytes': c.get('accounting_max_bytes', 4294967296)}
            # Read actual policies, never assume that template defaults are live.
            compose = self.compose()
            prom=self.bound_file(compose,'prometheus','/etc/prometheus/prometheus.yml')
            global_config=(yaml.safe_load(prom.read_text()) or {}).get('global',{})
            interval=re.fullmatch(r'(\d+)([smh])',str(global_config.get('scrape_interval','1m')))
            if not interval:raise Problem('scrape_policy_review','Register a whole-second monitoring interval before adoption.')
            d['monitoring_interval']=int(interval[1])*{'s':1,'m':60,'h':3600}[interval[2]]
            loki = self.bound_file(compose, 'loki', '/etc/loki/local-config.yaml', optional=True)
            if loki:
                value = yaml.safe_load(loki.read_text()) or {}
                retention = value.get('limits_config', {}).get('retention_period', '')
                match = re.fullmatch(r'(\d+)([hd])', str(retention))
                if match and int(match[1])>0 and (match[2]=='d' or int(match[1])%24==0):
                    d['retention']['loki_days'] = int(match[1]) // (24 if match[2] == 'h' else 1)
                else:
                    raise Problem('retention_policy_review','Register an explicit whole-day Loki retention policy before adoption. No default was substituted.')
            else:
                raise Problem('custom_mount_review','Register the actual Loki configuration mount before adoption.')
            found=False
            for argument in compose['services'].get('prometheus', {}).get('command', []):
                m = re.fullmatch(r'--storage.tsdb.retention.time=(\d+)d', argument)
                if m:
                    d['retention']['prometheus_days'] = int(m[1])
                    found=True
            if not found:raise Problem('retention_policy_review','Register an explicit whole-day Prometheus retention flag before adoption. No default was substituted.')
        if not existing:
            d['fortigate_host'] = '192.0.2.1'  # Draft-only placeholder, visibly not a connection result.
        settings = Settings.model_validate(d).model_dump()
        atomic(self.state_path, {'revision': 0, 'applied_revision': 0 if existing else None, 'settings': settings,
                                'applied': settings if existing else None, 'configured': existing, 'site_file': self.site_name})

    def state(self):
        try:data = load(self.state_path)
        except (OSError,ValueError) as exc:
            report_failure('configuration_state_read_failed',exc)
            raise Problem('configuration_state_unavailable','Saved configuration is unavailable. Review host storage; do not initialize over an existing installation.',503) from None
        if (not isinstance(data,dict) or not isinstance(data.get('site_file'),str)
                or not isinstance(data.get('settings'),dict) or type(data.get('revision')) is not int
                or data['revision']<0 or type(data.get('configured')) is not bool):
            raise Problem('configuration_state_invalid','Saved configuration needs host-side review. Existing files and monitoring data were not reset.',503)
        guarded(self.root,data['site_file'])
        self.site_name = data['site_file']
        return data

    def compose(self):
        p = guarded(self.root, self.compose_name)
        try:
            if p.exists() and not p.is_file():raise ValueError('invalid_compose_file')
            value = yaml.safe_load(p.read_text(encoding='utf-8-sig')) if p.is_file() else None
        except OSError as exc:
            report_failure('compose_read_failed',exc)
            raise Problem('configuration_unavailable','The registered Compose file is unavailable. Review host storage and permissions.',503) from None
        except (ValueError,yaml.YAMLError) as exc:
            report_failure('compose_invalid',exc)
            raise Problem('compose_unsupported','The registered Compose file cannot be safely interpreted.') from None
        if value is not None and (not isinstance(value, dict) or not isinstance(value.get('services'), dict)):
            raise Problem('compose_unsupported', 'The registered Compose file cannot be safely interpreted.')
        return value

    def bound_file(self, compose, service, container_path, optional=False):
        matches = []
        for volume in compose['services'].get(service, {}).get('volumes', []):
            if isinstance(volume, str):
                parts = volume.split(':')
                source, target = parts[:2] if len(parts) >= 2 else ('', '')
            else:
                source, target = volume.get('source', ''), volume.get('target', '')
            if not source or not str(source).startswith(('.', '/')):
                continue
            if container_path == target or container_path.startswith(target.rstrip('/') + '/'):
                base = Path(source) if Path(source).is_absolute() else self.root / source
                file = base / container_path[len(target):].lstrip('/') if target != container_path else base
                if not file.resolve().is_relative_to(self.root):
                    raise Problem('external_configuration', 'A service config is outside the registered directory; use the documented host migration.')
                matches.append(guarded(self.root, file.relative_to(self.root)))
        if len(matches) != 1:
            if optional:
                return None
            raise Problem('custom_mount_review', 'A custom service config mount needs a host-side registration review.')
        return matches[0]

    def token(self):
        pending = load(self.secret_path, {})
        if pending.get('token'):
            return pending['token']
        compose = self.compose()
        if not compose:
            return ''
        command = compose['services']['fortigate-exporter'].get('command', [])
        destination = next((command[i + 1] for i, x in enumerate(command[:-1]) if x == '-auth-file'), '/config/fortigate-key.yaml')
        p = self.bound_file(compose, 'fortigate-exporter', destination)
        auth = yaml.safe_load(p.read_text()) or {}
        old = load(self.root / self.state()['site_file'], {})
        record = auth[self.auth_key(auth,old)]
        if not isinstance(record, dict) or not isinstance(record.get('token'), str):
            raise Problem('secret_unavailable', 'The existing exporter credential could not be mapped safely.')
        return record['token']

    @staticmethod
    def auth_key(auth,config,*,optional=False):
        """Recognize one HTTPS origin, including the legacy implicit443 form."""
        if not isinstance(auth,dict):raise Problem('secret_unavailable','The exporter credential mapping needs a host-side review.')
        wanted=urllib.parse.urlsplit(setup.target_url({**setup.DEFAULTS,**config}))
        matches=[]
        for key in auth:
            try:
                item=urllib.parse.urlsplit(key)
                if item.scheme=='https' and not item.username and not item.password and item.path in ('','/') and not item.query and not item.fragment and item.hostname==wanted.hostname and (item.port or 443)==(wanted.port or 443):matches.append(key)
            except (TypeError,ValueError):continue
        if not matches and optional:return None
        if len(matches)!=1:raise Problem('secret_unavailable','One unambiguous exporter credential must match the configured HTTPS origin; review its existing mapping.')
        return matches[0]

    def view(self):
        state = self.state()
        try:
            present = bool(self.token())
        except Problem:
            present = False
        return {key: state[key] for key in ('revision', 'applied_revision', 'configured', 'settings')} | {
            'secret_status': {'api_token_present': present, 'custom_ca_present': bool(load(self.secret_path, {}).get('ca') or load(self.root / state['site_file'], {}).get('api_ca_file'))},
            'pending_changes': state['applied_revision'] != state['revision'],
            'active_monitoring':(state.get('applied') or state['settings'])['monitoring'],
            'scope_locked': bool((self.control/'accounting-contract.json').exists() or state['configured'] and self.compose() and 'fortilens-usage' in self.compose()['services']),
            'collector_note': 'WAN, SLA and interface shortcuts share one exporter. It stops only when all three are disabled.'}

    def audit(self, event, fields=(), outcome='success'):
        # This append follows committed state changes. Its failure must not claim
        # those changes were undone or trigger a configuration-only rollback.
        try:
            with (self.control / 'audit.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'time': time.time(), 'event': event, 'fields': sorted(fields), 'outcome': outcome}) + '\n')
        except (OSError,ValueError) as exc:report_failure('configuration_audit_write_failed',exc)

    def save(self, change):
        change = Change.model_validate(change)
        with self.lock:
            state = self.state()
            if change.revision != state['revision']:
                raise Problem('revision_conflict', 'Settings changed in another window. Reload before saving.', 409)
            new = change.settings.model_dump()
            previous = state.get('applied')
            scope=load(self.control/'accounting-contract.json',previous)
            if self.view()['scope_locked'] and scope:
                for field in ('vdom', 'accounting_timezone', 'expected_devid'):
                    if new[field] != scope[field]:
                        raise Problem('accounting_migration_required', 'Changing historical accounting scope requires an explicit migration; no data was changed.', 409)
            if previous:
                for key, value in previous['retention'].items():
                    if new['retention'][key] < value:
                        raise Problem('retention_reduction_requires_review', 'Reducing retention or the database ceiling requires the documented data-preservation review.', 409)
            if not change.settings.api_tls_verify and state['settings']['api_tls_verify'] and not change.acknowledge_insecure_tls:
                raise Problem('tls_ack_required', 'Explicitly acknowledge disabling FortiGate certificate verification.')
            secret = load(self.secret_path, {})
            if change.api_token is not None:
                token = change.api_token.get_secret_value()
                if not change.replace_token or not token or len(token) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                    raise Problem('token_action_required', 'Select Replace token and provide a valid token, or leave the field empty.')
                secret['token'] = token
            if change.custom_ca is not None:
                if not change.replace_ca:
                    raise Problem('ca_action_required', 'Explicitly confirm replacing the custom CA certificate.')
                ca = change.custom_ca.get_secret_value()
                try:
                    if ca:
                        ssl.create_default_context(cadata=ca)
                except ssl.SSLError:
                    raise Problem('invalid_ca', 'The custom CA is not a valid PEM trust bundle.') from None
                secret['ca'] = ca
            # One state transaction owns draft and write-only secret values.
            # Secret staging does not alter active service credentials.
            secret['revision']=state['revision']+1
            atomic(self.secret_path, secret)
            changed = [key for key in new if new[key] != state['settings'].get(key)]
            state.update(settings=new, revision=state['revision'] + 1)
            atomic(self.state_path, state)
            self.audit('settings_saved', changed + (['api_token'] if change.api_token else []))
            return self.view()

    def _patch(self, stage, settings, c):
        compose = self.compose()
        if not compose:
            return {p.relative_to(stage).as_posix(): p.read_bytes() for p in stage.rglob('*') if p.is_file()}
        generated = setup.load(stage / 'compose.yaml')
        files = {}
        active = load(self.root / self.state()['site_file'], {})
        endpoint_changed=setup.target_url(c)!=setup.target_url({**setup.DEFAULTS,**active})
        for service in ('fortilens-usage', 'fortilens-portal'):
            if service not in compose['services'] and service in generated['services']:
                if service == 'fortilens-portal':
                    # Existing Grafana authentication is not silently reconfigured.
                    # The independent management UI can still link to native Grafana.
                    c['portal_enabled'] = False
                    continue
                compose['services'][service] = generated['services'][service]
                compose['services'][service]['image']='fortilens-usage:'+self.project
                if service=='fortilens-usage':
                    volumes=compose['services'][service]['volumes']
                    volumes[:]=[('./'+self.state()['site_file']+':/config/site.json:ro') if v=='./site.json:/config/site.json:ro' else v for v in volumes if 'fortigate-ca.pem:' not in v]
                    if c['api_ca_file']:volumes.append('./'+c['api_ca_file']+':/config/'+c['api_ca_file']+':ro')
                compose.setdefault('volumes', {}).setdefault('usage-data', {})
                for key in ('usage_admin_password', 'fortigate_api_token'):
                    compose.setdefault('secrets', {}).setdefault(key, generated['secrets'][key])
                    relative = 'secrets/' + key
                    if not (self.root / relative).exists():
                        files[relative] = (stage / relative).read_bytes()
                for p in (stage / 'usage').iterdir():
                    files['usage/' + p.name] = p.read_bytes()
        # Keep an existing source-contract identity, ports, data volumes and images.
        c['portal_enabled'] = active.get('portal_enabled', False)
        if 'fortilens-portal' in compose['services']:
            c['portal_enabled'] = True
        if 'fortilens-usage' in compose['services']:
            # Worker config keeps its source scope, even when the service is stopped.
            c['accounting_enabled'] = True
        prom = self.bound_file(compose, 'prometheus', '/etc/prometheus/prometheus.yml')
        pdata = yaml.safe_load(prom.read_text())
        for job in pdata['scrape_configs']:
            if job.get('job_name') == 'fortigate':
                if endpoint_changed:job['static_configs'][0]['targets'] = [setup.target_url(c)]
                for relabel in job.get('relabel_configs', []):
                    if relabel.get('target_label') == 'instance':
                        relabel['replacement'] = c['fortigate_host']
        if settings.monitoring.accounting and not any(j.get('job_name') == 'fortilens-accounting' for j in pdata['scrape_configs']):
            pdata['scrape_configs'].append(generated_job := setup.load(stage / 'prometheus/prometheus.yml')['scrape_configs'][-1])
            if generated_job.get('job_name') != 'fortilens-accounting':
                raise Problem('accounting_job_missing', 'The accounting scrape candidate could not be generated.')
            compose['services']['prometheus'].setdefault('secrets', []).append('usage_admin_password')
        applied=self.state().get('applied') or {}
        prometheus_policy(pdata,settings.monitoring_interval,update_global=settings.monitoring_interval!=applied.get('monitoring_interval'))
        files[str(prom.relative_to(self.root))] = yaml.safe_dump(pdata, sort_keys=False).encode()
        service = compose['services']['prometheus']
        service['command'] = [('--storage.tsdb.retention.time=' + str(settings.retention.prometheus_days) + 'd') if str(arg).startswith('--storage.tsdb.retention.time=') else arg for arg in service['command']]
        loki = self.bound_file(compose, 'loki', '/etc/loki/local-config.yaml')
        ldata = yaml.safe_load(loki.read_text())
        ldata.setdefault('limits_config', {})['retention_period'] = str(settings.retention.loki_days) + 'd'
        files[str(loki.relative_to(self.root))] = yaml.safe_dump(ldata, sort_keys=False).encode()
        alloy = self.bound_file(compose, 'alloy', '/etc/alloy/config.alloy')
        text = alloy.read_text()
        for name, value in [('protocol', settings.syslog_protocol), ('syslog_format', settings.syslog_format)]:
            pattern = rf'(?m)^(\s*{name}\s*=\s*)"[^"]*"'
            if len(re.findall(pattern, text)) != 1:
                if value != active.get(name):
                    raise Problem('custom_alloy_review', 'Custom syslog syntax requires a controlled host-side migration.')
            else:
                text = re.sub(pattern, lambda m: m[1] + json.dumps(value), text)
        files[str(alloy.relative_to(self.root))] = text.encode()
        if settings.monitoring.accounting and not active.get('accounting_enabled'):
            files[str(alloy.relative_to(self.root))]=re.sub(r'(?m)^(\s*use_incoming_timestamp\s*=\s*)true\s*$',lambda m:m[1]+'false',text).encode()
            c['loki_receive_timestamps']=True
        old_port = active.get('syslog_port', 1514)
        ports = compose['services']['alloy'].get('ports', [])
        if ports and not all(isinstance(p, str) for p in ports):
            raise Problem('custom_ports_review', 'Custom published-port syntax requires a host-side review.')
        replaced=[];receiver_found=False
        for binding in ports:
            address=binding.split('/')[0];parts=address.rsplit(':',2)
            if parts[-1]!='1514':
                replaced.append(binding);continue
            receiver_found=True
            parts[-2]=str(settings.syslog_port)
            replaced.append(':'.join(parts)+'/'+settings.syslog_protocol)
        if not receiver_found and (settings.syslog_port!=old_port or settings.syslog_protocol!=active.get('syslog_protocol','tcp')):
            raise Problem('custom_ports_review','Register the actual syslog target port before changing its listener.')
        compose['services']['alloy']['ports']=replaced
        exporter = compose['services']['fortigate-exporter']
        cmd = exporter.get('command', [])
        destination = next((cmd[i+1] for i,x in enumerate(cmd[:-1]) if x == '-auth-file'), '/config/fortigate-key.yaml')
        authfile = self.bound_file(compose, 'fortigate-exporter', destination)
        auth = yaml.safe_load(authfile.read_text())
        if endpoint_changed or load(self.secret_path,{}).get('token'):
            old_key=self.auth_key(auth,active)
            key=setup.target_url(c) if endpoint_changed else old_key
            if endpoint_changed:
                existing_key=self.auth_key(auth,c,optional=True)
                if existing_key and not load(self.secret_path,{}).get('token'):
                    raise Problem('token_action_required','Changing to an existing credential target requires an explicit token replacement.')
                key=existing_key or key
            record=auth.get(key,auth[old_key])
            if not isinstance(record,dict):raise Problem('secret_unavailable','Review the existing exporter credential record before changing it.')
            auth[key] = {**record,'token': self.token()}
            files[str(authfile.relative_to(self.root))] = yaml.safe_dump(auth).encode()
        exporter['command'] = [x for x in cmd if x != '-insecure'] + ([] if c['api_tls_verify'] else ['-insecure'])
        pending=load(self.secret_path,{})
        if 'ca' in pending:
            clean=[];skip=False
            for arg in exporter['command']:
                if skip:skip=False;continue
                if arg=='-extra-ca-certs':skip=True;continue
                if str(arg).startswith('-extra-ca-certs='):continue
                clean.append(arg)
            exporter['command']=clean
            if pending['ca']:
                exporter['command']+=['-extra-ca-certs','/config/web-fortigate-ca.pem']
                mount='./secrets/web-fortigate-ca.pem:/config/web-fortigate-ca.pem:ro'
                if mount not in exporter['volumes']:exporter['volumes'].append(mount)
                if 'fortilens-usage' in compose['services']:
                    mount='./secrets/web-fortigate-ca.pem:/config/secrets/web-fortigate-ca.pem:ro'
                    if mount not in compose['services']['fortilens-usage']['volumes']:compose['services']['fortilens-usage']['volumes'].append(mount)
        # Keep the actual worker's token file, rather than assuming a template path.
        secret = compose.get('secrets', {}).get('fortigate_api_token', {})
        if secret.get('file') and load(self.secret_path, {}).get('token'):
            p = Path(secret['file'])
            p = p if p.is_absolute() else self.root / p
            p = guarded(self.root, p.relative_to(self.root))
            files[str(p.relative_to(self.root))] = (self.token() + '\n').encode()
        files[self.compose_name] = yaml.safe_dump(compose, sort_keys=False).encode()
        for name,service in compose['services'].items():
            ports=service.get('ports',[])
            if name not in ('grafana','fortilens-usage'):continue
            if not all(isinstance(p,str) for p in ports):
                raise Problem('custom_ports_review','A custom port binding requires host-side registration review.')
            replacements=[]
            for binding in ports:
                target=binding.rsplit(':',1)[-1].split('/')[0]
                if target in ('3000','8080','8081'):
                    newport=settings.grafana_port if target=='3000' else settings.usage_port
                    parts=binding.rsplit(':',2)
                    if len(parts)==2:parts[0]=str(newport)
                    else:parts[1]=str(newport)
                    binding=':'.join(parts)
                replacements.append(binding)
            service['ports']=replacements
        environment=compose['services']['grafana'].get('environment',{})
        if isinstance(environment,dict) and environment.get('GF_SERVER_ROOT_URL') and settings.grafana_port!=active.get('grafana_port',3001):
            url=urllib.parse.urlsplit(environment['GF_SERVER_ROOT_URL'])
            if url.hostname and url.port==active.get('grafana_port',3001):
                host='['+url.hostname+']' if ':' in url.hostname else url.hostname
                environment['GF_SERVER_ROOT_URL']=urllib.parse.urlunsplit((url.scheme,host+':'+str(settings.grafana_port),url.path,url.query,url.fragment))
        files[self.compose_name] = yaml.safe_dump(compose, sort_keys=False).encode()
        return files

    def prepare(self, revision):
        with self.lock:
            state = self.state()
            if revision != state['revision']:
                raise Problem('revision_conflict', 'Reload the latest saved revision.', 409)
            settings = Settings.model_validate(state['settings'])
            token = self.token()
            if not token:
                raise Problem('token_required', 'Save the FortiGate API token before preparing deployment.')
            baseline = load(self.root / state['site_file'], {})
            pending = load(self.secret_path, {})
            if pending and pending.get('revision')!=state['revision']:
                raise Problem('interrupted_draft','The last draft save was interrupted. Save the form again before preparing.')
            # Resolve write-only CA operations before validating the effective
            # candidate. An applied CA must not invalidate its pending removal.
            baseline = deepcopy(baseline)
            if 'ca' in pending:
                baseline['api_ca_file'] = 'secrets/web-fortigate-ca.pem' if pending['ca'] else ''
            if not settings.api_tls_verify and baseline.get('api_ca_file'):
                raise Problem('tls_ca_conflict','A custom CA cannot be actively used while TLS verification is disabled. Explicitly remove the custom CA or enable certificate verification.')
            c = settings.portable(baseline)
            identity = secrets.token_hex(16)
            folder = self.control / 'plans' / identity
            folder.mkdir(parents=True, mode=0o700)
            # render reads CA files but accepts no browser-supplied paths.
            render_config = deepcopy(c)
            if c['api_ca_file']:
                path = folder / 'ca.pem' if 'ca' in pending else guarded(self.root, c['api_ca_file'])
                if 'ca' in pending:
                    atomic(path, pending['ca'].encode())
                render_config['api_ca_file'] = str(path)
            stage = folder / 'generated'
            setup.render(render_config, stage, token)
            files = self._patch(stage, settings, c)
            if not self.compose():
                c=setup.load(stage/'site.json')
                files['compose.yaml'] = (stage / 'compose.yaml').read_bytes()
                prom = json.loads(files['prometheus/prometheus.yml'])
                prometheus_policy(prom,settings.monitoring_interval)
                files['prometheus/prometheus.yml'] = setup.dumps(prom).encode()
                ldata = json.loads(files['loki/loki-config.yaml']);ldata['limits_config']['retention_period'] = str(settings.retention.loki_days) + 'd'
                files['loki/loki-config.yaml'] = setup.dumps(ldata).encode()
                comp = json.loads(files.pop('compose.yaml'))
                for name in ('fortilens-usage','fortilens-portal'):
                    if name in comp['services']:comp['services'][name]['image']='fortilens-usage:'+self.project
                comp['services']['prometheus']['command'] = [('--storage.tsdb.retention.time=' + str(settings.retention.prometheus_days) + 'd') if x.startswith('--storage.tsdb.retention.time=') else x for x in comp['services']['prometheus']['command']]
                files[self.compose_name] = setup.dumps(comp).encode()
            if self.compose() and 'ca' in pending and pending['ca']:
                files['secrets/web-fortigate-ca.pem'] = pending['ca'].encode()
            files[state['site_file']] = setup.dumps(c).encode()
            records = []
            for name, data in files.items():
                target = guarded(self.root, name)
                candidate = guarded(folder / 'candidate', name)
                atomic(candidate, data, mode=0o644)
                meta=target.stat() if target.exists() else None
                records.append({'path': name, 'before': fingerprint(target), 'after': fingerprint(candidate),
                                'mode':stat.S_IMODE(meta.st_mode) if meta else 0o644,
                                'uid':meta.st_uid if meta else None,'gid':meta.st_gid if meta else None})
            plan = {'id': identity, 'revision': revision, 'status': 'PREPARED', 'files': records, 'fresh': not state['configured'],
                    'settings': state['settings'], 'previous': state, 'created': time.time(), 'secret_fingerprint':fingerprint(self.secret_path)}
            atomic(folder / 'plan.json', plan)
            return self.review(plan)

    def review(self, plan):
        return {'plan_id': plan['id'], 'revision': plan['revision'], 'status': plan['status'], 'fresh_installation': plan['fresh'],
                'changed_files': [r['path'] for r in plan['files'] if r['before'] != r['after']],
                'services_requiring_review': list(SERVICES), 'settings': plan['settings'],
                'warnings': ['Service restarts may create collection gaps. Existing alert rules are not changed.',
                             'Syslog sender configuration and FortiGate read permissions must be set by its administrator.',
                             'WAN/SLA/interface views share the existing exporter.', 'Missing telemetry remains unknown; a completed deployment does not certify traffic accuracy.']}

    def plan(self, identity):
        if not re.fullmatch(r'[a-f0-9]{32}', identity):
            raise Problem('invalid_plan', 'Choose a server-generated configuration plan.')
        folder = self.control / 'plans' / identity
        try:plan = load(folder / 'plan.json')
        except (OSError,ValueError) as exc:
            report_failure('configuration_plan_read_failed',exc)
            raise Problem('plan_unavailable','The reviewed plan is unreadable. Review host storage and prepare a new candidate.',503) from None
        if not plan:
            raise Problem('plan_missing', 'This reviewed plan is not available.', 404)
        if (not isinstance(plan,dict) or plan.get('id')!=identity or type(plan.get('revision')) is not int
                or plan.get('status') not in ('PREPARED','APPLYING','APPLIED','FAILED','ROLLED_BACK')
                or not isinstance(plan.get('files'),list) or not isinstance(plan.get('settings'),dict)
                or not isinstance(plan.get('previous'),dict)):
            raise Problem('plan_invalid','The reviewed plan is incomplete. Review the current installation and prepare a new candidate before applying.',503)
        for row in plan['files']:
            if (not isinstance(row,dict) or not isinstance(row.get('path'),str)
                    or type(row.get('mode')) is not int or not 0<=row['mode']<=0o7777
                    or not all(key in row for key in ('before','after','uid','gid'))
                    or row['before'] is not None and not re.fullmatch(r'[a-f0-9]{64}',str(row['before']))
                    or not re.fullmatch(r'[a-f0-9]{64}',str(row['after']))):
                raise Problem('plan_invalid','The reviewed plan is incomplete. Review the current installation and prepare a new candidate before applying.',503)
            guarded(self.root,row['path'])
        return folder, plan

    def install_files(self, identity):
        with self.lock:
            folder, plan = self.plan(identity)
            if plan['status'] != 'PREPARED' or plan['revision'] != self.state()['revision']:
                raise Problem('stale_plan', 'Prepare and review the latest revision.', 409)
            if fingerprint(self.secret_path)!=plan['secret_fingerprint']:
                raise Problem('draft_changed','The write-only credential draft changed after review.',409)
            for row in plan['files']:
                if fingerprint(guarded(self.root,row['path'])) != row['before'] or fingerprint(guarded(folder/'candidate',row['path'])) != row['after']:
                    raise Problem('external_edit', 'Files changed after review. Prepare a new candidate; nothing was overwritten.', 409)
            for row in plan['files']:
                if row['before'] is not None:
                    atomic(guarded(folder/'before',row['path']), guarded(self.root,row['path']).read_bytes())
            plan['status'] = 'APPLYING';atomic(folder/'plan.json', plan)
            if plan['settings']['monitoring']['accounting'] and not (self.control/'accounting-contract.json').exists():
                atomic(self.control/'accounting-contract.json',{k:plan['settings'][k] for k in ('vdom','accounting_timezone','expected_devid')})
            for row in plan['files']:
                self.write_target(row,guarded(folder/'candidate',row['path']).read_bytes())
            return plan

    def restore(self, identity):
        with self.lock:
            folder, plan = self.plan(identity)
            if plan['status'] not in ('APPLYING', 'APPLIED', 'FAILED'):
                raise Problem('rollback_unavailable', 'This plan has no applied configuration to restore.', 409)
            for row in plan['files']:
                if fingerprint(guarded(self.root,row['path'])) not in (row['before'], row['after']):
                    raise Problem('rollback_external_edit', 'An operator changed a managed file. Rollback will not overwrite it.', 409)
            for row in plan['files']:
                if row['before'] is not None:
                    self.write_target(row,guarded(folder/'before',row['path']).read_bytes())
                # Newly created files/volumes are retained for recovery, never deleted.
            previous = plan['previous'];previous['revision'] = self.state()['revision'] + 1
            previous['applied_revision'] = previous['revision'] if previous['configured'] else None
            atomic(self.state_path, previous)
            # Do not record a completed rollback before its configuration state
            # is durable. A failed state write keeps the plan recoverable.
            plan['status'] = 'ROLLED_BACK';atomic(folder/'plan.json', plan)
            self.audit('configuration_rollback')
            return previous

    def write_target(self,row,data):
        target=guarded(self.root,row['path'])
        # Directory mounts must be traversable by the existing nonroot service
        # users. The installation/control roots and host secret directory stay
        # private; individual secret binds retain their container-readable mode.
        missing=[];parent=target.parent
        while not parent.exists() and parent!=self.root:
            missing.append(parent);parent=parent.parent
        for parent in reversed(missing):
            parent.mkdir(mode=0o700 if parent.name=='secrets' else 0o755)
        atomic(target,data,mode=row['mode'])
        if hasattr(os,'geteuid') and os.geteuid()==0 and row['uid'] is not None:
            os.chown(target,row['uid'],row['gid'])

    def complete(self, identity):
        with self.lock:
            folder, plan = self.plan(identity)
            state = self.state();state.update(configured=True, applied_revision=plan['revision'], applied=plan['settings'])
            plan['status'] = 'APPLIED';atomic(folder/'plan.json', plan)
            atomic(self.state_path, state)
            atomic(self.secret_path, {})
            self.audit('configuration_applied', [row['path'] for row in plan['files'] if row['before'] != row['after']])
