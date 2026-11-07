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
print(json.dumps({section: namespace['hosted_report'](page, section) for section in ['devices', 'vlans', 'reports', 'applications', 'websites', 'live', 'wan', 'quality']}))
`], {cwd:root, encoding:'utf8', maxBuffer:4*1024*1024}));

const settings = {
  site_name:'Synthetic lab · demo data', installation_name:'synthetic-lab',
  timezone:'UTC', date_format:'YYYY-MM-DD', fortigate_host:'192.0.2.1',
  api_port:443, vdom:'root', sla:'synthetic-sla', api_tls_verify:true,
  wan_interfaces:['uplink-a','uplink-b'], wan_confirmed:true,
  syslog_protocol:'udp', syslog_port:1514, syslog_format:'fortigate', syslog_network:'bridge',
  accounting_timezone:'UTC', expected_devid:'SYNTHETIC-FIXTURE',
  grafana_port:3001, usage_port:3002, monitoring_interval:60,
  monitoring:{wan:true,sla:true,interfaces:true,syslog:true,accounting:true,vlans:true,server:true,websites:true},
  retention:{loki_days:60,prometheus_days:90,ledger_days:8,summary_days:400,database_bytes:9*GiB},
};
const vlans = [
  {key:'vlan:100:staff',status:'MAPPED',vlan_id:100,interface:'staff'},
  {key:'vlan:200:guests',status:'MAPPED',vlan_id:200,interface:'guests'},
  {key:'unknown:lab',status:'UNKNOWN',vlan_id:null,interface:'lab'},
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
// Synthetic Application Control labels; names are generic examples, not observed traffic.
const appDefinitions = [
  ['QUIC','Network.Service','low',true,6,9.80], ['Microsoft Teams','Collaboration','elevated',false,5,7.42],
  ['Video streaming','Video/Audio','elevated',false,4,6.15], ['Software Update','Update','low',false,7,4.88],
  ['Cloud Storage','Storage.Backup','medium',false,3,3.92], ['SSL_TLSv1.3','Network.Service','medium',true,8,3.10],
  ['Web Mail','Email','medium',false,6,1.64], ['AI Assistant','GenAI','medium',false,3,0.86],
  ['Code Hosting','Collaboration','low',false,2,0.71], ['Remote Desktop','Remote.Access','high',false,1,0.38],
  ['','unscanned','',false,4,0.52],
];
function applications(url) {
  const rows=appDefinitions.map(([app,category,risk,generic,count,gb])=>{const total=Math.round(gb*1e9),upload=Math.round(total*.12);
    return {app:app||null,category,risk:risk||null,generic,devices:count,download_bytes:total-upload,upload_bytes:upload,
      pending_upload_bytes:0,pending_download_bytes:0,records:count*900,total_bytes:total}});
  const total=sum(rows,'total_bytes'),generic=sum(rows.filter(x=>x.generic),'total_bytes'),unidentified=sum(rows.filter(x=>!x.app),'total_bytes');
  const categories=[...new Set(rows.map(x=>x.category))].map(category=>{const group=rows.filter(x=>x.category===category);
    return {category,apps:group.length,devices:Math.max(...group.map(x=>x.devices)),download_bytes:sum(group,'download_bytes'),upload_bytes:sum(group,'upload_bytes'),total_bytes:sum(group,'total_bytes')}}).sort((a,b)=>b.total_bytes-a.total_bytes);
  const genai=rows.find(x=>x.category==='GenAI');
  return {start_utc:'2026-11-05T00:00:00+00:00',end_utc:'2026-11-05T12:00:00+00:00',boundary:'[start,end)',timezone:'UTC',
    applications:rows,categories,devices:[],totals:{upload_bytes:sum(rows,'upload_bytes'),download_bytes:sum(rows,'download_bytes'),pending_upload_bytes:0,pending_download_bytes:0},
    summary:{total_bytes:total,applications:rows.length,top_application:'Microsoft Teams',identified_bytes:total-generic-unidentified,unidentified_bytes:unidentified,generic_bytes:generic,genai_bytes:genai.total_bytes,genai_devices:genai.devices},
    unidentified_bytes:unidentified,untracked:null,tracking_started_ns:(seconds-30*day)*1e9,applications_since:'2026-10-06',
    traffic_coverage:'UNKNOWN',status:'RECORDED_ONLY',period:url.searchParams.get('period')||'today',processing:report(url).processing};
}
// Synthetic FortiView-style live rates in bits per second; generic example names only.
function live() {
  const doing=[['Microsoft Teams','Web Mail'],['Software Update'],['Video streaming','Cloud Storage'],['Video streaming'],['AI Assistant','Code Hosting'],['Remote Desktop'],['Web Mail'],['Video streaming']];
  const rows=devices.map((d,i)=>({device_id:d.device_id,hostname:d.hostname,ip:d.ip,mac:d.mac,profile:i%3?{vendor:'Example',os:'Windows',type:'Computer'}:{os:'Android',type:'Mobile'},
    vlan:d.vlans[0],download_bps:Math.round([18.4,9.6,7.2,4.1,2.6,1.2,0.6,0.3][i]*1e6),upload_bps:Math.round([2.1,6.8,0.9,0.4,0.5,0.9,0.1,0.05][i]*1e6),sessions:[42,18,25,12,9,6,4,3][i],top_apps:doing[i]}));
  const download=sum(rows,'download_bps'),upload=sum(rows,'upload_bps');
  const apps=[['Video streaming','Video/Audio',14.1,0.9],['Microsoft Teams','Collaboration',6.2,1.8],['Software Update','Update',5.9,3.1],['Cloud Storage','Storage.Backup',3.4,4.2],['AI Assistant','GenAI',1.9,0.4],['Web Mail','Email',1.1,0.3]]
    .map(([app,category,d,u],i)=>({appid:String(41000+i),app,category,risk:'medium',download_bps:Math.round(d*1e6),upload_bps:Math.round(u*1e6),sessions:10+i}));
  const segmentRows=vlans.map(vlan=>{const r=rows.filter(d=>d.vlan.key===vlan.key);return {vlan,download_bps:sum(r,'download_bps'),upload_bps:sum(r,'upload_bps'),devices:r.length}}).filter(x=>x.devices);
  return {observed_at:seconds,source:'FORTIVIEW_REALTIME',unit:'bits_per_second',totals_source:'INTERFACE_COUNTERS',
    totals:{download_bps:Math.round(download*1.08),upload_bps:Math.round(upload*1.12),sessions:sum(rows,'sessions'),devices:rows.length},
    listed:{download_bps:download,upload_bps:upload},listed_sources:rows.length,truncated:false,
    links:[{interface:'uplink-a',download_bps:Math.round(download*0.78),upload_bps:Math.round(upload*0.8),measured_seconds:5},{interface:'uplink-b',download_bps:Math.round(download*0.3),upload_bps:Math.round(upload*0.32),measured_seconds:5}],
    devices:rows,vlans:segmentRows,applications:apps,wan_interfaces:['uplink-a','uplink-b'],
    note:'Synthetic live estimates.'};
}
// Synthetic website rows: generic example names only, one site selected.
function websites(url) {
  const sites=[['video.example',9.6,0.42,6],['cloud-storage.example',3.1,2.6,4],['office.example',4.2,1.1,8],['news.example',1.2,0.08,7],['ai-assistant.example',0.9,0.21,3],['software-updates.example',0.8,0.02,8],['maps.example',0.31,0.04,5],['mail.example',0.22,0.09,8]]
    .map(([site,d,u,n])=>({site,download_bytes:Math.round(d*1e9),upload_bytes:Math.round(u*1e9),total_bytes:Math.round((d+u)*1e9),records:200,devices:n}));
  const selected=url.searchParams.get('site')||'video.example',total=sum(sites,'total_bytes');
  const share=[0.46,0.21,0.14,0.09,0.06,0.04];
  const rows=selected?devices.slice(0,6).map((d,i)=>({device_id:d.device_id,hostname:d.hostname,ip:d.ip,mac:d.mac,identity_kind:'MAC',vlans:d.vlans,profile:null,
    download_bytes:Math.round(9.6e9*share[i]),upload_bytes:Math.round(0.42e9*share[i]),total_bytes:Math.round(10.02e9*share[i]),records:40})):[];
  return {start_utc:new Date((seconds-6*day)*1000).toISOString(),end_utc:new Date(now).toISOString(),first_day:'2026-10-30',last_day:'2026-11-05',timezone:'UTC',
    enabled:true,since_ns:(seconds-40*day)*1e9,retention_days:90,site:selected,sites,devices:rows,truncated:false,
    summary:{total_bytes:total,sites:sites.length,top_site:sites[0].site,internet_bytes:Math.round(total/0.82),coverage:0.82},
    note:'Synthetic website rows.',period:url.searchParams.get('period')||'week'};
}
const inventory = () => ({schema_version:1,collected_at:new Date(now-600000).toISOString(),interfaces:[
  ...vlans.filter(v=>v.vlan_id).map(v=>({name:v.interface,vdom:'root',type:'vlan',vlan_id:v.vlan_id,parent:'internal',role:'lan'})),
  {name:'printers',vdom:'root',type:'vlan',vlan_id:300,parent:'internal',role:'lan'},
  {name:'uplink-a',vdom:'root',type:'physical',vlan_id:null,parent:null,role:'wan'}]});
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
  {id:'accounting_metadata',status:'WARN',message:'Some retained records have unknown segment mappings.',action:'Inspect Data quality; do not infer historical VLANs from the current IP subnet.'},
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
    if(['devices','vlans','reports','applications','websites','live','wan','quality'].includes(initial)) {
      const target={devices:'/console/?section=devices#overview',vlans:'/console/?section=vlans#overview',reports:'/console/?section=reports#reports',applications:'/console/?section=applications#applications',websites:'/console/?section=websites#websites',live:'/console/?section=live#live',wan:'/console/#wan',quality:'/console/#quality'}[initial];
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
      else if(url.pathname==='/console/api/applications') payload=applications(url);
      else if(url.pathname==='/console/api/inventory') payload=inventory();
      else if(url.pathname==='/console/api/live') payload=live();
      else if(url.pathname==='/console/api/websites') payload=websites(url);
      else {unhandled.push(url.pathname);status=404;payload={error:'not_found'};}
      await route.fulfill({status,contentType:'application/json',body:JSON.stringify(payload)});
    });
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(baseURL+'/?view='+pageName+'#'+pageName);
    if(auth==='signed-in') {
      await page.waitForFunction(()=>!document.getElementById('workspace').hidden);
      if(pageName==='home') await page.waitForFunction(()=>!document.getElementById('home-refresh').disabled);
      else if(['devices','reports','applications','websites','live','quality','wan'].includes(pageName)) {
        const frame=page.frameLocator('#console-frame');
        await frame.locator('#application').waitFor({state:'visible'});
        if(pageName==='wan') await frame.locator('#wan-body tr').first().waitFor();
        else if(pageName==='applications') await frame.locator('#apps-body tr').first().waitFor();
        else if(pageName==='live') await frame.locator('#live-devices tr').first().waitFor();
        else if(pageName==='websites') {await frame.locator('#sites-body .entity').first().click();await frame.locator('#sites-devices tr').first().waitFor();}
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
      {name:'18-websites',pageName:'websites'},
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
