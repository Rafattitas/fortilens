"""Independent nonroot web management process. No Docker or installation mounts."""
import argparse
from contextlib import asynccontextmanager
import base64
import hmac
import json
from pathlib import Path
from urllib.parse import urlsplit, urlencode
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
from pydantic import Field, SecretStr
from .auth import Auth
from .common import Problem
from .helper import Client
from .models import Apply, Change, Job, Revision, Strict

STATIC=Path(__file__).parent/'static'
COOKIE='fortilens_management'


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
    auth=Auth(data,bootstrap_file)
    @asynccontextmanager
    async def lifespan(app):
        try:yield
        finally:auth.close()
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
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        embedded=request.url.path.startswith(('/console/','/grafana/'))
        response.headers['Content-Security-Policy']=("frame-ancestors 'self'" if embedded else "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        response.headers['X-Frame-Options']='SAMEORIGIN' if embedded else 'DENY'
        return response

    @app.get('/')
    def index():return FileResponse(STATIC/'index.html')
    app.mount('/static',StaticFiles(directory=STATIC),name='static')

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

    @app.api_route('/console/{path:path}',methods=['GET','POST'])
    async def console(request:Request,path:str):
        suffix='/'+path+('?' + request.url.query if request.url.query else '')
        payload=await request.body()
        result=await run_in_threadpool(helper.call,'console',{'path':suffix,'method':request.method,'body':base64.b64encode(payload).decode(),
                                      'content_type':request.headers.get('content-type','application/json')})
        body=base64.b64decode(result['body']);kind=result['content_type']
        if 'text/html' in kind:
            text=body.decode('utf-8')
            if path.startswith('grafana/') and request.url.path.startswith('/console/'):
                # Existing nested links retain an authenticated compatibility
                # redirect, while native assets/settings use the stable mount.
                return Response(status_code=307,headers={'Location':suffix})
            if path=='':
                text=text.replace("'/api/","'/console/api/")
                # Existing reporting code renders received records unchanged.
                # Hide its duplicate chrome when hosted in the management shell.
                section=request.query_params.get('section')
                css='header,aside,.page-head,footer{display:none!important}.layout{display:block!important}main{padding:12px!important}'
                if section=='devices':css+='#vlan-section{display:none!important}'
                if section=='vlans':css+='#device-section{display:none!important}'
                text=text.replace('</style>','\n'+css+'</style>')
            body=text.encode()
        response=Response(body,status_code=result['status'],media_type=kind)
        if result.get('location'):
            location=result['location']
            if not location.startswith('/grafana/'):
                raise Problem('console_redirect_rejected','An unexpected console redirect was blocked.',502)
            response.headers['Location']=location
        return response

    return app


def main():
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
    uvicorn.run(app,host=args.host,port=args.port,ssl_certfile=args.tls_cert,ssl_keyfile=args.tls_key,access_log=False,proxy_headers=False,workers=1)


if __name__=='__main__':main()
