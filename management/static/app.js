'use strict';
const $=id=>document.getElementById(id);
const state={navOpen:false,csrf:'',bootstrap:false,initialNavigation:true,view:null,draftSettings:null,apiTokenDraft:null,customCADraft:null,system:null,monitoringTimeOffset:undefined,section:'general',plan:null,job:null,jobPending:false,jobUnknown:false,discoveryStale:false,inventory:[],selected:new Set()};
const sections=['general','connection','interfaces','monitoring','storage','review'];
const compactNavigation=window.matchMedia('(max-width: 900px)');
function setNavigation(open,restoreFocus=true){state.navOpen=!!open&&compactNavigation.matches;$('workspace').classList.toggle('nav-open',state.navOpen);$('menu-toggle').setAttribute('aria-expanded',String(state.navOpen));$('workspace-nav').inert=compactNavigation.matches&&!state.navOpen;$('main-shell').inert=state.navOpen;if(state.navOpen){$('workspace-nav').setAttribute('role','dialog');$('workspace-nav').setAttribute('aria-modal','true')}else{$('workspace-nav').removeAttribute('role');$('workspace-nav').removeAttribute('aria-modal')}$('nav-scrim').hidden=!state.navOpen;if(state.navOpen)$('menu-close').focus();else if(restoreFocus&&compactNavigation.matches)$('menu-toggle').focus()}
compactNavigation.addEventListener('change',()=>setNavigation(false,false));
$('menu-toggle').addEventListener('click',()=>setNavigation(!state.navOpen));
$('menu-close').addEventListener('click',()=>setNavigation(false));
$('nav-scrim').addEventListener('click',()=>setNavigation(false));
$('workspace-nav').addEventListener('click',e=>{if(state.navOpen&&e.target.closest('a[href^="#"]')){setNavigation(false,false);$('page-title').focus()}});
$('skip-content').addEventListener('click',e=>{e.preventDefault();$('main-content').focus()});
window.addEventListener('keydown',e=>{if(!state.navOpen)return;if(e.key==='Escape'){e.preventDefault();setNavigation(false);return}if(e.key==='Tab'){const items=[...$('workspace-nav').querySelectorAll('button,a[href]')].filter(x=>!x.disabled&&!x.hidden&&x.getClientRects().length);const first=items[0],last=items.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}});
setNavigation(false,false);
function clearConsoleCheck(){clearInterval(state.consoleCheck);state.consoleCheck=null}
// Monitoring time alignment: only trusted server evidence scopes a default launch.
function monitoringClockOffset(serverSeconds,clientNow=Date.now()){
 if(serverSeconds===undefined)return undefined;
 if(typeof serverSeconds!=='number'||!Number.isFinite(serverSeconds)||serverSeconds<=0||!Number.isFinite(clientNow))return null;
 const difference=serverSeconds*1000-clientNow;
 if(!Number.isFinite(difference)||Math.abs(difference)>86400000)return null;
 return Math.abs(difference)>60000?Math.round(difference/1000):0;
}
function monitoringLaunch(path,offset,supported){
 const unchanged={path,aligned:false};
 if(typeof path!=='string'||!Number.isInteger(offset)||offset===0||Math.abs(offset)>86400||supported!==true)return unchanged;
 let url;try{url=new URL(path,location.origin)}catch{return unchanged}
 if(url.origin!==location.origin||!/^\/grafana\/d\/[A-Za-z0-9_-]+\/$/.test(url.pathname))return unchanged;
 url.searchParams.set('fortilens-clock','server');
 return {path:url.pathname+url.search+url.hash,aligned:true};
}
function monitoringClockNotice(offset,compensated){
 if(offset===undefined||offset===0)return '';
 if(offset===null)return 'Monitoring clock alignment is unavailable. The computer/server time difference could not be used safely; the selected range has not been changed.';
 return compensated?'Your computer clock differs from the server. Monitoring uses server time; stored timestamps and data have not been changed.':'Your computer clock differs from the server. Server-time alignment is unavailable for this monitoring connection; the selected range has not been changed.';
}
// End monitoring time alignment.
function rememberSystem(value){state.system=value;state.systemAt=performance.now();state.monitoringTimeOffset=monitoringClockOffset(value.server_time_seconds);renderEvidenceBar()}
function consoleDocumentReady(frame){
 if(frame!==$('console-frame'))return false;
 try{
  const doc=frame.contentDocument;
  if(!doc||doc.URL==='about:blank'||doc.readyState==='loading')return false;
  clearConsoleCheck();$('console-loading').hidden=true;frame.removeAttribute('aria-busy');
  if(doc.contentType==='application/json'){
   frame.hidden=true;$('console-error').textContent='This view is unavailable. Reload it or open Diagnostics.';$('console-error').hidden=false;
  }
  return true;
 }catch(e){return false}
}
const titles={home:['Overview','What the links, logs and usage evidence say right now.'],overview:['System','Monitoring services, storage and configuration readiness.'],wan:['Internet','Observed health, physical link and route evidence remain separate.'],devices:['Usage','Recorded upload and download, with identity and coverage limits.'],vlans:['Segments','Internet usage grouped by exact interface metadata.'],reports:['Reports','Daily, weekly and monthly reports from the existing accounting engine.'],monitoring:['Live monitoring','Firewall health, Internet links and interface traffic in native Grafana.'],quality:['Evidence','Collection gaps, accounting evidence and identity limits.'],settings:['Settings','Manage your installation without editing configuration files.'],diagnostics:['Diagnostics','Read-only evidence, service readiness and actionable next steps.']};
const pageGroups={home:'Network',monitoring:'Overview',wan:'Network',devices:'Network',vlans:'Usage',reports:'Usage',quality:'Trust',overview:'Installation',diagnostics:'System',settings:'Installation'};
// Theme choice is shared with the same-origin report frame through storage.
function savedTheme(){try{const value=localStorage.getItem('fortilens.theme');if(value==='light'||value==='dark')return value}catch{}return window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'}
function applyTheme(theme){document.documentElement.dataset.theme=theme;try{const doc=$('console-frame').contentDocument;if(doc?.location.pathname.startsWith('/console/'))doc.documentElement.dataset.theme=theme}catch{}$('theme-toggle').textContent=theme==='dark'?'Light':'Dark';$('theme-toggle').setAttribute('aria-label','Switch to '+(theme==='dark'?'light':'dark')+' theme')}
function toggleTheme(){const next=document.documentElement.dataset.theme==='dark'?'light':'dark';try{localStorage.setItem('fortilens.theme',next)}catch{}applyTheme(next)}
applyTheme(savedTheme());
$('theme-toggle').addEventListener('click',toggleTheme);
const names={'fortigate-exporter':'FortiGate Exporter',prometheus:'Prometheus',grafana:'Grafana',loki:'Loki',alloy:'Grafana Alloy','node-exporter':'Node Exporter','fortilens-usage':'Accounting Service','fortilens-portal':'Monitoring Gateway','fortilens-syslog':'Native Syslog Receiver'};
const E=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
const size=n=>n===null||n===undefined?'Unavailable':n>=2**30?(n/2**30).toFixed(1)+' GiB':(n/2**20).toFixed(1)+' MiB';
function stamp(value){const p=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:state.view?.settings.timezone||'UTC',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value)).map(x=>[x.type,x.value]));const pattern=state.view?.settings.date_format||'YYYY-MM-DD';return pattern.replace('YYYY',p.year).replace('MM',p.month).replace('DD',p.day)+' '+p.hour+':'+p.minute+':'+p.second}
function notice(text,bad=false){$('notice').textContent=text||'';$('notice').hidden=!text;$('notice').classList.toggle('error',bad)}
// Transport boundary: bounded requests and trusted, redacted UI errors.
function safeError(message,code='operation_unavailable'){const error=new Error(message);error.code=code;error.fortilensSafe=true;return error}
function errorText(error,fallback='This operation is unavailable. Review service health and try again.'){return error?.fortilensSafe===true&&typeof error.message==='string'?error.message:fallback}
function requestError(code,write){const messages={network:'The service could not be reached. Check your connection and service health.',timeout:'The service response timed out. Review service health.',invalid_response:'The service did not return a readable response. Review service health.'};return safeError(messages[code]+(write?' The operation may have completed. Review the current state before trying again.':''),code)}
function responseError(data,fallback){return typeof data?.error==='string'&&/^[a-z][a-z0-9_]{0,79}$/.test(data.error)&&typeof data.message==='string'&&data.message.length>0&&data.message.length<=600&&!/[\x00-\x1f\x7f]/.test(data.message)?safeError(data.message,data.error):safeError(fallback)}
async function requestJSON(path,options={},timeoutMs=60000){
 const controller=new AbortController(),write=!['GET','HEAD'].includes((options.method||'GET').toUpperCase());
 let timer,expired=false;
 const deadline=new Promise((resolve,reject)=>{timer=setTimeout(()=>{expired=true;controller.abort();reject(requestError('timeout',write))},timeoutMs)});
 try{
  return await Promise.race([(async()=>{
   let response;
   try{response=await fetch(path,{...options,signal:controller.signal})}catch{throw requestError(expired?'timeout':'network',write)}
   if(!response||typeof response.status!=='number'||typeof response.json!=='function')throw requestError('invalid_response',write);
   // Revocation must work even when a proxy returns an HTML or stalled 401 body.
   if(response.status===401){controller.abort();return {response,data:null}}
   let data;
   try{data=await response.json()}catch{throw requestError(expired?'timeout':'invalid_response',write)}
   if(data===null||typeof data!=='object'||Array.isArray(data))throw requestError('invalid_response',write);
   return {response,data};
  })(),deadline]);
 }finally{clearTimeout(timer)}
}
function validPayload(path,data){
 const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
 if(path==='/api/session'||path==='/api/login')return typeof data.csrf==='string'&&data.csrf.length>0&&typeof data.username==='string';
 if(path==='/api/bootstrap/status')return typeof data.required==='boolean';
 if(path==='/api/settings')return object(data.settings)&&object(data.settings.monitoring)&&object(data.settings.retention)&&Array.isArray(data.settings.wan_interfaces)&&object(data.secret_status)&&typeof data.configured==='boolean'&&Number.isInteger(data.revision);
 if(path==='/api/system')return Array.isArray(data.services)&&object(data.disk)&&object(data.storage_projection)&&object(data.links);
 if(path==='/api/diagnostics')return object(data.system)&&object(data.monitoring)&&Array.isArray(data.monitoring.checks);
 if(path.startsWith('/api/jobs/')||['/api/configuration/apply','/api/configuration/rollback'].includes(path))return typeof data.job_id==='string'&&typeof data.phase==='string'&&typeof data.status==='string'&&Array.isArray(data.events);
 if(path==='/api/configuration/prepare')return typeof data.plan_id==='string'&&Array.isArray(data.changed_files)&&object(data.settings);
 if(path==='/api/interfaces/discover')return Array.isArray(data.interfaces)&&Array.isArray(data.notes);
 return true;
}
async function api(path,body){
 const headers={'X-FortiLens-Request':'1'};
 if(body!==undefined){headers['Content-Type']='application/json';if(state.csrf)headers['X-CSRF-Token']=state.csrf}
 const {response,data}=await requestJSON(path,{method:body===undefined?'GET':'POST',headers,credentials:'same-origin',cache:'no-store',body:body===undefined?undefined:JSON.stringify(body)});
 if(response.status===401){if(state.csrf)signedOut();throw safeError('Your session has expired or sign-in was rejected. Please sign in again.','authentication_required')}
 if(!response.ok)throw responseError(data,'This operation is unavailable.');
 if(!validPayload(path,data))throw requestError('invalid_response',body!==undefined);
 return data;
}
// End transport boundary.
async function action(button,run){button.disabled=true;const label=button.textContent;button.textContent='Working…';try{await run()}catch(e){notice(errorText(e),true);if(button.id==='test-connection')$('connection-result').textContent=errorText(e);if(button.id==='discover-interfaces')$('inventory-caption').textContent=errorText(e)}finally{button.disabled=false;button.textContent=label;if(typeof updateDeploymentControls==='function')updateDeploymentControls()}}
function dirty(){state.plan=null;$('plan-review').hidden=true;$('approve-plan').checked=false;$('save-status').textContent='Unsaved changes';updateDeploymentControls()}
function pathGet(obj,path){return path.split('.').reduce((v,k)=>v?.[k],obj)}
function pathSet(obj,path,value){const parts=path.split('.');let target=obj;for(const k of parts.slice(0,-1))target=target[k];target[parts.at(-1)]=value}
const databaseGiB=bytes=>String(Number((bytes/2**30).toFixed(4)));
function renderDatabaseLimitHint(){
 const input=$('database-gib');input.setCustomValidity(state.databaseLimitError||'');input.setAttribute('aria-invalid',state.databaseLimitError?'true':'false');
 $('database-limit-help').textContent=state.databaseLimitError||'Enter a size such as 9 or 9.5 GiB. Includes the database and its write log; disk space is not reserved in advance.';
}
// Draft values come from reviewed settings and intentional visible edits, never a hidden DOM scan.
function renderDraftInputs(){
 if(!state.draftSettings)return;
 $('syslog-network').disabled=state.draftSettings.syslog_format!=='fortigate';
 for(const input of document.querySelectorAll('[data-setting]')){const value=pathGet(state.draftSettings,input.dataset.setting);if(input.type==='checkbox')input.checked=!!value;else input.value=input.dataset.setting==='retention.database_bytes'?(state.databaseLimitInput??databaseGiB(value)):value??''}
 renderDatabaseLimitHint();
 const exclusions=state.draftSettings.optional_exporter_exclusions??state.view.exporter_policy?.optional_exclusions??[];
 for(const input of document.querySelectorAll('[data-optional-probe]')){input.checked=exclusions.includes(input.dataset.optionalProbe);input.disabled=state.view.exporter_policy?.available===false}
 $('syslog-profile-help').textContent=state.draftSettings.syslog_format==='fortigate'?'Native FortiGate default messages require UDP. Set the incoming port to match the firewall (commonly 514). FortiLens forwards internally to Alloy over RFC5424/TCP. UDP delivery is best effort.':'Match the sender protocol, port and RFC format. Native FortiGate default messages need the FortiGate default / native UDP profile.';
 $('sla-help').textContent=state.draftSettings.sla?'Uses this exact check name. A failed check remains failed; another destination is never substituted.':'No SLA selected. Internet state, latency, jitter and loss remain unknown until you choose the device’s actual check name. Diagnostics lists recently observed names.';
}
function intentionalDraftEdit(input,event){
 if(!state.draftSettings||!event.isTrusted||input.disabled||input.readOnly||$('workspace').hidden||$('settings-page').hidden||$('configuration-form').hidden||!input.getClientRects().length)return false;
 // Native checkbox/select interaction need not focus the control in every browser.
 return input.type==='checkbox'||input.tagName==='SELECT'||document.activeElement===input;
}
function setDraftFlag(name,value){pathSet(state.draftSettings,'monitoring.'+name,value);document.querySelector('[data-setting="monitoring.'+name+'"]').checked=value}
function connectionDraftChanged(){invalidateDiscovery();$('connection-result').textContent='Connection settings changed. Save and test this connection.'}
function settingChanged(input,event){
 if(!intentionalDraftEdit(input,event))return;
 const key=input.dataset.setting;let value=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value.trim();
 if(key==='retention.database_bytes'){
  const amount=input.valueAsNumber,bytes=Math.round(amount*2**30),saved=state.view.settings.retention.database_bytes;
  state.databaseLimitInput=input.value;
  state.databaseLimitError=!Number.isFinite(amount)||amount<0.0625||amount>1024||!Number.isSafeInteger(bytes)?'Enter a database limit between 0.0625 and 1024 GiB.':'';
  renderDatabaseLimitHint();dirty();
  if(state.databaseLimitError)return;
  // Preserve arbitrary existing byte ceilings when their rounded display is unchanged.
  value=amount===Number(databaseGiB(saved))?saved:bytes;
 }
 if(Object.is(pathGet(state.draftSettings,key),value))return;
 pathSet(state.draftSettings,key,value);dirty();
 if(['fortigate_host','api_port','vdom','api_tls_verify'].includes(key)){state.inventory=[];renderInterfaces();connectionDraftChanged()}
 if(key==='api_tls_verify')$('insecure-row').hidden=value;
 if(key==='syslog_format'&&value==='fortigate'){state.draftSettings.syslog_protocol='udp';renderDraftInputs()}
 if(key==='syslog_format'&&value!=='fortigate')state.draftSettings.syslog_network='bridge';
 if(key==='sla'||key==='syslog_format'||key==='syslog_network')renderDraftInputs();
 if(key==='monitoring.syslog'&&!value){setDraftFlag('accounting',false);setDraftFlag('vlans',false)}
 if(key==='monitoring.accounting'){if(value)setDraftFlag('syslog',true);else setDraftFlag('vlans',false)}
 if(key==='monitoring.vlans'&&value){setDraftFlag('accounting',true);setDraftFlag('syslog',true)}
}
function signedOut(){delete window.__fortilensReportCache;if($('palette').open)$('palette').close();state.system=null;state.evidence=null;setNavigation(false,false);clearConsoleCheck();clearTimeout(state.jobTimer);clearTimeout(state.successTimer);state.jobTimer=null;$('workspace-loading').hidden=true;document.title='FortiLens · Management';state.csrf='';state.view=null;state.monitoringTimeOffset=undefined;$('console-clock-warning').hidden=true;state.draftSettings=null;state.apiTokenDraft=null;state.customCADraft=null;state.plan=null;state.job=null;state.jobPending=false;state.jobUnknown=false;$('deployment').hidden=true;$('deployment-history').hidden=true;$('console-frame').removeAttribute('src');$('workspace').hidden=true;$('auth').hidden=false;$('auth-form').reset();$('password-form').reset();for(const id of ['api-token','custom-ca','bootstrap-token'])$(id).value=''}
async function enter(){const session=await api('/api/session');state.csrf=session.csrf;window.__fortilensReportCache={epoch:crypto.randomUUID(),entries:[]};state.initialNavigation=true;$('session-user').textContent=session.username;$('auth').hidden=true;$('workspace').hidden=true;$('workspace-loading').hidden=false;let failure=null;try{await reloadSettings();await loadSystem()}catch(e){failure=e}finally{$('workspace-loading').hidden=true}if(!state.csrf)return;$('workspace').hidden=false;if(failure){navigate('diagnostics');notice(errorText(failure),true);return}navigate(state.view.configured?(location.hash.slice(1)||new URL(location.href).searchParams.get('view')||defaultPage()):'settings');restoreLatestJob(state.system?.latest_job)}
async function reloadSettings(){state.view=await api('/api/settings');state.selected=new Set(state.view.settings.wan_interfaces);fillSettings();state.plan=null;$('plan-review').hidden=true}
function fillSettings(){
 state.draftSettings=structuredClone(state.view.settings);
 state.databaseLimitInput=null;state.databaseLimitError='';
 if(!state.view.configured&&state.view.revision===0)state.draftSettings.fortigate_host='';
 state.apiTokenDraft=null;state.customCADraft=null;
 for(const id of ['api-token','custom-ca'])$(id).value='';
 $('replace-ca').checked=false;$('insecure-ack').checked=false;
 $('workspace').classList.toggle('configured',state.view.configured);renderDraftInputs();
 $('token-status').textContent=state.view.secret_status.api_token_present?'Stored token configured. It is never returned to the browser.':'No stored API token.';
 $('replace-token').checked=!state.view.secret_status.api_token_present;$('wizard-intro').hidden=state.view.configured;$('site-label').textContent=state.view.settings.site_name;
 for(const id of ['vdom','accounting-timezone','expected-devid'])$(id).readOnly=state.view.scope_locked;
 $('revision').textContent=state.view.pending_changes?'Draft revision '+state.view.revision:state.view.configured?'Applied revision '+state.view.applied_revision:'Not configured';
 $('revision').className='pill '+(state.view.pending_changes?'warn':state.view.configured?'good':'');$('insecure-row').hidden=$('verify-tls').checked;
 for(const link of document.querySelectorAll('[data-page]'))link.hidden=state.view.configured&&!pageEnabled(link.dataset.page);
 updateSelection();renderReview();renderWorkflowReadiness();
}
function readSettings(){const settings=structuredClone(state.draftSettings||state.view.settings);settings.wan_interfaces=[...state.selected];return settings}
async function saveDraft(){if(state.databaseLimitError)throw safeError(state.databaseLimitError,'invalid_fields');const body={revision:state.view.revision,settings:readSettings(),acknowledge_insecure_tls:$('insecure-ack').checked};if(state.apiTokenDraft){body.api_token=state.apiTokenDraft;body.replace_token=$('replace-token').checked}if($('replace-ca').checked){body.custom_ca=state.customCADraft??'';body.replace_ca=true}state.view=await api('/api/settings',body);state.plan=null;$('plan-review').hidden=true;fillSettings();$('save-status').textContent='Draft saved. Active services have not changed.';notice('Configuration draft saved. Review and apply when ready.');return state.view}
function selectSection(section){state.section=section;renderDraftInputs();if(section==='connection'){$('api-token').value=state.apiTokenDraft??'';$('custom-ca').value=state.customCADraft??''}$('settings-section-picker').value=section;for(const b of document.querySelectorAll('[data-section]')){const active=b.dataset.section===section;b.classList.toggle('active',active);if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')}const auth=section==='authentication';$('configuration-form').hidden=auth;$('authentication-section').hidden=!auth;for(const part of document.querySelectorAll('[data-form-section]'))part.hidden=part.dataset.formSection!==section;const index=sections.indexOf(section);$('step-label').textContent='Step '+(index+2)+' of 7';$('previous-section').hidden=index===0;$('next-section').hidden=index===sections.length-1||auth;$('save').hidden=auth;renderReview()}
async function loadSystem(){try{rememberSystem(await api('/api/system'));renderSystem();return true}catch(e){notice(errorText(e),true);$('overview-cards').replaceChildren(metric('Management portal','Running','Independent of monitoring'),metric('Monitoring status','Unavailable','Open Diagnostics for recovery'));return false}}
function metric(title,value,caption){const card=E('div',undefined,'metric-card');card.append(E('span',title,'metric-label'),E('div',value,'metric'),E('small',caption));return card}
function fact(root,title,value){const block=E('div',undefined,'fact');block.append(E('span',title),E('strong',value));root.append(block)}
function renderStorage(projection,disk){
 const root=$('storage-facts');root.replaceChildren();
 for(const [name,value]of [['Total server storage',size(disk.total)],['Used storage',size(disk.used)],['Available storage',size(disk.available)]])fact(root,name,value);
 for(const [service,value]of Object.entries(projection.components||{})){
  if(!value||typeof value!=='object'||Array.isArray(value))continue;
  const name=names[service]||service;fact(root,name+' measured size',size(value.bytes));
  const observed=new Date(value.projection_at_seconds*1000);
  const available=value.forecast_status==='available'&&value.horizon_seconds===86400&&Number.isFinite(value.projection_bytes)&&value.projection_bytes>=0&&Number.isInteger(value.sample_count)&&value.sample_count>=4&&Number.isFinite(value.observation_seconds)&&value.observation_seconds>=3600&&Number.isFinite(value.projection_at_seconds)&&value.projection_at_seconds>0&&Number.isFinite(observed.getTime());
  if(available){
   fact(root,name+' estimated additional storage (next 24h)',size(value.projection_bytes));
   fact(root,name+' observation window',(value.observation_seconds/3600).toFixed(1)+' hours · observed through '+stamp(observed.getTime())+' '+(state.view?.settings.timezone||'UTC'));
  }else{
   const legacy=value.projection_bytes!==undefined&&value.horizon_seconds!==86400;
   const reason=value.forecast_status!=='available'&&typeof value.forecast_message==='string'&&value.forecast_message?value.forecast_message:legacy?'A current 24-hour growth estimate requires an updated helper.':'A current 24-hour growth estimate is unavailable.';
   fact(root,name+' next 24h estimate',reason);
  }
  if(service==='fortilens-usage'){
   if(Number.isFinite(value.capacity_limit_bytes)&&value.capacity_limit_bytes>=0)fact(root,name+' configured database ceiling',size(value.capacity_limit_bytes));
   if(Number.isInteger(value.ledger_days)&&value.ledger_days>0&&Number.isInteger(value.summary_days)&&value.summary_days>0)fact(root,name+' applied retention',value.ledger_days+' days ledger · '+value.summary_days+' days summaries');
   if(typeof value.capacity_warning==='string'&&value.capacity_warning)fact(root,name+' capacity warning',value.capacity_warning);
  }
 }
 $('storage-projection').textContent=projection.message;
}
function renderSystem(){const s=state.system;const running=s.services.filter(r=>r.status==='running').length;$('overview-cards').replaceChildren(metric('Console','Available','Settings and diagnostics'),metric('Services running',running+' / '+s.services.length,'Docker: '+s.docker),metric('Available storage',size(s.disk.available),'of '+size(s.disk.total)),metric('Configuration',state.view.pending_changes?'Changes pending':state.view.configured?'Configured':'Setup needed',state.view.pending_changes?'Draft '+state.view.revision+' · Applied '+state.view.applied_revision:state.view.configured?'Applied revision '+state.view.applied_revision:'Not yet applied'));$('service-list').replaceChildren();for(const service of Object.keys(names)){const row=s.services.find(x=>x.service===service);const line=E('div',undefined,'service-row');const status=row?(row.health==='healthy'?'Healthy':row.health==='unhealthy'?'Failed':row.health==='starting'?'Starting':row.status==='running'?'Running':row.status==='disabled'?'Disabled':row.status==='exited'?'Stopped':row.status):'Not started';line.append(E('span',names[service]),E('span',status,'pill '+(status==='Healthy'||status==='Running'?'good':'warn')));$('service-list').append(line)}$('server-facts').replaceChildren();for(const [name,value]of [['Hostname',s.hostname],['Operating system',s.os],['CPU / Architecture',s.cpu_count+' cores · '+s.architecture],['RAM',size(s.ram_bytes)],['Available disk',size(s.disk.available)],['Docker',s.docker]])fact($('server-facts'),name,value);renderStorage(s.storage_projection,s.disk);$('next-title').textContent=state.view.configured?'Workspace settings':'Connect your network.';$('next-caption').textContent=state.view.configured?'Review the connection, Internet interfaces and data retention for this installation.':'Review the connection, confirm Internet interfaces and apply your monitoring configuration.'}
function updateSelection(){$('wan-selection').textContent=state.selected.size?'Selected Internet interfaces: '+[...state.selected].join(', '):'No Internet interfaces selected. FortiLens will not guess WAN roles.'}
function renderInterfaces(){const query=$('interface-search').value.toLowerCase();$('interface-rows').replaceChildren();for(const r of state.inventory.filter(x=>[x.name,x.vdom,x.parent,x.vlan_id].join(' ').toLowerCase().includes(query))){const row=E('tr'),select=E('td'),check=E('input');check.type='checkbox';check.checked=state.selected.has(r.name);check.setAttribute('aria-label','Select '+r.name+' as Internet interface');check.disabled=r.vdom!==state.view.settings.vdom;check.addEventListener('change',()=>{if(check.checked)state.selected.add(r.name);else state.selected.delete(r.name);$('confirm-wans').checked=false;state.draftSettings.wan_confirmed=false;dirty();updateSelection()});select.append(check);row.append(select);for(const value of [r.name,(r.type||'Unknown')+' / '+(r.role||'Review required'),r.ip_address||'Unknown',r.vdom||'Unknown',(r.vlan_id??'—')+' / '+(r.parent||'—'),r.status||'Unknown',r.discovery_confidence||r.review_status||'Review required'])row.append(E('td',String(value)));$('interface-rows').append(row)}}
function renderReview(){if(!state.view)return;const s=readSettings();$('review-summary').replaceChildren();for(const [name,value]of [['Company / Installation',s.site_name+' / '+s.installation_name],['Timezone / Date format',s.timezone+' / '+s.date_format],['FortiGate',s.fortigate_host+':'+s.api_port+' · VDOM '+s.vdom],['TLS verification',s.api_tls_verify?'Enabled':'Explicitly disabled'],['Expected FortiGate serial',s.expected_devid||'Not bound'],['Confirmed Internet interfaces',s.wan_confirmed?[...state.selected].join(', ')||'None':'Not confirmed'],['Enabled monitoring',Object.entries(s.monitoring).filter(([,v])=>v).map(([k])=>k).join(', ')||'No collectors'],['Syslog',s.syslog_protocol.toUpperCase()+' / '+s.syslog_port+' / '+s.syslog_format+' / '+s.syslog_network],['Retention',s.retention.loki_days+'d logs · '+s.retention.prometheus_days+'d metrics · '+s.retention.ledger_days+'d ledger · '+s.retention.summary_days+'d summaries'],['Accounting database ceiling',state.databaseLimitError?'Review the database limit':databaseGiB(s.retention.database_bytes)+' GiB'],['Native service ports','Grafana '+s.grafana_port+' · Protected reporting '+s.usage_port],['Network access',location.protocol==='https:'?'HTTPS management origin':'Loopback management / SSH tunnel'],['Credentials','Stored privately; raw tokens and passwords are never included in this review.']]){const row=E('div');row.append(E('dt',name),E('dd',value));$('review-summary').append(row)}}
function defaultPage(){return 'home'}
function pageEnabled(page){const f=state.view?.active_monitoring;if(!f)return true;return page==='wan'?f.wan:['devices','reports','quality'].includes(page)?f.accounting:page==='vlans'?f.vlans:page==='monitoring'?(f.wan||f.sla||f.interfaces||f.server):true}
function navigate(page){
 if(!titles[page])page=defaultPage();
 if(!state.view)page='diagnostics';else if(!pageEnabled(page))page='overview';
 if(state.view&&!state.view.configured&&!['settings','diagnostics'].includes(page))page='settings';
 const embedded=!['home','overview','settings','diagnostics'].includes(page);
 const route=new URL('/',location.origin);route.searchParams.set('view',page);route.hash=page;
 // Legacy hash links remain valid. If the server could not know that first
 // destination (including immediately after sign-in), request its initial HTML.
 if(state.initialNavigation&&embedded&&$('console-frame').dataset.initialView!==page){
  $('workspace').hidden=true;$('workspace-loading').hidden=false;
  if(route.href===location.href)location.reload();else location.replace(route.href);
  return;
 }
 state.initialNavigation=false;
 $('workspace').classList.toggle('console-mode',embedded);document.title=titles[page][0]+' · FortiLens';
 const menuWasOpen=state.navOpen;setNavigation(false,false);if(menuWasOpen)$('page-title').focus();
 if(!embedded){clearConsoleCheck();$('console-frame').removeAttribute('src');delete $('console-frame').dataset.initialView;$('console-loading').hidden=true}
 for(const a of document.querySelectorAll('[data-page]')){a.classList.toggle('active',a.dataset.page===page);if(a.dataset.page===page)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current')}
 state.page=page;clearTimeout(state.homeTimer);$('workspace').classList.toggle('home-mode',page==='home');
 $('page-title').textContent=titles[page][0];$('page-caption').textContent=titles[page][1];
 for(const id of ['home-page','overview-page','settings-page','diagnostics-page','console-page'])$(id).hidden=true;
 if(!embedded)$(page+'-page').hidden=false;else{$('console-page').hidden=false;loadConsole(page)}
 if(page==='settings')selectSection(state.section);if(page==='diagnostics')runDiagnostics();if(page==='overview')loadSystem();if(page==='home')loadHome();
 if(location.hash!=='#'+page)history.pushState(null,'',route);else history.replaceState(null,'',route);
 notice('');
}
function loadConsole(page){
 clearConsoleCheck();
 const previous=$('console-frame');
 $('console-error').hidden=true;$('console-loading').hidden=true;
 $('console-clock-warning').hidden=true;
 if(!state.system?.links?.console){
  previous.removeAttribute('src');$('console-error').hidden=false;
  $('console-error').textContent='This existing deployment retains separate native service authentication. Open the existing service below, or review the unified-console migration guide.';
  if(state.system?.links?.grafana_port){
   const link=E('a','Open native Grafana (separate sign-in)');
   link.href='http://'+location.hostname+':'+state.system.links.grafana_port+(state.system.links.grafana_prefix||'/');
   link.target='_blank';link.rel='noopener';$('console-error').append(E('br'),link);
  }
  return;
 }
 const urls={quality:'/console/#quality',wan:'/console/#wan',devices:'/console/?section=devices#overview',vlans:'/console/?section=vlans#overview',reports:'/console/?section=reports#reports',monitoring:state.system.links.dashboard};
 const original=urls[page]||urls.reports;
 const launch=page==='monitoring'?monitoringLaunch(original,state.monitoringTimeOffset,state.system.server_clock_supported):{path:original,aligned:false};
 const destination=launch.path,compensated=launch.aligned;
 const clockNotice=page==='monitoring'?monitoringClockNotice(state.monitoringTimeOffset,compensated):'';
 $('console-clock-warning').textContent=clockNotice;$('console-clock-warning').hidden=!clockNotice;
 // A new browsing context cannot display the preceding page during navigation.
 // Keep its own loader visible; do not wait for every Grafana subresource.
 const initial=previous.dataset.initialView===page&&!compensated;
 const frame=initial?previous:previous.cloneNode(false);delete frame.dataset.initialView;if(!initial)frame.removeAttribute('src');
 frame.hidden=false;frame.style.visibility='visible';frame.title=titles[page][0];frame.setAttribute('aria-busy','true');
 frame.onload=()=>consoleDocumentReady(frame);if(!initial)previous.replaceWith(frame);
 $('console-loading').textContent='Loading '+titles[page][0].toLowerCase()+'…';$('console-loading').hidden=false;
 if(!initial)frame.src=destination;
 let checks=0;state.consoleCheck=setInterval(()=>{if(consoleDocumentReady(frame))return;if(++checks>=100){clearConsoleCheck();$('console-loading').textContent='This view is still loading. Reload it or check Diagnostics.'}},100);
}
async function runDiagnostics(){
 $('diagnostic-checks').setAttribute('aria-busy','true');
 $('diagnostic-help').textContent='Checking services and collection…';
 try{
  if(!state.view)await reloadSettings();
  const d=await api('/api/diagnostics');
  rememberSystem(d.system);renderSystem();
  if(d.collection){const seen=Date.parse(d.collection.latest_log_at);state.evidence={lastLog:seen>0?seen/1000:null,lag:d.collection.processing_lag_seconds??NaN};renderEvidenceBar()}
  $('diagnostic-summary').replaceChildren(metric('Console','Available','Settings and diagnostics'),metric('Monitoring evidence',d.monitoring.status,'Missing samples remain unknown'),metric('Available disk',size(d.system.disk.available),'Read-only measurement'));
  if(d.collection){
   $('diagnostic-summary').append(
    metric('Latest log timestamp',d.collection.latest_log_at?stamp(d.collection.latest_log_at):'Unavailable','Last 24 hours · '+d.collection.log_timestamp_kind),
    metric('Latest FortiGate metric',d.collection.latest_metric_at?stamp(d.collection.latest_metric_at):'Unavailable','A metric sample is not proof of Internet health'),
    metric('Accounting processing lag',d.collection.processing_lag_seconds===null?'Unknown':Number(d.collection.processing_lag_seconds).toLocaleString(undefined,{maximumFractionDigits:1})+' s','No zero is inferred from missing evidence'));
  }
  $('diagnostic-checks').replaceChildren();
  for(const c of d.monitoring.checks){
   const row=E('section',undefined,'panel diagnostic'),title=E('h3',c.id.replaceAll('_',' '));
   title.append(E('span',c.status,'pill '+(c.status==='PASS'?'good':c.status==='FAIL'?'bad':'warn')));
   row.append(title,E('p',c.message));if(c.action)row.append(E('small',c.action));
   $('diagnostic-checks').append(row);
  }
 }catch(e){
  $('diagnostic-summary').replaceChildren(metric('Console','Available','Sign-in is independent of monitoring'),metric('Management helper','Unavailable','Review fortilens-helper on the host'));
  notice(errorText(e),true);
 }finally{
  $('diagnostic-checks').removeAttribute('aria-busy');
  $('diagnostic-help').textContent='Read-only · No firewall changes.';
 }
}
// Deployment lifecycle: completed jobs belong in details, not live progress.
function jobActive(job){return !!job&&!['complete','failed'].includes(job.status)}
function acknowledgedJob(id){try{return JSON.parse(sessionStorage.getItem('fortilens.acknowledgedJobs')||'[]').includes(id)}catch{return false}}
function acknowledgeJob(){if(!state.job||state.job.status!=='failed')return;try{const old=JSON.parse(sessionStorage.getItem('fortilens.acknowledgedJobs')||'[]');sessionStorage.setItem('fortilens.acknowledgedJobs',JSON.stringify([...new Set([...old,state.job.job_id])].slice(-32)))}catch{}$('deployment').hidden=true}
function discoveryBlocked(){return state.discoveryStale||!!(state.view?.discovery?.required&&!state.view.discovery.current)}
function updateDeploymentControls(){
 const busy=state.jobPending||state.jobUnknown||jobActive(state.job);
 for(const id of ['save','prepare','test-connection','discover-interfaces'])$(id).disabled=busy;
 $('apply').disabled=busy||!state.plan||!$('approve-plan').checked||discoveryBlocked();
 $('rollback').disabled=busy;$('history-rollback').disabled=busy;
 $('deployment-acknowledge').hidden=state.job?.status!=='failed'||state.jobUnknown;
}
function renderWorkflowReadiness(){
 if(!state.view)return;
 const d=state.view.discovery,changed=d?.changed_fields||[];
 let message=state.discoveryStale?'Connection or credential settings changed. Save, test the connection and discover interfaces again before applying.':d?.required&&!d.current?(d.reason||'Discover interfaces for the saved connection before applying.'):'Connection discovery is current for the saved settings.';
 if(!d&&!state.discoveryStale)message='Save, test the connection, discover interfaces and confirm Internet interfaces before reviewing changes.';
 if(changed.length)message+=' Changed scope: '+changed.join(', ')+'.';
 $('workflow-readiness').textContent=message;$('apply-readiness').textContent=message;
 $('workflow-readiness').classList.toggle('warn-text',discoveryBlocked());
 $('pending-state').textContent=state.view.pending_changes?'Draft '+state.view.revision+' is saved; active services still use applied revision '+(state.view.applied_revision??'unavailable')+'. Review and apply when ready.':'The saved configuration is applied.';
 updateDeploymentControls();
}
function invalidateDiscovery(){state.discoveryStale=true;renderWorkflowReadiness()}
function renderJobEvents(target,job){
 target.replaceChildren();
 for(const event of job.events||[]){if(!event||typeof event.message!=='string')continue;const at=Number.isFinite(event.at)?stamp(event.at*1000):'Timestamp unavailable';target.append(E('li',at+' · '+event.message))}
}
function renderJobHistory(job){
 if(!job||typeof job.job_id!=='string')return;
 $('deployment-history').hidden=false;
 $('history-summary').textContent='Deployment history · '+(job.status==='complete'?'Last operation completed':job.status==='failed'?'Last operation needs review':'Operation in progress');
 $('history-result').textContent=job.status==='complete'?'Completed':job.status==='failed'?'Failed':job.status;
 $('history-job-id').textContent=job.job_id;
 const at=(job.events||[]).filter(e=>Number.isFinite(e?.at)).at(-1)?.at;
 $('history-time').textContent=at===undefined?'Unavailable':stamp(at*1000);
 $('history-revision').textContent=Number.isInteger(job.applied_revision)?String(job.applied_revision):'Unavailable for this recorded job';
 $('history-requested-revision').textContent=Number.isInteger(job.requested_revision)?String(job.requested_revision):'Unavailable for this recorded job';
 $('history-message').textContent=job.message||'No additional message was recorded.';
 $('history-stage').textContent=job.failed_step||job.phase||'Unavailable';
 renderJobEvents($('history-events'),job);
 $('history-warnings').replaceChildren();
 for(const check of job.diagnostics?.monitoring?.checks||[]){if(check.status!=='PASS')$('history-warnings').append(E('li',check.status+' · '+check.message))}
 $('history-rollback').hidden=!job.rollback_available;
 $('history-rollback').dataset.planId=job.plan_id||'';
 updateDeploymentControls();
}
function restoreLatestJob(job){
 if(!job||typeof job.job_id!=='string')return;
 state.job=job;renderJobHistory(job);
 if(job.status==='complete'){state.jobPending=false;state.jobUnknown=false;$('deployment').hidden=true;updateDeploymentControls();return}
 if(job.status==='failed'&&acknowledgedJob(job.job_id)){$('deployment').hidden=true;updateDeploymentControls();return}
 watchJob(job.job_id).catch(e=>notice(errorText(e),true));
}
async function watchJob(id){
 if(!state.csrf)return;
 clearTimeout(state.jobTimer);state.jobTimer=null;
 try{
  const job=await api('/api/jobs/'+id);if(!state.csrf)return;
  state.job=job;state.jobPending=false;state.jobUnknown=false;renderJobHistory(job);
  $('deployment').hidden=job.status==='complete'||job.status==='failed'&&acknowledgedJob(job.job_id);
  $('deployment-title').textContent=job.status==='failed'?'Deployment needs review':'Applying configuration';
  $('deployment-status').textContent=job.status;$('deployment-message').textContent=(job.failed_step?'Failed stage: '+job.failed_step+'. ':'')+(job.message||'');
  renderJobEvents($('deployment-events'),job);$('rollback').hidden=!job.rollback_available;updateDeploymentControls();
  if(['complete','failed'].includes(job.status)){
   const firstSetup=!state.view.configured;let refreshed=false;
   try{await reloadSettings();refreshed=await loadSystem()}catch(error){
    if(!state.csrf)return;
    if(job.status==='complete'){$('deployment').hidden=true;notice('Configuration applied. Settings or service health could not be refreshed; review Diagnostics before another operation.',true)}
    else notice((job.message||'Deployment failed.')+' Current settings could not be refreshed; review Diagnostics.',true);
    updateDeploymentControls();return;
   }
   if(!state.csrf)return;
   if(job.status==='complete'){
    $('deployment').hidden=true;
    if(firstSetup&&state.view.configured)navigate(defaultPage());
    const success=refreshed===false?'Configuration applied. Current service health could not be refreshed; review Diagnostics.':'Configuration applied successfully. Service status and settings are refreshed; review collection warnings in Diagnostics.';
    notice(success,refreshed===false);
    clearTimeout(state.successTimer);if(refreshed!==false)state.successTimer=setTimeout(()=>{if($('notice').textContent===success&&!$('notice').classList.contains('error'))notice('')},8000);
   }else if(!acknowledgedJob(job.job_id))notice(job.message,true);
   return;
  }
  state.jobTimer=setTimeout(()=>watchJob(id).catch(e=>notice(errorText(e),true)),1200);
 }catch(error){
  state.jobUnknown=true;state.jobPending=false;updateDeploymentControls();
  $('deployment').hidden=false;$('deployment-title').textContent='Deployment status unavailable';$('deployment-status').textContent='Unknown';
  $('deployment-message').textContent=errorText(error)+' Deployment may still be running. Refresh deployment status and review Diagnostics before repeating Apply or rollback.';
  notice(errorText(error),true);
 }
}
async function beginDeployment(path,body){
 if(state.jobPending||state.jobUnknown||jobActive(state.job))throw safeError('An operation may still be running. Refresh deployment status before trying again.');
 state.jobPending=true;updateDeploymentControls();
 try{const job=await api(path,body);state.job=job;state.jobPending=false;updateDeploymentControls();await watchJob(job.job_id)}
 catch(error){state.jobPending=false;if(['network','timeout','invalid_response','internal_error','operation_failed'].includes(error.code))state.jobUnknown=true;updateDeploymentControls();throw error}
}
// End deployment lifecycle.
$('auth-form').addEventListener('submit',e=>{e.preventDefault();action($('auth-submit'),async()=>{const username=$('username').value,password=$('password').value;$('auth-error').textContent='';try{if(state.bootstrap){await api('/api/bootstrap',{username,password,confirmation:$('confirmation').value,bootstrap_token:$('bootstrap-token').value});state.bootstrap=false;$('bootstrap-fields').hidden=true;$('confirmation-field').hidden=true}$('auth-form').reset();const r=await api('/api/login',{username,password});state.csrf=r.csrf;await enter()}catch(error){$('password').value='';$('confirmation').value='';$('bootstrap-token').value='';$('auth-error').textContent=errorText(error)}})});
$('signout').addEventListener('click',()=>action($('signout'),async()=>{await api('/api/logout',{});signedOut()}));
$('configuration-form').addEventListener('submit',e=>{e.preventDefault();action($('save'),saveDraft)});
$('settings-section-picker').addEventListener('change',()=>selectSection($('settings-section-picker').value));
for(const b of document.querySelectorAll('[data-section]'))b.addEventListener('click',()=>selectSection(b.dataset.section));
$('next-section').addEventListener('click',()=>selectSection(sections[Math.min(sections.length-1,sections.indexOf(state.section)+1)]));$('previous-section').addEventListener('click',()=>selectSection(sections[Math.max(0,sections.indexOf(state.section)-1)]));

for(const input of document.querySelectorAll('[data-setting]'))for(const type of ['input','change'])input.addEventListener(type,event=>settingChanged(input,event));
for(const input of document.querySelectorAll('[data-optional-probe]'))input.addEventListener('change',event=>{
 if(!intentionalDraftEdit(input,event))return;
 const selected=new Set(state.draftSettings.optional_exporter_exclusions??state.view.exporter_policy?.optional_exclusions??[]);
 if(input.checked)selected.add(input.dataset.optionalProbe);else selected.delete(input.dataset.optionalProbe);
 state.draftSettings.optional_exporter_exclusions=[...selected];dirty();
});
for(const id of ['api-token','custom-ca','replace-token','replace-ca','insecure-ack'])for(const type of ['input','change'])$(id).addEventListener(type,event=>{const input=$(id);if(!intentionalDraftEdit(input,event))return;if(id==='api-token')state.apiTokenDraft=input.value;if(id==='custom-ca')state.customCADraft=input.value;dirty();connectionDraftChanged()});
$('connection-secrets-form').addEventListener('submit',event=>{event.preventDefault();action($('save'),saveDraft)});
$('test-connection').addEventListener('click',()=>action($('test-connection'),async()=>{$('connection-result').textContent='Testing saved connection…';await saveDraft();const d=await api('/api/connection/test',{revision:state.view.revision});$('connection-result').textContent=d.message+(Object.keys(d.device).length?' '+Object.values(d.device).join(' · '):'')}));
$('discover-interfaces').addEventListener('click',()=>action($('discover-interfaces'),async()=>{state.inventory=[];renderInterfaces();$('inventory-caption').textContent='Discovery in progress. Previous selections require confirmation against this connection.';await saveDraft();const report=await api('/api/interfaces/discover',{revision:state.view.revision});await reloadSettings();state.discoveryStale=false;state.inventory=report.interfaces;renderInterfaces();renderWorkflowReadiness();$('inventory-caption').textContent=report.coverage+' · '+report.notes.join(' ')}));$('interface-search').addEventListener('input',renderInterfaces);
$('prepare').addEventListener('click',()=>action($('prepare'),async()=>{await saveDraft();state.plan=await api('/api/configuration/prepare',{revision:state.view.revision});$('plan-files').replaceChildren(...state.plan.changed_files.map(p=>E('li',p)));$('plan-warnings').replaceChildren(...state.plan.warnings.map(p=>E('li',p)));$('plan-review').hidden=false;$('approve-plan').checked=false;if(state.plan.discovery)state.view.discovery=state.plan.discovery;renderWorkflowReadiness()}));
$('apply').addEventListener('click',()=>action($('apply'),async()=>{if(!state.plan||!$('approve-plan').checked)throw safeError('Review the candidate and approve its changes first.');if(discoveryBlocked())throw safeError('Save, test and discover interfaces for this connection before applying.');await beginDeployment('/api/configuration/apply',{revision:state.plan.revision,plan_id:state.plan.plan_id,confirm:true})}));
$('rollback').addEventListener('click',()=>action($('rollback'),async()=>{if(!state.job)throw safeError('Choose a deployment with a retained configuration backup.');await beginDeployment('/api/configuration/rollback',{revision:state.view.revision,plan_id:state.job.plan_id,confirm:true})}));
$('approve-plan').addEventListener('change',updateDeploymentControls);
$('deployment-acknowledge').addEventListener('click',acknowledgeJob);
$('deployment-refresh').addEventListener('click',()=>action($('deployment-refresh'),async()=>{await loadSystem();const latest=state.system?.latest_job;if(latest){state.jobUnknown=false;restoreLatestJob(latest)}else notice('No deployment history was returned. Review Diagnostics before retrying.',true)}));
$('history-lookup-form').addEventListener('submit',e=>{e.preventDefault();action($('history-lookup'),async()=>{const id=$('history-lookup-id').value.trim();if(!/^[a-f0-9]{32}$/.test(id))throw safeError('Enter a recorded 32-character deployment ID.');const job=await api('/api/jobs/'+id);renderJobHistory(job)})});
$('history-rollback').addEventListener('click',()=>action($('history-rollback'),async()=>{const plan=$('history-rollback').dataset.planId;if(!plan)throw safeError('Choose a deployment with a retained configuration backup.');await beginDeployment('/api/configuration/rollback',{revision:state.view.revision,plan_id:plan,confirm:true})}));
$('password-form').addEventListener('submit',e=>{e.preventDefault();action($('password-form').querySelector('button'),async()=>{await api('/api/password',{current:$('current-password').value,password:$('new-password').value,confirmation:$('confirm-password').value});signedOut();$('auth-caption').textContent='Password changed. Sign in with your new password.'})});
$('refresh-system').addEventListener('click',()=>action($('refresh-system'),loadSystem));$('run-diagnostics').addEventListener('click',()=>action($('run-diagnostics'),runDiagnostics));$('reload-console').addEventListener('click',()=>loadConsole(location.hash.slice(1)));
window.addEventListener('hashchange',()=>{if(state.csrf)navigate(location.hash.slice(1))});
// Overview: a plain status line and channels drawn only from received evidence.
const reportBytes=n=>n===null||n===undefined||!Number.isFinite(Number(n))?'Unknown':Number(n)>=1e12?(Number(n)/1e12).toFixed(2)+' TB':Number(n)>=1e9?(Number(n)/1e9).toFixed(2)+' GB':Number(n)>=1e6?(Number(n)/1e6).toFixed(2)+' MB':Number(n)>=1e3?(Number(n)/1e3).toFixed(2)+' KB':Number(n)+' B';
const reading=(n,suffix='')=>n===null||n===undefined||!Number.isFinite(Number(n))?'No data':Number(n).toLocaleString(undefined,{maximumFractionDigits:1})+suffix;
const stateClass=s=>s==='UP'?'up':s==='DOWN'?'down':s==='ERROR'?'degraded':'unknown';
const stateWord=s=>s==='UP'?'Up':s==='DOWN'?'Down':s==='ERROR'?'Error':'No data';
function marker(s){return E('span',stateWord(s),'st '+stateClass(s))}
function segmentName(v){return v?.status==='MAPPED'?'VLAN '+v.vlan_id:v?.status==='NOT_VLAN'?'Interface segment':'Unknown mapping'}
function clockFormat(seconds,withDate){const tz=state.view?.settings.timezone||'UTC';try{return new Intl.DateTimeFormat('en-GB',{timeZone:tz,hourCycle:'h23',hour:'2-digit',minute:'2-digit',...(withDate?{month:'short',day:'2-digit'}:{})}).format(new Date(seconds*1000))}catch{return 'Time unavailable'}}
function homeStatus(kind,head,sub,action,label){const root=$('home-status');root.className='stat '+kind;$('home-head').textContent=head;$('home-sub').textContent=sub;const link=$('home-action');link.hidden=!action;if(action){link.href='#'+action;link.textContent=label}}
function renderHomeStatus(wan,report,links){
 const wans=wan?.interfaces||[],now=serverNow(),lastLog=state.evidence?.lastLog,accounting=state.view?.active_monitoring?.accounting!==false,age=lastLog?now-lastLog:null;
 const failing=wans.filter(w=>w.internet_state==='DOWN'),errored=wans.filter(w=>w.internet_state==='ERROR');
 if(!links?.console)return homeStatus('is-u','This deployment keeps separate service sign-in.','Live evidence opens in the existing services until the unified-console migration is complete. Nothing here is treated as an outage.','diagnostics','Open diagnostics');
 if(failing.length){const w=failing[0],more=failing.length>1?' '+(failing.length-1)+' more failing.':'';return homeStatus('is-w',w.interface+' is failing its Internet SLA.','Latency '+reading(w.latency_ms,' ms')+', loss '+reading(w.packet_loss_pct,'%')+'. '+(w.physical_state==='UP'?'The physical link is up, so look upstream first.':w.physical_state==='DOWN'?'The physical link is also down.':'Physical link evidence is unavailable.')+more,'wan','Inspect '+w.interface)}
 if(errored.length)return homeStatus('is-w',errored[0].interface+' reports an SLA error.','The check could not produce a healthy result. Review the observed history before drawing conclusions.','wan','Inspect '+errored[0].interface);
 if(accounting&&report&&(!lastLog||age>900))return homeStatus('is-u',lastLog?'No logs received for '+agoText(age).replace(' ago','')+'.':'No logs have been received yet.','Usage may read low. That does not prove zero traffic or an outage.','quality','Find where it stopped');
 if(!wan||!wans.length||wans.some(w=>stateClass(w.internet_state)==='unknown')||(accounting&&!report))return homeStatus('is-u','Some evidence is unavailable.','Missing samples stay unknown. They are not an outage and not zero traffic.','diagnostics','Open diagnostics');
 homeStatus('is-g','All links pass'+(accounting?' and logs are current.':'.'),wans.map(w=>w.interface+' '+reading(w.latency_ms,' ms')).join(', ')+'.'+(lastLog?' Last log '+agoText(age)+'.':''));
}
function drawChannels(wan){
 const root=$('home-channels'),axis=$('home-axis');root.replaceChildren();axis.replaceChildren();state.home=null;$('home-cursor').textContent='';
 if(!wan||!wan.interfaces.length){root.append(E('p',wan?'No current WAN evidence. Missing collection is not an outage.':'WAN evidence is unavailable. No outage is inferred.','empty'));return}
 const end=wan.observed_at,seconds=Number($('home-window').value)||86400,start=end-seconds;state.home={start,end,wans:wan.interfaces};
 for(const w of wan.interfaces){
  const row=E('div',undefined,'ch'),label=E('div',undefined,'ch-l'),track=E('div',undefined,'tr'),lane=E('div',undefined,'lane');
  label.append(E('strong',w.interface),marker(w.internet_state),E('span',reading(w.latency_ms,' ms')+' · '+reading(w.packet_loss_pct,'%')+' loss','ch-v'),E('span','Physical '+stateWord(w.physical_state).toLowerCase(),'ch-v'));
  lane.setAttribute('role','img');
  lane.setAttribute('aria-label',w.interface+' observed Internet state. Observed up '+reading(w.history?.availability_percent,'%')+', valid coverage '+reading(w.history?.valid_state_coverage_percent,'%')+'. Uncovered time is unknown.');
  for(const s of w.history?.segments||[]){
   const from=Math.max(start,s.start),to=Math.min(end,s.end);if(!(to>from))continue;
   const part=E('i',undefined,s.degraded?'degraded':stateClass(s.state));part.style.left=(from-start)/seconds*100+'%';part.style.width=(to-from)/seconds*100+'%';lane.append(part);
  }
  const hair=E('div',undefined,'xh');hair.hidden=true;track.append(lane,hair);row.append(label,track);root.append(row);
 }
 const ticks=E('div',undefined,'xticks');for(let i=0;i<5;i++)ticks.append(E('span',clockFormat(start+seconds*i/4,seconds>86400)));
 axis.append(E('span','Server timezone','xlab'),ticks);
}
function setCrosshair(fraction){
 const h=state.home;if(!h)return;
 for(const hair of document.querySelectorAll('#home-channels .xh')){hair.hidden=fraction===null;if(fraction!==null)hair.style.left=fraction*100+'%'}
 if(fraction===null){$('home-cursor').textContent='';state.homeCursor=null;return}
 state.homeCursor=fraction;const at=h.start+(h.end-h.start)*fraction;
 $('home-cursor').textContent=clockFormat(at,h.end-h.start>86400)+' · '+h.wans.map(w=>{const s=(w.history?.segments||[]).find(x=>x.start<=at&&at<x.end);return w.interface+' '+(!s?'no evidence':s.degraded?'degraded':s.state==='UP'?'up':s.state==='DOWN'?'down':'no evidence')}).join(' · ');
}
function renderHomeWan(wan){
 const body=$('home-wan-body');body.replaceChildren();$('home-wan').hidden=!wan?.interfaces?.length;
 $('home-route').textContent=wan?(wan.route?.status==='OBSERVED'?'Observed route to '+wan.route.destination+': '+wan.route.interface+'.':'Route selection is unknown. Link health alone does not identify the active route.'):'';
 for(const w of wan?.interfaces||[]){const tr=E('tr'),sla=E('td'),phys=E('td');sla.append(marker(w.internet_state));phys.append(marker(w.physical_state));tr.append(E('td',w.interface),sla,phys,E('td',reading(w.download_mbps)+' / '+reading(w.upload_mbps),'r'),E('td',reading(w.latency_ms)+' / '+reading(w.jitter_ms,' ms'),'r'),E('td',reading(w.packet_loss_pct,'%'),'r'),E('td',reading(w.history?.availability_percent,'%'),'r'));['Link','Internet SLA','Physical','Down / Up Mbps','Latency / Jitter','Loss','Observed up'].forEach((name,i)=>{tr.children[i].dataset.label=name});body.append(tr)}
}
function renderHomeUsage(report,failed){
 const fields=[['Recorded today',report?reportBytes((report.totals.upload_bytes??NaN)+(report.totals.download_bytes??NaN)):'Unknown'],['Identities',report&&report.devices.length?reading(report.observed_endpoint_identities):'Unknown'],['Pending, not added',report?reportBytes((report.totals.pending_upload_bytes||0)+(report.totals.pending_download_bytes||0)):'Unknown']];
 $('home-fields').replaceChildren(...fields.map(([k,v])=>{const d=E('div');d.append(E('dt',k),E('dd',v));return d}));
 const consumers=$('home-consumers'),segments=$('home-segments');consumers.replaceChildren();segments.replaceChildren();
 const none=failed?'Usage evidence is unavailable. No zero is inferred.':'No received accounting evidence for today. This is not proof of zero traffic.';
 const devices=report?.devices||[],top=Number(devices[0]?.total_bytes)||1;
 if(!devices.length)consumers.append(E('li',none,'help'));
 for(const d of devices.slice(0,8)){
  const li=E('li',undefined,'rank'),line=E('div',undefined,'kv-line'),name=E('a',d.hostname||d.ip||'Unidentified endpoint','ent');name.href='#devices';
  const who=E('span');who.append(name);if(d.identity_kind!=='MAC')who.append(' ',E('span','Unverified','flag'));
  line.append(who,E('span',reportBytes(d.total_bytes)));
  const bar=E('div',undefined,'bar'),down=E('i',undefined,'d'),up=E('i',undefined,'u');bar.style.width=Math.max(3,Number(d.total_bytes)/top*100)+'%';down.style.flexGrow=Number(d.download_bytes)||0;up.style.flexGrow=Number(d.upload_bytes)||0;bar.setAttribute('aria-hidden','true');bar.append(down,up);
  li.append(line,E('span',(d.vlans||[]).map(v=>segmentName(v)+', '+v.interface).join(' · ')||d.ip||'','ident'),bar);consumers.append(li);
 }
 const vlans=report?.vlans||[],big=Math.max(1,...vlans.map(v=>Number(v.total_bytes)||0));
 if(!vlans.length)segments.append(E('li',failed?'Segment evidence is unavailable.':'No received segment totals are available.','help'));
 for(const v of vlans){const li=E('li',undefined,'rank'),line=E('div',undefined,'kv-line'),bar=E('div',undefined,'bar'),fill=E('i',undefined,'d');line.append(E('span',segmentName(v.vlan)+', '+v.vlan.interface,'muted'),E('span',reportBytes(v.total_bytes)));fill.style.flexGrow=1;bar.style.width=Math.max(2,Number(v.total_bytes)/big*100)+'%';bar.setAttribute('aria-hidden','true');bar.append(fill);li.append(line,bar);segments.append(li)}
}
function renderHomeServices(){
 const s=state.system,root=$('home-services');root.replaceChildren();
 if(!s){root.append(E('li','Service state is unavailable. Monitoring may still be running.','help'));return}
 const add=(k,v)=>{const li=E('li',undefined,'kv-line');li.append(E('span',k,'muted'),E('span',v));root.append(li)};
 const enabled=s.services.filter(r=>r.status!=='disabled');
 add('Running services',enabled.filter(r=>r.status==='running').length+' of '+enabled.length);add('Available storage',size(s.disk.available));add('Configuration',state.view?.pending_changes?'Draft '+state.view.revision+' pending':state.view?.configured?'Revision '+state.view.applied_revision:'Not configured');
}
async function loadHome(){
 const sequence=state.homeSequence=(state.homeSequence||0)+1;clearTimeout(state.homeTimer);$('home-refresh').disabled=true;$('home-channels').setAttribute('aria-busy','true');
 await loadSystem();if(sequence!==state.homeSequence||!state.csrf||state.page!=='home')return;
 const links=state.system?.links,flags=state.view?.active_monitoring,connected=!!links?.console;
 const [wanResult,reportResult]=await Promise.allSettled([connected&&flags?.wan!==false?api('/console/api/wan?seconds='+encodeURIComponent($('home-window').value)):null,connected&&flags?.accounting!==false?api('/console/api/report?period=today&search=&vlan='):null]);
 if(sequence!==state.homeSequence||!state.csrf||state.page!=='home')return;
 const wan=wanResult.status==='fulfilled'&&Array.isArray(wanResult.value?.interfaces)&&typeof wanResult.value.observed_at==='number'?wanResult.value:null;
 const usable=r=>r&&typeof r.totals==='object'&&r.totals&&Array.isArray(r.devices)&&Array.isArray(r.vlans);
 const report=reportResult.status==='fulfilled'&&usable(reportResult.value)?reportResult.value:null;
 const delivery=Number(report?.processing?.last_delivery_ns);state.evidence={lastLog:delivery>0?delivery/1e9:null,lag:report?.processing?.processing_lag_seconds??NaN};renderEvidenceBar();
 renderHomeStatus(wan,report,links);drawChannels(wan);renderHomeWan(wan);renderHomeUsage(report,reportResult.status==='rejected');renderHomeServices();
 if(state.homeCursor!==null&&state.homeCursor!==undefined)setCrosshair(state.homeCursor);
 $('home-refresh').disabled=false;$('home-channels').removeAttribute('aria-busy');
 state.homeTimer=setTimeout(()=>{if(state.page==='home'&&state.csrf&&!document.hidden&&!$('workspace').hidden)loadHome()},60000);
}
$('home-refresh').addEventListener('click',()=>loadHome());$('home-window').addEventListener('change',()=>{state.homeCursor=null;loadHome()});
$('home-channels').addEventListener('pointermove',e=>{const track=e.target.closest('.tr');if(!track)return;const box=track.getBoundingClientRect();setCrosshair(Math.min(1,Math.max(0,(e.clientX-box.left)/box.width)))});
$('home-channels').addEventListener('pointerleave',()=>setCrosshair(null));
$('home-channels').addEventListener('keydown',e=>{if(!state.home)return;const step=e.key==='ArrowLeft'?-1/48:e.key==='ArrowRight'?1/48:0;if(step){e.preventDefault();setCrosshair(Math.min(1,Math.max(0,(state.homeCursor??1)+step)))}else if(e.key==='Escape')setCrosshair(null)});
// Evidence status bar: every value is received evidence; absent values stay unknown.
function serverNow(){const t=state.system?.server_time_seconds;return typeof t==='number'&&Number.isFinite(t)?t+(performance.now()-(state.systemAt||0))/1000:Date.now()/1000}
function agoText(seconds){if(!Number.isFinite(seconds)||seconds<0)return 'time unknown';if(seconds<90)return Math.round(seconds)+' seconds ago';if(seconds<5400)return Math.round(seconds/60)+' minutes ago';if(seconds<172800)return Math.round(seconds/3600)+' hours ago';return Math.round(seconds/86400)+' days ago'}
function renderEvidenceBar(){
 const s=state.system,v=state.view,e=state.evidence||{};
 const logs=$('evidence-logs');logs.textContent=e.lastLog?'Logs: '+agoText(serverNow()-e.lastLog):state.evidence?'Logs: none received':'Logs: open Evidence to check';logs.classList.toggle('attention',!!state.evidence&&(!e.lastLog||serverNow()-e.lastLog>900));
 $('evidence-lag').hidden=!Number.isFinite(e.lag);if(Number.isFinite(e.lag))$('evidence-lag').textContent='Processing lag: '+Number(e.lag).toLocaleString(undefined,{maximumFractionDigits:1})+' s';
 if(s){const running=s.services.filter(r=>r.status==='running').length,enabled=s.services.filter(r=>r.status!=='disabled').length;$('evidence-services').textContent='Services: '+running+' of '+enabled+' running';$('evidence-services').classList.toggle('attention',running<enabled)}else{$('evidence-services').textContent='Services: unknown';$('evidence-services').classList.add('attention')}
 $('evidence-revision').textContent=v?(v.pending_changes?'Draft '+v.revision+' · Applied '+(v.applied_revision??'none'):v.configured?'Applied revision '+v.applied_revision:'Not configured'):'';
 const grafana=$('native-grafana');let href=null;
 if(s?.links?.console&&typeof s.links.dashboard==='string'&&/^\/grafana\/d\/[A-Za-z0-9_-]+\//.test(s.links.dashboard))href=s.links.dashboard.split('?')[0];
 else if(s?.links?.grafana_port)href='http://'+location.hostname+':'+s.links.grafana_port+(s.links.grafana_prefix||'/');
 grafana.hidden=!href||!pageEnabled('monitoring');if(href)grafana.href=href;
}
window.__fortilensEvidence=(lastLog,lag)=>{state.evidence={lastLog:lastLog>0?lastLog:null,lag:Number.isFinite(lag)?lag:NaN};renderEvidenceBar()};
// Command palette: pages and actions only, built from text nodes.
function paletteItems(){const items=[...document.querySelectorAll('#workspace-nav nav a[data-page]')].filter(a=>!a.hidden).map(a=>({label:titles[a.dataset.page][0],hint:pageGroups[a.dataset.page],run:()=>{location.hash=a.dataset.page}}));items.push({label:document.documentElement.dataset.theme==='dark'?'Use light theme':'Use dark theme',hint:'Action',run:toggleTheme},{label:'Run read-only diagnostics',hint:'Action',run:()=>{location.hash='diagnostics'}});if(!$('native-grafana').hidden)items.push({label:'Open native Grafana',hint:'New tab',run:()=>window.open($('native-grafana').href,'_blank','noopener')});return items}
function renderPalette(){const query=$('palette-input').value.trim().toLowerCase(),list=$('palette-list');state.paletteMatches=paletteItems().filter(x=>(x.label+' '+x.hint).toLowerCase().includes(query));state.paletteIndex=Math.min(state.paletteIndex||0,Math.max(0,state.paletteMatches.length-1));list.replaceChildren(...state.paletteMatches.map((item,i)=>{const li=E('li',undefined,i===state.paletteIndex?'current':''),b=E('button',undefined);b.type='button';b.append(E('span',item.label),E('small',item.hint));b.addEventListener('click',()=>runPalette(i));li.append(b);return li}));if(!state.paletteMatches.length)list.append(E('li','No matching pages or actions.','help'))}
function runPalette(index){const item=state.paletteMatches?.[index];if(!item)return;$('palette').close();item.run()}
function openPalette(){if($('palette').open||$('workspace').hidden)return;$('palette-input').value='';state.paletteIndex=0;renderPalette();$('palette').showModal();$('palette-input').focus()}
$('palette-open').addEventListener('click',openPalette);$('palette-input').addEventListener('input',()=>{state.paletteIndex=0;renderPalette()});
$('palette-input').addEventListener('keydown',e=>{const count=state.paletteMatches?.length||0;if(e.key==='ArrowDown'&&count){e.preventDefault();state.paletteIndex=(state.paletteIndex+1)%count;renderPalette()}else if(e.key==='ArrowUp'&&count){e.preventDefault();state.paletteIndex=(state.paletteIndex-1+count)%count;renderPalette()}else if(e.key==='Enter'){e.preventDefault();runPalette(state.paletteIndex)}});
window.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'&&!e.altKey&&!e.shiftKey){e.preventDefault();openPalette()}});
// Local SVG navigation icons; no trackers, CDN or external assets.
for(const node of document.querySelectorAll('.nav-icon')){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');const p=document.createElementNS(svg.namespaceURI,'path');p.setAttribute('d',({home:'M3 12h4l2.500-7 4 14 2.500-7h5',wan:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18',devices:'M5 20v-9M12 20V4M19 20v-6',quality:'M12 3l8 3v6c0 5-3.500 8-8 9-4.500-1-8-4-8-9V6zM8.500 12l2.500 2.500 4.500-5',overview:'M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01',settings:'M4 7h10M18 7h2M4 17h2M10 17h10M16 5a2 2 0 1 0 0 4 2 2 0 0 0 0-4M8 15a2 2 0 1 0 0 4 2 2 0 0 0 0-4'})[node.parentElement.dataset.page]||'M4 5h16v14H4z');p.setAttribute('fill','none');p.setAttribute('stroke','currentColor');p.setAttribute('stroke-width','1.6');p.setAttribute('stroke-linecap','round');p.setAttribute('stroke-linejoin','round');svg.append(p);node.replaceChildren(svg)}
(async()=>{try{const b=await api('/api/bootstrap/status');state.bootstrap=b.required;$('bootstrap-fields').hidden=!b.required;$('confirmation-field').hidden=!b.required;if(b.required){$('auth-title').textContent='Your workspace starts here.';$('auth-caption').textContent='Create the first administrator using the one-time installation token.';$('auth-submit').textContent='Create administrator';$('password').autocomplete='new-password'}else{try{const s=await api('/api/session');state.csrf=s.csrf;await enter()}catch(e){if(e.code!=='authentication_required')$('auth-error').textContent=errorText(e)}}}catch(e){$('auth-error').textContent=errorText(e)}})();
