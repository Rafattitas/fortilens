const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.FORTILENS_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const output = process.env.FORTILENS_BROWSER_OUTPUT;
const settings = {site_name:'Synthetic site',installation_name:'browser-fixture',timezone:'UTC',date_format:'YYYY-MM-DD',fortigate_host:'192.0.2.1',api_port:443,vdom:'root',sla:'',api_tls_verify:true,wan_interfaces:['wan1'],wan_confirmed:true,syslog_protocol:'tcp',syslog_port:1514,syslog_format:'rfc5424',accounting_timezone:'UTC',expected_devid:'',grafana_port:3001,usage_port:3002,monitoring_interval:60,monitoring:{wan:true,sla:true,interfaces:true,syslog:true,accounting:true,vlans:true,server:true},retention:{loki_days:60,prometheus_days:90,ledger_days:8,summary_days:400,database_bytes:4294967296}};
let mode='complete', discovery=true, applies=0, requests=0;
const events=[{at:1793606400,phase:'starting',message:'Starting synthetic services.'}];
const job = () => ({job_id:'a'.repeat(32),plan_id:'b'.repeat(32),status:mode==='unavailable'?'starting':mode,phase:mode==='failed'?'failed':mode==='complete'?'complete':'starting',message:mode==='failed'?'Synthetic readiness failed. Review service health.':'Synthetic configuration applied.',events:[...events,{at:1793606460,phase:mode,message:mode==='failed'?'Synthetic readiness failed.':'Synthetic operation '+mode+'.'}],requested_revision:7,...(mode==='complete'?{applied_revision:7}:{}),...(mode==='failed'?{failed_step:'readiness',rollback_available:true}:{}),diagnostics:{monitoring:{checks:[{status:'WARN',message:'Synthetic collection remains unverified.'}]}}});
const view = () => ({settings,configured:true,revision:7,applied_revision:mode==='complete'?7:6,pending_changes:mode!=='complete',scope_locked:true,secret_status:{api_token_present:true,custom_ca_present:false},active_monitoring:settings.monitoring,discovery:{required:true,current:discovery,reason:discovery?'Discovery matches this saved connection.':'Connection settings changed. Discover interfaces again.',changed_fields:discovery?[]:['api_tls_verify']}});
const system = () => ({services:[{service:'grafana',status:'running',health:'healthy'}],disk:{total:10000000000,used:1000000000,available:9000000000},storage_projection:{available:false,components:{},message:'Synthetic capacity unavailable.'},links:{console:false},latest_job:job(),hostname:'synthetic-host',os:'Synthetic OS',architecture:'x86_64',cpu_count:2,ram_bytes:4000000000,docker:'Available'});
const server=http.createServer((req,res)=>{
 const pathname=new URL(req.url,'http://localhost').pathname;
 const files={'/':'management/static/index.html','/static/app.js':'management/static/app.js','/static/app.css':'management/static/app.css','/static/logo.svg':'management/static/logo.svg'};
 const file=files[pathname];if(!file){res.writeHead(404);res.end('Not found');return}
 res.setHeader('Content-Type',pathname.endsWith('.js')?'application/javascript':pathname.endsWith('.css')?'text/css':pathname.endsWith('.svg')?'image/svg+xml':'text/html');res.end(fs.readFileSync(path.join(root,file)));
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,...(process.env.FORTILENS_BROWSER_EXECUTABLE?{executablePath:process.env.FORTILENS_BROWSER_EXECUTABLE}:{})});
 try{
 const context=await browser.newContext({viewport:{width:1280,height:900},reducedMotion:'reduce'});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const pathname=new URL(route.request().url()).pathname;let payload,status=200;
  if(pathname==='/api/bootstrap/status')payload={required:false};
  else if(pathname==='/api/session')payload={username:'synthetic-admin',csrf:'synthetic-csrf'};
  else if(pathname==='/api/settings')payload=view();
  else if(pathname==='/api/system')payload=system();
  else if(pathname==='/api/configuration/prepare')payload={plan_id:'b'.repeat(32),revision:7,settings,changed_files:['site.json'],warnings:['Synthetic warning.'],discovery:view().discovery};
  else if(pathname==='/api/configuration/apply'){applies++;mode='starting';payload=job()}
  else if(pathname.startsWith('/api/jobs/')){requests++;if(mode==='unavailable'){status=503;payload={error:'operation_unavailable',message:'Synthetic helper unavailable.'}}else payload=pathname.endsWith('c'.repeat(32))?{...job(),job_id:'c'.repeat(32),status:'complete',phase:'complete',applied_revision:5,requested_revision:5}:job()}
  else if(pathname==='/api/diagnostics')payload={monitoring:{status:'UNKNOWN',checks:[]},system:system()};
  else{status=404;payload={error:'not_found'}}
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(payload)});
 });
 const url='http://127.0.0.1:'+server.address().port+'/?view=settings#settings';
 const visible=id=>page.locator('#'+id).isVisible();
 await page.goto(url);await page.waitForFunction(()=>!document.getElementById('workspace').hidden);
 assert.equal(await visible('deployment'),false,'Completed startup must not restore progress');
 assert.equal(await visible('deployment-history'),true);assert.equal(await page.locator('#deployment-history').getAttribute('open'),null);
 await page.locator('#history-summary').click();assert.equal(await page.locator('#history-revision').textContent(),'7');
 await page.locator('#history-lookup-id').fill('c'.repeat(32));await page.locator('#history-lookup').click();await page.waitForFunction(()=>document.getElementById('history-revision').textContent==='5');
 await page.locator('#history-summary').click();
 await page.locator('[data-section="review"]').click();await page.locator('#prepare').click();await page.locator('#approve-plan').check();assert.equal(await page.locator('#apply').isEnabled(),true);
 await page.locator('#apply').click();await page.waitForFunction(()=>!document.getElementById('deployment').hidden);
 assert.equal(await page.locator('#apply').isDisabled(),true);assert.equal(await page.locator('#prepare').isDisabled(),true);
 await page.locator('#apply').evaluate(el=>el.click());assert.equal(applies,1,'Duplicate Apply was sent');
 mode='complete';await page.waitForFunction(()=>document.getElementById('deployment').hidden);assert.match(await page.locator('#notice').textContent(),/applied successfully/);
 assert.equal(await visible('deployment-history'),true);await page.reload();await page.waitForFunction(()=>!document.getElementById('workspace').hidden);assert.equal(await visible('deployment'),false,'Completed reload restored progress');
 await page.locator('a[data-page="overview"]').click();await page.locator('a[data-page="settings"]').click();assert.equal(await visible('deployment'),false,'Navigation restored completed progress');
 mode='starting';await page.reload();await page.waitForFunction(()=>!document.getElementById('deployment').hidden);assert.equal(await page.locator('#prepare').isDisabled(),true,'Running reload did not lock Apply workflow');
 mode='failed';await page.waitForFunction(()=>document.getElementById('deployment-status').textContent==='failed');assert.equal(await visible('rollback'),true);assert.match(await page.locator('#deployment-message').textContent(),/Failed stage: readiness/);
 await page.waitForTimeout(1400);assert.equal(await visible('deployment'),true,'Failure disappeared without acknowledgement');
 await page.locator('#deployment-acknowledge').click();assert.equal(await visible('deployment'),false);await page.reload();await page.waitForFunction(()=>!document.getElementById('workspace').hidden);assert.equal(await visible('deployment'),false,'Acknowledged failure returned');
 await page.locator('#history-summary').click();assert.match(await page.locator('#history-message').textContent(),/readiness failed/);assert.equal(await visible('history-rollback'),true);
 if(output){fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'deployment-failure-history.png'),fullPage:true})}
 mode='complete';discovery=false;await page.reload();await page.waitForFunction(()=>!document.getElementById('workspace').hidden);await page.locator('[data-section="review"]').click();await page.locator('#prepare').click();await page.locator('#approve-plan').check();assert.equal(await page.locator('#apply').isDisabled(),true);assert.match(await page.locator('#apply-readiness').textContent(),/api_tls_verify/);
 mode='unavailable';await page.reload();await page.waitForFunction(()=>document.getElementById('deployment-status').textContent==='Unknown');assert.equal(await page.locator('#prepare').isDisabled(),true);const before=requests;await page.waitForTimeout(1500);assert.equal(requests,before,'Unknown status retried automatically');
 mode='complete';discovery=true;await page.reload();await page.waitForFunction(()=>!document.getElementById('workspace').hidden);await page.setViewportSize({width:375,height:812});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Mobile horizontal overflow');
 if(output)await page.screenshot({path:path.join(output,'deployment-complete-mobile.png'),fullPage:true});
 assert.deepEqual(errors,[],'Browser JavaScript errors');
 console.log(JSON.stringify({passed:true,checks:['completed startup/reload/navigation hidden','running restore and duplicate prevention','completion notification and health refresh','failure acknowledgement and recovery history','historical job lookup','discovery gating','unknown outcome no retry','mobile layout'],browser:await browser.version(),screenshots:output||null}));
 }finally{await browser.close();server.close()}
})().catch(error=>{console.error(error.stack);server.close();process.exitCode=1});