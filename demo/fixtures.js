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
      monitoring: {wan: true, sla: true, interfaces: true, syslog: true, accounting: true, vlans: true, server: true, websites: true},
      retention: {loki_days: 60, prometheus_days: 90, ledger_days: 8, summary_days: 400, database_bytes: 9 * GiB},
      notifications: {
        telegram: {enabled: true, chat_id: '-1001234567890'},
        email: {enabled: true, host: 'smtp.example.com', port: 587, security: 'starttls', user: 'fortilens', sender: 'fortilens@example.com', to: ['noc@example.com']},
        rules: Object.fromEntries(['line_down', 'line_slow', 'line_starved', 'heavy_device', 'logs_stopped', 'fortigate_unreachable', 'accounting_behind', 'storage_full', 'sdwan_changed', 'line_loss', 'line_full', 'checks_disagree', 'new_device'].map(name => [name, true])),
        work_hours: {start: '08:00', end: '18:00', days: ['sun', 'mon', 'tue', 'wed', 'thu']},
        reports: {daily: {enabled: true, time: '08:00'}, weekly: {enabled: true, day: 'sun', time: '08:00'}, monthly: {enabled: true, time: '08:00'}},
      },
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
      // The build server and office tablet follow the engineering rule to uplink-b; the rest go to uplink-a.
      const rule = {engineering: {order: 1, name: 'engineering', mode: 'sla', lines: ['uplink-b', 'uplink-a'], all_traffic: true, certain: true},
        everyone: {order: 2, name: 'everyone-else', mode: 'manual', lines: ['uplink-a'], all_traffic: true, certain: true}};
      for (const [i, d] of rows.entries()) {
        const line = i === 1 || i === 4 ? 'uplink-b' : 'uplink-a';
        d.lines = {[line]: {download_bps: d.download_bps, upload_bps: d.upload_bps, sessions: d.sessions}};
        d.sdwan = {rules: [line === 'uplink-b' ? rule.engineering : rule.everyone], settled: true};
      }
      const onLine = (line, key) => sum(rows.filter(d => d.lines[line]).map(d => d.lines[line]), key);
      const download = sum(rows, 'download_bps'), upload = sum(rows, 'upload_bps');
      const apps = [['Video streaming', 'Video/Audio', 14.1, 0.9], ['Microsoft Teams', 'Collaboration', 6.2, 1.8], ['Software Update', 'Update', 5.9, 3.1], ['Cloud Storage', 'Storage.Backup', 3.4, 4.2], ['AI Assistant', 'GenAI', 1.9, 0.4], ['Web Mail', 'Email', 1.1, 0.3]]
        .map(([app, category, d, u], i) => ({appid: String(41000 + i), app, category, risk: 'medium', download_bps: Math.round(d * 1e6 * jitter), upload_bps: Math.round(u * 1e6 * jitter), sessions: 10 + i}));
      const segmentRows = vlans.map(vlan => {const r = rows.filter(d => d.vlan.key === vlan.key); return {vlan, download_bps: sum(r, 'download_bps'), upload_bps: sum(r, 'upload_bps'), devices: r.length};}).filter(x => x.devices);
      return {observed_at: seconds, source: 'FORTIVIEW_REALTIME', unit: 'bits_per_second', totals_source: 'INTERFACE_COUNTERS',
        totals: {download_bps: Math.round(download * 1.08), upload_bps: Math.round(upload * 1.1), sessions: sum(rows, 'sessions'), devices: rows.length},
        listed: {download_bps: download, upload_bps: upload}, listed_sources: rows.length, truncated: false,
        links: [{interface: 'uplink-a', download_bps: Math.round(onLine('uplink-a', 'download_bps') * 1.08), upload_bps: Math.round(onLine('uplink-a', 'upload_bps') * 1.1), measured_seconds: 5, sessions_now: 742, speed: {down_mbps: 100, up_mbps: 20}}, {interface: 'uplink-b', download_bps: Math.round(onLine('uplink-b', 'download_bps') * 1.08), upload_bps: Math.round(onLine('uplink-b', 'upload_bps') * 1.1), measured_seconds: 5, sessions_now: 118, speed: null}],
        devices: rows, vlans: segmentRows, applications: apps, wan_interfaces: ['uplink-a', 'uplink-b'], sdwan: {status: 'OBSERVED'},
        note: 'Synthetic live estimates.'};
    }
    // Generic example site names only; one site selected.
    function websites(url) {
      const sites = [['video.example', 9.6, 0.42, 6], ['cloud-storage.example', 3.1, 2.6, 4], ['office.example', 4.2, 1.1, 8], ['news.example', 1.2, 0.08, 7], ['ai-assistant.example', 0.9, 0.21, 3], ['software-updates.example', 0.8, 0.02, 8], ['maps.example', 0.31, 0.04, 5], ['mail.example', 0.22, 0.09, 8]]
        .map(([site, d, u, n]) => ({site, download_bytes: Math.round(d * 1e9), upload_bytes: Math.round(u * 1e9), total_bytes: Math.round((d + u) * 1e9), records: 200, devices: n}));
      const selected = url.searchParams.get('site') || 'video.example', total = sum(sites, 'total_bytes');
      const share = [0.46, 0.21, 0.14, 0.09, 0.06, 0.04];
      const rows = selected ? devices.slice(0, 6).map((d, i) => ({device_id: d.device_id, hostname: d.hostname, ip: d.ip, mac: d.mac, identity_kind: 'MAC', vlans: d.vlans, profile: null,
        download_bytes: Math.round(9.6e9 * share[i]), upload_bytes: Math.round(0.42e9 * share[i]), total_bytes: Math.round(10.02e9 * share[i]), records: 40})) : [];
      return {start_utc: new Date((seconds - 6 * day) * 1000).toISOString(), end_utc: new Date(now).toISOString(), first_day: dateOf(seconds - 6 * day), last_day: dateOf(seconds), timezone: 'UTC',
        enabled: true, since_ns: (seconds - 40 * day) * 1e9, retention_days: 90, site: selected, sites, devices: rows, truncated: false,
        summary: {total_bytes: total, sites: sites.length, top_site: sites[0].site, internet_bytes: Math.round(total / 0.82), coverage: 0.82},
        note: 'Synthetic website rows.', period: url.searchParams.get('period') || 'week'};
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
          internet_state: degraded && i ? 'DOWN' : 'UP', physical_state: 'UP', sessions: i ? 118 : 742,
          download_mbps: i ? 12.4 : 86.7, upload_mbps: i ? 1.2 : 8.3,
          latency_ms: degraded && i ? null : i ? 24.8 : 12.6, jitter_ms: i ? 2.8 : 1.4, packet_loss_pct: degraded && i ? 100 : 0,
          history: {window_seconds: day, availability_percent: degraded && i ? 97.9 : 100, valid_state_coverage_percent: 97,
            segments: segments(!!i), outages: degraded && i ? [{start: seconds - 1800, end: seconds, observed_seconds: 1800, end_censored: true}] : [],
            degradation: [{start: seconds - day * .46, end: seconds - day * .43, observed_seconds: 2592, end_censored: false}]}}))};
    }
    // Synthetic diagnosis: uplink-a is busy and loses packets at midday peaks, uplink-b
    // sometimes fails with it, and one of uplink-b's health checks misfires one morning.
    function wanInsights(url, degraded = false) {
      const span = Math.min(7 * day, Math.max(3600, Number(url.searchParams.get('seconds')) || 7 * day));
      const step = Math.max(60, Math.ceil(span / 1008 / 60) * 60), buckets = Math.ceil(span / step);
      const end = Math.floor(seconds / step) * step, start = end - buckets * step;
      const minute = i => Math.floor(((start + i * step) % day) / 60);
      const busy = i => minute(i) >= 480 && minute(i) < 1080;
      const lines = {
        'uplink-a': {sessions: i => (busy(i) ? 900 : 220), bad: i => (minute(i) >= 660 && minute(i) < 700) || (minute(i) >= 930 && minute(i) < 960), loss: [[100, 500, 0.21], [500, 1000, 2.4]], mbps: [38.2, 6.1]},
        'uplink-b': {sessions: () => 120, bad: i => minute(i) >= 670 && minute(i) < 690, loss: [[100, 500, 0.34]], mbps: [6.8, 1.1]},
      };
      const misfire = i => start + i * step >= end - 2 * day + 6 * 3600 && start + i * step < end - 2 * day + 10 * 3600;
      const links = Object.entries(lines).map(([name, l], li) => {
        const codes = Array.from({length: buckets}, (_, i) => (degraded && li && i >= buckets - Math.ceil(1800 / step) ? 'd' : l.bad(i) ? 'w' : 'g')).join('');
        const disagree = Array.from({length: buckets}, (_, i) => (li && misfire(i) ? 'p' : '.')).join('');
        const sessions = Array.from({length: buckets}, (_, i) => l.sessions(i));
        const lossMinutes = [...codes].filter(c => c === 'w').length * step / 60;
        return {interface: name, check: 'synthetic-sla', check_fallback: false, checks: ['synthetic-ping', 'synthetic-sla'], states: codes, disagree, sessions,
          speed: li ? null : {down_mbps: 100, up_mbps: 20, avg_down_pct: 38, avg_up_pct: 31, busy_down_pct: 71, busy_up_pct: 64},
          loss_by_load: l.loss.map(([min, max, loss_pct]) => ({min, max, loss_pct, samples: Math.round(buckets / l.loss.length)})),
          summary: {coverage_pct: 100, avg_sessions: sessions.reduce((a, b) => a + b, 0) / buckets, peak_sessions: Math.max(...sessions) + 140,
            avg_download_mbps: l.mbps[0], avg_upload_mbps: l.mbps[1], avg_loss_pct: li ? 0.36 : 0.88, avg_latency_ms: li ? 24.8 : 12.6,
            loss_minutes: lossMinutes, down_minutes: degraded && li ? 30 : 0, disagree_minutes: [...disagree].filter(c => c === 'p').length * step / 60}};
      });
      const total = links.reduce((n, l) => n + l.summary.avg_sessions, 0);
      for (const l of links) l.summary.session_share_pct = 100 * l.summary.avg_sessions / total;
      const incidents = [];
      links.forEach(l => {
        let begin = -1;
        for (let i = 0; i <= buckets; i++) {
          const on = i < buckets && l.states[i] !== 'g';
          if (on && begin < 0) begin = i;
          if (!on && begin >= 0) {
            if ((i - begin) * step >= 1200) incidents.push({interface: l.interface, start: start + begin * step, end: start + i * step,
              state: l.states.slice(begin, i).includes('d') ? 'DOWN' : 'LOSS', peak_loss_pct: l.states[begin] === 'd' ? 100 : 8.5,
              avg_sessions: l.sessions[begin], others: links.filter(o => o !== l).map(o => ({interface: o.interface,
                affected_fraction: [...o.states.slice(begin, i)].filter(c => c !== 'g').length / (i - begin)}))});
            begin = -1;
          }
        }
      });
      incidents.sort((x, y) => x.start - y.start);
      const shared = incidents.filter(x => x.interface === 'uplink-b' && x.others[0].affected_fraction >= .5).length;
      const [a, b] = links;
      const findings = [
        {kind: 'load', severity: 'warn', interfaces: ['uplink-a'], evidence: 'load',
          title: `uplink-a carries ${Math.round(a.summary.session_share_pct)}% of all sessions, and its loss rises with load`,
          detail: `Below 500 sessions uplink-a loses 0.21% of packets; above that it loses 2.4%. The other line carries ${Math.round(b.summary.avg_sessions)} sessions on average.`},
        ...(shared >= 3 ? [{kind: 'shared', severity: 'warn', interfaces: ['uplink-a', 'uplink-b'], evidence: 'incidents',
          title: 'uplink-a and uplink-b often lose packets at the same time',
          detail: `${shared} incidents on these two lines overlapped. When lines fail together, look at what they share: the same router, subnet or provider, or the FortiGate itself.`}] : []),
        ...(b.summary.disagree_minutes >= 60 ? [{kind: 'checks_disagree', severity: 'probe', interfaces: ['uplink-b'], evidence: 'timeline',
          title: `The health checks on uplink-b disagreed for ${b.summary.disagree_minutes >= 90 ? (b.summary.disagree_minutes / 60).toFixed(1) + ' hours' : Math.round(b.summary.disagree_minutes) + ' minutes'}`,
          detail: '“synthetic-ping” reported uplink-b dead while “synthetic-sla” reported it alive. These intervals are not counted as down time. Check that health check\'s server and settings before relying on failover that uses it.'}] : []),
      ];
      return {status: 'OBSERVED_ONLY', start, step, buckets, observed_at: seconds, window_seconds: span, check: 'synthetic-sla',
        loss_threshold_pct: 5, incident_min_seconds: 1200, links, incidents, findings, routing: {status: 'OBSERVED', note: "Rules apply in this order and the first match wins. Traffic that matches no rule follows the zone's implicit rule.", rules: [
          {id: 1, name: 'engineering', order: 1, enabled: true, mode: 'sla', sources: ['eng-net'], destinations: ['all'], services: [], restricted_by: [], lines: ['uplink-b', 'uplink-a'], health_checks: ['synthetic-sla'], issues: []},
          {id: 2, name: 'everyone-else', order: 2, enabled: true, mode: 'manual', sources: ['all'], destinations: ['all'], services: [], restricted_by: [], lines: ['uplink-a'], health_checks: [], catch_all: true, issues: [{kind: 'catch_all_manual', text: 'Takes all remaining traffic and sends it to uplink-a only. Manual mode ignores line quality: packet loss on uplink-a does not move this traffic.'}]}]},
        note: 'Synthetic diagnosis for the demo; no real network.'};
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
        scope_locked: true, accounting_scope: {vdom: settings.vdom, accounting_timezone: settings.accounting_timezone, expected_devid: settings.expected_devid}, secret_status: {api_token_present: true, custom_ca_present: true, telegram_bot_token_present: true, smtp_password_present: true}, active_monitoring: settings.monitoring,
        active_sla: settings.sla, discovery: {required: true, current: true, reason: 'Synthetic discovery matches this fixture.', changed_fields: []}};
    }
    const diagnostics = dashboard => ({system: system(dashboard), monitoring: {status: 'Recorded evidence', checks: [
      {id: 'services', status: 'PASS', message: 'All nine synthetic monitoring services are running.'},
      {id: 'recent_logs', status: 'PASS', message: 'Recent delivery is observed in this synthetic fixture.'},
      {id: 'accounting_poll', status: 'PASS', message: 'The accounting engine recently processed a poll.'},
      {id: 'accounting_lag', status: 'PASS', message: 'Synthetic usage processing is near the present.'},
      {id: 'accounting_metadata', status: 'WARN', message: 'Some retained records have unknown segment mappings.', action: 'Inspect Data quality; do not infer historical VLANs from the current IP subnet.'},
      {id: 'fortigate_tls', status: 'PASS', message: 'Certificate verification is enabled in this synthetic configuration.'},
      {id: 'exporter_probe_policy', status: 'INFO', message: 'Not collected, as chosen in Settings: System/SensorInfo. Required interface, device and SLA probes are unaffected.', action: 'To collect them again, clear these options under Settings > Monitoring & Syslog > Optional exporter capabilities.'},
      {id: 'completeness', status: 'UNKNOWN', message: 'Received records do not certify complete traffic collection.', action: 'Compare sender, listener and processing evidence before interpreting totals.'},
    ]}, collection: {latest_log_at: new Date(now - 12000).toISOString(), latest_metric_at: new Date(now - 15000).toISOString(), processing_lag_seconds: 4, log_timestamp_kind: 'receipt time'}});

    // Synthetic alert state: one alert open now and what was sent before it.
    const RULES = [['line_down', 'Line down', true], ['line_slow', 'Internet slow on a line', false], ['line_starved', 'Line carries little traffic', false],
      ['heavy_device', 'One device fills a line', false], ['logs_stopped', 'Logs stopped arriving', true], ['fortigate_unreachable', 'FortiGate unreachable', true],
      ['accounting_behind', 'Accounting behind', false], ['storage_full', 'Storage nearly full', true], ['sdwan_changed', 'SD-WAN rules changed', false],
      ['line_loss', 'Line losing packets', false, true], ['line_full', 'Line nearly full', false, true], ['checks_disagree', 'Health checks disagree', false, true],
      ['new_device', 'New device on the network', false, true]];
    function alerts() {
      const both = [{channel: 'telegram', status: 'delivered'}, {channel: 'email', status: 'delivered'}];
      const telegram = [{channel: 'telegram', status: 'delivered'}];
      const heavy = 'Build server uses 18.4 of 20 Mbps upload on uplink-a (92%). FortiGate live estimate.';
      return {enabled: true, channels: {telegram: true, email: true}, at_work: true,
        rules: Object.fromEntries(RULES.map(([name, label, critical, summary]) => [name, {label, critical, summary: !!summary, enabled: true}])),
        reports: settings.notifications.reports, reports_sent: {},
        open: [{rule: 'heavy_device', subject: 'uplink-a · Build server', title: 'One device fills a line · uplink-a · Build server', text: heavy, opened: seconds - 1500}],
        history: [
          {at: seconds - 300, key: 'heavy_device:uplink-a · Build server', kind: 'start', title: 'One device fills a line · uplink-a · Build server', text: heavy, held: false, deliveries: telegram},
          {at: seconds - 3 * 3600, key: 'line_loss:uplink-a', kind: 'start', title: 'Line losing packets · uplink-a', text: 'Packet loss 6.2% on health check synthetic-sla.', held: true, deliveries: []},
          {at: seconds - 20 * 3600, key: 'line_down:uplink-b', kind: 'end', title: 'Recovered · uplink-b', text: 'Line down ended after 14 min.', held: false, deliveries: both},
          {at: seconds - 20.3 * 3600, key: 'line_down:uplink-b', kind: 'start', title: 'Line down · uplink-b', text: 'No health check reports uplink-b alive (synthetic-ping down, synthetic-sla down).', held: false, deliveries: both},
          {at: seconds - 26 * 3600, key: 'sdwan_changed:', kind: 'event', title: 'SD-WAN rules changed', text: 'engineering now uses uplink-b, uplink-a.', held: false, deliveries: [{channel: 'telegram', status: 'failed', error: 'telegram_unreachable'}]},
        ]};
    }
    // What Preview shows in the demo: the layout of a real report, from the synthetic lab.
    function preview(kind) {
      const title = {daily: 'Daily summary', weekly: 'Weekly report', monthly: 'Monthly report'}[kind] || 'Daily summary';
      const when = new Date(now).toUTCString().slice(0, 11);
      const gb = bytes => (bytes / 1e9).toFixed(1) + ' GB';
      const top = devices.slice(0, kind === 'daily' ? 5 : 10).map(d => '  ' + d.hostname + ' (' + d.ip + ')  ' + gb(d.download_bytes) + ' down  ' + gb(d.upload_bytes) + ' up');
      const lines = kind === 'daily'
        ? ['Lines and services', 'uplink-a: line losing packets 1×', 'uplink-b: line down 1×', '', 'Alerts sent: 3']
        : ['Internet lines', '  uplink-a  avg loss 0.9%  incidents 12  busy use 71% / 64%  sessions 742', '  uplink-b  avg loss 0.4%  incidents 3  busy use not measured  sessions 118'];
      const text = [title + ' · ' + when, settings.site_name, '', ...lines, '', 'Usage',
        gb(sum(devices, 'download_bytes')) + ' down, ' + gb(sum(devices, 'upload_bytes')) + ' up, ' + devices.length + ' devices.',
        '', 'Top devices', ...top, '', 'New devices', '1 seen for the first time.', '  Guest phone · VLAN 200 · guests · ' + dateOf(seconds),
        ...(kind === 'monthly' ? ['', 'Provider report · uplink-a', 'Worked 99.82% of observed time; 2 outages of 2 minutes or longer; 5 degraded periods.'] : []),
        '', 'Usage counts only sessions the FortiGate logs. Missing data is shown as missing, never as zero.'];
      return {subject: 'FortiLens · ' + title + ' · ' + when, text: text.join('\n'), html: ''};
    }

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
        if (p === '/api/alerts') return {status: 200, payload: alerts()};
        return null;
      }
      const routes = {
        '/api/session': () => ({authenticated: true}),
        '/api/portal': () => ({enabled: true}),
        '/api/report': () => report(url, false, degraded),
        '/api/device': () => report(url, true, degraded),
        '/api/wan': () => wan(degraded),
        '/api/wan/insights': () => wanInsights(url, degraded),
        '/api/wan/incident': () => ({available: true, complete: true, interface: url.searchParams.get('interface'), speed: url.searchParams.get('interface') === 'uplink-a' ? {down_mbps: 100, up_mbps: 20} : null, sdwan: {status: 'OBSERVED'}, start: Number(url.searchParams.get('start')), end: Number(url.searchParams.get('end')), scanned_until: Number(url.searchParams.get('end')), ledger_days: 8, device_count: 14, upload_bytes: 0.6 * GiB, download_bytes: 3.1 * GiB,
          devices: [['design-ws-01', 'design', 1.4, 0.21], ['finance-lt-07', 'finance', 0.62, 0.08], ['meeting-room-tv', 'guest-wifi', 0.41, 0.02]].map(([hostname, vlan, down, up], i) => ({device_id: String(i + 1).repeat(64), hostname, ip: '192.0.2.' + (20 + i), mac: '', vlan, download_bytes: down * GiB, upload_bytes: up * GiB, records: 40 - i * 9,
            sdwan: {rules: [{order: 2, name: 'everyone-else', mode: 'manual', lines: ['uplink-a'], all_traffic: true, certain: true}], settled: true}}))}),
        '/api/applications': () => applications(url),
        '/api/inventory': () => inventory(),
        '/api/live': () => live(jitter),
        '/api/websites': () => websites(url),
      };
      return routes[p] ? {status: 200, payload: routes[p]()} : null;
    }
    return {now, seconds, settings, devices, respond, preview};
  }

  const api = {create};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FortiLensFixtures = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
