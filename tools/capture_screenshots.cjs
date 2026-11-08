#!/usr/bin/env node
'use strict';

// Documentation-only rendering of the shipped UI. No company data, credentials,
// live APIs, Docker, generated installation or external network is used.
// Install development Playwright, or select it with FORTILENS_PLAYWRIGHT_MODULE.
// FORTILENS_BROWSER_EXECUTABLE and FORTILENS_PYTHON override local executables.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.FORTILENS_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const now = Date.UTC(2026, 10, 5, 12, 0, 0);
const privateDir = path.join(root, 'qa-private', 'presentation');
const designDir = path.join(root, 'docs', 'design', 'screenshots');
const args=process.argv.slice(2);
if(args.includes('--help')) {
  console.log('Usage: node tools/capture_screenshots.cjs [--manifest repository/relative/path.json]');
  process.exit(0);
}
assert.ok(args.length===0||(args.length===2&&args[0]==='--manifest'),'Unsupported capture arguments');
const manifestPath=path.resolve(root,args[1]||'docs/design/screenshots/manifest.json');
assert.ok(manifestPath.startsWith(root+path.sep),'Manifest must stay inside the checkout');
for (const directory of [privateDir, designDir, path.dirname(manifestPath)]) fs.mkdirSync(directory, {recursive:true});

// Reuse the production hosted-document adapter instead of approximating its CSS.
// AST extraction only evaluates its literal constants and pure transform function;
// it does not import FastAPI, start the management service or read configuration.
const python = process.env.FORTILENS_PYTHON || (fs.existsSync(path.join(root, '.venv', 'Scripts', 'python.exe')) ? path.join(root, '.venv', 'Scripts', 'python.exe') : 'python');
const hostedPages = JSON.parse(execFileSync(python, ['-c', `
import ast, json
from pathlib import Path
root = Path.cwd()
source = root / 'management/app.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
names = {'HOSTED_REPORT_CSS', 'HOSTED_REPORT_STATUS', 'HOSTED_REPORT_SCRIPT'}
nodes = [n for n in tree.body if (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)) or (isinstance(n, ast.FunctionDef) and n.name == 'hosted_report')]
namespace = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
page = (root / 'portable/reporting.html').read_text(encoding='utf-8')
print(json.dumps({section: namespace['hosted_report'](page, section) for section in ['devices', 'vlans', 'reports', 'applications', 'live', 'wan', 'quality']}))
`], {cwd:root, encoding:'utf8', maxBuffer:4*1024*1024}));

const fixtures=require('../demo/fixtures.js').create(now);

let baseURL;
const unhandled=[];
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://127.0.0.1');
  let file,body,contentType='text/html; charset=utf-8';
  if(url.pathname==='/') {
    body=fs.readFileSync(path.join(root,'management/static/index.html'),'utf8');
    const initial=url.searchParams.get('view');
    if(['devices','vlans','reports','applications','live','wan','quality'].includes(initial)) {
      const target={devices:'/console/?section=devices#overview',vlans:'/console/?section=vlans#overview',reports:'/console/?section=reports#reports',applications:'/console/?section=applications#applications',live:'/console/?section=live#live',wan:'/console/#wan',quality:'/console/#quality'}[initial];
      body=body.replace('id="console-frame"','id="console-frame" data-initial-view="'+initial+'" src="'+target+'"');
    }
  } else if(url.pathname==='/console/') body=hostedPages[url.searchParams.get('section')||'wan'];
  else if(url.pathname==='/qa/contact-sheet') body='<!doctype html><meta charset="utf-8"><title>Synthetic UI captures</title><style>body{margin:16px;background:#dedfe0;color:#222;font:14px sans-serif}main{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}figure{margin:0;background:#fff;padding:8px}img{width:100%;height:220px;object-fit:contain;background:#ccc}figcaption{padding:6px 0}</style><h1>Shipped FortiLens UI · synthetic fixtures only</h1><main>'+screenshots.map(item=>'<figure><img src="/screenshots/'+path.basename(item.file)+'"><figcaption>'+path.basename(item.file)+'</figcaption></figure>').join('')+'</main>';
  else if(/^\/screenshots\/[a-z0-9-]+\.png$/.test(url.pathname)) {file=path.join(designDir,path.basename(url.pathname));contentType='image/png';}
  else if(url.pathname.startsWith('/static/')) {
    const relative=url.pathname.slice('/static/'.length);
    const allowed=['app.js','app.css','logo.svg',...fs.readdirSync(path.join(root,'management/static/fonts')).filter(name=>name.endsWith('.woff2')).map(name=>'fonts/'+name)];
    if(allowed.includes(relative)) file=path.join(root,'management/static',relative);
  } else if(url.pathname==='/favicon.ico') {res.writeHead(204);res.end();return;}
  if(file) {
    contentType=file.endsWith('.png')?'image/png':file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'image/svg+xml';
    body=fs.readFileSync(file);
  }
  if(body===undefined) {unhandled.push(req.url);res.writeHead(404);res.end('Synthetic fixture route not found.');return;}
  res.writeHead(200,{'Content-Type':contentType,'Cache-Control':'no-store'});res.end(body);
});
const screenshots=[];
async function capture(browser,{name,pageName='home',light=false,degraded=false,mobile=false,auth='signed-in',inspector=false}) {
  const errors=[],external=[];
  const context=await browser.newContext({viewport:mobile?{width:375,height:900}:{width:1600,height:1050},colorScheme:light?'light':'dark',reducedMotion:'reduce',locale:'en-GB',timezoneId:'UTC',serviceWorkers:'block'});
  try {
    await context.addInitScript(({now,light})=>{
      const OriginalDate=Date;
      window.Date=class extends OriginalDate {constructor(...args){super(...(args.length?args:[now]))}static now(){return now}};
      localStorage.setItem('fortilens.theme',light?'light':'dark');
    },{now,light});
    await context.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin!==baseURL) {external.push(url.origin+url.pathname);await route.abort();return;}
      if(!url.pathname.includes('/api/')) {await route.continue();return;}
      assert.equal(route.request().method(),'GET','Screenshot fixtures never perform writes');
      let payload,status=200;
      const answer=url.pathname.startsWith('/api/')||url.pathname.startsWith('/console/api/')?fixtures.respond(url,{auth,degraded}):null;
      if(answer) ({status,payload}=answer);
      else {unhandled.push(url.pathname);status=404;payload={error:'not_found'};}
      await route.fulfill({status,contentType:'application/json',body:JSON.stringify(payload)});
    });
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(baseURL+'/?view='+pageName+'#'+pageName);
    if(auth==='signed-in') {
      await page.waitForFunction(()=>!document.getElementById('workspace').hidden);
      if(pageName==='home') await page.waitForFunction(()=>!document.getElementById('home-refresh').disabled);
      else if(['devices','reports','applications','live','quality','wan'].includes(pageName)) {
        const frame=page.frameLocator('#console-frame');
        await frame.locator('#application').waitFor({state:'visible'});
        if(pageName==='wan') await frame.locator('#wan-body tr').first().waitFor();
        else if(pageName==='applications') await frame.locator('#apps-body tr').first().waitFor();
        else if(pageName==='live') await frame.locator('#live-devices tr').first().waitFor();
        else await frame.locator('#refresh:not([disabled])').waitFor({state:'attached'});
        if(inspector) {await frame.locator('#device-body .entity').first().click();await frame.locator('#details-title').filter({hasText:'Design workstation'}).waitFor();}
        if(mobile&&pageName==='devices') await frame.locator('#device-body tr').first().scrollIntoViewIfNeeded();
        await page.waitForFunction(()=>document.getElementById('console-loading').hidden&&!document.getElementById('console-frame').hasAttribute('aria-busy'));
      } else if(pageName==='diagnostics') await page.locator('#diagnostic-checks .diagnostic').first().waitFor();
    } else {
      await page.locator('#auth-submit').filter({hasText:auth==='first-run'?'Create administrator':'Sign in'}).waitFor();
      await page.locator('#username').fill('synthetic-admin');
    }
    for(const frame of page.frames()) await frame.evaluate(()=>document.fonts.ready);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.deepEqual(errors,[],'Browser errors in '+name);
    assert.deepEqual(external,[],'External request attempted by '+name);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Horizontal overflow in '+name);
    const destination=path.join(designDir,name+'.png');
    await page.screenshot({path:destination,animations:'disabled',fullPage:false});
    screenshots.push({file:path.relative(root,destination).split(path.sep).join('/'),sha256:createHash('sha256').update(fs.readFileSync(destination)).digest('hex'),viewport:page.viewportSize(),page:pageName,theme:light?'light':'dark',auth,inspector,degraded,synthetic:true,browserErrors:errors,externalRequests:external});
  } finally {await context.close();}
}

