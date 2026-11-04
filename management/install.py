"""Explicit Ubuntu host bootstrap. Never initializes or starts monitoring data."""
import argparse
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
from urllib.parse import urlsplit


def run(*args,check=True):
    try:
        result=subprocess.run(list(args),text=True,capture_output=True,stdin=subprocess.DEVNULL,timeout=900)
    except (OSError,UnicodeError,subprocess.SubprocessError):
        raise SystemExit('A required host operation could not run or timed out. Review permissions and the documented prerequisites; process output is suppressed.') from None
    if check and result.returncode:raise SystemExit('Required host operation failed: '+Path(str(args[0])).name+'. Review permissions and the documented prerequisites.')
    return result


def path_value(value):
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+',value) or '..' in Path(value).parts:
        raise argparse.ArgumentTypeError('Use an absolute host path with letters, numbers, dots, dashes and slashes.')
    path=Path(value)
    if any(parent.is_symlink() for parent in (path,*path.parents)):
        raise argparse.ArgumentTypeError('Installation paths must not traverse symbolic links.')
    return path.resolve()


def put(path,data,mode=0o600,uid=0,gid=0):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink():raise SystemExit('Refusing to replace an installation symlink')
    temp=path.with_name('.'+path.name+'.new-'+secrets.token_hex(6))
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.chown(temp,uid,gid);os.chmod(temp,mode);os.replace(temp,path)
    finally:
        try:temp.unlink(missing_ok=True)
        except OSError:pass


def saved_metadata(path, required):
    """Validate only installer-owned metadata; never echo submitted contents."""
    try:value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (ValueError,UnicodeError):
        raise SystemExit('Saved installer metadata is invalid. Preserve existing files and review the documented recovery procedure.') from None
    if not isinstance(value,dict) or not all(key in value for key in required):
        raise SystemExit('Saved installer metadata is incomplete. Preserve existing files and review the documented recovery procedure.')
    if any(not isinstance(value[key],str) and not (key=='project' and value[key] is None and 'adopt' in required) for key in ('data','origin','installation','project','compose_file') if key in required):
        raise SystemExit('Saved installer metadata has unsupported field types. No installation was reinitialized.')
    if 'source' in required and (not isinstance(value['source'],dict) or not all(isinstance(key,str) and isinstance(item,str) and re.fullmatch(r'[0-9a-f]{64}',item) for key,item in value['source'].items())):
        raise SystemExit('Saved source fingerprints are invalid. Preserve the installation and review its original source.')
    return value


def source_files(source):
    # Existing checkouts may also contain generated installations, exports and
    # secrets. Never recursively copy them into world-readable application code.
    names=['management/'+name for name in (
        '__init__.py','app.py','auth.py','common.py','configuration.py','helper.py',
        'install.py','models.py','recovery.py','runtime.py','storage.py','requirements.txt',
        'requirements-test.txt','Dockerfile','static/app.css','static/app.js','static/grafana-clock.js','static/index.html')]
    names+=['portable/'+name for name in (
        'setup.py','doctor.py','interfaces.py','accounting.py','usage_service.py','portal.py',
        'wan_health.py','upgrade.py','usage_quality.py','dashboard_style.py','alerts.py',
        'syslog_receiver.py','reporting.html','site.example.json','README.md','LICENSE',
        'templates/compose.json','templates/prometheus.json','templates/loki.json',
        'templates/config.alloy','templates/datasources.json','templates/dashboard.json','templates/Dockerfile.usage',
        'templates/Dockerfile.syslog')]
    result={};source=Path(source).resolve()
    for name in names:
        file=source/name
        if any(p.is_symlink() for p in (file,*file.parents) if p.is_relative_to(source)):
            raise SystemExit('Installation source must not contain symlinks')
        if not file.is_file():raise SystemExit('The reviewed installation source is incomplete.')
        result[name]=hashlib.sha256(file.read_bytes()).hexdigest()
    return result


def wait_web(bind,port,origin,tls):
    parsed=urlsplit(origin);address='127.0.0.1' if bind=='0.0.0.0' else '::1' if bind=='::' else bind
    for _ in range(30):
        conn=None
        try:
            if tls:
                context=ssl.create_default_context(cafile='/etc/fortilens/tls.crt')
                class LocalTLS(http.client.HTTPSConnection):
                    def connect(self):self.sock=context.wrap_socket(socket.create_connection((address,port),timeout=2),server_hostname=parsed.hostname)
                conn=LocalTLS(parsed.hostname,port,timeout=2,context=context)
            else:conn=http.client.HTTPConnection(address,port,timeout=2)
            conn.request('GET','/health',headers={'Host':parsed.netloc})
            response=conn.getresponse()
            payload=json.loads(response.read(4096)) if response.status==200 else None
            if isinstance(payload,dict) and payload.get('independent_of_monitoring') is True:return
        except (OSError,ValueError,http.client.HTTPException):pass
        finally:
            if conn:conn.close()
        time.sleep(1)
    raise SystemExit('The web service did not confirm readiness. Existing data is retained; review journalctl -u fortilens-web and rerun the same installer.')


