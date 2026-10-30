"""Single-origin FortiLens console. Private upstreams never receive browser credentials.

Grafana auth proxy must trust only loopback. The portal container shares its
network namespace, not its filesystem, with Grafana. Native admin login remains
separate; the portal always supplies a Viewer identity and retains upstream cookies
only in server memory, scoped to the FortiLens session.
"""
import argparse
import base64
import hashlib
import hmac
from http import cookies
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.parse

COOKIE = 'fortilens_session'
VIEWER = 'fortilens-portal'
REPORT_ROUTES = {'/api/report', '/api/device', '/api/diagnostics', '/api/inventory', '/api/wan'}
QUERY_ROUTES = {
    '/grafana/api/ds/query', '/grafana/api/frontend-metrics',
    '/grafana/api/user/auth-tokens/rotate',
    '/grafana/apis/features.grafana.app/v0alpha1/namespaces/default/ofrep/v1/evaluate/flags',
}


def query_post(path):
    if path in QUERY_ROUTES:
        return True
    # Prometheus/Loki variable editors use resource/proxy POST requests for
    # read queries too. Do not allow arbitrary datasource resource mutations.
    uid = r'[A-Za-z0-9_-]{1,80}'
    prefix = rf'/grafana/api/datasources/(?:uid/{uid}/resources|proxy/uid/{uid})'
    operation = r'(?:query|query_range|series|labels|label/[A-Za-z_][A-Za-z0-9_]*/values)'
    return bool(re.fullmatch(prefix + r'/(?:api/v1|loki/api/v1)/' + operation, path))


class Portal:
    def __init__(self, password, root, *, usage='http://fortilens-usage:8080',
                 grafana='http://127.0.0.1:3000', dashboard='forti-monitor', secure_cookie=False,
                 session_seconds=28800):
        if len(password) < 16 or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', dashboard):
            raise ValueError('Invalid portal credential or dashboard UID')
        self.password = password
        self.root = Path(root)
        self.usage = self.upstream(usage)
        self.grafana = self.upstream(grafana)
        self.dashboard = dashboard
        self.secure_cookie = secure_cookie
        self.session_seconds = session_seconds
        self.sessions = {}
        self.grafana_sessions = {}
        self.attempts = {}
        self.lock = threading.Lock()
        self.report_auth = 'Basic ' + base64.b64encode(('admin:' + password).encode()).decode()

    @staticmethod
    def upstream(value):
        parsed = urllib.parse.urlsplit(value)
        # Private container/loopback HTTP only. This is never a browser-supplied URL.
        if parsed.scheme != 'http' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ValueError('Use a private HTTP upstream origin without credentials or a path')
        return parsed

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def issue(self):
        now = time.monotonic()
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            if len(self.sessions) >= 128:
                del self.sessions[min(self.sessions, key=self.sessions.get)]
            self.sessions[self.digest(token)] = now + self.session_seconds
            self.grafana_sessions = {k: v for k, v in self.grafana_sessions.items() if k in self.sessions}
        return token

    def token(self, headers):
        try:
            jar = cookies.SimpleCookie(headers.get('Cookie', ''))
            return jar[COOKIE].value if COOKIE in jar else ''
        except cookies.CookieError:
            return ''

    def authenticated(self, headers):
        key = self.digest(self.token(headers))
        with self.lock:
            return self.sessions.get(key, 0) > time.monotonic()

    def revoke(self, headers):
        with self.lock:
            key = self.digest(self.token(headers))
            self.sessions.pop(key, None)
            self.grafana_sessions.pop(key, None)

    def grafana_headers(self, headers):
        key = self.digest(self.token(headers))
        with self.lock:
            saved = self.grafana_sessions.get(key, {}).get('cookies', {})
            value = '; '.join(name + '=' + token for name, token in saved.items())
        result = {'X-FortiLens-Viewer': VIEWER, 'X-FortiLens-Role': 'Viewer'}
        if value:
            result['Cookie'] = value
        return result

    def save_grafana_cookies(self, headers, response):
        key = self.digest(self.token(headers))
        with self.lock:
            if self.sessions.get(key, 0) <= time.monotonic():
                return
            saved = self.grafana_sessions.setdefault(key, {'cookies': {}, 'verified_until': 0})['cookies']
            for name, value in response.getheaders():
                if name.lower() != 'set-cookie':
                    continue
                jar = cookies.SimpleCookie(value)
                for name in ('grafana_session', 'grafana_session_expiry'):
                    if name in jar:
                        saved[name] = jar[name].value

    def allowed_attempt(self, address):
        now = time.monotonic()
        with self.lock:
            self.attempts = {k: v for k, v in self.attempts.items() if v[0] > now - 60}
            if address not in self.attempts and len(self.attempts) >= 256:
                return False
            start, count = self.attempts.get(address, (now, 0))
            self.attempts[address] = (start, count + 1)
            return count < 10

    def cookie(self, token='', *, clear=False):
        return (f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; '
                f'Max-Age={0 if clear else self.session_seconds}' + ('; Secure' if self.secure_cookie else ''))

    def connect(self, upstream):
        return http.client.HTTPConnection(upstream.hostname, upstream.port or 80, timeout=120)


