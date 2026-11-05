"""Independent nonroot web management process. No Docker or installation mounts."""
import argparse
from contextlib import asynccontextmanager
import base64
import hmac
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit, urlencode
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
from pydantic import Field, SecretStr
from .auth import Auth
from .common import Problem, report_failure
from .helper import Client
from .models import Apply, Change, Job, Revision, Strict

STATIC=Path(__file__).parent/'static'
COOKIE='fortilens_management'
EMBEDDED_VIEWS={'monitoring','wan','devices','vlans','reports','quality'}


def monitoring_destination(target, active_sla=''):
    """Scope a fixed Grafana destination without accepting arbitrary query URLs."""
    if not isinstance(target,str) or not re.fullmatch(r'/grafana/d/[A-Za-z0-9_-]+/(?:\?kiosk(?:=true)?&theme=dark)?',target):
        raise Problem('console_redirect_rejected','An unexpected monitoring destination was blocked.',502)
    if isinstance(active_sla,str) and re.fullmatch(r'[A-Za-z0-9_.:@ /+\-]{1,250}',active_sla):
        target+=('&' if '?' in target else '?')+urlencode({'var-sla':active_sla})
    return target


def monitoring_clock_document(text, server_seconds):
    """Use a page-local clock before Grafana initializes its relative ranges."""
    marker='id="fortilens-monitoring-clock"'
    if marker in text:return text
    match=re.search(r'<head(?:\s[^>]*)?>',text,re.IGNORECASE)
    if not match:
        raise Problem('view_unavailable','Monitoring could not initialize its server-time view. Reload Monitoring.',503)
    script='<script '+marker+' src="/static/grafana-clock.js" data-server-time-ms="'+str(int(server_seconds*1000))+'"></script>'
    return text[:match.end()]+script+text[match.end():]


def immutable_asset(path,method,status,content_type):
    """Only authenticated, successful fingerprinted Grafana code is cacheable."""
    match=re.fullmatch(r'/grafana/public/build/[A-Za-z0-9_./-]+\.[0-9a-f]{8,}\.(js|css)',path)
    if method!='GET' or status!=200 or not match or '..' in path.split('/'):return False
    kind=content_type.split(';',1)[0].strip().lower()
    return kind in ({'application/javascript','text/javascript'} if match[1]=='js' else {'text/css'})

# Only the existing reporting document receives shell styling. Native Grafana
# keeps its own configured mount, boot settings, plugin URLs and saved dashboard.
HOSTED_REPORT_CSS='''
:root{--bg:#111217;--surface:#181c25;--border:#2b3342;--text:#e9edf5;--muted:#a5afc1;--blue:#87b4ff}
header,aside,.page-head,footer,#login{display:none!important}
#application[hidden]{display:none!important}
#application:not([hidden]){display:block!important;min-height:0!important}
#managed-report-status{padding:24px 2px;color:var(--muted);font-size:14px}
#managed-report-status p{margin-bottom:12px}#managed-report-status a{color:var(--blue);display:inline-flex;min-height:44px;align-items:center}
main{padding:0 2px 24px!important;max-width:none!important}
.card,.section,.toolbar,.chart{background:transparent;border:0;border-radius:0;box-shadow:none}
.toolbar{padding:0 0 18px;margin:0}.filters{margin-top:18px}
.stats{gap:0;margin:0 0 24px;padding:18px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.stats .card{padding:0 20px;border-left:1px solid var(--border)}
.stats .card:first-child{padding-left:0;border-left:0}
.metric-value{font-size:27px;font-variant-numeric:tabular-nums}.metric-label{font-size:12px}
.section-head{padding:18px 0;border-bottom:1px solid var(--border)}
.section-head h2{font-size:16px}.section-head p{font-size:12px}
.section{margin-bottom:24px}.empty{border:0;border-radius:0;padding:28px 12px}
.periods button{min-height:44px}.context{font-size:12px}
input,select{border-color:#63728b}.notice{border-radius:0;border-width:0 0 0 2px}
.quality-grid>.card{padding:18px 0}.quality-grid{column-gap:32px}
.pager{padding:14px 0}.chart{padding:18px 0}
@media(max-width:700px){main{padding:0 0 20px!important}.toolbar{padding:0 0 16px}.stats{row-gap:22px}.stats .card{padding:0 12px}.stats .card:nth-child(2n+1){border-left:0;padding-left:0}.metric-value{font-size:23px}.periods{gap:4px}.periods button{font-size:12px;padding:8px 10px}input,select{font-size:16px}.filters{gap:14px}.section-head{padding:18px 0}.context{font-size:11px}.quality-grid>.card{padding:16px 0}}
'''

