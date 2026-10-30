"""Portal trust boundaries, session lifetime and actual proxied request behavior."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portable'))
import portal

PASSWORD = 'synthetic-portal-password'


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.viewer_role = 'Viewer'
        self.server_admin = False
        owner = self
        calls = self.requests

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                calls.append((self.command, self.path, dict(self.headers), data))
                payload = json.dumps([{'orgId': 1, 'role': owner.viewer_role}] if self.path.endswith('/orgs') else {'login': portal.VIEWER, 'orgId': 1, 'isGrafanaAdmin': owner.server_admin}).encode()
                if self.path == '/grafana/redirect':
                    self.send_response(302)
                    self.send_header('Location', 'http://localhost:3000/grafana/d/example?orgId=1')
                else:
                    self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Set-Cookie', 'grafana_session=must-not-leave-upstream')
                self.end_headers()
                self.wfile.write(payload)
            do_POST = do_GET

        self.upstream = self.server(Upstream)
        upstream = 'http://127.0.0.1:' + str(self.upstream.server_port)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        Path(self.temp.name, 'reporting.html').write_text('<h1>FortiLens</h1>')
        self.portal = portal.Portal(PASSWORD, self.temp.name, usage=upstream, grafana=upstream)
        self.gateway = self.server(portal.handler(self.portal))
        self.cookie = ''

    def server(self, handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown(); server.server_close(); thread.join()
        self.addCleanup(close)
        return server

    def request(self, path, method='GET', body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=5)
        supplied = {'Cookie': self.cookie} if self.cookie else {}
        supplied.update(headers or {})
        connection.request(method, path, body=body, headers=supplied)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def login(self, **options):
        status, headers, body = self.request('/api/session', 'POST', json.dumps({'username': 'admin', 'password': PASSWORD}), {'X-FortiLens-Request': '1', **options})
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';')[0]
        return headers, body

    def test_no_upstream_access_without_session_even_with_spoofed_identity(self):
        for path in ('/api/report', '/grafana/api/user', '/api/monitoring-status'):
            self.assertEqual(self.request(path, headers={'X-FortiLens-Viewer': 'admin'})[0], 401)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.request('/health')[0], 200)

    def test_session_uses_httponly_cookie_and_logout_revokes_replays(self):
        headers, body = self.login()
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        self.assertNotIn(PASSWORD.encode(), body)
        self.assertEqual(self.request('/api/session')[0], 200)
        self.assertEqual(self.request('/api/session/logout', 'POST', '{}', {'X-FortiLens-Request': '1'})[0], 200)
        self.assertEqual(self.request('/grafana/api/user')[0], 401)

    def test_expired_and_restart_sessions_are_rejected(self):
        self.login()
        self.portal.sessions = {key: 0 for key in self.portal.sessions}
        self.assertEqual(self.request('/api/report')[0], 401)
        self.portal.sessions.clear()
        self.assertEqual(self.request('/api/session')[0], 401)

    def test_upstream_credentials_are_fixed_and_browser_identity_is_discarded(self):
        self.login()
        spoof = {'Authorization': 'Bearer malicious', 'X-FortiLens-Viewer': 'admin', 'X-FortiLens-Role': 'Admin', 'X-Forwarded-For': '127.0.0.1'}
        status, headers, _ = self.request('/grafana/api/user', headers=spoof)
        self.assertEqual(status, 200)
        self.assertNotIn('Set-Cookie', headers)
        actual = self.requests[-1][2]
        self.assertEqual(actual['X-FortiLens-Viewer'], portal.VIEWER)
        self.assertEqual(actual['X-FortiLens-Role'], 'Viewer')
        self.assertNotIn(self.cookie.split('=', 1)[1], actual.get('Cookie', ''))
        self.assertNotIn('Authorization', actual)
        self.assertNotIn('X-Forwarded-For', actual)
        self.request('/api/report?period=month', headers=spoof)
        actual = self.requests[-1][2]
        self.assertEqual(actual['Authorization'], 'Basic ' + base64.b64encode(('admin:' + PASSWORD).encode()).decode())
        self.assertNotIn('X-FortiLens-Viewer', actual)

    def test_csrf_bad_paths_mutations_and_external_proxy_targets_are_rejected(self):
        self.assertEqual(self.request('/api/session', 'POST', '{}')[0], 403)
        self.assertEqual(self.request('/api/session', 'POST', '{}', {'X-FortiLens-Request': '1', 'Origin': 'https://other.example'})[0], 403)
        self.login()
        for path in ('/grafana/api/admin/users', '/grafana/api/dashboards/db'):
            self.assertEqual(self.request(path, 'POST', '{}')[0], 403)
        self.assertEqual(self.request('/grafana/api/ds/query', 'POST', '{}', {'Origin': 'https://other.example'})[0], 403)
        for path in ('/grafana/%2e%2e/api/admin', 'http://other.example/grafana/', '/grafana/%5cadmin'):
            self.assertEqual(self.request(path)[0], 400)
        self.assertEqual(self.request('/api/session/logout', 'POST', '{}', {'X-FortiLens-Request': '1', 'Origin': 'https://other.example'})[0], 403)
        self.assertEqual(self.request('/api/session')[0], 200)

    def test_real_query_post_preserves_body_and_rewrites_redirect_to_same_origin(self):
        self.login()
        origin = 'http://127.0.0.1:' + str(self.gateway.server_port)
        data = b'{"queries":[{"refId":"A"}]}'
        self.assertEqual(self.request('/grafana/api/ds/query', 'POST', data, {'Origin': origin})[0], 200)
        self.assertEqual(self.requests[-1][3], data)
        status, headers, _ = self.request('/grafana/redirect')
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], '/grafana/d/example?orgId=1')

    def test_readiness_requires_viewer_and_upstream_failure_is_explicit(self):
        self.login()
        self.assertEqual(json.loads(self.request('/api/monitoring-status')[2])['role'], 'Viewer')
        self.upstream.shutdown(); self.upstream.server_close()
        self.assertEqual(self.request('/api/report')[0], 503)

    def test_login_rate_limit_does_not_expose_password(self):
        for _ in range(10):
            self.assertEqual(self.request('/api/session', 'POST', '{"username":"admin","password":"wrong"}', {'X-FortiLens-Request': '1'})[0], 401)
        status, _, body = self.request('/api/session', 'POST', '{}', {'X-FortiLens-Request': '1'})
        self.assertEqual(status, 429)
        self.assertNotIn(PASSWORD.encode(), body)

    def test_grafana_admin_or_editor_identity_is_refused_before_dashboard_access(self):
        self.login()
        self.viewer_role = 'Editor'
        self.assertEqual(self.request('/grafana/d/example')[0], 503)
        self.viewer_role = 'Viewer'
        self.server_admin = True
        self.assertEqual(self.request('/api/monitoring-status')[0], 503)

    def test_basic_api_compatibility_does_not_unlock_grafana(self):
        auth = 'Basic ' + base64.b64encode(('admin:' + PASSWORD).encode()).decode()
        self.assertEqual(self.request('/api/diagnostics', headers={'Authorization': auth})[0], 200)
        self.assertEqual(self.request('/grafana/api/user', headers={'Authorization': auth})[0], 401)

    def test_datasource_variable_post_is_read_only_and_endpoint_scoped(self):
        self.login()
        origin = 'http://127.0.0.1:' + str(self.gateway.server_port)
        path = '/grafana/api/datasources/uid/example/resources/api/v1/query'
        self.assertEqual(self.request(path, 'POST', 'query=up', {'Origin': origin})[0], 200)
        self.assertEqual(self.requests[-1][3], b'query=up')
        self.assertEqual(self.request('/grafana/api/datasources/uid/example/resources/api/v1/admin/tsdb/delete_series', 'POST', '{}', {'Origin': origin})[0], 403)

    def test_setup_bootstrap_preserves_other_acl_entries_and_revokes_its_session(self):
        from test_portable_setup import setup
        existing = [{'userId': 42, 'permission': 2, 'isInherited': False}, {'role': 'Editor', 'permission': 2, 'isInherited': True}]
        writes = []
        def admin(root, config, path, method='GET', body=None):
            if path == '/api/org/users':
                return [{'userId': 5, 'login': portal.VIEWER, 'role': 'Viewer'}]
            if method == 'POST':
                writes.append(body)
                return {'message': 'Updated'}
            return existing
        self.portal.secure_cookie = True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'secrets').mkdir()
            (root / 'secrets/usage_admin_password').write_text(PASSWORD)
            config = {'portal_enabled': True, 'usage_bind': '127.0.0.1', 'usage_port': self.gateway.server_port}
            with patch.object(setup, 'grafana_request', side_effect=admin):
                setup.connect_portal(root, config)
            self.assertEqual(writes, [{'items': [{'userId': 42, 'permission': 2}, {'userId': 5, 'permission': 1}]}])
            saved = next((root / 'grafana').glob('portal-permissions-before-*.json'))
            self.assertEqual(json.loads(saved.read_text()), existing)
            self.assertEqual(self.portal.sessions, {})
            self.assertEqual(self.portal.grafana_sessions, {})

    def test_generated_portal_uses_loopback_trust_and_preserves_worker_volume(self):
        from test_portable_setup import setup
        with tempfile.TemporaryDirectory() as root:
            output = Path(root, 'site')
            config = setup.validate_config({'fortigate_host': '192.0.2.1', 'accounting_enabled': True, 'portal_enabled': True})
            setup.render(config, output, 'synthetic-token')
            compose = setup.load(output / 'compose.yaml')
            services = compose['services']
            self.assertEqual(services['fortilens-portal']['network_mode'], 'service:grafana')
            self.assertEqual(services['grafana']['environment']['GF_AUTH_PROXY_WHITELIST'], '127.0.0.1')
            self.assertEqual(services['grafana']['environment']['GF_AUTH_PROXY_ENABLE_LOGIN_TOKEN'], 'true')
            self.assertNotIn('ports', services['fortilens-usage'])
            self.assertEqual(services['fortilens-usage']['volumes'][0], 'usage-data:/data')
            self.assertIn('127.0.0.1:3002:8081', services['grafana']['ports'])
            self.assertEqual(services['fortilens-portal']['secrets'], ['usage_admin_password'])
            self.assertTrue((output / 'usage/portal.py').is_file())


if __name__ == '__main__':
    unittest.main()