def handler(portal):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'FortiLens'

        def log_message(self, *args):
            pass  # URLs, headers and passwords never enter an access log.

        def send(self, status, payload, kind='application/json; charset=utf-8', cookie=None):
            data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.response_started = True
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def parsed_path(self):
            if len(self.path) > 16384:
                raise ValueError('Path too long')
            parsed = urllib.parse.urlsplit(self.path)
            decoded = urllib.parse.unquote(parsed.path)
            if parsed.scheme or parsed.netloc or decoded.startswith('//') or '\\' in decoded or any(x in decoded.split('/') for x in ('.', '..')) or any(ord(x) < 32 for x in decoded):
                raise ValueError('Invalid path')
            return parsed.path

        def same_origin(self, *, custom=False):
            if custom and self.headers.get('X-FortiLens-Request') != '1':
                return False
            origin = self.headers.get('Origin') or self.headers.get('Referer')
            if not origin:
                return custom  # Non-browser clients must supply the non-simple header.
            parsed = urllib.parse.urlsplit(origin)
            return parsed.scheme in ('http', 'https') and parsed.netloc.lower() == self.headers.get('Host', '').lower()

        def body(self, limit):
            if hasattr(self, 'request_body'):
                if len(self.request_body) > limit:
                    raise ValueError('Request too large')
                return self.request_body
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('Chunked requests are not supported')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= limit:
                raise ValueError('Request too large')
            return self.rfile.read(size)

        def proxy(self, upstream, *, grafana=False, data=None):
            connection = portal.connect(upstream)
            # Construct an allowlist. Never forward client auth, cookies, proxy
            # identity headers or forwarded-for headers into either upstream.
            headers = {key: self.headers[key] for key in ('Accept', 'Accept-Encoding', 'Accept-Language', 'Content-Type') if key in self.headers}
            if grafana:
                headers.update(portal.grafana_headers(self.headers))
            else:
                headers['Authorization'] = portal.report_auth
            try:
                connection.request(self.command, self.path, body=data, headers=headers)
                response = connection.getresponse()
                if grafana:
                    portal.save_grafana_cookies(self.headers, response)
                location = response.getheader('Location')
                if location:
                    parsed = urllib.parse.urlsplit(location)
                    if not grafana or not parsed.path.startswith('/grafana/'):
                        return self.send(502, {'error': 'upstream_redirect_rejected'})
                    location = urllib.parse.urlunsplit(('', '', parsed.path, parsed.query, ''))
                self.response_started = True
                self.send_response(response.status)
                for name in ('Content-Type', 'Content-Length', 'Content-Encoding', 'Content-Disposition'):
                    value = response.getheader(name)
                    if value is not None:
                        self.send_header(name, value)
                if location:
                    self.send_header('Location', location)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Frame-Options', 'SAMEORIGIN')
                self.send_header('Content-Security-Policy', "frame-ancestors 'self'")
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                if self.command != 'HEAD':
                    while True:
                        block = response.read(65536)
                        if not block:
                            break
                        self.wfile.write(block)
            finally:
                connection.close()

        def verify_viewer(self):
            key = portal.digest(portal.token(self.headers))
            with portal.lock:
                if portal.grafana_sessions.get(key, {}).get('verified_until', 0) > time.monotonic():
                    return True
            connection = portal.connect(portal.grafana)
            try:
                # Grafana issues an auth-proxy session at /login. Retain this
                # cookie only inside the portal so its frontend can rotate the
                # session without a second browser login or a reload loop.
                connection.request('GET', '/grafana/login', headers=portal.grafana_headers(self.headers))
                response = connection.getresponse()
                portal.save_grafana_cookies(self.headers, response)
                response.read()
                connection.request('GET', '/grafana/api/user', headers=portal.grafana_headers(self.headers))
                response = connection.getresponse()
                portal.save_grafana_cookies(self.headers, response)
                user = json.loads(response.read(16384)) if response.status == 200 else {}
                connection.request('GET', '/grafana/api/user/orgs', headers=portal.grafana_headers(self.headers))
                response = connection.getresponse()
                portal.save_grafana_cookies(self.headers, response)
                orgs = json.loads(response.read(16384)) if response.status == 200 else []
                role = next((org.get('role') for org in orgs if org.get('orgId') == user.get('orgId')), None)
                if user.get('login') != VIEWER or role != 'Viewer' or user.get('isGrafanaAdmin'):
                    return False
                with portal.lock:
                    if portal.sessions.get(key, 0) <= time.monotonic():
                        return False
                    portal.grafana_sessions[key]['verified_until'] = time.monotonic() + 30
                return True
            finally:
                connection.close()

        def dispatch(self):
            path = self.parsed_path()
            if self.command in ('GET', 'HEAD'):
                if path == '/':
                    return self.send(200, (portal.root / 'reporting.html').read_text(encoding='utf-8'), 'text/html; charset=utf-8')
                if path == '/health':
                    return self.send(200, {'service': 'running', 'component': 'portal'})
                if path == '/api/portal':
                    return self.send(200, {'enabled': True, 'session_seconds': portal.session_seconds})
            if self.command == 'POST' and path == '/api/session':
                if not self.same_origin(custom=True):
                    return self.send(403, {'error': 'origin_rejected'})
                if not portal.allowed_attempt(self.client_address[0]):
                    return self.send(429, {'error': 'login_rate_limited'})
                supplied = json.loads(self.body(4096))
                username, password = supplied.get('username'), supplied.get('password')
                if not isinstance(username, str) or not isinstance(password, str) or username != 'admin' or not hmac.compare_digest(password.encode(), portal.password.encode()):
                    return self.send(401, {'error': 'authentication_required'})
                # No upstream or Grafana credential is returned to the browser.
                return self.send(200, {'authenticated': True}, cookie=portal.cookie(portal.issue()))
            api_credential = (self.command in ('GET', 'HEAD') and path in REPORT_ROUTES and
                              hmac.compare_digest(self.headers.get('Authorization', ''), portal.report_auth))
            if not portal.authenticated(self.headers) and not api_credential:
                return self.send(401, {'error': 'authentication_required'})
            if path == '/api/session' and self.command == 'GET':
                return self.send(200, {'authenticated': True})
            if path == '/api/session/logout' and self.command == 'POST':
                if not self.same_origin(custom=True):
                    return self.send(403, {'error': 'origin_rejected'})
                portal.revoke(self.headers)
                return self.send(200, {'authenticated': False}, cookie=portal.cookie(clear=True))
            if path == '/api/monitoring-status' and self.command == 'GET':
                if not self.verify_viewer():
                    return self.send(503, {'available': False, 'error': 'monitoring_viewer_unavailable'})
                return self.send(200, {'available': True, 'role': 'Viewer', 'path': '/grafana/d/' + portal.dashboard + '/?kiosk&theme=dark'})
            if self.command in ('GET', 'HEAD') and path in REPORT_ROUTES:
                return self.proxy(portal.usage)
            if path.startswith('/grafana/'):
                if not self.verify_viewer():
                    return self.send(503, {'error': 'monitoring_viewer_unavailable'})
                if self.command in ('GET', 'HEAD'):
                    return self.proxy(portal.grafana, grafana=True)
                if self.command == 'POST' and query_post(path) and self.same_origin():
                    return self.proxy(portal.grafana, grafana=True, data=self.body(2097152))
                return self.send(403, {'error': 'read_only_monitoring'})
            return self.send(404, {'error': 'not_found'})

        def do_GET(self):
            self.response_started = False
            try:
                if self.command == 'POST':
                    # Drain bounded bodies even on rejection. Closing a socket
                    # with unread data can discard the 401/403 response on Windows.
                    self.request_body = self.body(2097152)
                self.dispatch()
            except (ValueError, TypeError, AttributeError):
                self.send(400, {'error': 'invalid_request'})
            except (OSError, http.client.HTTPException):
                # Some bytes may already have been sent; never append a second
                # response to a partially streamed asset or API body.
                if self.response_started:
                    return
                self.send(503, {'error': 'upstream_unavailable'})

        do_HEAD = do_GET
        do_POST = do_GET

    return Handler


