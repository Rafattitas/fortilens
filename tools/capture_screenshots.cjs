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
const seconds = now / 1000;
const day = 86400;
const GiB = 2 ** 30;
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
print(json.dumps({section: namespace['hosted_report'](page, section) for section in ['devices', 'vlans', 'reports', 'wan', 'quality']}))
`], {cwd:root, encoding:'utf8', maxBuffer:4*1024*1024}));

const settings = {
  site_name:'Synthetic lab · demo data', installation_name:'synthetic-lab',
  timezone:'UTC', date_format:'YYYY-MM-DD', fortigate_host:'192.0.2.1',
  api_port:443, vdom:'root', sla:'synthetic-sla', api_tls_verify:true,
  wan_interfaces:['uplink-a','uplink-b'], wan_confirmed:true,
  syslog_protocol:'udp', syslog_port:1514, syslog_format:'fortigate', syslog_network:'bridge',
  accounting_timezone:'UTC', expected_devid:'SYNTHETIC-FIXTURE',
  grafana_port:3001, usage_port:3002, monitoring_interval:60,
  monitoring:{wan:true,sla:true,interfaces:true,syslog:true,accounting:true,vlans:true,server:true},
  retention:{loki_days:60,prometheus_days:90,ledger_days:8,summary_days:400,database_bytes:9*GiB},
};
const vlans = [
  {key:'root:staff',status:'MAPPED',vlan_id:100,interface:'staff'},
  {key:'root:guests',status:'MAPPED',vlan_id:200,interface:'guests'},
  {key:'root:lab',status:'UNKNOWN',vlan_id:null,interface:'lab'},
];
const definitions = [
  ['Design workstation',31.20,2.86,0], ['Build server',22.40,7.20,0],
  ['Research laptop',18.10,1.24,0], ['Guest notebook',8.92,.74,1],
  ['Office tablet',5.81,.34,0], ['Lab endpoint',3.21,.17,2],
  ['Meeting display',1.67,.08,0], ['Guest phone',.91,.06,1],
];
const devices = definitions.map(([hostname,down,up,segment],i) => {
  const download_bytes=Math.round(down*1e9), upload_bytes=Math.round(up*1e9);
  return {device_id:'synthetic-'+(i+1), hostname, ip:'192.0.2.'+(i+10),
    mac:i===5?'':'02:00:00:00:00:'+String(i+1).padStart(2,'0'),
    identity_kind:i===5?'IP':'MAC',vdom:'root',source_interfaces:[vlans[segment].interface],
    vlans:[vlans[segment]],download_bytes,upload_bytes,total_bytes:download_bytes+upload_bytes};
});
const sum = (rows,key) => rows.reduce((n,row)=>n+row[key],0);
const history = (rows,count,monthly=false) => Array.from({length:count},(_,i)=>({
  period:monthly?'2026-'+String(i+6).padStart(2,'0'):'2026-11-'+String(i+1).padStart(2,'0'),
  upload_bytes:Math.round(sum(rows,'upload_bytes')*(monthly?[3,3.6,2.7,4,3.8,4][i]:.6+i*.1)),
  download_bytes:Math.round(sum(rows,'download_bytes')*(monthly?[3,3.6,2.7,4,3.8,4][i]:.6+i*.1)),
  pending_upload_bytes:0,pending_download_bytes:0,
}));
function report(url,device=false,degraded=false) {
  const selected=device?devices.filter(d=>d.device_id===url.searchParams.get('device')):devices;
  const rows=selected.length?selected:devices.slice(0,1);
  const segmentRows=vlans.flatMap(vlan=>{
    const endpoints=rows.filter(d=>d.vlans[0].key===vlan.key);
    return endpoints.length?[{vlan,observed_endpoint_identities:endpoints.length,
      download_bytes:sum(endpoints,'download_bytes'),upload_bytes:sum(endpoints,'upload_bytes'),
      total_bytes:sum(endpoints,'total_bytes')}]:[];
  });
  return {devices:rows,vlans:segmentRows,
    totals:{upload_bytes:sum(rows,'upload_bytes'),download_bytes:sum(rows,'download_bytes'),pending_upload_bytes:1400000,pending_download_bytes:7200000},
    flags:{unknown_vlan:14,counter_boundary_uncertain:3},
    source_record_classifications:{INTERNET:48126,INTERNAL:1942,UNCLASSIFIED:14},
    processing:{last_poll_ok:true,last_delivery_ns:(seconds-(degraded?1200:12))*1e9,
      processing_lag_seconds:degraded?1208:4,last_failure_seconds:null,last_error:'',
      source_identity_bound:true,inventory_available:true,parser_rejections:2,late_rejections:0,
      replay_candidates:0,loki_timestamps_at_receipt:true},
    observed_endpoint_identities:rows.length,unverified_endpoint_identities:rows.filter(d=>d.identity_kind!=='MAC').length,
    status:'RECORDED_ONLY',timezone:'UTC',
    start_utc:url.searchParams.get('period')==='week'?'2026-10-29T12:00:00+00:00':'2026-11-05T00:00:00+00:00',
    end_utc:'2026-11-05T12:00:00+00:00',daily_history:history(rows,5),monthly_history:history(rows,6,true)};
}
function wan(degraded=false) {
  const segments = (second=false) => [
    {start:seconds-day,end:seconds-day*.79,state:'UP'},
    {start:seconds-day*.76,end:seconds-day*.46,state:'UP'},
    {start:seconds-day*.46,end:seconds-day*.43,state:'UP',degraded:true},
    {start:seconds-day*.43,end:seconds-1800,state:'UP'},
    {start:seconds-1800,end:seconds,state:degraded&&second?'DOWN':'UP'},
  ];
  return {observed_at:seconds,route:{status:'OBSERVED',destination:'198.51.100.1',interface:'uplink-a',scope:'Synthetic destination-specific route evidence.'},
    interfaces:['uplink-a','uplink-b'].map((name,i)=>({interface:name,
      internet_state:degraded&&i?'DOWN':'UP',physical_state:'UP',
      download_mbps:i?12.4:86.7,upload_mbps:i?1.2:8.3,
      latency_ms:degraded&&i?null:i?24.8:12.6,jitter_ms:i?2.8:1.4,packet_loss_pct:degraded&&i?100:0,
      history:{window_seconds:day,availability_percent:degraded&&i?97.9:100,valid_state_coverage_percent:97,
        segments:segments(!!i),outages:degraded&&i?[{start:seconds-1800,end:seconds,observed_seconds:1800,end_censored:true}]:[],
        degradation:[{start:seconds-day*.46,end:seconds-day*.43,observed_seconds:2592,end_censored:false}]}}))};
}
function system() {
  return {services:['fortigate-exporter','prometheus','grafana','loki','alloy','node-exporter','fortilens-usage','fortilens-portal','fortilens-syslog'].map(service=>({service,status:'running',health:service.startsWith('fortilens-')?'healthy':''})),
    disk:{total:64*GiB,used:18*GiB,available:46*GiB},storage_projection:{available:true,
      message:'Synthetic 24-hour growth trend. Compaction and future traffic can change actual use.',
      components:Object.fromEntries([['loki',480*2**20,160*2**20],['prometheus',58*2**20,9*2**20],['fortilens-usage',2.4*GiB,420*2**20]].map(([key,bytes,growth])=>[key,{bytes,forecast_status:'available',horizon_seconds:day,projection_bytes:growth,observation_seconds:7200,sample_count:9,projection_at_seconds:seconds,...(key==='fortilens-usage'?{capacity_limit_bytes:9*GiB,ledger_days:8,summary_days:400}:{})}]))},
    links:{console:true,dashboard:'/grafana/d/synthetic/'},latest_job:null,
    hostname:'synthetic-lab',os:'Ubuntu 24.04 LTS (synthetic fixture)',architecture:'x86_64',
    cpu_count:4,ram_bytes:8*GiB,docker:'Available',server_time_seconds:seconds,server_clock_supported:true};
}
function view() {
  return {settings,configured:true,revision:7,applied_revision:7,pending_changes:false,
    scope_locked:true,secret_status:{api_token_present:true,custom_ca_present:true},active_monitoring:settings.monitoring,
    active_sla:settings.sla,discovery:{required:true,current:true,reason:'Synthetic discovery matches this fixture.',changed_fields:[]}};
}
const diagnostics = () => ({system:system(),monitoring:{status:'Recorded evidence',checks:[
  {id:'services',status:'PASS',message:'All nine synthetic monitoring services are running.'},
  {id:'recent_logs',status:'PASS',message:'Recent delivery is observed in this synthetic fixture.'},
  {id:'accounting_poll',status:'PASS',message:'The accounting engine recently processed a poll.'},
  {id:'accounting_lag',status:'PASS',message:'Synthetic usage processing is near the present.'},
  {id:'accounting_metadata',status:'WARN',message:'Some retained records have unknown segment mappings.',action:'Inspect Evidence; do not infer historical VLANs from the current IP subnet.'},
  {id:'fortigate_tls',status:'PASS',message:'Certificate verification is enabled in this synthetic configuration.'},
  {id:'completeness',status:'UNKNOWN',message:'Received records do not certify complete traffic collection.',action:'Compare sender, listener and processing evidence before interpreting totals.'},
]},collection:{latest_log_at:new Date(now-12000).toISOString(),latest_metric_at:new Date(now-15000).toISOString(),processing_lag_seconds:4,log_timestamp_kind:'receipt time'}});

let baseURL;
const unhandled=[];
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://127.0.0.1');
  let file,body,contentType='text/html; charset=utf-8';
  if(url.pathname==='/') {
    body=fs.readFileSync(path.join(root,'management/static/index.html'),'utf8');
    const initial=url.searchParams.get('view');
    if(['devices','vlans','reports','wan','quality'].includes(initial)) {
      const target={devices:'/console/?section=devices#overview',vlans:'/console/?section=vlans#overview',reports:'/console/?section=reports#reports',wan:'/console/#wan',quality:'/console/#quality'}[initial];
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
      if(url.pathname==='/api/bootstrap/status') payload={required:auth==='first-run'};
      else if(url.pathname==='/api/session') {
        if(auth==='signed-in') payload={username:'synthetic-admin',csrf:'synthetic-csrf'};
        else {status=401;payload={error:'authentication_required',message:'Sign in to the synthetic lab.'};}
      } else if(url.pathname==='/api/settings') payload=view();
      else if(url.pathname==='/api/system') payload=system();
      else if(url.pathname==='/api/diagnostics') payload=diagnostics();
      else if(url.pathname==='/console/api/session') payload={authenticated:true};
      else if(url.pathname==='/console/api/portal') payload={enabled:true};
      else if(url.pathname==='/console/api/report') payload=report(url,false,degraded);
      else if(url.pathname==='/console/api/device') payload=report(url,true,degraded);
      else if(url.pathname==='/console/api/wan') payload=wan(degraded);
      else {unhandled.push(url.pathname);status=404;payload={error:'not_found'};}
      await route.fulfill({status,contentType:'application/json',body:JSON.stringify(payload)});
    });
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(baseURL+'/?view='+pageName+'#'+pageName);
    if(auth==='signed-in') {
      await page.waitForFunction(()=>!document.getElementById('workspace').hidden);
      if(pageName==='home') await page.waitForFunction(()=>!document.getElementById('home-refresh').disabled);
      else if(['devices','reports','quality','wan'].includes(pageName)) {
        const frame=page.frameLocator('#console-frame');
        await frame.locator('#application').waitFor({state:'visible'});
        if(pageName==='wan') await frame.locator('#wan-body tr').first().waitFor();
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
    ]) await capture(browser,options);
    const sheet=await browser.newPage({viewport:{width:1600,height:1200}});
    try {await sheet.goto(baseURL+'/qa/contact-sheet');await sheet.evaluate(()=>Promise.all([...document.images].map(img=>img.decode())));await sheet.screenshot({path:path.join(privateDir,'contact-sheet.png'),fullPage:true});} finally {await sheet.close();}
    assert.deepEqual(unhandled,[],'Unimplemented synthetic fixture routes');
    const sources=['tools/capture_screenshots.cjs','management/app.py','management/static/index.html','management/static/app.js','management/static/app.css','portable/reporting.html',...fs.readdirSync(path.join(root,'management/static/fonts')).filter(name=>name.endsWith('.woff2')).map(name=>'management/static/fonts/'+name)];
    const source_sha256=Object.fromEntries(sources.map(file=>[file,createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')]));
    const result={fixture:'fortilens-synthetic-lab-v1',synthetic:true,fixture_time:new Date(now).toISOString(),browser:await browser.version(),source_sha256,screenshots,unhandled};
    fs.writeFileSync(manifestPath,JSON.stringify(result,null,2)+'\n');
    fs.writeFileSync(path.join(privateDir,'capture-results.json'),JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify({passed:true,browser:result.browser,captures:screenshots.length,synthetic:true,errors:0}));
  } finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error.stack);server.close();process.exitCode=1;});
