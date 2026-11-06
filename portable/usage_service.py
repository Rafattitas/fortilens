"""Optional single-writer usage engine and authenticated read-only reporting API."""
import argparse
import base64
import bisect
from collections import OrderedDict, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import logging
import math
from pathlib import Path
import signal
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import accounting
import interfaces
import wan_health


class ServiceError(ValueError):
    pass


LOGGER = logging.getLogger(__name__)


def log_failure(event, code=None, *, warning=True):
    """Fixed event/canonical code only; diagnostics cannot replace a failure."""
    try:
        method=LOGGER.warning if warning else LOGGER.error
        if code is None:method(event)
        else:method('%s code=%s',event,code)
    except Exception:
        pass

ERROR_CODES = frozenset({
    'response_budget_exceeded', 'upstream_query_incomplete', 'invalid_response',
    'unexpected_loki_response', 'loki_paging_budget_exhausted', 'loki_timestamp_overflow',
    'loki_record_budget_exhausted', 'device_endpoint_not_allowlisted',
    'accounting_storage_limit', 'wan_history_window_outside_budget',
    'unexpected_prometheus_response', 'report_template_unavailable',
})


def fetch_json(base, route, params=None, *, timeout=25, headers=None, context=None, budget=32*1024*1024):
    url = base.rstrip("/")+route+("?"+urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    handlers = [urllib.request.ProxyHandler({}), interfaces.NoRedirect()]
    if context:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    with urllib.request.build_opener(*handlers).open(request, timeout=timeout) as response:
        body = response.read(budget+1)
    if len(body)>budget:
        raise ServiceError("response_budget_exceeded")
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError):
        raise ServiceError('invalid_response') from None
    if not isinstance(result, dict):
        raise ServiceError('invalid_response')
    if result.get("status") not in (None,"success") or result.get("warnings"):
        raise ServiceError("upstream_query_incomplete")
    return result


LIVE_ROUTE='/api/v2/monitor/fortiview/statistics'
LIVE_ROWS=500
LIVE_CACHE_SECONDS=5


def fv_rows(response):
    """FortiView realtime rows; anything else is rejected, never read as zero."""
    results=response.get('results') if isinstance(response,dict) else None
    rows=results.get('details') if isinstance(results,dict) else results
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        raise ServiceError('invalid_response')
    return rows


def _rate(row,key):
    value=row.get(key,0)
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and value>=0 else 0


def build_live(sources,applications,wan,inventory,vdom,by_mac,by_ip,catalog,now):
    """Group FortiView rows that leave through a WAN interface.

    FortiView reports bits per second from the source's side: tx is what the
    source sends (upload) and rx what it receives (download).
    """
    wan=set(wan)
    def app_label(entry):
        appid=str(entry.get('id',''))
        known=catalog.get(appid)
        if known:return {'appid':appid,'app':known['app'],'category':known.get('category') or None,'risk':known.get('risk') or None}
        if appid in ('','0'):return {'appid':None,'app':str(entry.get('name') or 'Unidentified'),'category':None,'risk':None}
        return {'appid':appid,'app':None,'category':None,'risk':None}
    totals={'download_bps':0,'upload_bps':0,'sessions':0}
    links,devices,segments,apps={}, {}, {}, {}
    for row in sources:
        out=str(row.get('dstintf',''))
        if out not in wan:continue
        down,up,sessions=_rate(row,'rx_bandwidth'),_rate(row,'tx_bandwidth'),int(_rate(row,'sessions'))
        totals['download_bps']+=down;totals['upload_bps']+=up;totals['sessions']+=sessions
        link=links.setdefault(out,{'interface':out,'download_bps':0,'upload_bps':0,'sessions':0})
        link['download_bps']+=down;link['upload_bps']+=up;link['sessions']+=sessions
        mac=str(row.get('srcmac') or '').lower();ip=str(row.get('srcaddr') or '')
        known=by_mac.get(mac) if mac else None
        known=known or by_ip.get(ip)
        vlan=interfaces.map_source_interface(inventory,vdom,str(row.get('srcintf','')))
        key=mac or ip
        device=devices.setdefault(key,{'device_id':known and known['device_id'],'hostname':(known or {}).get('hostname',''),
            'ip':ip,'mac':mac,'profile':(known or {}).get('profile'),'vlan':vlan,'download_bps':0,'upload_bps':0,'sessions':0,'apps':{}})
        device['download_bps']+=down;device['upload_bps']+=up;device['sessions']+=sessions
        for entry in row.get('apps') or []:
            if isinstance(entry,dict):
                label=app_label(entry);name=label['app'] or 'Application '+str(label['appid'])
                device['apps'][name]=device['apps'].get(name,0)+int(_rate(entry,'count'))
        segment=segments.setdefault(vlan['key'],{'vlan':vlan,'download_bps':0,'upload_bps':0,'devices':set()})
        segment['download_bps']+=down;segment['upload_bps']+=up;segment['devices'].add(key)
    for row in applications:
        if str(row.get('dstintf','')) not in wan:continue
        entries=[e for e in row.get('apps') or [] if isinstance(e,dict)]
        label=app_label(entries[0]) if entries else {'appid':None,'app':'Unidentified','category':None,'risk':None}
        key=label['appid'] or label['app']
        app=apps.setdefault(key,{**label,'download_bps':0,'upload_bps':0,'sessions':0})
        app['download_bps']+=_rate(row,'rx_bandwidth');app['upload_bps']+=_rate(row,'tx_bandwidth');app['sessions']+=int(_rate(row,'sessions'))
    def ordered(values):
        return sorted(values,key=lambda x:-(x['download_bps']+x['upload_bps']))
    for device in devices.values():
        device['top_apps']=[name for name,_ in sorted(device.pop('apps').items(),key=lambda x:-x[1])[:3]]
    for segment in segments.values():
        segment['devices']=len(segment['devices'])
    return {'observed_at':now,'source':'FORTIVIEW_REALTIME','unit':'bits_per_second','totals':{**totals,'devices':len(devices)},
            'links':ordered(links.values()),'devices':ordered(devices.values()),'vlans':ordered(segments.values()),
            'applications':ordered(apps.values()),'wan_interfaces':sorted(wan),
            'note':'Live estimates from FortiGate realtime session statistics for traffic leaving through WAN interfaces. They are not recorded usage totals.'}