HOSTED_REPORT_STATUS='''<section id="managed-report-status" role="status"><p id="managed-report-message">Connecting to reports…</p><a id="managed-report-recovery" href="/#diagnostics" target="_top" hidden>Open diagnostics</a></section>'''
HOSTED_REPORT_SCRIPT='''<script>
(()=>{const app=document.getElementById('application'),status=document.getElementById('managed-report-status'),message=document.getElementById('managed-report-message'),recovery=document.getElementById('managed-report-recovery');let connected=false;
const timer=setTimeout(()=>{if(app.hidden){message.textContent='Reports are taking longer than expected. Refresh this view or check diagnostics.';recovery.hidden=false}},8000);
const update=()=>{status.hidden=!app.hidden;if(!app.hidden){connected=true;clearTimeout(timer)}else if(connected){message.textContent='Reports need to reconnect. Refresh this view or check diagnostics.';recovery.hidden=false}};
new MutationObserver(update).observe(app,{attributes:true,attributeFilter:['hidden']});update()})();
</script>'''


def hosted_report(text,section=None):
    """Style the trusted report template; untrusted input never becomes CSS."""
    if 'id="managed-report-status"' in text:return text
    css=HOSTED_REPORT_CSS
    if section=='devices':css+='#vlan-section{display:none!important}'
    if section=='vlans':css+='#device-section{display:none!important}'
    body='<body'+(' data-report-section="'+section+'"' if section in ('devices','vlans','reports') else '')+'>'
    return (text.replace("'/api/","'/console/api/").replace('</style>','\n'+css+'</style>')
            .replace('<body>',body+HOSTED_REPORT_STATUS).replace('</body>',HOSTED_REPORT_SCRIPT+'</body>'))


class Login(Strict):
    username:str=Field(max_length=64)
    password:SecretStr=Field(max_length=256)


class Bootstrap(Login):
    confirmation:SecretStr=Field(max_length=256)
    bootstrap_token:SecretStr=Field(max_length=256)


class Password(Strict):
    current:SecretStr
    password:SecretStr
    confirmation:SecretStr


