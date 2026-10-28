"""Optional single-writer usage engine and authenticated read-only reporting API."""
import argparse
import base64
import bisect
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import math
from pathlib import Path
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
    result = json.loads(body)
    if result.get("status") not in (None,"success") or result.get("warnings"):
        raise ServiceError("upstream_query_incomplete")
    return result


class LokiReader:
    def __init__(self, base, *, limit=2000, requests=31, records=30000, fetch=fetch_json):
        self.base, self.limit, self.max_requests, self.max_records, self.fetch = base, limit, requests, records, fetch

    def first(self, start, end):
        while start<end:
            stop=min(end,start+30*86400*accounting.NS)
            result=self.fetch(self.base,'/loki/api/v1/query_range',{'query':'{job="fortigate-syslog"}','start':str(start),'end':str(stop),'direction':'forward','limit':'1'})
            if result['data']['resultType']!='streams':raise ServiceError('unexpected_loki_response')
            stamps=[int(v[0]) for row in result['data']['result'] for v in row.get('values',[])]
            if stamps:return min(stamps)
            start=stop
        return None

    def read(self,start,end):
        budget={'requests':0,'records':0}
        def page(a,b):
            budget['requests']+=1
            if budget['requests']>self.max_requests:raise ServiceError('loki_paging_budget_exhausted')
            response=self.fetch(self.base,'/loki/api/v1/query_range',{'query':'{job="fortigate-syslog"}','start':str(a),'end':str(b),'direction':'forward','limit':str(self.limit)})
            data=response['data']
            if data['resultType']!='streams':raise ServiceError('unexpected_loki_response')
            rows=[{'timestamp_ns':int(v[0]),'line':v[1]} for stream in data['result'] for v in stream.get('values',[])]
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
        return 'http_'+str(exc.code)
    if isinstance(exc,ServiceError):
        return str(exc)
    return {TimeoutError:'timeout',ssl.SSLError:'tls_verification_failed',ValueError:'invalid_response'}.get(type(exc),'upstream_unavailable')


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

    def read_device(self,route,params):
        if route not in ('/api/v2/monitor/router/lookup','/api/v2/monitor/system/status'):
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
            report=interfaces.collect(self.config,token,installation=self.config_root,use_api=True)
            with self.lock,self.store.db:
                self.store.inventory=report
                self.store.set_state('inventory',report)
                self.store.set_state('inventory_error',None if report['interfaces'] else 'inventory_unavailable')
        except Exception as exc:
            with self.lock,self.store.db:self.store.set_state('inventory_error',safe_error(exc))

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
            path=self.store.db.execute('PRAGMA database_list').fetchone()[2]
            size=sum(p.stat().st_size for p in (Path(path),Path(path+'-wal')) if path and p.exists())
            if size>self.config.get('accounting_max_bytes',4*1024**3):raise ServiceError('accounting_storage_limit')
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

    def report(self,period='today',*,at=None,start=None,end=None,vlan=None,device=None,search=''):
        at=at or datetime.now(timezone.utc).isoformat()
        first,last=accounting.period_bounds(period,at,self.config['accounting_timezone'],start=start,end=end)
        with self.lock:
            result=self.store.report(first,last,vlan=vlan,device=device,search=search)
            result['processing']=self.diagnostics()
        result['period']=period
        return result

    def diagnostics(self):
        with self.lock:
            details=self.store.diagnostics()
            checkpoint=details.get('checkpoint_ns')
            details.update(last_poll_ok=self.last_poll_ok,last_error=self.last_error,last_attempt=self.last_attempt,
                processing_lag_seconds=max(0,time.time()-checkpoint/accounting.NS) if checkpoint else None,
                inventory_error=self.store.state('inventory_error'),
                loki_timestamps_at_receipt=self.config.get('loki_receive_timestamps',False))
            return details

    def wan(self,seconds=300):
        if not 60<=seconds<=7*86400:raise ServiceError('wan_history_window_outside_budget')
        now=time.time();c=self.config;freshness=max(120,c.get('prometheus_scrape_seconds',60)*2)
        metrics='up|fortigate_virtual_wan_status|fortigate_virtual_wan_latency_seconds|fortigate_virtual_wan_latency_jitter_seconds|fortigate_virtual_wan_packet_loss_ratio|fortigate_interface_link_up|fortigate_interface_receive_bytes_total|fortigate_interface_transmit_bytes_total'
        expr='{__name__=~'+json.dumps(metrics)+',job="fortigate",instance='+json.dumps(c['fortigate_host'])+'}['+str(seconds)+'s]'
        response=self.fetch(self.prometheus,'/api/v1/query',{'query':expr,'time':str(now)})
        data=response['data']
        if data['resultType']!='matrix':raise ServiceError('unexpected_prometheus_response')
        up=[];values=defaultdict(lambda:defaultdict(list));states=defaultdict(lambda:defaultdict(set));timeline=defaultdict(dict)
        for series in data['result']:
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
                if now-last_metadata>=3600:
                    self.refresh_inventory();last_metadata=now
                caught_up=self.poll(now)
                if now-last_wan>=60:
                    try:self.wan_cache=self.wan()
                    except Exception as exc:self.wan_cache={'status':'UNAVAILABLE','interfaces':[],'reason':safe_error(exc)}
                    last_wan=now
                if now-last_prune>=3600:
                    with self.lock:
                        cursor=self.store.state('checkpoint_ns')
                        if cursor:self.store.prune(cursor,self.config.get('accounting_ledger_days',8),self.config.get('accounting_summary_days',400))
                    last_prune=now
            except Exception as exc:
                self.last_poll_ok=False;self.last_error=safe_error(exc);self.last_attempt=now
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
        def log_message(self,*args):pass
        def send(self,status,payload,kind='application/json; charset=utf-8'):
            data=payload.encode() if isinstance(payload,str) else json.dumps(payload,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers();self.wfile.write(data)
        def do_GET(self):
            parsed=urllib.parse.urlsplit(self.path)
            if len(self.path)>4096:return self.send(414,{'error':'request_too_large'})
            if parsed.path=='/':
                return self.send(200,Path(root,'reporting.html').read_text(encoding='utf-8'),'text/html; charset=utf-8')
            if parsed.path=='/health':return self.send(200,{'service':'running','poll_success':engine.last_poll_ok})
            supplied=self.headers.get('Authorization','')
            if not hmac.compare_digest(supplied,expected):
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
                    with engine.lock:return self.send(200,engine.store.inventory)
                if parsed.path=='/api/wan':
                    return self.send(200,engine.wan(int(value('seconds','86400'))))
                if parsed.path in ('/api/report','/api/device'):
                    result=engine.report(value('period','today'),at=value('at'),start=value('start'),end=value('end'),
                        vlan=value('vlan') or None,device=value('device') or None,search=value('search',''))
                    if parsed.path=='/api/device':
                        device=value('device')
                        with engine.lock:
                            result['daily_history']=engine.store.history(device)
                            result['monthly_history']=engine.store.history(device,months=True,limit=36)
                    return self.send(200,result)
                self.send(404,{'error':'not_found'})
            except ValueError:
                self.send(400,{'error':'invalid_or_unavailable_report_range','message':'Review the period, timezone and retained detail. Expired partial-day detail is not returned as zero.'})
            except Exception as exc:
                self.send(503,{'error':safe_error(exc),'message':'Required evidence is unavailable; no outage or zero usage is inferred.'})
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
    config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    config.setdefault('accounting_timezone','UTC')
    args.database.parent.mkdir(parents=True,exist_ok=True)
    password=args.password_file.read_text().strip()
    if len(password)<16:raise ValueError('Usage reporting password must have at least16characters')
    store=accounting.Store(args.database,config)
    store.inventory=store.state('inventory',{'interfaces':[]})
    engine=Engine(store,loki=args.loki_url,prometheus=args.prometheus_url,token_file=args.token_file,config_root=args.config.parent)
    thread=threading.Thread(target=engine.run,daemon=True);thread.start()
    server=ThreadingHTTPServer((args.listen,args.port),handler(engine,password,Path(__file__).parent))
    try:server.serve_forever()
    finally:
        engine.stop.set();server.server_close();thread.join(timeout=30);store.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
