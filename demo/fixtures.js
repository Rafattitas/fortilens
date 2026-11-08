// Synthetic FortiLens API responses. No company data: names, addresses and
// numbers are invented examples (RFC 5737 documentation addresses only).
// Shared by the documentation screenshots (Node) and the online demo (browser).
(function (root) {
  'use strict';
  const GiB = 2 ** 30;
  const day = 86400;
  const pad = n => String(n).padStart(2, '0');
  const sum = (rows, key) => rows.reduce((n, row) => n + row[key], 0);

  function create(nowMs) {
    const now = nowMs;
    const seconds = now / 1000;
    const iso = s => new Date(s * 1000).toISOString().replace('.000Z', '+00:00');
    const startOfDay = Math.floor(seconds / day) * day;
    const dateOf = s => new Date(s * 1000).toISOString().slice(0, 10);
    const monthOf = offset => {const d = new Date(now); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() + offset); return d.toISOString().slice(0, 7);};

    const settings = {
      site_name: 'Synthetic lab · demo data', installation_name: 'synthetic-lab',
      timezone: 'UTC', date_format: 'YYYY-MM-DD', fortigate_host: '192.0.2.1',
      api_port: 443, vdom: 'root', sla: 'synthetic-sla', api_tls_verify: true,
      wan_interfaces: ['uplink-a', 'uplink-b'], wan_confirmed: true,
      syslog_protocol: 'udp', syslog_port: 1514, syslog_format: 'fortigate', syslog_network: 'bridge',
      accounting_timezone: 'UTC', expected_devid: 'SYNTHETIC-FIXTURE',
      grafana_port: 3001, usage_port: 3002, monitoring_interval: 60,
      monitoring: {wan: true, sla: true, interfaces: true, syslog: true, accounting: true, vlans: true, server: true},
      retention: {loki_days: 60, prometheus_days: 90, ledger_days: 8, summary_days: 400, database_bytes: 9 * GiB},
    };
    const vlans = [
      {key: 'vlan:100:staff', status: 'MAPPED', vlan_id: 100, interface: 'staff'},
      {key: 'vlan:200:guests', status: 'MAPPED', vlan_id: 200, interface: 'guests'},
      {key: 'unknown:lab', status: 'UNKNOWN', vlan_id: null, interface: 'lab'},
    ];
    const definitions = [
      ['Design workstation', 31.20, 2.86, 0], ['Build server', 22.40, 7.20, 0],
      ['Research laptop', 18.10, 1.24, 0], ['Guest notebook', 8.92, .74, 1],
      ['Office tablet', 5.81, .34, 0], ['Lab endpoint', 3.21, .17, 2],
      ['Meeting display', 1.67, .08, 0], ['Guest phone', .91, .06, 1],
    ];
    const devices = definitions.map(([hostname, down, up, segment], i) => {
      const download_bytes = Math.round(down * 1e9), upload_bytes = Math.round(up * 1e9);
      return {device_id: 'synthetic-' + (i + 1), hostname, ip: '192.0.2.' + (i + 10),
        mac: i === 5 ? '' : '02:00:00:00:00:' + pad(i + 1),
        identity_kind: i === 5 ? 'IP' : 'MAC', vdom: 'root', source_interfaces: [vlans[segment].interface],
        vlans: [vlans[segment]], download_bytes, upload_bytes, total_bytes: download_bytes + upload_bytes};
    });
    const history = (rows, count, monthly = false) => Array.from({length: count}, (_, i) => ({
      period: monthly ? monthOf(i - count + 1) : dateOf(startOfDay - (count - 1 - i) * day),
      upload_bytes: Math.round(sum(rows, 'upload_bytes') * (monthly ? [3, 3.6, 2.7, 4, 3.8, 4][i] : .6 + i * .1)),
      download_bytes: Math.round(sum(rows, 'download_bytes') * (monthly ? [3, 3.6, 2.7, 4, 3.8, 4][i] : .6 + i * .1)),
      pending_upload_bytes: 0, pending_download_bytes: 0,
    }));
    function report(url, device = false, degraded = false) {
      const selected = device ? devices.filter(d => d.device_id === url.searchParams.get('device')) : devices;
      const rows = selected.length ? selected : devices.slice(0, 1);
      const segmentRows = vlans.flatMap(vlan => {
        const endpoints = rows.filter(d => d.vlans[0].key === vlan.key);
        return endpoints.length ? [{vlan, observed_endpoint_identities: endpoints.length,
          download_bytes: sum(endpoints, 'download_bytes'), upload_bytes: sum(endpoints, 'upload_bytes'),
          total_bytes: sum(endpoints, 'total_bytes')}] : [];
      });
      return {devices: rows, vlans: segmentRows,
        totals: {upload_bytes: sum(rows, 'upload_bytes'), download_bytes: sum(rows, 'download_bytes'), pending_upload_bytes: 1400000, pending_download_bytes: 7200000},
        flags: {unknown_vlan: 14, counter_boundary_uncertain: 3},
        source_record_classifications: {INTERNET: 48126, INTERNAL: 1942, UNCLASSIFIED: 14},
        processing: {last_poll_ok: true, last_delivery_ns: (seconds - (degraded ? 1200 : 12)) * 1e9,
          processing_lag_seconds: degraded ? 1208 : 4, last_failure_seconds: null, last_error: '',
          source_identity_bound: true, inventory_available: true, parser_rejections: 2, late_rejections: 0,
          replay_candidates: 0, loki_timestamps_at_receipt: true},
        observed_endpoint_identities: rows.length, unverified_endpoint_identities: rows.filter(d => d.identity_kind !== 'MAC').length,
        status: 'RECORDED_ONLY', timezone: 'UTC',
        start_utc: url.searchParams.get('period') === 'week' ? iso(seconds - 7 * day) : iso(startOfDay),
        end_utc: iso(seconds), daily_history: history(rows, 5), monthly_history: history(rows, 6, true)};
    }
    // Generic Application Control labels; examples, not observed traffic.
    const appDefinitions = [
      ['QUIC', 'Network.Service', 'low', true, 6, 9.80], ['Microsoft Teams', 'Collaboration', 'elevated', false, 5, 7.42],
      ['Video streaming', 'Video/Audio', 'elevated', false, 4, 6.15], ['Software Update', 'Update', 'low', false, 7, 4.88],
      ['Cloud Storage', 'Storage.Backup', 'medium', false, 3, 3.92], ['SSL_TLSv1.3', 'Network.Service', 'medium', true, 8, 3.10],
      ['Web Mail', 'Email', 'medium', false, 6, 1.64], ['AI Assistant', 'GenAI', 'medium', false, 3, 0.86],
      ['Code Hosting', 'Collaboration', 'low', false, 2, 0.71], ['Remote Desktop', 'Remote.Access', 'high', false, 1, 0.38],
      ['', 'unscanned', '', false, 4, 0.52],
    ];
    function applications(url) {
      const rows = appDefinitions.map(([app, category, risk, generic, count, gb]) => {const total = Math.round(gb * 1e9), upload = Math.round(total * .12);
        return {app: app || null, category, risk: risk || null, generic, devices: count, download_bytes: total - upload, upload_bytes: upload,
          pending_upload_bytes: 0, pending_download_bytes: 0, records: count * 900, total_bytes: total};});
      const total = sum(rows, 'total_bytes'), generic = sum(rows.filter(x => x.generic), 'total_bytes'), unidentified = sum(rows.filter(x => !x.app), 'total_bytes');
      const categories = [...new Set(rows.map(x => x.category))].map(category => {const group = rows.filter(x => x.category === category);
        return {category, apps: group.length, devices: Math.max(...group.map(x => x.devices)), download_bytes: sum(group, 'download_bytes'), upload_bytes: sum(group, 'upload_bytes'), total_bytes: sum(group, 'total_bytes')};}).sort((a, b) => b.total_bytes - a.total_bytes);
      const genai = rows.find(x => x.category === 'GenAI');
      return {start_utc: iso(startOfDay), end_utc: iso(seconds), boundary: '[start,end)', timezone: 'UTC',
        applications: rows, categories, devices: [], totals: {upload_bytes: sum(rows, 'upload_bytes'), download_bytes: sum(rows, 'download_bytes'), pending_upload_bytes: 0, pending_download_bytes: 0},
        summary: {total_bytes: total, applications: rows.length, top_application: 'Microsoft Teams', identified_bytes: total - generic - unidentified, unidentified_bytes: unidentified, generic_bytes: generic, genai_bytes: genai.total_bytes, genai_devices: genai.devices},
        unidentified_bytes: unidentified, untracked: null, tracking_started_ns: (seconds - 30 * day) * 1e9, applications_since: dateOf(seconds - 30 * day),
        traffic_coverage: 'UNKNOWN', status: 'RECORDED_ONLY', period: url.searchParams.get('period') || 'today', processing: report(url).processing};
    }
    // FortiView-style live rates in bits per second; generic example names only.
    function live(jitter = 1) {
      const doing = [['Microsoft Teams', 'Web Mail'], ['Software Update'], ['Video streaming', 'Cloud Storage'], ['Video streaming'], ['AI Assistant', 'Code Hosting'], ['Remote Desktop'], ['Web Mail'], ['Video streaming']];
      const rows = devices.map((d, i) => ({device_id: d.device_id, hostname: d.hostname, ip: d.ip, mac: d.mac, profile: i % 3 ? {vendor: 'Example', os: 'Windows', type: 'Computer'} : {os: 'Android', type: 'Mobile'},
        vlan: d.vlans[0], download_bps: Math.round([18.4, 9.6, 7.2, 4.1, 2.6, 1.2, 0.6, 0.3][i] * 1e6 * jitter), upload_bps: Math.round([2.1, 6.8, 0.9, 0.4, 0.5, 0.9, 0.1, 0.05][i] * 1e6 * jitter), sessions: [42, 18, 25, 12, 9, 6, 4, 3][i], top_apps: doing[i]}));
      const download = sum(rows, 'download_bps'), upload = sum(rows, 'upload_bps');
      const apps = [['Video streaming', 'Video/Audio', 14.1, 0.9], ['Microsoft Teams', 'Collaboration', 6.2, 1.8], ['Software Update', 'Update', 5.9, 3.1], ['Cloud Storage', 'Storage.Backup', 3.4, 4.2], ['AI Assistant', 'GenAI', 1.9, 0.4], ['Web Mail', 'Email', 1.1, 0.3]]
        .map(([app, category, d, u], i) => ({appid: String(41000 + i), app, category, risk: 'medium', download_bps: Math.round(d * 1e6 * jitter), upload_bps: Math.round(u * 1e6 * jitter), sessions: 10 + i}));
      const segmentRows = vlans.map(vlan => {const r = rows.filter(d => d.vlan.key === vlan.key); return {vlan, download_bps: sum(r, 'download_bps'), upload_bps: sum(r, 'upload_bps'), devices: r.length};}).filter(x => x.devices);
      return {observed_at: seconds, source: 'FORTIVIEW_REALTIME', unit: 'bits_per_second', totals_source: 'INTERFACE_COUNTERS',
        totals: {download_bps: Math.round(download * 1.08), upload_bps: Math.round(upload * 1.12), sessions: sum(rows, 'sessions'), devices: rows.length},
        listed: {download_bps: download, upload_bps: upload}, listed_sources: rows.length, truncated: false,
        links: [{interface: 'uplink-a', download_bps: Math.round(download * 0.78), upload_bps: Math.round(upload * 0.8), measured_seconds: 5}, {interface: 'uplink-b', download_bps: Math.round(download * 0.3), upload_bps: Math.round(upload * 0.32), measured_seconds: 5}],
        devices: rows, vlans: segmentRows, applications: apps, wan_interfaces: ['uplink-a', 'uplink-b'],
        note: 'Synthetic live estimates.'};
    }
    const inventory = () => ({schema_version: 1, collected_at: new Date(now - 600000).toISOString(), interfaces: [
      ...vlans.filter(v => v.vlan_id).map(v => ({name: v.interface, vdom: 'root', type: 'vlan', vlan_id: v.vlan_id, parent: 'internal', role: 'lan'})),
      {name: 'printers', vdom: 'root', type: 'vlan', vlan_id: 300, parent: 'internal', role: 'lan'},
      {name: 'uplink-a', vdom: 'root', type: 'physical', vlan_id: null, parent: null, role: 'wan'}]});
    function wan(degraded = false) {
      const segments = (second = false) => [
        {start: seconds - day, end: seconds - day * .79, state: 'UP'},
        {start: seconds - day * .76, end: seconds - day * .46, state: 'UP'},
        {start: seconds - day * .46, end: seconds - day * .43, state: 'UP', degraded: true},
        {start: seconds - day * .43, end: seconds - 1800, state: 'UP'},
        {start: seconds - 1800, end: seconds, state: degraded && second ? 'DOWN' : 'UP'},
      ];
      return {observed_at: seconds, route: {status: 'OBSERVED', destination: '198.51.100.1', interface: 'uplink-a', scope: 'Synthetic destination-specific route evidence.'},
        interfaces: ['uplink-a', 'uplink-b'].map((name, i) => ({interface: name,
          internet_state: degraded && i ? 'DOWN' : 'UP', physical_state: 'UP',
          download_mbps: i ? 12.4 : 86.7, upload_mbps: i ? 1.2 : 8.3,
          latency_ms: degraded && i ? null : i ? 24.8 : 12.6, jitter_ms: i ? 2.8 : 1.4, packet_loss_pct: degraded && i ? 100 : 0,
          history: {window_seconds: day, availability_percent: degraded && i ? 97.9 : 100, valid_state_coverage_percent: 97,
            segments: segments(!!i), outages: degraded && i ? [{start: seconds - 1800, end: seconds, observed_seconds: 1800, end_censored: true}] : [],
            degradation: [{start: seconds - day * .46, end: seconds - day * .43, observed_seconds: 2592, end_censored: false}]}}))};
    }
    function system(dashboard = '/grafana/d/synthetic/') {
      return {services: ['fortigate-exporter', 'prometheus', 'grafana', 'loki', 'alloy', 'node-exporter', 'fortilens-usage', 'fortilens-portal', 'fortilens-syslog'].map(service => ({service, status: 'running', health: service.startsWith('fortilens-') ? 'healthy' : ''})),
        disk: {total: 64 * GiB, used: 18 * GiB, available: 46 * GiB}, storage_projection: {available: true,
          message: 'Synthetic 24-hour growth trend. Compaction and future traffic can change actual use.',
          components: Object.fromEntries([['loki', 480 * 2 ** 20, 160 * 2 ** 20], ['prometheus', 58 * 2 ** 20, 9 * 2 ** 20], ['fortilens-usage', 2.4 * GiB, 420 * 2 ** 20]].map(([key, bytes, growth]) => [key, {bytes, forecast_status: 'available', horizon_seconds: day, projection_bytes: growth, observation_seconds: 7200, sample_count: 9, projection_at_seconds: seconds, ...(key === 'fortilens-usage' ? {capacity_limit_bytes: 9 * GiB, ledger_days: 8, summary_days: 400} : {})}]))},
        links: {console: true, dashboard}, latest_job: null,
        hostname: 'synthetic-lab', os: 'Ubuntu 24.04 LTS (synthetic fixture)', architecture: 'x86_64',
        cpu_count: 4, ram_bytes: 8 * GiB, docker: 'Available', server_time_seconds: seconds, server_clock_supported: true};
    }
    function view() {
      return {settings, configured: true, revision: 7, applied_revision: 7, pending_changes: false,
        scope_locked: true, secret_status: {api_token_present: true, custom_ca_present: true}, active_monitoring: settings.monitoring,
        active_sla: settings.sla, discovery: {required: true, current: true, reason: 'Synthetic discovery matches this fixture.', changed_fields: []}};
    }
    const diagnostics = dashboard => ({system: system(dashboard), monitoring: {status: 'Recorded evidence', checks: [
      {id: 'services', status: 'PASS', message: 'All nine synthetic monitoring services are running.'},
      {id: 'recent_logs', status: 'PASS', message: 'Recent delivery is observed in this synthetic fixture.'},
      {id: 'accounting_poll', status: 'PASS', message: 'The accounting engine recently processed a poll.'},
      {id: 'accounting_lag', status: 'PASS', message: 'Synthetic usage processing is near the present.'},
      {id: 'accounting_metadata', status: 'WARN', message: 'Some retained records have unknown segment mappings.', action: 'Inspect Data quality; do not infer historical VLANs from the current IP subnet.'},
      {id: 'fortigate_tls', status: 'PASS', message: 'Certificate verification is enabled in this synthetic configuration.'},
      {id: 'completeness', status: 'UNKNOWN', message: 'Received records do not certify complete traffic collection.', action: 'Compare sender, listener and processing evidence before interpreting totals.'},
    ]}, collection: {latest_log_at: new Date(now - 12000).toISOString(), latest_metric_at: new Date(now - 15000).toISOString(), processing_lag_seconds: 4, log_timestamp_kind: 'receipt time'}});

    // Answer one read-only API request. Returns null for routes without a fixture.
    function respond(url, {auth = 'signed-in', degraded = false, dashboard, jitter = 1} = {}) {
      const p = url.pathname.slice(url.pathname.indexOf('/api/'));
      const consoleRoute = url.pathname.includes('/console/api/');
      if (!consoleRoute) {
        if (p === '/api/bootstrap/status') return {status: 200, payload: {required: auth === 'first-run'}};
        if (p === '/api/session') return auth === 'signed-in'
          ? {status: 200, payload: {username: 'synthetic-admin', csrf: 'synthetic-csrf'}}
          : {status: 401, payload: {error: 'authentication_required', message: 'Sign in to the synthetic lab.'}};
        if (p === '/api/settings') return {status: 200, payload: view()};
        if (p === '/api/system') return {status: 200, payload: system(dashboard)};
        if (p === '/api/diagnostics') return {status: 200, payload: diagnostics(dashboard)};
        return null;
      }
      const routes = {
        '/api/session': () => ({authenticated: true}),
        '/api/portal': () => ({enabled: true}),
        '/api/report': () => report(url, false, degraded),
        '/api/device': () => report(url, true, degraded),
        '/api/wan': () => wan(degraded),
        '/api/applications': () => applications(url),
        '/api/inventory': () => inventory(),
        '/api/live': () => live(jitter),
      };
      return routes[p] ? {status: 200, payload: routes[p]()} : null;
    }
    return {now, seconds, settings, devices, respond};
  }

  const api = {create};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FortiLensFixtures = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
