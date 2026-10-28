"""Reviewable, hash-guarded config upgrades. No Docker or device mutations."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import time


class UpgradeError(ValueError):
    pass


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def guarded(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise UpgradeError('Unsafe upgrade path')
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise UpgradeError('Upgrade path escapes installation')
    for path in (target, *target.parents):
        if path == root.parent:
            break
        if path.is_symlink():
            raise UpgradeError('Upgrade does not follow symlinks')
    return target


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.fortilens-new')
    if temporary.exists():
        raise UpgradeError('An unfinished file replacement needs review')
    temporary.write_bytes(data)
    # Compose file-backed secrets retain host permissions. Nonroot container
    # users must read the bind-mounted file; the installation parent is 0700.
    temporary.chmod(0o644)
    os.replace(temporary, path)


def prepare(module, installation, config, *, enable_accounting=False, zone=None, expected_devid=None):
    """Preserve custom service settings; refuse formats this tool cannot merge."""
    root = Path(installation).resolve()
    if config.get('accounting_enabled'):
        raise UpgradeError('This migration is for an existing six-service installation; preserve the current engine database and review later upgrades separately')
    if not enable_accounting:
        raise UpgradeError('Persistent accounting requires explicit --enable-accounting')
    for name in ('compose.yaml', 'site.json', 'prometheus/prometheus.yml', 'alloy/config.alloy', 'secrets/fortigate-key.yaml'):
        guarded(root, name)
    try:
        compose = module.load(root / 'compose.yaml')
        prom = module.load(root / 'prometheus/prometheus.yml')
    except ValueError:
        raise UpgradeError('Automatic merge supports generated JSON/YAML only. Custom YAML needs a manual reviewed merge; no files changed') from None
    if 'fortilens-usage' in compose['services'] or 'usage-data' in compose.get('volumes', {}):
        raise UpgradeError('The new service/volume name already exists; nothing replaced')
    if any(x.get('job_name') == 'fortilens-accounting' for x in prom['scrape_configs']):
        raise UpgradeError('Accounting scrape job already exists; review it manually')
    alloy = (root / 'alloy/config.alloy').read_text(encoding='utf-8')
    pattern = r'(?m)^(\s*use_incoming_timestamp\s*=\s*)(true|false)(\s*)$'
    if len(re.findall(pattern, alloy)) != 1:
        raise UpgradeError('Custom/multiple syslog timestamp settings require manual review')
    new = module.validate_config({**config, 'accounting_enabled': True, 'loki_receive_timestamps': True,
        'accounting_timezone': zone or config['accounting_timezone'],
        'expected_devid': config['expected_devid'] if expected_devid is None else expected_devid})
    migration = 'usage-' + str(time.time_ns())
    plan_root = guarded(root, '.upgrades/' + migration)
    plan_root.mkdir(parents=True, mode=0o700)
    plan_root.chmod(0o700)
    draft_config = dict(new)
    if draft_config['api_ca_file'] and not Path(draft_config['api_ca_file']).is_absolute():
        draft_config['api_ca_file'] = str(guarded(root, draft_config['api_ca_file']))
    token = module.load(root / 'secrets/fortigate-key.yaml')[module.target_url(config)]['token']
    # Draft secrets are private; the existing API/Grafana secrets are not changed.
    draft = module.render(draft_config, plan_root / 'draft', token)
    generated = module.load(draft / 'compose.yaml')
    compose = deepcopy(compose)
    old_image='quay.io/prometheuscommunity/fortigate-exporter@sha256:0880186a3dd69e66dc28be8a4cd37569637170e90c2aa657d2b3172a58551725'
    repair_image=compose['services'].get('fortigate-exporter',{}).get('image')==old_image
    if repair_image:
        compose['services']['fortigate-exporter']['image']=generated['services']['fortigate-exporter']['image']
    compose['services']['fortilens-usage'] = generated['services']['fortilens-usage']
    compose.setdefault('volumes', {})['usage-data'] = {}
    compose.setdefault('secrets', {}).update({k: generated['secrets'][k] for k in ('usage_admin_password', 'fortigate_api_token')})
    compose['services']['prometheus'].setdefault('secrets', []).append('usage_admin_password')
    prom['scrape_configs'].append(module.load(draft / 'prometheus/prometheus.yml')['scrape_configs'][-1])
    new['api_ca_file'] = config['api_ca_file']
    files = {'compose.yaml': module.dumps(compose).encode(), 'site.json': module.dumps(new).encode(),
        'prometheus/prometheus.yml': module.dumps(prom).encode(),
        'alloy/config.alloy': re.sub(pattern, lambda m: m[1]+'false'+m[3], alloy).encode()}
    for name in ('usage_admin_password', 'fortigate_api_token'):
        files['secrets/' + name] = (draft / 'secrets' / name).read_bytes()
    for path in (draft / 'usage').iterdir():
        files['usage/' + path.name] = path.read_bytes()
    candidate = plan_root / 'candidate'
    records = []
    for name, data in files.items():
        target = guarded(root, name)
        if name not in ('compose.yaml', 'site.json', 'prometheus/prometheus.yml', 'alloy/config.alloy') and target.exists():
            raise UpgradeError('A proposed new file already exists; nothing replaced')
        put(candidate / name, data)
        records.append({'path': name, 'before': fingerprint(target), 'after': fingerprint(candidate / name)})
    # A styled dashboard is only a candidate: never overwrite saved/live edits.
    put(plan_root / 'dashboard.visual-candidate.json', (draft / 'grafana/dashboard.json').read_bytes())
    manifest = {'schema_version': 1, 'id': migration, 'installation': str(root), 'status': 'PREPARED', 'files': records,
        'changes': ['Add optional worker and a new usage-data volume; preserve existing volume names and service settings.',
            'Add authenticated accounting scrape; keep existing scrape jobs.',
            'Timestamp newly received Loki entries at receipt; retain FortiGate eventtime inside the line.',
            'Preserve existing secrets, retention, datasource provisioning, alerts and saved dashboards.'],
        'runtime_action': 'After reviewing and applying files, explicitly run setup.py start. This plan never starts/restarts services.'}
    if repair_image:
        manifest['changes'].append('Repair the v0.1.0 default exporter reference: its old digest is unavailable from that registry. Pin the published v1.25.0 linux/amd64 manifest; custom image references are preserved.')
    put(plan_root / 'plan.json', module.dumps(manifest).encode())
    return manifest


def read_plan(root, identity):
    if not re.fullmatch(r'usage-[0-9]+', identity):
        raise UpgradeError('Choose a generated upgrade ID')
    plan_root = guarded(Path(root), '.upgrades/' + identity)
    manifest = json.loads((plan_root / 'plan.json').read_text())
    if manifest.get('installation') != str(Path(root).resolve()) or manifest.get('id') != identity:
        raise UpgradeError('This plan belongs to a different installation')
    allowed={'compose.yaml','site.json','prometheus/prometheus.yml','alloy/config.alloy',
        'secrets/usage_admin_password','secrets/fortigate_api_token',
        *('usage/'+name for name in ('accounting.py','interfaces.py','wan_health.py','usage_service.py','reporting.html','Dockerfile'))}
    if {x['path'] for x in manifest['files']}!=allowed or len(manifest['files'])!=len(allowed):
        raise UpgradeError('Unexpected migration file list')
    return plan_root, manifest


def apply(module, root, identity):
    root = Path(root).resolve()
    plan_root, manifest = read_plan(root, identity)
    if manifest['status'] != 'PREPARED':
        raise UpgradeError('Plan has already been applied or rolled back')
    # Check every input before replacing anything. Include operator edits and
    # concurrent preparations; never silently overwrite either.
    for item in manifest['files']:
        target = guarded(root, item['path'])
        candidate = guarded(plan_root / 'candidate', item['path'])
        if fingerprint(target) != item['before'] or fingerprint(candidate) != item['after']:
            raise UpgradeError('Files changed since preparation; prepare/review a new plan')
    for item in manifest['files']:
        if item['before'] is not None:
            put(plan_root / 'before' / item['path'], (root / item['path']).read_bytes())
    # Write recovery metadata first so an interrupted replacement is reviewable.
    manifest['status'] = 'APPLYING'
    put(plan_root / 'plan.json', module.dumps(manifest).encode())
    for item in manifest['files']:
        put(root / item['path'], (plan_root / 'candidate' / item['path']).read_bytes())
    manifest['status'] = 'APPLIED'
    put(plan_root / 'plan.json', module.dumps(manifest).encode())
    return manifest


def rollback(module, root, identity):
    root = Path(root).resolve()
    plan_root, manifest = read_plan(root, identity)
    if manifest['status'] not in ('APPLIED', 'APPLYING'):
        raise UpgradeError('No applied change to roll back')
    for item in manifest['files']:
        current = fingerprint(guarded(root, item['path']))
        if current not in (item['after'], item['before']):
            raise UpgradeError('A file was edited after application; manual merge required')
        if item['before'] is not None and fingerprint(guarded(plan_root / 'before', item['path'])) != item['before']:
            raise UpgradeError('The rollback backup failed its hash check')
    for item in manifest['files']:
        if item['before'] is not None:
            put(root / item['path'], (plan_root / 'before' / item['path']).read_bytes())
        # Added worker files and passwords remain private for recovery. The old
        # Compose no longer references them; no database or volume is deleted.
    manifest['status'] = 'ROLLED_BACK'
    put(plan_root / 'plan.json', module.dumps(manifest).encode())
    return manifest