def loki_rows(response):
    """Reject malformed pages before a caller can advance its checkpoint."""
    data = response.get('data') if isinstance(response, dict) else None
    if not isinstance(data, dict) or data.get('resultType') != 'streams' or not isinstance(data.get('result'), list):
        raise ServiceError('unexpected_loki_response')
    rows = []
    for stream in data['result']:
        values = stream.get('values') if isinstance(stream, dict) else None
        if not isinstance(values, list):
            raise ServiceError('unexpected_loki_response')
        for value in values:
            if not isinstance(value, (list, tuple)) or len(value) != 2 or not isinstance(value[1], str):
                raise ServiceError('unexpected_loki_response')
            try:
                stamp = int(value[0])
            except (ValueError, TypeError, OverflowError):
                raise ServiceError('unexpected_loki_response') from None
            if isinstance(value[0], bool) or not 0 <= stamp < 2**63:
                raise ServiceError('unexpected_loki_response')
            rows.append({'timestamp_ns': stamp, 'line': value[1]})
    return rows


class LokiReader:
    def __init__(self, base, *, limit=2000, requests=31, records=30000, fetch=fetch_json):
        self.base, self.limit, self.max_requests, self.max_records, self.fetch = base, limit, requests, records, fetch

    def first(self, start, end):
        while start<end:
            stop=min(end,start+30*86400*accounting.NS)
            result=self.fetch(self.base,'/loki/api/v1/query_range',{'query':'{job="fortigate-syslog"}','start':str(start),'end':str(stop-1),'direction':'forward','limit':'1'})
            stamps=[row['timestamp_ns'] for row in loki_rows(result)]
            if stamps:return min(stamps)
            start=stop
        return None

    def read(self,start,end):
        budget={'requests':0,'records':0}
        def page(a,b):
            budget['requests']+=1
            if budget['requests']>self.max_requests:raise ServiceError('loki_paging_budget_exhausted')
            # Loki's endpoint includes both boundaries; the engine uses [a,b).
            response=self.fetch(self.base,'/loki/api/v1/query_range',{'query':'{job="fortigate-syslog"}','start':str(a),'end':str(b-1),'direction':'forward','limit':str(self.limit)})
            rows=loki_rows(response)
            if len(rows)>=self.limit:
                if b-a<=1:raise ServiceError('loki_timestamp_overflow')
                middle=(a+b)//2
                return page(a,middle)+page(middle,b)
            budget['records']+=len(rows)
            if budget['records']>self.max_records:raise ServiceError('loki_record_budget_exhausted')
            return rows
        return page(start,end),budget


def safe_error(exc):
    if isinstance(exc,urllib.error.HTTPError):
        return 'http_'+str(exc.code) if type(exc.code) is int and 100<=exc.code<=599 else 'upstream_unavailable'
    if isinstance(exc,ServiceError):
        return str(exc) if str(exc) in ERROR_CODES else 'upstream_unavailable'
    if isinstance(exc,sqlite3.Error):return 'accounting_storage_unavailable'
    reason=exc.reason if isinstance(exc,urllib.error.URLError) else exc
    if isinstance(reason,TimeoutError):return 'timeout'
    if isinstance(reason,ssl.SSLError):return 'tls_verification_failed'
    if isinstance(exc,(ValueError,UnicodeError)):return 'invalid_response'
    return 'upstream_unavailable'


