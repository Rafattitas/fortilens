#!/usr/bin/env node
'use strict';

// Opens every page of the built online demo under a project-site path prefix
// (like https://owner.github.io/fortilens/) and fails on browser errors,
// missing files, external requests or empty pages.
// Usage: node tools/check_demo.cjs [built directory, default site]
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.FORTILENS_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const site = path.resolve(root, process.argv[2] || 'site');
const shots = path.join(root, 'qa-private', 'demo');
const prefix = '/fortilens/';
const types = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2'};
const missing = [];
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  let relative = url.pathname.startsWith(prefix) ? decodeURIComponent(url.pathname.slice(prefix.length)) : null;
  if (relative !== null && (relative === '' || relative.endsWith('/'))) relative += 'index.html';
  const file = relative === null ? null : path.join(site, relative);
  if (!file || !file.startsWith(site + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    missing.push(url.pathname); res.writeHead(404); res.end('Not found'); return;
  }
  res.writeHead(200, {'Content-Type': types[path.extname(file)] || 'application/octet-stream'});
  res.end(fs.readFileSync(file));
});

const pages = [
  ['home', null], ['live', '#live-devices tr'], ['wan', '#wan-body tr'], ['monitoring', null],
  ['devices', '#device-body tr'], ['vlans', '#vlan-body tr'], ['applications', '#apps-body tr'], ['reports', '#refresh'],
  ['overview', null], ['quality', '#refresh'], ['diagnostics', null], ['settings', null],
];

(async () => {
  fs.mkdirSync(shots, {recursive: true});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}${prefix}`;
  const browser = await chromium.launch({headless: true, ...(process.env.FORTILENS_BROWSER_EXECUTABLE ? {executablePath: process.env.FORTILENS_BROWSER_EXECUTABLE} : {})});
  const results = [];
  try {
    for (const theme of ['dark', 'light']) {
      const context = await browser.newContext({viewport: {width: 1440, height: 900}, colorScheme: theme});
      const errors = [], external = [];
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.host !== new URL(base).host) {external.push(url.href); return route.abort();}
        return route.continue();
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => {if (message.type() === 'error' && !/404|Failed to load resource/.test(message.text())) errors.push(message.text());});
      await page.goto(base + '#home');
      await page.waitForFunction(() => !document.getElementById('workspace').hidden);
      for (const [name, rows] of pages) {
        await page.evaluate(target => {location.hash = target;}, name);
        await page.waitForFunction(target => document.querySelector(`[data-page="${target}"]`)?.classList.contains('active'), name);
        if (rows) {
          const frame = page.frameLocator('#console-frame');
          await frame.locator('#application').waitFor({state: 'visible'});
          await frame.locator(rows).first().waitFor({state: 'attached'});
          await page.waitForFunction(() => document.getElementById('console-loading').hidden);
        } else if (name === 'home') await page.waitForFunction(() => !document.getElementById('home-refresh').disabled);
        else if (name === 'diagnostics') await page.locator('#diagnostic-checks .diagnostic').first().waitFor();
        await page.waitForTimeout(150);
        if (theme === 'dark') await page.screenshot({path: path.join(shots, name + '.png')});
        results.push(theme + ':' + name);
      }
      // Writes are refused with an explanation instead of failing silently.
      const refused = await page.evaluate(async () => (await fetch('api/configuration/prepare', {method: 'POST', body: '{}'})).json());
      assert.equal(refused.error, 'demo_read_only');
      assert.deepEqual(errors, [], 'Browser errors (' + theme + ')');
      assert.deepEqual(external, [], 'External requests (' + theme + ')');
      await context.close();
    }
    assert.deepEqual(missing, [], 'Missing demo files');
    console.log(JSON.stringify({passed: true, pages: results.length, browser: await browser.version()}));
  } finally {
    await browser.close();
    server.close();
  }
})().catch(error => {console.error(error.stack || error); process.exitCode = 1;});
