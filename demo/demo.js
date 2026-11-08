// Online demo only: answers the console's API calls in the browser from the
// synthetic fixtures. Nothing is sent anywhere and nothing can be changed.
// This file is never part of an installation.
(function () {
  'use strict';
  const fixtures = window.FortiLensFixtures.create(Date.now());
  const original = window.fetch.bind(window);
  const json = (status, payload) => new Response(JSON.stringify(payload), {status, headers: {'Content-Type': 'application/json'}});
  const readOnly = {
    error: 'demo_read_only',
    message: 'This online demo uses synthetic data and is read-only. Install FortiLens to connect your own FortiGate.',
  };

  window.fetch = function (input, init) {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin !== location.origin || !url.pathname.includes('/api/')) return original(input, init);
    const method = ((init && init.method) || (typeof input === 'string' ? 'GET' : input.method) || 'GET').toUpperCase();
    if (method !== 'GET') return Promise.resolve(json(403, readOnly));
    const answer = fixtures.respond(url, {dashboard: 'grafana.html', jitter: 0.85 + Math.random() * 0.3});
    return Promise.resolve(answer ? json(answer.status, answer.payload) : json(404, {error: 'not_found', message: 'This view is not part of the online demo.'}));
  };

  if (window.top !== window) return;
  // The management service names the first embedded view in its HTML; a static
  // site cannot, so the demo does the same right after the frame is parsed.
  const initialViews = {
    devices: 'console/devices.html#overview', vlans: 'console/vlans.html#overview', reports: 'console/reports.html#reports',
    applications: 'console/applications.html#applications', websites: 'console/websites.html#websites', live: 'console/live.html#live',
    wan: 'console/index.html#wan', quality: 'console/index.html#quality',
  };
  window.FortiLensDemoInitialView = function () {
    const view = new URL(location.href).searchParams.get('view');
    const frame = document.getElementById('console-frame');
    if (frame && Object.prototype.hasOwnProperty.call(initialViews, view)) {
      frame.dataset.initialView = view;
      frame.src = initialViews[view];
    }
  };
  // A small, dismissible label so nobody mistakes the demo for a live installation.
  const label = () => {
    const box = document.createElement('aside');
    box.id = 'demo-label';
    box.setAttribute('aria-label', 'About this demo');
    box.innerHTML = '<strong>Online demo</strong><span>Synthetic data. Read-only.</span>' +
      '<a href="https://github.com/Rafattitas/fortilens" rel="noopener">Get FortiLens</a>' +
      '<button type="button" aria-label="Hide demo label">×</button>';
    const style = document.createElement('style');
    style.textContent = '#demo-label{position:fixed;right:16px;bottom:40px;z-index:50;display:flex;gap:10px;align-items:center;' +
      'padding:8px 10px 8px 14px;border:1px solid var(--line,#c7cecd);border-radius:6px;background:var(--panel,#f3f5f4);' +
      'color:var(--ink,#14191b);font:13px/1.3 inherit;box-shadow:0 4px 16px rgba(0,0,0,.18)}' +
      '#demo-label span{color:var(--sub,#4a5559)}#demo-label a{color:var(--acc,#0c5a70);font-weight:600}' +
      '#demo-label button{border:0;background:none;color:var(--sub,#4a5559);font-size:18px;line-height:1;cursor:pointer;padding:0 4px}' +
      '@media (max-width:640px){#demo-label span{display:none}}';
    box.querySelector('button').addEventListener('click', () => box.remove());
    document.head.appendChild(style);
    document.body.appendChild(box);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', label); else label();
})();