def _main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix',type=path_value,default=Path('/opt/fortilens'))
    parser.add_argument('--data',type=path_value,default=Path('/var/lib/fortilens'))
    parser.add_argument('--bind',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=3030)
    parser.add_argument('--origin')
    parser.add_argument('--tls-cert',type=path_value);parser.add_argument('--tls-key',type=path_value)
    parser.add_argument('--adopt',type=path_value,help='Explicitly register an existing installation; no reinitialization')
    parser.add_argument('--compose-file',default='compose.yaml')
    parser.add_argument('--project',help='Exact existing Compose project; required with --adopt')
    parser.add_argument('--dashboard-uid',default='forti-monitor')
    parser.add_argument('--install-dependencies',action='store_true',help='Explicitly authorize Ubuntu apt prerequisites')
    args=parser.parse_args()
    if sys.platform!='linux' or not hasattr(os,'geteuid') or os.geteuid()!=0:
        raise SystemExit('Run this installer as root on an Ubuntu Linux server. It does not modify Windows.')
    import pwd
    import grp
    osrelease=Path('/etc/os-release').read_text()
    if not re.search(r'^ID=ubuntu$',osrelease,re.M) or platform.machine() not in ('x86_64','amd64'):
        raise SystemExit('This release supports Ubuntu x86-64; monitoring images are pinned for linux/amd64.')
    if sys.version_info<(3,11):raise SystemExit('Web management requires Python3.11+ (Ubuntu24.04+ recommended). The portable CLI remains Python3.10 compatible.')
    if args.install_dependencies:
        run('apt-get','update');run('apt-get','install','-y','python3-venv','ca-certificates','tzdata','docker.io','docker-compose-v2')
    for command in ('docker','systemctl','useradd'):
        if not shutil.which(command):raise SystemExit('Missing '+command+'. Install the documented prerequisites or explicitly choose --install-dependencies.')
    run('docker','version','--format','{{.Server.Version}}');run('docker','compose','version')
    if not 1024<=args.port<=65535:raise SystemExit('Choose a management port from1024 to65535.')
    try:ipaddress.ip_address(args.bind)
    except ValueError:raise SystemExit('--bind must be a literal local IP address.') from None
    tls=bool(args.tls_cert and args.tls_key)
    if bool(args.tls_cert)!=bool(args.tls_key):raise SystemExit('Provide both --tls-cert and --tls-key.')
    if args.bind not in ('127.0.0.1','::1') and not tls:raise SystemExit('LAN listeners require TLS certificate and key. Loopback/SSH is the default.')
    if tls:
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(args.tls_cert,args.tls_key)
    origin=args.origin or ('https' if tls else 'http')+'://'+('['+args.bind+']' if ':' in args.bind else args.bind)+':'+str(args.port)
    try:parsed=urlsplit(origin)
    except ValueError:
        raise SystemExit('Choose a plain HTTP(S) browser origin, without paths, credentials or query parameters.') from None
    if not re.fullmatch(r'https?://[A-Za-z0-9.\[\]:-]+/?',origin) or parsed.scheme not in ('http','https') or not parsed.hostname or parsed.path not in ('','/') or parsed.username or parsed.query or parsed.fragment:
        raise SystemExit('Choose a plain HTTP(S) browser origin, without paths, credentials or query parameters.')
    try:parsed.port
    except ValueError:raise SystemExit('Choose a valid origin port.') from None
    if parsed.scheme=='http' and parsed.hostname not in ('127.0.0.1','localhost','::1'):
        raise SystemExit('Use HTTPS for network access.')
    if tls and parsed.scheme!='https':raise SystemExit('A TLS listener needs an HTTPS origin.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',args.dashboard_uid):raise SystemExit('Invalid dashboard UID.')
    meta=args.prefix/'installation.json'
    progress=args.prefix/'installing.json'
    previous=saved_metadata(meta,('data','origin','source')) if meta.exists() else None
    partial=saved_metadata(progress,('data','origin','source','adopt','project','compose_file')) if progress.exists() else None
    for protected in (args.prefix,args.data,Path('/etc/fortilens')):
        if protected.exists() and (protected.is_symlink() or protected.stat().st_uid!=0 or protected.stat().st_mode&0o022):
            raise SystemExit('Management installation/state directories must be root-owned and not group/world writable.')
    if previous and (previous['data']!=str(args.data) or previous['origin']!=origin):
        raise SystemExit('An installation already exists with different access settings. Preserve it and use the documented host change procedure.')
    if not previous and not partial and args.prefix.exists() and any(args.prefix.iterdir()):raise SystemExit('The installation prefix is not empty; nothing overwritten.')
    if partial and (partial['data']!=str(args.data) or partial['origin']!=origin):raise SystemExit('Resume the interrupted installation with the same options.')
    active=run('systemctl','is-active','--quiet','fortilens-web',check=False).returncode==0
    if not active:
        probe=socket.socket(socket.AF_INET6 if ':' in args.bind else socket.AF_INET,socket.SOCK_STREAM)
        try:probe.bind((args.bind,args.port))
        except OSError:raise SystemExit('The management port is already in use; no service was changed.') from None
        finally:probe.close()
    source=Path(__file__).resolve().parents[1];hashes=source_files(source)
    if previous and previous['source']!=hashes:
        raise SystemExit('Installed source differs. This bootstrap does not perform version upgrades; use the reviewed manual update procedure.')
    if partial and partial['source']!=hashes:raise SystemExit('The interrupted installation used different source. Restore that source before resuming.')
    if args.adopt and not args.project:raise SystemExit('--adopt requires the exact --project to preserve its existing Docker volume identity.')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,80}',args.project or 'fortilens'):raise SystemExit('Invalid Compose project.')
    if Path(args.compose_file).is_absolute() or '..' in Path(args.compose_file).parts:raise SystemExit('Compose file must be inside the registered installation.')
    if args.adopt and not (args.adopt/args.compose_file).is_file():raise SystemExit('The existing Compose file was not found. No initialization performed.')
    try:account=pwd.getpwnam('fortilens')
    except KeyError:
        run('useradd','--system','--user-group','--home-dir',str(args.data/'web'),'--shell','/usr/sbin/nologin','fortilens')
        account=pwd.getpwnam('fortilens')
    try:
        if 'fortilens' in grp.getgrnam('docker').gr_mem or account.pw_gid==grp.getgrnam('docker').gr_gid:
            raise SystemExit('The web user must not belong to the Docker group. Correct host permissions first.')
    except KeyError:pass
    args.prefix.mkdir(parents=True,exist_ok=True);os.chmod(args.prefix,0o755)
    if not previous and not partial:
        put(progress,json.dumps({'data':str(args.data),'origin':origin,'source':hashes,'adopt':str(args.adopt) if args.adopt else None,'project':args.project,'compose_file':args.compose_file}))
    if partial and (partial.get('adopt')!=(str(args.adopt) if args.adopt else None) or partial.get('project')!=args.project or partial['compose_file']!=args.compose_file):
        raise SystemExit('Resume adoption with exactly the original --adopt, --project and --compose-file options.')
    args.data.mkdir(parents=True,exist_ok=True);os.chmod(args.data,0o755)
    web=args.data/'web';control=args.data/'control'
    for directory,uid,gid in ((web,account.pw_uid,account.pw_gid),(control,0,0)):
        directory.mkdir(parents=True,exist_ok=True);os.chown(directory,uid,gid);os.chmod(directory,0o700)
    stack=args.adopt or control/'stack'
    if not args.adopt:stack.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not previous:
        for name in hashes:
            target=args.prefix/'src'/name;target.parent.mkdir(parents=True,exist_ok=True)
            put(target,(source/name).read_text(encoding='utf-8'),mode=0o644)
        run(sys.executable,'-m','venv',str(args.prefix/'venv'))
        run(str(args.prefix/'venv/bin/python'),'-m','pip','install','--disable-pip-version-check','-r',str(args.prefix/'src/management/requirements.txt'))
    key=Path('/etc/fortilens/helper.key')
    if not key.exists():put(key,secrets.token_urlsafe(48)+'\n',mode=0o640,gid=account.pw_gid)
    if not (web/'auth.sqlite').exists() and not (web/'bootstrap.token').exists():
        put(web/'bootstrap.token',secrets.token_urlsafe(32)+'\n',uid=account.pw_uid,gid=account.pw_gid)
    registry={'installation':str(stack),'control':str(control),'project':args.project or 'fortilens-'+hashlib.sha256(str(stack).encode()).hexdigest()[:10],
              'compose_file':args.compose_file,'socket':'/run/fortilens/helper.sock','key':str(key),
              'web_uid':account.pw_uid,'web_gid':account.pw_gid,'dashboard_uid':args.dashboard_uid,'management_port':args.port}
    if not previous:put('/etc/fortilens/registry.json',json.dumps(registry,indent=2))
    else:
        registry=saved_metadata('/etc/fortilens/registry.json',('installation','project'))
        if args.adopt and str(args.adopt)!=registry['installation'] or args.project and args.project!=registry['project']:
            raise SystemExit('The existing installation is registered to a different project. Nothing was reinitialized.')
        stack=Path(registry['installation'])
    # Validate adoption with the installed interpreter before starting either unit.
    validate="from management.configuration import Configuration; import json; c=json.load(open('/etc/fortilens/registry.json')); Configuration(c['installation'],c['control'],project=c['project'],compose_file=c['compose_file'])"
    env=os.environ.copy();env['PYTHONPATH']=str(args.prefix/'src')
    result=subprocess.run([str(args.prefix/'venv/bin/python'),'-c',validate],env=env,capture_output=True,text=True,timeout=60)
    if result.returncode:raise SystemExit('Existing configuration registration needs review. It was not reinitialized or overwritten; see the migration guide.')
    certargs=''
    if tls:
        put('/etc/fortilens/tls.crt',args.tls_cert.read_text(),mode=0o644,gid=account.pw_gid)
        put('/etc/fortilens/tls.key',args.tls_key.read_text(),mode=0o640,gid=account.pw_gid)
        certargs=' --tls-cert /etc/fortilens/tls.crt --tls-key /etc/fortilens/tls.key'
    python=args.prefix/'venv/bin/python';work=args.prefix/'src'
    common=f'WorkingDirectory={work}\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nEnvironment=PYTHONUNBUFFERED=1\nRestart=on-failure\nRestartSec=3\nNoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=strict\nProtectKernelTunables=true\nProtectControlGroups=true\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n'
    helper=f'''[Unit]
Description=FortiLens restricted management helper
After=docker.service
[Service]
User=root
Group=fortilens
{common}ExecStart={python} -m management.helper --registry /etc/fortilens/registry.json
RuntimeDirectory=fortilens
RuntimeDirectoryMode=0750
ReadWritePaths={control} {stack} /run/fortilens
ProtectHome=read-only
MemoryMax=512M
[Install]
WantedBy=multi-user.target
'''
    webunit=f'''[Unit]
Description=FortiLens independent web management
After=network.target fortilens-helper.service
Wants=fortilens-helper.service
[Service]
User=fortilens
Group=fortilens
{common}ExecStart={python} -m management.app --data {web} --bootstrap-file {web}/bootstrap.token --helper-socket /run/fortilens/helper.sock --helper-key {key} --host {args.bind} --port {args.port} --origin {origin}{certargs}
ProtectHome=true
ReadWritePaths={web}
InaccessiblePaths=-/var/run/docker.sock {control} {stack}
MemoryMax=256M
[Install]
WantedBy=multi-user.target
'''
    put('/etc/systemd/system/fortilens-helper.service',helper,mode=0o644)
    put('/etc/systemd/system/fortilens-web.service',webunit,mode=0o644)
    run('systemctl','daemon-reload');run('systemctl','enable','--now','fortilens-helper','fortilens-web')
    run('systemctl','is-active','--quiet','fortilens-helper','fortilens-web')
    wait_web(args.bind,args.port,origin,tls)
    put(meta,json.dumps({'data':str(args.data),'origin':origin,'source':hashes},indent=2))
    progress.unlink(missing_ok=True)
    print('FortiLens management: '+origin)
    print('Monitoring has not been reinitialized. Review and apply configuration in the web interface.')
    if (web/'bootstrap.token').exists():
        print('One-time administrator bootstrap token (not included in the URL):')
        print((web/'bootstrap.token').read_text().strip())
    else:print('Administrator already configured. Existing authentication and monitoring data preserved.')
    if not tls:print('SSH alternative: ssh -L '+str(args.port)+':127.0.0.1:'+str(args.port)+' user@server, then open '+origin)


def main():
    try:return _main()
    except (OSError,UnicodeError,subprocess.SubprocessError):
        raise SystemExit('Installation could not read/update required host files or complete a host operation. Review permissions, storage, TLS and service prerequisites. Keep existing state and resume with the same reviewed options; private details are suppressed.') from None
    except KeyboardInterrupt:
        print('Installation cancelled. Some host steps may have completed; preserve state and review the same options before resuming.',file=sys.stderr)
        raise SystemExit(130) from None


if __name__=='__main__':main()