class Engine:
    def __init__(self, store, *, loki='http://loki:3100', prometheus='http://prometheus:9090', token_file=None, config_root=None, fetch=fetch_json):
        self.store,self.config,self.lock=store,store.config,threading.RLock()
        self.loki=LokiReader(loki,fetch=fetch)
        self.prometheus,self.fetch,self.token_file,self.config_root=prometheus,fetch,token_file,config_root
        self.stop=threading.Event()
        self.last_poll_ok=False
        self.last_error='not_started'
        self.last_attempt=None
        self.wan_cache={'status':'UNAVAILABLE','interfaces':[]}
        self._initial_inventory=json.loads(json.dumps(store.inventory))
        self.read_lock=threading.RLock()
        self._reader=None
        self._reader_identity=None
        self._report_cache=OrderedDict()
        self._diagnostics_cache=None
        self.live_lock=threading.Lock()
        self._live_cache=None

    @contextmanager
    def _read_snapshot(self):
        if self.store.path is None:
            # Ordinary in-memory databases are private to their one connection.
            # This compatibility path is not used by the deployed file store.
            with self.lock:
                yield self.store,None
            return
        with self.read_lock:
            info=self.store.path.stat();identity=(info.st_dev,info.st_ino)
            if self._reader is None or self._reader_identity!=identity:
                if self._reader is not None:self._reader.close()
                self._reader=None
                self._report_cache.clear();self._diagnostics_cache=None
                self._reader=self.store.reader();self._reader_identity=identity
            reader=self._reader
            reader.db.execute('BEGIN')
            try:
                # The first SELECT pins the committed snapshot before reading
                # its connection-local data_version. Never use immutable=1 or
                # read_uncommitted on this live WAL database.
                expected={key:self.config.get(key) for key in ('accounting_timezone','source_id','vdom','expected_devid')}
                if reader.state('contract')!=expected:
                    raise ValueError('Accounting identity/timezone contract changed; review the existing database')
                version=(identity,reader.db.execute('PRAGMA data_version').fetchone()[0])
                reader.inventory=reader.state('inventory',self._initial_inventory)
                yield reader,version
            finally:
                reader.db.rollback()

    def close(self):
        with self.read_lock:
            if self._reader is not None:self._reader.close()
            self._reader=None;self._report_cache.clear();self._diagnostics_cache=None

    def _snapshot_diagnostics(self,store,version):
        if version is None or self._diagnostics_cache is None or self._diagnostics_cache[0]!=version:
            details=store.diagnostics()
            if version is not None:self._diagnostics_cache=(version,details)
        else:details=self._diagnostics_cache[1]
        details=dict(details)
        checkpoint=details.get('checkpoint_ns')
        details.update(last_poll_ok=self.last_poll_ok,last_error=self.last_error,last_attempt=self.last_attempt,
            processing_lag_seconds=max(0,time.time()-checkpoint/accounting.NS) if checkpoint else None,
            inventory_error=store.state('inventory_error'),last_failure_seconds=store.state('last_failure_seconds'),
            loki_timestamps_at_receipt=self.config.get('loki_receive_timestamps',False))
        return details

    def inventory(self):
        with self._read_snapshot() as (store,_version):
            return json.loads(json.dumps(store.inventory))

    def live(self,now=None):
        """Current Internet bandwidth from FortiGate's realtime FortiView session view.

        Read-only and cached for a few seconds so several open pages share one
        pair of requests. These are FortiGate estimates of current sessions, not
        recorded accounting totals.
        """
        now=time.time() if now is None else now
        with self.live_lock:
            if self._live_cache and now-self._live_cache[0]<LIVE_CACHE_SECONDS:
                return json.loads(self._live_cache[1])
            fixed={'realtime':'true','count':str(LIVE_ROWS)}
            sources=self.read_device(LIVE_ROUTE,{**fixed,'report_by':'source'})
            applications=self.read_device(LIVE_ROUTE,{**fixed,'report_by':'application'})
            with self._read_snapshot() as (store,_version):
                by_mac,by_ip=store.recent_identities()
                ids={str(a.get('id')) for row in fv_rows(applications)+fv_rows(sources) for a in row.get('apps') or [] if isinstance(a,dict)}
                catalog=store.app_catalog(ids)
                inventory=store.inventory
            wan=self.config.get('confirmed_wan_interfaces') or self.config.get('wan_interfaces') or []
            result=build_live(fv_rows(sources),fv_rows(applications),wan,inventory,self.config.get('vdom',''),by_mac,by_ip,catalog,now)
            self._live_cache=(now,json.dumps(result))
            return result

    def read_device(self,route,params):
        if route not in ('/api/v2/monitor/router/lookup','/api/v2/monitor/system/status',LIVE_ROUTE):
            raise ServiceError('device_endpoint_not_allowlisted')
        c=self.config
        token=Path(self.token_file).read_text().strip()
        ca=c.get('api_ca_file')
        if ca and not Path(ca).is_absolute():ca=str(Path(self.config_root)/ca)
        context=ssl.create_default_context(cafile=ca or None)
        if c.get('api_tls_verify',True) is False:
            context.check_hostname=False;context.verify_mode=ssl.CERT_NONE
        host=c['fortigate_host'];host='['+host+']' if ':' in host else host
        return self.fetch('https://'+host+':'+str(c['api_port']),route,{'vdom':c['vdom'],**params},headers={'Authorization':'Bearer '+token},context=context,timeout=15,budget=8*1024*1024)

    def refresh_inventory(self):
        if not self.token_file:return
        try:
            token=Path(self.token_file).read_text().strip()
            def exporter_request(_p,_c,path,**options):
                params=urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
                return self.fetch(self.prometheus,'/api/v1/query',{'query':params['query'][0]},timeout=15)
            report=interfaces.collect(self.config,token,installation=self.config_root,use_api=True,request=exporter_request)
            with self.lock,self.store.db:
                # An unchanged mapping keeps its original verification time, so
                # an hourly refresh does not mark recent records as unmapped.
                report=accounting.carry_mapping_since(self.store.inventory,report)
                self.store.inventory=report
                self.store.set_state('inventory',report)
                reason=next((note.split(':',1)[0] for note in report.get('notes',[]) if note.startswith('api_http_')),'inventory_unavailable')
                self.store.set_state('inventory_error',None if report['interfaces'] else reason)
        except Exception as exc:
            try:
                with self.lock,self.store.db:self.store.set_state('inventory_error',safe_error(exc))
            except (sqlite3.Error,OSError):
                log_failure('accounting_inventory_state_unavailable')

    def used_bytes(self):
        """Live pages plus WAL. Freed pages are reused, so they are not counted."""
        db=self.store.db
        page=db.execute('PRAGMA page_size').fetchone()[0]
        live=(db.execute('PRAGMA page_count').fetchone()[0]-db.execute('PRAGMA freelist_count').fetchone()[0])*page
        path=db.execute('PRAGMA database_list').fetchone()[2]
        wal=Path(path+'-wal') if path else None
        return live+(wal.stat().st_size if wal and wal.exists() else 0)

    def fit_storage(self,cursor):
        """Near the limit, expire the oldest raw records instead of stopping.

        Daily summaries are kept. Only when even one day of raw records exceeds
        the limit does ingestion stop with the storage-limit error.
        """
        limit=self.config.get('accounting_max_bytes',4*1024**3)
        with self.lock:
            if self.used_bytes()<=limit:return
            days=self.config.get('accounting_ledger_days',3)
            summary=self.config.get('accounting_summary_days',400)
            while days>1 and self.used_bytes()>limit*0.9:
                days-=1
                self.store.prune(cursor,days,max(days,summary))
                log_failure('accounting_storage_pruned',str(days))
            if self.used_bytes()>limit:raise ServiceError('accounting_storage_limit')

    def poll(self,now=None):
        now=time.time() if now is None else now
        end=int((now-self.config.get('accounting_settle_seconds',5))*accounting.NS)
        with self.lock:
            checkpoint=self.store.state('checkpoint_ns')
        if checkpoint is None:
            start=end-self.config.get('accounting_backfill_days',31)*86400*accounting.NS
            first=self.loki.first(start,end)
            with self.lock,self.store.db:
                self.store.set_state('scan_start_ns',start)
                self.store.set_state('checkpoint_ns',first if first is not None else end)
                self.store.set_state('initial_empty_prefix_end_ns',first if first is not None else end)
            checkpoint=first if first is not None else end
        target=min(end,checkpoint+self.config.get('accounting_step_seconds',120)*accounting.NS)
        if target>checkpoint:
            # New installations timestamp Loki at receipt time, retaining source
            # eventtime in the line. Late source events therefore enter this
            # stream without rescanning a whole raw-log day every poll.
            start=max(self.store.state('scan_start_ns',0),checkpoint-self.config.get('accounting_overlap_seconds',30)*accounting.NS)
            records,budget=self.loki.read(start,target)
            self.fit_storage(target)
            with self.lock:
                result=self.store.ingest(records,checkpoint=target)
                with self.store.db:
                    self.store.set_state('last_poll_seconds',now)
                    self.store.set_state('last_batch_records',len(records))
                    self.store.set_state('last_batch',result)
                    self.store.set_state('last_query_requests',budget['requests'])
        else:
            with self.lock,self.store.db:self.store.set_state('last_poll_seconds',now)
        self.last_poll_ok=True;self.last_error=None;self.last_attempt=now
        return target>=end

    def report(self,period='today',*,at=None,start=None,end=None,vlan=None,device=None,search='',include_history=False):
        at=at or datetime.now(timezone.utc).isoformat()
        first,last=accounting.period_bounds(period,at,self.config['accounting_timezone'],start=start,end=end)
        with self._read_snapshot() as (store,version):
            key=(version,period,first,last,vlan,device,search,include_history)
            cached=self._report_cache.get(key) if version is not None else None
            if cached is not None:
                self._report_cache.move_to_end(key)
                result=json.loads(cached)
            else:
                result=store.report(first,last,vlan=vlan,device=device,search=search)
                if include_history:
                    result['daily_history']=store.history(device)
                    result['monthly_history']=store.history(device,months=True,limit=36)
                if version is not None:
                    encoded=json.dumps(result,ensure_ascii=False)
                    # At most four immutable results, each at most 2 MiB.
                    if len(encoded.encode('utf-8','surrogatepass'))<=2*1024*1024:
                        self._report_cache[key]=encoded
                        while len(self._report_cache)>4:self._report_cache.popitem(last=False)
            result['processing']=self._snapshot_diagnostics(store,version)
        result['period']=period
        return result

    def applications(self,period='today',*,at=None,start=None,end=None,app=None,category=None,device=None,vlan=None):
        at=at or datetime.now(timezone.utc).isoformat()
        first,last=accounting.period_bounds(period,at,self.config['accounting_timezone'],start=start,end=end)
        with self._read_snapshot() as (store,version):
            key=('applications',version,period,first,last,app,category,device,vlan)
            cached=self._report_cache.get(key) if version is not None else None
            if cached is not None:
                self._report_cache.move_to_end(key)
                result=json.loads(cached)
            else:
                result=store.applications(first,last,app=app,category=category,device=device,vlan=vlan,
                    history=app is not None or category is not None)
                if version is not None:
                    encoded=json.dumps(result,ensure_ascii=False)
                    if len(encoded.encode('utf-8','surrogatepass'))<=2*1024*1024:
                        self._report_cache[key]=encoded
                        while len(self._report_cache)>4:self._report_cache.popitem(last=False)
            result['processing']=self._snapshot_diagnostics(store,version)
        result['period']=period
        return result

    def diagnostics(self):
        with self._read_snapshot() as (store,version):
            return self._snapshot_diagnostics(store,version)

    def wan(self,seconds=300):
        if not 60<=seconds<=7*86400:raise ServiceError('wan_history_window_outside_budget')
        now=time.time();c=self.config;freshness=max(120,c.get('prometheus_scrape_seconds',60)*2)
        metrics='up|fortigate_virtual_wan_status|fortigate_virtual_wan_latency_seconds|fortigate_virtual_wan_latency_jitter_seconds|fortigate_virtual_wan_packet_loss_ratio|fortigate_interface_link_up|fortigate_interface_receive_bytes_total|fortigate_interface_transmit_bytes_total'
        expr='{__name__=~'+json.dumps(metrics)+',job="fortigate",instance='+json.dumps(c['fortigate_host'])+'}['+str(seconds)+'s]'
        response=self.fetch(self.prometheus,'/api/v1/query',{'query':expr,'time':str(now)})
        data=response.get('data') if isinstance(response,dict) else None
        if not isinstance(data,dict) or data.get('resultType')!='matrix' or not isinstance(data.get('result'),list):raise ServiceError('unexpected_prometheus_response')
        up=[];values=defaultdict(lambda:defaultdict(list));states=defaultdict(lambda:defaultdict(set));timeline=defaultdict(dict)
        for series in data['result']:
            if not isinstance(series,dict) or not isinstance(series.get('metric'),dict) or not isinstance(series.get('values',[]),list):
                raise ServiceError('unexpected_prometheus_response')
            if any(not isinstance(point,(list,tuple)) or len(point)!=2 for point in series.get('values',[])):
                raise ServiceError('unexpected_prometheus_response')
            labels=series['metric'];metric=labels.get('__name__','')
            points=[(float(t),float(v)) for t,v in series.get('values',[]) if wan_health.finite(t) is not None and wan_health.finite(v) is not None]
            if metric=='up':up.extend(points);continue
            name=labels.get('interface',labels.get('name'))
            if not name or labels.get('vdom',c['vdom'])!=c['vdom'] or labels.get('sla',c.get('sla'))!=c.get('sla'):continue
            values[name][metric].extend(points)
            for stamp,value in points:
                sample=timeline[name].setdefault(stamp,{'timestamp':stamp,'state':'NO DATA'})
                if metric=='fortigate_virtual_wan_status' and value==1:
                    state=labels.get('state','unknown').upper()
                    states[name][stamp].add({'DISABLE':'DISABLED','ALIVE':'UP','DEAD':'DOWN'}.get(state,state))
                elif metric=='fortigate_virtual_wan_latency_seconds':sample['latency_ms']=value*1000
                elif metric=='fortigate_virtual_wan_packet_loss_ratio':sample['packet_loss_pct']=value*100
        up.sort()
        up_stamps=[p[0] for p in up]
        latest_up=up[-1] if up else None
        collected=bool(latest_up and 0<=now-latest_up[0]<=freshness and latest_up[1]==1)
        output=[]
        names=c.get('confirmed_wan_interfaces') or c.get('wan_interfaces') or sorted(values)
        for name in names:
            for stamp,active in states[name].items():
                sample=timeline[name].setdefault(stamp,{'timestamp':stamp})
                sample['state']=next(iter(active)) if len(active)==1 else 'UNKNOWN'
                index=bisect.bisect_right(up_stamps,stamp)-1
                prior=up[index] if index>=0 else None
                sample['collection_ok']=bool(prior and stamp-prior[0]<=freshness and prior[1]==1)
            def latest(metric):
                points=sorted(values[name].get(metric,[]))
                return points[-1][1] if collected and points and 0<=now-points[-1][0]<=freshness else None
            def bandwidth(metric):
                points=sorted(values[name].get(metric,[]))[-20:]
                if not collected or len(points)<2 or now-points[-1][0]>freshness or any(b[0]-a[0]>freshness for a,b in zip(points,points[1:])):return None
                duration=points[-1][0]-points[0][0]
                if duration<=0:return None
                change=points[-1][1]-points[0][1]+sum(a[1] for a,b in zip(points,points[1:]) if b[1]<a[1])
                return max(0,change)*8/duration/1e6
            rows=[{'state':state,'value':1,'timestamp':stamp} for stamp,active in states[name].items() for state in active]
            link=latest('fortigate_interface_link_up')
            output.append({'interface':name,'internet_state':wan_health.current_state(rows,now,freshness,collected),
                'physical_state':'UP' if link==1 else 'DOWN' if link==0 else 'NO DATA',
                'download_mbps':bandwidth('fortigate_interface_receive_bytes_total'),'upload_mbps':bandwidth('fortigate_interface_transmit_bytes_total'),
                'latency_ms':None if latest('fortigate_virtual_wan_latency_seconds') is None else latest('fortigate_virtual_wan_latency_seconds')*1000,
                'jitter_ms':None if latest('fortigate_virtual_wan_latency_jitter_seconds') is None else latest('fortigate_virtual_wan_latency_jitter_seconds')*1000,
                'packet_loss_pct':None if latest('fortigate_virtual_wan_packet_loss_ratio') is None else latest('fortigate_virtual_wan_packet_loss_ratio')*100,
                'history':wan_health.summarize(list(timeline[name].values()),now-seconds,now,max_hold=freshness,
                    latency_ms=c.get('wan_latency_warning_ms',150),packet_loss_pct=c.get('wan_loss_warning_pct',5))})
        route=wan_health.route_evidence({},names,c.get('route_probe_destination','1.1.1.1'))
        if self.token_file and self.config.get('route_lookup_enabled',False):
            try:route=wan_health.route_evidence(self.read_device('/api/v2/monitor/router/lookup',{'destination':route['destination']}),names,route['destination'])
            except Exception as exc:route['reason']=safe_error(exc)
        return {'status':'OBSERVED' if collected else 'NO DATA','interfaces':output,'route':route,'observed_at':now,
                'bandwidth_note':'Observed counter-sample rate; not proof of the routing decision for every flow.'}

    def run(self):
        last_metadata,last_prune,last_wan=0,0,0
        while not self.stop.is_set():
            now=time.time();caught_up=True
            try:
                if now-last_prune>=3600:
                    # Retention must keep running when ingestion stops at the
                    # storage ceiling, so a failed poll cannot skip it.
                    try:
                        with self.lock:
                            cursor=self.store.state('checkpoint_ns')
                            if cursor:self.store.prune(cursor,self.config.get('accounting_ledger_days',3),self.config.get('accounting_summary_days',400))
                        last_prune=now
                    except Exception as exc:
                        log_failure('accounting_prune_failed',safe_error(exc))
                metadata_interval=3600 if self.store.inventory.get('interfaces') and not self.store.state('inventory_error') else 60
                if now-last_metadata>=metadata_interval:
                    self.refresh_inventory();last_metadata=now
                caught_up=self.poll(now)
                if now-last_wan>=60:
                    try:self.wan_cache=self.wan()
                    except Exception as exc:self.wan_cache={'status':'UNAVAILABLE','interfaces':[],'reason':safe_error(exc)}
                    last_wan=now
            except Exception as exc:
                self.last_poll_ok=False;self.last_error=safe_error(exc);self.last_attempt=now
                log_failure('accounting_poll_failed',self.last_error)
                try:
                    with self.lock,self.store.db:self.store.set_state('last_failure_seconds',now)
                except (sqlite3.Error,OSError):
                    # A full/unavailable database cannot persist its own failure.
                    # Keep in-memory failure evidence and the worker alive.
                    log_failure('accounting_failure_state_unavailable')
            self.stop.wait(self.config.get('accounting_poll_seconds',30) if caught_up or not self.last_poll_ok else 0.05)