(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  baseURL='http://127.0.0.1:'+server.address().port;
  let browser;
  try {
    browser=await chromium.launch({headless:true,...(process.env.FORTILENS_BROWSER_EXECUTABLE?{executablePath:process.env.FORTILENS_BROWSER_EXECUTABLE}:{})});
    for(const options of [
      {name:'01-overview-dark'}, {name:'02-overview-light',light:true},
      {name:'03-overview-degraded',degraded:true},
      {name:'04-usage-docked',pageName:'devices',inspector:true},
      {name:'05-reports',pageName:'reports'}, {name:'06-evidence',pageName:'quality'},
      {name:'07-internet',pageName:'wan'}, {name:'08-system',pageName:'overview'},
      {name:'09-diagnostics',pageName:'diagnostics'}, {name:'10-settings',pageName:'settings'},
      {name:'11-first-run',auth:'first-run'}, {name:'12-sign-in',auth:'signed-out'},
      {name:'14-overview-narrow',mobile:true}, {name:'15-usage-narrow',pageName:'devices',mobile:true},
      {name:'16-applications',pageName:'applications'},
      {name:'17-live',pageName:'live'},
    ]) await capture(browser,options);
    const sheet=await browser.newPage({viewport:{width:1600,height:1200}});
    try {await sheet.goto(baseURL+'/qa/contact-sheet');await sheet.evaluate(()=>Promise.all([...document.images].map(img=>img.decode())));await sheet.screenshot({path:path.join(privateDir,'contact-sheet.png'),fullPage:true});} finally {await sheet.close();}
    assert.deepEqual(unhandled,[],'Unimplemented synthetic fixture routes');
    const sources=['tools/capture_screenshots.cjs','demo/fixtures.js','management/app.py','management/static/index.html','management/static/app.js','management/static/app.css','portable/reporting.html',...fs.readdirSync(path.join(root,'management/static/fonts')).filter(name=>name.endsWith('.woff2')).map(name=>'management/static/fonts/'+name)];
    const source_sha256=Object.fromEntries(sources.map(file=>[file,createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')]));
    const result={fixture:'fortilens-synthetic-lab-v1',synthetic:true,fixture_time:new Date(now).toISOString(),browser:await browser.version(),source_sha256,screenshots,unhandled};
    fs.writeFileSync(manifestPath,JSON.stringify(result,null,2)+'\n');
    fs.writeFileSync(path.join(privateDir,'capture-results.json'),JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify({passed:true,browser:result.browser,captures:screenshots.length,synthetic:true,errors:0}));
  } finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error.stack);server.close();process.exitCode=1;});