def configure(compose, config):
    """Opt-in generated install integration; no alerts, volumes or secrets replaced."""
    if not config.get('portal_enabled'):
        return
    if not config.get('accounting_enabled'):
        raise ValueError('The unified portal requires persistent accounting')
    services = compose['services']
    grafana = services['grafana']
    native_host = config['grafana_bind'] if config['grafana_bind'] != '0.0.0.0' else '127.0.0.1'
    grafana['environment'].update({
        'GF_SERVER_ROOT_URL': f"http://{native_host}:{config['grafana_port']}/grafana/",
        'GF_SERVER_SERVE_FROM_SUB_PATH': 'true',
        'GF_SECURITY_ALLOW_EMBEDDING': 'true',
        'GF_SECURITY_CONTENT_SECURITY_POLICY': 'true',
        'GF_SECURITY_CONTENT_SECURITY_POLICY_TEMPLATE': "frame-ancestors 'self';",
        'GF_AUTH_PROXY_ENABLED': 'true', 'GF_AUTH_PROXY_HEADER_NAME': 'X-FortiLens-Viewer',
        'GF_AUTH_PROXY_HEADER_PROPERTY': 'username', 'GF_AUTH_PROXY_AUTO_SIGN_UP': 'true',
        'GF_AUTH_PROXY_WHITELIST': '127.0.0.1', 'GF_AUTH_PROXY_SYNC_TTL': '0',
        'GF_AUTH_PROXY_HEADERS': 'Role:X-FortiLens-Role', 'GF_AUTH_PROXY_ENABLE_LOGIN_TOKEN': 'true',
        'GF_USERS_AUTO_ASSIGN_ORG_ROLE': 'Viewer', 'GF_PLUGINS_PREINSTALL_AUTO_UPDATE': 'false'})
    services['fortilens-usage'].pop('ports', None)
    grafana['ports'].append(f"{config['usage_bind']}:{config['usage_port']}:8081")
    services['fortilens-portal'] = {
        'image': services['fortilens-usage']['image'], 'build': services['fortilens-usage']['build'].copy(),
        'platform': 'linux/amd64', 'restart': 'unless-stopped',
        'network_mode': 'service:grafana', 'depends_on': ['grafana', 'fortilens-usage'],
        'command': ['python', 'portal.py', '--password-file', '/run/secrets/usage_admin_password'] +
                   (['--secure-cookie'] if config.get('portal_secure_cookie') else []),
        'secrets': ['usage_admin_password'], 'read_only': True,
        'tmpfs': ['/tmp:rw,noexec,nosuid,size=8m'], 'cap_drop': ['ALL'],
        'security_opt': ['no-new-privileges:true'], 'cpus': '0.25', 'mem_limit': '96m',
        'healthcheck': {'test': ['CMD', 'python', '-c', "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/health',timeout=3)"], 'interval': '30s', 'timeout': '5s', 'retries': 3},
        'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--password-file', required=True, type=Path)
    parser.add_argument('--usage-url', default='http://fortilens-usage:8080')
    parser.add_argument('--grafana-url', default='http://127.0.0.1:3000')
    parser.add_argument('--dashboard-uid', default='forti-monitor')
    parser.add_argument('--listen', default='0.0.0.0')
    parser.add_argument('--port', default=8081, type=int)
    parser.add_argument('--secure-cookie', action='store_true')
    args = parser.parse_args()
    portal = Portal(args.password_file.read_text().strip(), Path(__file__).parent,
                    usage=args.usage_url, grafana=args.grafana_url,
                    dashboard=args.dashboard_uid, secure_cookie=args.secure_cookie)
    server = ThreadingHTTPServer((args.listen, args.port), handler(portal))
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