def metric_line(name,value,labels=None):
    if value is None:return ''
    def escape(value):return str(value).replace('\\','\\\\').replace('\n','\\n').replace('"','\\"')
    suffix='{'+','.join(k+'="'+escape(v)+'"' for k,v in (labels or {}).items())+'}' if labels else ''
    return name+suffix+' '+str(value)+'\n'


def metrics(engine):
    details=engine.diagnostics()
    output=metric_line('fortilens_accounting_poll_success',int(engine.last_poll_ok))
    output+=metric_line('fortilens_accounting_traffic_coverage_known',0)
    output+=metric_line('fortilens_accounting_last_poll_seconds',details.get('last_attempt'))
    output+=metric_line('fortilens_accounting_last_record_seconds',details['last_received_event_ns']/accounting.NS if details.get('last_received_event_ns') else None)
    output+=metric_line('fortilens_accounting_last_delivery_seconds',details['last_delivery_ns']/accounting.NS if details.get('last_delivery_ns') else None)
    output+=metric_line('fortilens_accounting_processing_lag_seconds',details.get('processing_lag_seconds'))
    output+=metric_line('fortilens_accounting_parser_rejections',details.get('parser_rejections',0))
    output+=metric_line('fortilens_accounting_late_rejections',details.get('late_rejections',0))
    truncated=0
    for period in ('today','week','month'):
        report=engine.report(period)
        for direction in ('upload','download'):
            output+=metric_line('fortilens_usage_total_bytes',report['totals'][direction+'_bytes'],{'period':period,'direction':direction,'vdom':engine.config['vdom']})
        output+=metric_line('fortilens_usage_observed_identities',report['observed_endpoint_identities'] if report['devices'] else None,{'period':period})
        limit=engine.config.get('accounting_metrics_rows',5000)
        truncated+=max(0,len(report['rows'])-limit)
        for row in report['rows'][:limit]:
            labels={'period':period,'device_id':row['device_id'],'identity_kind':row['identity_kind'],
                    'srcip':row['ip'],'mac':row['mac'],'hostname':row['hostname'],'vdom':row['vdom'],
                    'source_interface':row['source_interface'],'vlan':row['vlan']['key'],
                    'vlan_id':str(row['vlan']['vlan_id']) if row['vlan']['vlan_id'] is not None else 'unknown'}
            for direction in ('upload','download'):
                output+=metric_line('fortilens_usage_bytes',row[direction+'_bytes'],{**labels,'direction':direction})
                output+=metric_line('fortilens_usage_pending_bytes',row['pending_'+direction+'_bytes'],{**labels,'direction':direction})
        for row in report['vlans']:
            labels={'period':period,'vdom':row['vdom'],'vlan':row['vlan']['key'],'interface':row['vlan']['interface'],
                    'vlan_id':str(row['vlan']['vlan_id']) if row['vlan']['vlan_id'] is not None else 'unknown'}
            for direction in ('upload','download'):
                output+=metric_line('fortilens_vlan_usage_bytes',row[direction+'_bytes'],{**labels,'direction':direction})
            output+=metric_line('fortilens_vlan_observed_identities',row['observed_endpoint_identities'],labels)
        for flag,count in report['flags'].items():output+=metric_line('fortilens_usage_quality_records',count,{'period':period,'reason':flag})
    output+=metric_line('fortilens_accounting_metrics_truncated_rows',truncated)
    return output


