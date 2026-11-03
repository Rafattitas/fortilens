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
async function setupAutofillRegression(browser,baseURL,{bootstrap,expectedDevice,scopeLocked=false}){
 const context=await browser.newContext({viewport:{width:1280,height:900},reducedMotion:'reduce'});
 const page=await context.newPage(),errors=[],saved=[],authRequests=[];
 const username='admin',password='synthetic-administrator-password',token='synthetic-read-only-api-token',bootstrapToken='synthetic-one-time-bootstrap-token';
 const device=expectedDevice||'FG-SYNTHETIC-TRUSTED';
 let signedIn=false,bootstrapRequired=bootstrap,revision=scopeLocked?7:0,connectionTests=0,storedToken=false;
 let currentSettings={...structuredClone(settings),fortigate_host:'',wan_interfaces:[],wan_confirmed:false,expected_devid:expectedDevice};
 const currentView=()=>({settings:structuredClone(currentSettings),configured:scopeLocked,revision,applied_revision:scopeLocked?7:null,pending_changes:!scopeLocked||revision!==7,scope_locked:scopeLocked,secret_status:{api_token_present:storedToken,custom_ca_present:false},active_monitoring:currentSettings.monitoring,discovery:{required:false,current:true,changed_fields:[],reason:'Synthetic first-run connection.'}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/api/**',async route=>{
  const request=route.request(),pathname=new URL(request.url()).pathname;let payload,status=200;
  if(pathname==='/api/bootstrap/status')payload={required:bootstrapRequired};
  else if(pathname==='/api/bootstrap'){
   const body=request.postDataJSON();authRequests.push({path:pathname,body});assert.equal(body.bootstrap_token,bootstrapToken);assert.equal(body.username,username);assert.equal(body.password,password);assert.equal(body.confirmation,password);bootstrapRequired=false;payload={status:'created'};
  }else if(pathname==='/api/login'){
   const body=request.postDataJSON();authRequests.push({path:pathname,body});assert.equal(body.username,username);assert.equal(body.password,password);signedIn=true;payload={username,csrf:'synthetic-setup-csrf'};
  }else if(!signedIn){status=401;payload={error:'authentication_required'}}
  else if(pathname==='/api/session')payload={username,csrf:'synthetic-setup-csrf'};
  else if(pathname==='/api/settings'){
   if(request.method()==='POST'){
    const body=request.postDataJSON();assert.equal(body.revision,revision);saved.push(body);currentSettings=structuredClone(body.settings);revision++;if(body.api_token)storedToken=true;
   }
   payload=currentView();
  }else if(pathname==='/api/system')payload={...system(),services:[],latest_job:null};
  else if(pathname==='/api/interfaces/discover')payload={interfaces:[{name:'wan1',vdom:'root',type:'physical',role:'wan',ip_address:'192.0.2.2/24',parent:'',vlan_id:null,status:'Configured enabled',review_status:'VERIFIED'}],coverage:'Synthetic interface inventory',notes:[]};
  else if(pathname==='/api/connection/test'){
   connectionTests++;assert.equal(request.postDataJSON().revision,revision);
   if(currentSettings.expected_devid&&currentSettings.expected_devid!==device){status=400;payload={error:'device_identity_mismatch',message:'The API device ID differs from the configured accounting source. Review the address and expected device ID.'}}
   else payload={status:'success',tls_verified:true,device:{serial:device},message:'Read-only synthetic connection succeeded.'};
  }else{status=404;payload={error:'not_found'}}
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(payload)});
 });
 try{
  await page.goto(baseURL+'/?view=settings#settings');
  await page.waitForFunction(required=>document.getElementById('bootstrap-fields').hidden!==required,bootstrap);
  assert.equal(await page.locator('#username').getAttribute('autocomplete'),'username');assert.equal(await page.locator('#password').getAttribute('autocomplete'),bootstrap?'new-password':'current-password');
  const tokenForm=await page.locator('#api-token').evaluate(input=>input.form?.id||'');assert.notEqual(tokenForm,'');assert.notEqual(tokenForm,'configuration-form','The settings form shares password-manager credential ownership');
  assert.equal(await page.evaluate(()=>Array.from(document.getElementById('configuration-form').elements).filter(input=>input.type==='password').length),0,'Noncredential settings still own a password field');
  if(bootstrap){await page.locator('#bootstrap-token').fill(bootstrapToken);await page.locator('#confirmation').fill(password)}
  await page.locator('#username').fill(username);await page.locator('#password').fill(password);await page.locator('#auth-submit').click();
  await page.waitForFunction(()=>!document.getElementById('workspace').hidden);
  assert.equal(await page.locator('[data-form-section="monitoring"]').isVisible(),false,'Setup visited Monitoring before connecting');
  await page.locator('[data-section="connection"]').click();
  await page.locator('#fortigate-host').fill('192.0.2.10');await page.locator('#api-port').fill('10443');await page.locator('#api-token').fill(token);
  // Password managers may mutate hidden DOM values without events, or emit
  // input/change events. Neither represents an edit to an unvisited setup step.
  await page.evaluate(({username,password})=>{
   for(const [id,value,events]of [['expected-devid',username,true],['site-name',password,false],['installation-name',username,true],['accounting-timezone',password,false]]){
    const input=document.getElementById(id);input.value=value;
    if(events){input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))}
   }
  },{username,password});
  const firstResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/connection/test');await page.locator('#test-connection').click();assert.equal((await firstResponse).status(),200,'Hidden autofill caused a false device-identity failure');
  assert.equal(saved.length,1);assert.equal(saved[0].settings.expected_devid,expectedDevice,'Hidden autofill replaced the saved accounting source');
  assert.equal(saved[0].settings.site_name,settings.site_name);assert.equal(saved[0].settings.installation_name,settings.installation_name);assert.equal(saved[0].settings.accounting_timezone,settings.accounting_timezone);
  assert.equal(saved[0].settings.fortigate_host,'192.0.2.10');assert.equal(saved[0].settings.api_port,10443);assert.equal(saved[0].api_token,token);assert.equal(saved[0].replace_token,true);
  for(const secret of [username,password,token,bootstrapToken])assert.equal(Object.values(saved[0].settings).includes(secret),false,'An authentication value entered noncredential settings');
  assert.equal(authRequests.filter(x=>x.path==='/api/bootstrap').length,bootstrap?1:0);assert.equal(authRequests.filter(x=>x.path==='/api/login').length,1);
  await page.evaluate(()=>{document.getElementById('expected-devid').value='admin'});await page.locator('[data-section="monitoring"]').click();
  assert.equal(await page.locator('#expected-devid').inputValue(),expectedDevice,'An untouched hidden value was displayed as a deliberate setup choice');
  await page.evaluate(({password,events})=>{
   const input=document.getElementById('api-token');input.value=password;
   if(events){input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))}
  },{password,events:!bootstrap});
  if(scopeLocked){
   assert.equal(await page.locator('#expected-devid').getAttribute('readonly'),'');
   await page.evaluate(()=>{const input=document.getElementById('expected-devid');input.value='admin';input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))});
   await page.locator('[data-section="connection"]').click();
   const lockedResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/connection/test');await page.locator('#test-connection').click();assert.equal((await lockedResponse).status(),200);assert.equal(saved.at(-1).settings.expected_devid,expectedDevice,'A read-only accounting source was changed by a DOM mutation');assert.equal(saved.at(-1).api_token,undefined,'Hidden password autofill replaced a stored API token');assert.equal(connectionTests,2);
   assert.deepEqual(errors,[],'Locked-source browser JavaScript errors');return;
  }
  await page.locator('#expected-devid').fill('FG-EXPLICIT-WRONG');await page.locator('#expected-devid').press('Tab');await page.locator('[data-section="connection"]').click();
  await page.evaluate(()=>{const input=document.getElementById('expected-devid');input.value='admin';input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))});
  const mismatchResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/connection/test');await page.locator('#test-connection').click();assert.equal((await mismatchResponse).status(),400,'An explicit mismatching source bypassed identity validation');
  assert.equal(saved.at(-1).settings.expected_devid,'FG-EXPLICIT-WRONG','Hidden autofill overwrote an earlier deliberate edit');assert.equal(saved.at(-1).api_token,undefined,'Hidden password autofill replaced a stored API token');await page.waitForFunction(()=>document.getElementById('connection-result').textContent.includes('API device ID differs'));
  await page.locator('[data-section="monitoring"]').click();await page.locator('#expected-devid').fill(device);await page.locator('#expected-devid').press('Tab');await page.locator('[data-section="connection"]').click();
  const correctedResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/connection/test');await page.locator('#test-connection').click();assert.equal((await correctedResponse).status(),200);assert.equal(saved.at(-1).settings.expected_devid,device);assert.equal(connectionTests,3);
  if(bootstrap){
   const save=async()=>{const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/settings'&&r.request().method()==='POST');await page.locator('#save').click();assert.equal((await response).status(),200);await page.waitForFunction(()=>document.getElementById('save').disabled===false);return saved.at(-1)};
   await page.locator('[data-section="monitoring"]').click();await page.locator('[data-setting="monitoring.syslog"]').uncheck();
   let body=await save();assert.equal(body.settings.monitoring.syslog,false);assert.equal(body.settings.monitoring.accounting,false);assert.equal(body.settings.monitoring.vlans,false);
   await page.locator('[data-setting="monitoring.vlans"]').check();body=await save();assert.equal(body.settings.monitoring.syslog,true);assert.equal(body.settings.monitoring.accounting,true);assert.equal(body.settings.monitoring.vlans,true);
   await page.evaluate(password=>{const input=document.getElementById('custom-ca');input.value=password;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))},password);
   await page.locator('[data-section="connection"]').click();await page.locator('[data-form-section="connection"] details summary').click();assert.equal(await page.locator('#custom-ca').inputValue(),'');await page.locator('#replace-ca').check();
   body=await save();assert.equal(body.custom_ca,'','Explicit CA clearing adopted an untouched hidden value');assert.equal(body.replace_ca,true);assert.equal(body.api_token,undefined);
   await page.locator('[data-section="interfaces"]').click();await page.locator('#discover-interfaces').click();
   const wan=page.getByRole('checkbox',{name:'Select wan1 as Internet interface'});await wan.waitFor({state:'visible'});await wan.check();await page.locator('#confirm-wans').check();
   body=await save();assert.deepEqual(body.settings.wan_interfaces,['wan1']);assert.equal(body.settings.wan_confirmed,true);
   await wan.uncheck();assert.equal(await page.locator('#confirm-wans').isChecked(),false);body=await save();assert.deepEqual(body.settings.wan_interfaces,[]);assert.equal(body.settings.wan_confirmed,false,'A changed WAN selection retained the previous confirmation');
   await wan.check();assert.equal(await page.locator('#confirm-wans').isChecked(),false);await page.locator('#confirm-wans').check();body=await save();assert.equal(body.settings.wan_confirmed,true);
  }
  assert.deepEqual(errors,[],'Fresh-setup browser JavaScript errors');
 }finally{await context.close()}
}
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,...(process.env.FORTILENS_BROWSER_EXECUTABLE?{executablePath:process.env.FORTILENS_BROWSER_EXECUTABLE}:{})});
 try{
 const baseURL='http://127.0.0.1:'+server.address().port;
 await setupAutofillRegression(browser,baseURL,{bootstrap:true,expectedDevice:''});
 await setupAutofillRegression(browser,baseURL,{bootstrap:false,expectedDevice:'FG-SAVED-SYNTHETIC'});
 await setupAutofillRegression(browser,baseURL,{bootstrap:false,expectedDevice:'FG-LOCKED-SYNTHETIC',scopeLocked:true});
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
 console.log(JSON.stringify({passed:true,checks:['first administrator setup ignores hidden autofill','saved source survives sign-in/autofill','locked source rejects DOM mutations','explicit source mismatch and deliberate edits remain enforced','collector dependency changes persist','explicit CA clearing ignores hidden pollution','WAN changes clear confirmation until reconfirmed','completed startup/reload/navigation hidden','running restore and duplicate prevention','completion notification and health refresh','failure acknowledgement and recovery history','historical job lookup','discovery gating','unknown outcome no retry','mobile layout'],browser:await browser.version(),screenshots:output||null}));
 }finally{await browser.close();server.close()}
})().catch(error=>{console.error(error.stack);server.close();process.exitCode=1});
