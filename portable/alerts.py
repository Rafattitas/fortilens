"""Opt-in Grafana candidates only. Never provision, unpause or send messages."""
import json
import re


def candidates(config, *, expected_log_interval=None, notification=None):
    c=config
    host=json.dumps(c['fortigate_host']);vdom=json.dumps(c['vdom']);sla=json.dumps(c['sla'])
    target='job="fortigate",instance='+host
    scope=target+',vdom='+vdom+',sla='+sla
    names=c.get('confirmed_wan_interfaces') or c.get('wan_interfaces') or []
    if names:scope+=',interface=~'+json.dumps('|'.join(re.escape(name) for name in names))
    collection=' and on(instance) (up{'+target+'} == 1) and on(instance) (probe_success{'+target+'} == 1)'
    observed=' and on(instance,vdom,interface,sla) (fortigate_virtual_wan_status{'+scope+',state="up"} == 1)'
    rules=[]
    def rule(identity,title,expression,hold,description):
        rules.append({'uid':'fortilens-'+identity,'title':title,'condition':'B','for':hold,
            'isPaused':True,'noDataState':'NoData','execErrState':'Error',
            'labels':{'project':'fortilens','category':'collection' if identity.startswith(('collection','usage','syslog')) else 'wan'},
            'annotations':{'summary':title,'description':description+' Missing telemetry is not a confirmed WAN outage.'},
            'data':[{'refId':'A','datasourceUid':'fm-prometheus','relativeTimeRange':{'from':600,'to':0},
                'model':{'refId':'A','datasource':{'type':'prometheus','uid':'fm-prometheus'},'expr':expression,'instant':True,'range':False,'intervalMs':1000,'maxDataPoints':43200}},
                {'refId':'B','datasourceUid':'__expr__','relativeTimeRange':{'from':0,'to':0},
                'model':{'refId':'B','type':'math','datasource':{'type':'__expr__','uid':'__expr__'},'expression':'$A > 0','intervalMs':1000,'maxDataPoints':43200}}]})
    if c['sla'] and names:
        rule('wan-down','Observed WAN Internet outage','(fortigate_virtual_wan_status{'+scope+',state="down"} == bool 1)'+collection,'2m',
            'Requires an explicit DOWN state while the FortiGate scrape and API probe succeed. Does not use physical link or missing series as DOWN.')
        rule('latency','High WAN latency','(fortigate_virtual_wan_latency_seconds{'+scope+'} * 1000 > bool '+str(c['wan_latency_warning_ms'])+')'+collection+observed,'5m',
            'Requires a current UP SLA state, successful collection and latency above the configured millisecond threshold.')
        rule('loss','High WAN packet loss','(fortigate_virtual_wan_packet_loss_ratio{'+scope+'} * 100 > bool '+str(c['wan_loss_warning_pct'])+')'+collection+observed,'5m',
            'Requires a current UP SLA state, successful collection and packet loss above the configured percentage threshold.')
    rule('collection','FortiGate collection unavailable','max((up{'+target+'} == bool 0) or (probe_success{'+target+'} == bool 0) or absent(up{'+target+'}))','5m',
        'Collection failure requires investigation; it does not identify the cause as a bad token or Internet outage.')
    if c.get('accounting_enabled'):
        rule('usage-poll','Usage processing needs review',
            'max((up{job="fortilens-accounting"} == bool 0) or (fortilens_accounting_poll_success == bool 0) or (fortilens_accounting_processing_lag_seconds > bool 300) or absent(up{job="fortilens-accounting"}))',
            '15m','Review bounded backfill, storage and Loki access. Catch-up during an initial backfill is expected.')
        if expected_log_interval is not None:
            if type(expected_log_interval) is not int or not 60<=expected_log_interval<=86400:
                raise ValueError('Expected log interval must be between 60 and 86400 seconds')
            if not c.get('loki_receive_timestamps'):
                raise ValueError('Receipt-time timestamps must be confirmed before preparing expected-delivery rules')
            rule('syslog-expected','Expected syslog delivery not observed',
                '(time() - fortilens_accounting_last_delivery_seconds > bool '+str(expected_log_interval)+') and on() (fortilens_accounting_poll_success == 1)',
                '5m','Opt-in expected traffic schedule only. Quiet networks must leave this rule absent; silence alone does not prove dropped traffic.')
    output={'rules':{'apiVersion':1,'groups':[{'orgId':1,'name':'FortiLens optional','folder':'FortiLens optional','interval':'60s','rules':rules}]}}
    if notification:
        if notification not in ('email','telegram'):raise ValueError('Choose email or telegram')
        settings={'addresses':'$FORTILENS_ALERT_EMAIL','singleEmail':False} if notification=='email' else {'bottoken':'$FORTILENS_TELEGRAM_BOT_TOKEN','chatid':'$FORTILENS_TELEGRAM_CHAT_ID'}
        output['contact-points']={'apiVersion':1,'contactPoints':[{'orgId':1,'name':'FortiLens optional '+notification,
            'receivers':[{'uid':'fortilens-opt-'+notification,'type':notification,'disableResolveMessage':False,'settings':settings}]}]}
        # No global notification policy is generated: preserve existing routing.
    return output
