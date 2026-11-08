#!/usr/bin/env node
'use strict';

// Builds the read-only online demo: the shipped console UI, unchanged except
// for relative paths, answered in the browser by demo/fixtures.js.
// Usage: node tools/build_demo.cjs [output directory, default site]
// Every rewrite must match; a UI change that breaks one fails the build.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');

const root = path.resolve(__dirname, '..');
const out = path.resolve(root, process.argv[2] || 'site');
assert.ok(out.startsWith(root + path.sep), 'Output must stay inside the checkout');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const write = (name, text) => {const file = path.join(out, name); fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, text);};
const copy = (from, to) => {const file = path.join(out, to); fs.mkdirSync(path.dirname(file), {recursive: true}); fs.copyFileSync(path.join(root, from), file);};

function rewrite(text, label, pairs) {
  for (const [from, to] of pairs) {
    const count = text.split(from).length - 1;
    assert.ok(count > 0, `${label}: expected "${from}"`);
    text = text.split(from).join(to);
  }
  return text;
}

// The production hosted-report adapter, evaluated from management/app.py so the
// demo reports look exactly like the console's embedded reports.
const python = process.env.FORTILENS_PYTHON || 'python3';
const sections = ['wan', 'devices', 'vlans', 'reports', 'applications', 'websites', 'live'];
const hosted = JSON.parse(execFileSync(python, ['-c', `
import ast, json, sys
from pathlib import Path
root = Path.cwd()
source = root / 'management/app.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
names = {'HOSTED_REPORT_CSS', 'HOSTED_REPORT_STATUS', 'HOSTED_REPORT_SCRIPT'}
nodes = [n for n in tree.body if (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)) or (isinstance(n, ast.FunctionDef) and n.name == 'hosted_report')]
namespace = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
page = (root / 'portable/reporting.html').read_text(encoding='utf-8')
print(json.dumps({s: namespace['hosted_report'](page, s) for s in json.loads(sys.argv[1])}))
`, JSON.stringify(sections)], {cwd: root, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024}));

fs.rmSync(out, {recursive: true, force: true});
const demoScripts = prefix => `<script src="${prefix}demo/fixtures.js"></script><script src="${prefix}demo/demo.js"></script>`;
const description = '<meta name="description" content="FortiLens online demo: self-hosted FortiGate observability with synthetic data.">';

let index = read('management/static/index.html');
index = rewrite(index, 'index.html', [
  ['"/static/', '"static/'],
  ['<title>FortiLens · Management</title>', '<title>FortiLens · Online demo</title>' + description],
  ['<script src="static/app.js" defer>', demoScripts('') + '<script src="static/app.js" defer>'],
  ['referrerpolicy="same-origin"></iframe>', 'referrerpolicy="same-origin"></iframe><script>FortiLensDemoInitialView()</script>'],
]);
write('index.html', index);

write('static/app.js', rewrite(read('management/static/app.js'), 'app.js', [
  ["'/console/?section=devices#", "'console/devices.html#"],
  ["'/console/?section=vlans#", "'console/vlans.html#"],
  ["'/console/?section=reports#", "'console/reports.html#"],
  ["'/console/?section=applications#", "'console/applications.html#"],
  ["'/console/?section=websites#", "'console/websites.html#"],
  ["'/console/?section=live#", "'console/live.html#"],
  ["'/console/#", "'console/index.html#"],
  ["pathname.startsWith('/console/')", "pathname.includes('/console/')"],
  ["new URL('/',location.origin)", 'new URL(location.pathname,location.origin)'],
]));
for (const name of ['app.css', 'favicon.svg', 'favicon-32.png', 'apple-touch-icon.png']) copy('management/static/' + name, 'static/' + name);
for (const name of fs.readdirSync(path.join(root, 'management/static/fonts'))) copy('management/static/fonts/' + name, 'static/fonts/' + name);

for (const section of sections) {
  const page = rewrite(hosted[section], 'console ' + section, [
    ['"/static/fonts/', '"../static/fonts/'],
    ["location.pathname.startsWith('/console/')", "location.pathname.includes('/console/')"],
    ['<title>', demoScripts('../') + '<title>'],
  ]);
  write(`console/${section === 'wan' ? 'index' : section}.html`, page);
}

write('grafana.html', `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Firewall dashboard · FortiLens demo</title>
<link rel="stylesheet" href="static/app.css"><style>body{margin:0;padding:32px;max-width:720px;background:var(--bg);color:var(--ink)}p{line-height:1.55;color:var(--sub)}</style>
<script>try{const t=localStorage.getItem('fortilens.theme');if(t==='light'||t==='dark')document.documentElement.dataset.theme=t}catch{}</script></head>
<body><h1>Firewall dashboard</h1>
<p>In an installation, this page embeds the native Grafana dashboard that FortiLens provisions: WAN latency, jitter and packet loss, interface traffic, SD-WAN health and firewall logs from your FortiGate.</p>
<p>Grafana needs a running server, so it is not part of this online demo. See <a href="https://github.com/Rafattitas/fortilens#readme" target="_top" rel="noopener">the README</a> for screenshots and installation.</p>
</body></html>
`);
copy('demo/fixtures.js', 'demo/fixtures.js');
copy('demo/demo.js', 'demo/demo.js');
write('.nojekyll', '');
console.log(JSON.stringify({built: path.relative(root, out), pages: 2 + sections.length}));
