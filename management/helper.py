"""Authenticated Unix-socket helper. Only the installed web UID may connect."""
import argparse
import hmac
import http.client
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
from .common import Problem, load
from .configuration import Configuration
from .runtime import Operations

MAX_REQUEST = 4*1024*1024
MAX_RESPONSE = 48*1024*1024


class Client:
    def __init__(self, path, key_file):
        self.path=str(path);self.key_file=Path(key_file)

    def call(self, operation, data=None):
        class Local(http.client.HTTPConnection):
            def connect(inner):
                inner.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
                inner.sock.settimeout(45);inner.sock.connect(self.path)
        connection=Local('localhost')
        try:
            connection.request('POST','/operation',json.dumps({'operation':operation,'data':data or {}}),
                               {'Authorization':'Bearer '+self.key_file.read_text().strip(),'Content-Type':'application/json'})
            r=connection.getresponse();payload=r.read(MAX_RESPONSE+1)
            if len(payload)>MAX_RESPONSE:raise Problem('helper_response_limit','The management response exceeded its safe limit.',503)
            value=json.loads(payload)
            if r.status!=200:raise Problem(value.get('error','helper_rejected'),value.get('message','The helper rejected this operation.'),r.status)
            return value
        except (OSError,ValueError,http.client.HTTPException):
            raise Problem('helper_unavailable','The management helper is unavailable. Sign-in still works; check its system service.',503) from None
        finally:connection.close()


def handler(operations, key, web_uid):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            try:
                if hasattr(socket,'SO_PEERCRED'):
                    _,uid,_=struct.unpack('3i',self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                    if uid not in (0,web_uid):raise Problem('peer_rejected','Local peer is not authorized.',403)
                if self.path!='/operation' or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+key):
                    raise Problem('helper_auth_required','Local management authentication failed.',403)
                if self.headers.get('Transfer-Encoding'):raise Problem('invalid_body','Chunked helper requests are not supported.')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=MAX_REQUEST:raise Problem('request_limit','The operation exceeded its size limit.')
                value=json.loads(self.rfile.read(length))
                if not isinstance(value,dict) or set(value)!={'operation','data'} or not isinstance(value['data'],dict):
                    raise Problem('invalid_operation','Choose a supported management operation.')
                result=operations.dispatch(value['operation'],value['data']);status=200
            except Problem as e:result={'error':e.code,'message':e.message};status=e.status
            except Exception:
                # Includes validation, YAML, path, Docker and upstream parsing
                # errors. Never serialize their input-bearing exception values.
                result={'error':'operation_failed','message':'The operation could not be completed safely. Review saved settings and diagnostics.'};status=400
            payload=json.dumps(result).encode()
            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    return Handler


class Server(socketserver.ThreadingMixIn,getattr(socketserver,'UnixStreamServer',socketserver.TCPServer)):
    daemon_threads=True
    def get_request(self):
        sock,address=super().get_request();sock.settimeout(30);return sock,address


def main():
    if not hasattr(socketserver,'UnixStreamServer') or not hasattr(socket,'SO_PEERCRED'):
        raise SystemExit('The management helper requires Linux Unix sockets and peer credentials.')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry',required=True,type=Path)
    args=parser.parse_args();config=load(args.registry)
    # The registry is created by install.py, protected from the web UID. Never
    # permit a web-selected root, Compose file or project name on this socket.
    if args.registry.is_symlink() or args.registry.stat().st_mode & 0o022 or args.registry.stat().st_uid!=os.geteuid():
        raise SystemExit('The helper registry must be owner-writable only.')
    c=Configuration(config['installation'],config['control'],project=config['project'],compose_file=config['compose_file'])
    path=Path(config['socket']);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if not path.is_socket():raise SystemExit('Refusing to replace a non-socket runtime file')
        probe=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:probe.connect(str(path))
        except OSError:path.unlink()
        else:raise SystemExit('A helper is already listening')
        finally:probe.close()
    operations=Operations(c,dashboard_uid=config.get('dashboard_uid','forti-monitor'))
    server=Server(str(path),handler(operations,Path(config['key']).read_text().strip(),config['web_uid']))
    os.chmod(path,0o660);os.chown(path,-1,config['web_gid'])
    try:server.serve_forever()
    finally:server.server_close();path.unlink(missing_ok=True)


if __name__=='__main__':main()
