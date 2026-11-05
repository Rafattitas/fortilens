"""Synthetic HTTPS FortiGate metadata for the isolated integration stack only."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import ssl
from urllib.parse import urlsplit


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.headers.get('Authorization')!='Bearer synthetic-integration-token':
            self.send_response(401);self.end_headers();return
        route=urlsplit(self.path).path
        if route=='/api/v2/cmdb/system/interface':
            result=[{'name':'staff','vdom':'root','type':'vlan','role':'lan','interface':'port1','vlanid':100},
                {'name':'guests','vdom':'root','type':'vlan','role':'lan','interface':'port1','vlanid':200},
                {'name':'uplink-a','vdom':'root','type':'physical','role':'wan'}]
        elif route=='/api/v2/monitor/router/lookup':result={'success':True,'interface':'uplink-a'}
        elif route=='/api/v2/monitor/system/status':result={'serial':'FG-FIXTURE','version':'synthetic'}
        else:
            self.send_response(404);self.end_headers();return
        data=json.dumps({'status':'success','vdom':'root','results':result}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)


server=ThreadingHTTPServer(('0.0.0.0',8443),Handler)
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain('/fixture/cert.pem','/fixture/key.pem')
server.socket=context.wrap_socket(server.socket,server_side=True)
server.serve_forever()