def create_app(data,bootstrap_file,helper,*,origin='http://127.0.0.1:3030',dashboard_uid='forti-monitor'):
    parsed=urlsplit(origin)
    if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('Set a single trusted browser origin')
    if parsed.scheme=='http' and parsed.hostname not in ('127.0.0.1','localhost','::1','testserver'):
        raise ValueError('Network access requires HTTPS; use loopback with an SSH tunnel otherwise')
    origin=origin.rstrip('/');secure=parsed.scheme=='https'
    # Validate required static resources before opening persistent auth state.
    static_files=StaticFiles(directory=STATIC)
    auth=Auth(data,bootstrap_file)
    @asynccontextmanager
    async def lifespan(app):
        try:yield
        finally:
            try:auth.close()
            except sqlite3.Error as exc:report_failure('web_auth_cleanup',exc)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.auth=auth;app.state.helper=helper

    @app.exception_handler(Problem)
    async def problem(request,e):return JSONResponse({'error':e.code,'message':e.message},status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def validation(request,e):
        # FastAPI's default includes submitted values: never return those for
        # configuration/auth endpoints carrying write-only secrets.
        known=set(Change.model_fields)|set(Login.model_fields)|set(Bootstrap.model_fields)|set(Password.model_fields)|set(Apply.model_fields)
        from .models import Settings,Retention,Monitoring
        known|=set(Settings.model_fields)|set(Retention.model_fields)|set(Monitoring.model_fields)
        fields=['.'.join(str(v) for v in x['loc'][1:] if v in known) or 'configuration' for x in e.errors()[:30]]
        return JSONResponse({'error':'invalid_fields','message':'Review the submitted fields and supported values.','fields':fields},status_code=422)

    @app.middleware('http')
    async def boundary(request,call_next):
        try:
            if request.headers.get('host','').lower()!=parsed.netloc.lower():
                raise Problem('host_rejected','Use the configured management address.',403)
            if request.method not in ('GET','HEAD','POST'):
                raise Problem('method_rejected','Unsupported management method.',405)
            if request.method=='POST':
                if request.headers.get('transfer-encoding') or 'content-length' not in request.headers:
                    raise Problem('invalid_body','A bounded Content-Length request is required.',411)
                if not 0<=int(request.headers['content-length'])<=2097152:
                    raise Problem('request_limit','The request is too large.',413)
                if request.headers.get('origin') not in (None,origin):
                    raise Problem('origin_rejected','This request did not originate from the management site.',403)
                if request.headers.get('x-fortilens-request')!='1' and not request.url.path.startswith(('/console/','/grafana/')):
                    raise Problem('request_header_required','Use the management interface to perform this action.',403)
            public=request.url.path in ('/','/health','/api/bootstrap/status','/api/bootstrap','/api/login') or request.url.path.startswith('/static/')
            if not public:
                session=auth.session(request.cookies.get(COOKIE,''));request.state.session=session
                if request.method=='POST' and (not request.headers.get('x-csrf-token') or not hmac.compare_digest(request.headers['x-csrf-token'],session['csrf'])):
                    # Native Grafana's same-origin query POSTs inside the proxy
                    # cannot add our CSRF header. They must have an exact Origin.
                    if not(request.url.path.startswith(('/console/grafana/','/grafana/')) and request.headers.get('origin')==origin):
                        raise Problem('csrf_rejected','The form expired. Reload before submitting.',403)
            response=await call_next(request)
        except Problem as e:response=JSONResponse({'error':e.code,'message':e.message},status_code=e.status)
        except (ValueError,TypeError):response=JSONResponse({'error':'invalid_request','message':'The request could not be validated.'},status_code=400)
        except sqlite3.Error as exc:
            report_failure('web_storage',exc)
            response=JSONResponse({'error':'storage_unavailable','message':'Management storage is unavailable. Review service health, permissions and available space before trying again.'},status_code=503)
        except OSError as exc:
            report_failure('web_io',exc)
            response=JSONResponse({'error':'service_unavailable','message':'The management service could not access a required resource. Review service health and permissions before trying again.'},status_code=503)
        except Exception as exc:
            report_failure('web_unexpected',exc)
            response=JSONResponse({'error':'internal_error','message':'The management service could not complete this operation safely. Review service health and diagnostics.'},status_code=500)
        response.headers['Cache-Control']=('private, max-age=86400, immutable' if
            immutable_asset(request.url.path,request.method,response.status_code,response.headers.get('content-type','')) else 'no-store')
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        embedded=request.url.path.startswith(('/console/','/grafana/'))
        hosted_page=request.url.path.startswith('/console/') and not request.url.path.startswith('/console/grafana/')
        response.headers['Content-Security-Policy']=("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'" if hosted_page else "frame-ancestors 'self'" if embedded else "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        response.headers['X-Frame-Options']='SAMEORIGIN' if embedded else 'DENY'
        return response

    @app.get('/')
    def index(request:Request):
        # A parser-created frame loads reliably on initial entry. Keep this
        # document independent of the helper/monitoring stack and never include
        # session credentials or settings in its HTML.
        text=(STATIC/'index.html').read_text(encoding='utf-8')
        page=request.query_params.get('view','')
        if page in EMBEDDED_VIEWS:
            try:auth.session(request.cookies.get(COOKIE,''))
            except Problem:pass
            else:
                text=text.replace('id="console-frame"','id="console-frame" data-initial-view="'+page+'" src="/console/initial-view?view='+page+'"',1)
        return Response(text,media_type='text/html')
    app.mount('/static',static_files,name='static')

    @app.get('/health')
    def health():return {'service':'running','independent_of_monitoring':True}

    @app.get('/api/bootstrap/status')
    def bootstrap_status():return {'required':not auth.exists()}

    @app.post('/api/bootstrap')
    def bootstrap(body:Bootstrap,request:Request):
        auth.limit(request.client.host)
        auth.create(body.username,body.password.get_secret_value(),body.confirmation.get_secret_value(),body.bootstrap_token.get_secret_value())
        return {'created':True,'message':'Administrator created. Sign in to configure FortiLens.'}

    @app.post('/api/login')
    def login(body:Login,request:Request):
        token,csrf=auth.login(body.username,body.password.get_secret_value(),request.client.host)
        response=JSONResponse({'authenticated':True,'csrf':csrf,'username':body.username})
        response.set_cookie(COOKIE,token,max_age=auth.ttl,httponly=True,secure=secure,samesite='strict',path='/')
        return response

    @app.get('/api/session')
    def session(request:Request):return request.state.session

    @app.post('/api/logout')
    def logout(request:Request):
        auth.logout(request.cookies.get(COOKIE,''));response=JSONResponse({'authenticated':False});response.delete_cookie(COOKIE,path='/');return response

    @app.post('/api/password')
    def password(body:Password):
        auth.change_password(body.current.get_secret_value(),body.password.get_secret_value(),body.confirmation.get_secret_value())
        response=JSONResponse({'changed':True,'message':'Password changed. Sign in again.'});response.delete_cookie(COOKIE,path='/');return response

    @app.get('/api/settings')
    def settings():return helper.call('view')

    @app.post('/api/settings')
    def save(body:Change):
        values=body.model_dump(mode='json')
        for name in ('api_token','custom_ca'):
            if getattr(body,name) is not None:values[name]=getattr(body,name).get_secret_value()
        return helper.call('save',values)

    @app.get('/api/system')
    def system():return helper.call('status')

    @app.get('/api/diagnostics')
    def diagnostics():return helper.call('diagnostics')

    @app.post('/api/connection/test')
    def connection(body:Revision):return helper.call('test',body.model_dump())

    @app.post('/api/interfaces/discover')
    def discovery(body:Revision):return helper.call('discover',body.model_dump())

    @app.post('/api/configuration/prepare')
    def prepare(body:Revision):return helper.call('prepare',body.model_dump())

    @app.post('/api/configuration/apply')
    def apply(body:Apply):return helper.call('apply',body.model_dump())

    @app.post('/api/configuration/rollback')
    def rollback(body:Apply):return helper.call('rollback',body.model_dump())

    @app.get('/api/jobs/{job_id}')
    def job(job_id:str):return helper.call('job',Job(job_id=job_id).model_dump())

    @app.api_route('/grafana/{path:path}',methods=['GET','POST'])
    async def native_monitoring(request:Request,path:str):
        # Keep Grafana's configured subpath intact. Rewriting its HTML alone
        # leaves runtime settings/router URLs inconsistent and can alter plugin
        # IDs and external documentation links containing the word grafana.
        return await console(request,'grafana/'+path)

    @app.get('/console/initial-view')
    async def initial_view(view:str='monitoring'):
        # Fixed read-only destinations only; the ordinary session boundary above
        # still protects the launch route and every redirected request.
        if view not in EMBEDDED_VIEWS:
            raise Problem('view_rejected','Choose a supported workspace view.',400)
        config=await run_in_threadpool(helper.call,'view')
        flags=config.get('active_monitoring')
        enabled=(not flags or (any(flags.get(k) for k in ('wan','sla','interfaces','server')) if view=='monitoring'
                 else flags.get('vlans' if view=='vlans' else 'wan' if view=='wan' else 'accounting')))
        if not config['configured'] or not enabled:
            raise Problem('view_unavailable','This view is not enabled. Review your installation settings.',503)
        paths={'wan':'/console/#wan','devices':'/console/?section=devices#overview',
               'vlans':'/console/?section=vlans#overview','reports':'/console/?section=reports#reports','quality':'/console/#quality'}
        if view=='monitoring':
            result=await run_in_threadpool(helper.call,'console',{'path':'/api/monitoring-status','method':'GET','body':'','content_type':'application/json'})
            try:status=json.loads(base64.b64decode(result['body']))
            except (ValueError,KeyError,TypeError):raise Problem('view_unavailable','Monitoring could not confirm its configured view.',503) from None
            if not isinstance(status,dict):
                raise Problem('view_unavailable','Monitoring could not confirm its configured view.',503)
            target=status.get('path')
            if result['status']!=200 or not status.get('available'):
                raise Problem('view_unavailable','Monitoring is unavailable. Review Diagnostics.',503)
            # Append only the applied, validated selector after checking the fixed
            # destination. Never accept a gateway-supplied arbitrary query URL.
            target=monitoring_destination(target,config.get('active_sla',''))
        else:target=paths[view]
        return Response(status_code=307,headers={'Location':target})

    @app.api_route('/console/{path:path}',methods=['GET','POST'])
    async def console(request:Request,path:str):
        suffix='/'+path+('?' + request.url.query if request.url.query else '')
        payload=await request.body()
        result=await run_in_threadpool(helper.call,'console',{'path':suffix,'method':request.method,'body':base64.b64encode(payload).decode(),
                                      'content_type':request.headers.get('content-type','application/json')})
        body=base64.b64decode(result['body']);kind=result['content_type']
        if path=='api/monitoring-status' and request.method=='GET' and result['status']==200:
            try:status=json.loads(body)
            except (ValueError,TypeError):raise Problem('view_unavailable','Monitoring could not confirm its configured view.',503) from None
            if not isinstance(status,dict):
                raise Problem('view_unavailable','Monitoring could not confirm its configured view.',503)
            if status.get('available'):
                # The inner reporting console can launch Monitoring too. Use
                # the applied selector here just as on the initial-view route.
                config=await run_in_threadpool(helper.call,'view')
                status['path']=monitoring_destination(status.get('path'),config.get('active_sla',''))
                status['server_time_seconds']=time.time()
                status['server_clock_supported']=True
                body=json.dumps(status).encode()
        if 'text/html' in kind:
            text=body.decode('utf-8')
            if path.startswith('grafana/') and request.url.path.startswith('/console/'):
                # Existing nested links retain an authenticated compatibility
                # redirect, while native assets/settings use the stable mount.
                return Response(status_code=307,headers={'Location':suffix})
            if path=='':
                text=hosted_report(text,request.query_params.get('section'))
            elif path.startswith('grafana/d/') and request.method=='GET' and result['status']==200 and request.query_params.get('fortilens-clock')=='server':
                text=monitoring_clock_document(text,time.time())
            body=text.encode()
        response=Response(body,status_code=result['status'],media_type=kind)
        if result.get('location'):
            location=result['location']
            if not location.startswith('/grafana/'):
                raise Problem('console_redirect_rejected','An unexpected console redirect was blocked.',502)
            response.headers['Location']=location
        return response

    return app


def _run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--bootstrap-file',type=Path,required=True)
    parser.add_argument('--helper-socket',type=Path,required=True)
    parser.add_argument('--helper-key',type=Path,required=True)
    parser.add_argument('--origin',default='http://127.0.0.1:3030')
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',default=3030,type=int)
    parser.add_argument('--tls-cert');parser.add_argument('--tls-key')
    args=parser.parse_args()
    if args.host not in ('127.0.0.1','::1') and not(args.tls_cert and args.tls_key):
        parser.error('LAN listeners require a certificate/key; use loopback with a trusted HTTPS proxy or SSH otherwise')
    import uvicorn
    app=create_app(args.data,args.bootstrap_file,Client(args.helper_socket,args.helper_key),origin=args.origin)
    try:uvicorn.run(app,host=args.host,port=args.port,ssl_certfile=args.tls_cert,ssl_keyfile=args.tls_key,access_log=False,proxy_headers=False,workers=1)
    finally:
        try:app.state.auth.close()
        except sqlite3.Error as exc:report_failure('web_auth_cleanup',exc)


def main():
    try:_run()
    except Problem as exc:raise SystemExit(exc.message) from None
    except Exception as exc:
        report_failure('web_startup',exc)
        raise SystemExit('Web management could not start safely. Review its configured origin, local state, permissions and service health.') from None


if __name__=='__main__':main()