def handler(engine,password,root):
    expected='Basic '+base64.b64encode(('admin:'+password).encode()).decode()
    class Handler(BaseHTTPRequestHandler):
        server_version='FortiLens'
        def setup(self):
            super().setup();self.connection.settimeout(30)
        def log_message(self,*args):pass
        def send(self,status,payload,kind='application/json; charset=utf-8'):
            data=(payload if isinstance(payload,str) else json.dumps(payload,ensure_ascii=False)).encode('utf-8','backslashreplace')
            self.response_started=True
            self.send_response(status)
            self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers();self.wfile.write(data)
        def fail(self,status,payload):
            if self.response_started:
                self.close_connection=True
                return
            try:self.send(status,payload)
            except OSError:self.close_connection=True
        def dispatch(self):
            if len(self.path)>4096:return self.send(414,{'error':'request_too_large'})
            parsed=urllib.parse.urlsplit(self.path)
            if parsed.scheme or parsed.netloc:raise ValueError('Invalid request path')
            if parsed.path=='/':
                try:page=Path(root,'reporting.html').read_text(encoding='utf-8')
                except (OSError,UnicodeError):raise ServiceError('report_template_unavailable') from None
                return self.send(200,page,'text/html; charset=utf-8')
            if parsed.path=='/health':return self.send(200,{'service':'running','poll_success':engine.last_poll_ok})
            supplied=self.headers.get('Authorization','')
            if not hmac.compare_digest(supplied.encode(),expected.encode()):
                # No browser Basic-auth dialog: the report page supplies a
                # transient Authorization header after its normal login form.
                return self.send(401,{'error':'authentication_required'})
            try:
                query=urllib.parse.parse_qs(parsed.query,keep_blank_values=True)
                def value(key,default=None):
                    values=query.get(key,[default])
                    if len(values)!=1 or isinstance(values[0],str) and len(values[0])>512:raise ValueError('Invalid query selection')
                    return values[0]
                if parsed.path=='/metrics':return self.send(200,metrics(engine),'text/plain; version=0.0.4; charset=utf-8')
                if parsed.path=='/api/diagnostics':return self.send(200,engine.diagnostics())
                if parsed.path=='/api/inventory':
                    return self.send(200,engine.inventory())
                if parsed.path=='/api/wan':
                    return self.send(200,engine.wan(int(value('seconds','86400'))))
                if parsed.path in ('/api/report','/api/device'):
                    result=engine.report(value('period','today'),at=value('at'),start=value('start'),end=value('end'),
                        vlan=value('vlan') or None,device=value('device') or None,search=value('search',''),include_history=parsed.path=='/api/device')
                    return self.send(200,result)
                if parsed.path=='/api/live':
                    return self.send(200,engine.live())
                if parsed.path=='/api/applications':
                    # An empty app or category selects unidentified records; absent means no filter.
                    result=engine.applications(value('period','today'),at=value('at'),start=value('start'),end=value('end'),
                        app=value('app'),category=value('category'),device=value('device') or None,vlan=value('vlan') or None)
                    return self.send(200,result)
                self.send(404,{'error':'not_found'})
            except ServiceError as exc:
                if str(exc)=='wan_history_window_outside_budget':
                    self.fail(400,{'error':'invalid_or_unavailable_report_range','message':'Review the period, timezone and retained detail. Expired partial-day detail is not returned as zero.'})
                else:self.fail(503,{'error':safe_error(exc),'message':'Required evidence is unavailable; no outage or zero usage is inferred.'})
            except ValueError:
                self.fail(400,{'error':'invalid_or_unavailable_report_range','message':'Review the period, timezone and retained detail. Expired partial-day detail is not returned as zero.'})
            except Exception as exc:
                if isinstance(exc,(BrokenPipeError,ConnectionResetError,ConnectionAbortedError)):
                    self.close_connection=True
                    return
                log_failure('accounting_request_failed',safe_error(exc))
                self.fail(503,{'error':safe_error(exc),'message':'Required evidence is unavailable; no outage or zero usage is inferred.'})
        def do_GET(self):
            self.response_started=False
            try:self.dispatch()
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                self.close_connection=True
            except ServiceError as exc:
                log_failure('accounting_request_failed',safe_error(exc))
                self.fail(503,{'error':safe_error(exc),'message':'Required evidence is unavailable; no outage or zero usage is inferred.'})
            except ValueError:
                self.fail(400,{'error':'invalid_or_unavailable_report_range','message':'Review the period, timezone and retained detail. Expired partial-day detail is not returned as zero.'})
            except Exception as exc:
                log_failure('accounting_request_failed',safe_error(exc))
                self.fail(503,{'error':safe_error(exc),'message':'Required evidence is unavailable; no outage or zero usage is inferred.'})
    return Handler


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--database',type=Path,default=Path('/data/usage.sqlite'))
    parser.add_argument('--password-file',type=Path,required=True)
    parser.add_argument('--token-file',type=Path)
    parser.add_argument('--loki-url',default='http://loki:3100')
    parser.add_argument('--prometheus-url',default='http://prometheus:9090')
    parser.add_argument('--listen',default='0.0.0.0');parser.add_argument('--port',type=int,default=8080)
    args=parser.parse_args(argv)
    if not 0<=args.port<=65535:parser.error('Choose a valid reporting port.')
    store=server=engine=thread=None
    shutdown_failed=False
    try:
        config=json.loads(args.config.read_text(encoding='utf-8-sig'))
        if not isinstance(config,dict):raise ServiceError('invalid_response')
        config.setdefault('accounting_timezone','UTC')
        args.database.parent.mkdir(parents=True,exist_ok=True)
        password=args.password_file.read_text().strip()
        if len(password)<16:raise ValueError('Usage reporting password must have at least 16 characters')
        # New files use compact storage; an existing file keeps its detected format.
        fresh=not args.database.exists() or args.database.stat().st_size==0
        store=accounting.Store(args.database,config,storage_format='compact' if fresh else None)
        store.inventory=store.state('inventory',{'interfaces':[]})
        engine=Engine(store,loki=args.loki_url,prometheus=args.prometheus_url,token_file=args.token_file,config_root=args.config.parent)
        # Bind before starting ingestion: a port failure must not leave a writer
        # running after startup has already failed.
        server=ThreadingHTTPServer((args.listen,args.port),handler(engine,password,Path(__file__).parent))
        thread=threading.Thread(target=engine.run,daemon=True);thread.start()
        if threading.current_thread() is threading.main_thread():signal.signal(signal.SIGTERM,stop_on_terminate)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,sqlite3.Error) as exc:
        parser.exit(1,'Reporting service could not start or continue ('+safe_error(exc)+'). Review private configuration, storage and port availability.\n')
    finally:
        if engine is not None:engine.stop.set()
        if server is not None:
            try:server.server_close()
            except OSError:
                log_failure('accounting_listener_close_failed',warning=False);shutdown_failed=True
        if thread is not None and thread.is_alive():thread.join(timeout=30)
        if thread is not None and thread.is_alive():
            log_failure('accounting_worker_shutdown_timed_out',warning=False);shutdown_failed=True
        elif store is not None:
            if engine is not None:
                try:engine.close()
                except sqlite3.Error:
                    log_failure('accounting_reader_close_failed',warning=False);shutdown_failed=True
            try:store.close()
            except sqlite3.Error:
                log_failure('accounting_store_close_failed',warning=False);shutdown_failed=True
    return 1 if shutdown_failed else 0


def stop_on_terminate(*_):
    # Container stop sends SIGTERM to PID 1. Close the database cleanly.
    raise KeyboardInterrupt


if __name__=='__main__':raise SystemExit(main())
