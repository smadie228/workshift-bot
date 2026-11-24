/* Real Chromium UI checks, only fictional fixtures; no production tokens or data. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const puppeteer=require(process.env.PUPPETEER_MODULE||'puppeteer');
const root=path.resolve(__dirname,'..');
const output=process.env.BROWSER_OUTPUT||path.join(require('node:os').tmpdir(),'workshift-browser-'+process.pid);
fs.mkdirSync(output,{recursive:true});
let scenario='normal', actions=0, reads=[], workspaceReads=0, mode='demo', operationResult=null;
let mockSecurity={enabled:false,pin:"",session:"fixture-unlock-token",recovery:"a".repeat(32),bio:null};
const errors=[];let checks=0;let planRevision='a'.repeat(32);
const csp="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'self' https://web.telegram.org https://*.web.telegram.org";
function agendaFixture(){
 const today=new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const days=Array.from({length:7},(_,i)=>{const d=new Date(today+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+i);return {day:d.toISOString().slice(0,10),status:i===1?'planned':'unspecified',plan:i===1?{status:'shift',start:'16:00',end:'02:00'}:null,start:i===1?'16:00':null,end:i===1?'02:00':null,next_day:i===1};});
 return {today,days,current_plan:null,next_shift:days[1],search_days:90,counts:{work:1,off:0,unspecified:6}};
}
function fixture(month){
 const active=['active','break','write','uncertain','offline','long-active'].includes(scenario);
 const paused=scenario==='break';
 return {month,agenda:agendaFixture(),plan_editor:{mode:'chat_confirmation',direct_writes:scenario==='offline',bot_username:'FictionalShiftBot',supports_past_plan:true,supports_exact_correction:true,supports_batch:true,supports_unset:true,batch_revision:planRevision.slice(0,24),revisions:Object.fromEntries(Array.from({length:31},(_,i)=>[month+'-'+String(i+1).padStart(2,'0'),planRevision]))},now:new Date().toISOString(),summary:{hours:36.75,shifts:4,earned:10290,total:10790,received:scenario==='no-money'?0:6000,additional:{bonus:400,allowance:0,reimbursement:200,deduction:100,net_adjustment:500}},
  active:active?{id:11,start:new Date(Date.now()-(scenario==='long-active'?15*3600000:7200000)).toISOString()}:null,
  break:{active,on_break:paused,tracked:true,minutes:0,break_id:paused?12:null,break_started_utc:paused?new Date(Date.now()-600000).toISOString():null},
  shifts:[{id:9,day:month+'-10',start:'16:00',end:'02:00',next_day:true,break_minutes:0,hours:10,earnings:2800,source:'button',revision:'a'.repeat(64)}],
  actual_editor:{default_break_minutes:30,revisions:{[month+'-10']:'a'.repeat(64)}},attendance_revision:'b'.repeat(64),
  attendance_hints:{active_revision:'c'.repeat(64),in:{ask_time:false,difference_minutes:0},out:{ask_time:true,difference_minutes:300,planned_at:new Date(Date.now()-3600000).toISOString()}},
  plan:[...(scenario==='history'?[{day:month+'-10',status:'off',source:'manual'}]:[]),{day:month+'-18',status:'shift',start:'16:00',end:'02:00',source:'manual'},{day:month+'-19',status:'off',source:'manual'}],
  office:{},office_status:{confirmed_days:0,complete:false},issues:[],
  periods:[{period:1,hours:20,earnings:5600,shifts:2},{period:2,hours:16.75,earnings:4690,shifts:2}],
  weeks:[{label:'01.01',hours:0},{label:'08.01',hours:20},{label:'15.01',hours:15},{label:'22.01',hours:30},{label:'29.01',hours:25},{label:'05.02',hours:10}],
  capabilities:{writes:['write','uncertain','offline'].includes(scenario),actual_edits:scenario==='offline',attendance_recovery:scenario==='offline',offline_submission:false},timezone:'Europe/Moscow'};
}
const server=http.createServer(async(req,res)=>{
 const route=new URL(req.url,'http://localhost').pathname;
 const json=(status,value)=>{if(res.destroyed)return;res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
 if(route.startsWith('/mini/api/')){
  let body='';for await(const chunk of req)body+=chunk;
  const value=JSON.parse(body||'{}');
  if(route==='/mini/api/workspace'&&value.view==='security'){
   if(value.action==='status')return json(200,{enabled:mockSecurity.enabled,
      unlocked:!mockSecurity.enabled||req.headers['x-workshift-unlock']===mockSecurity.session,
      biometric_enrolled:Boolean(mockSecurity.bio)});
   if(value.action==='setup'&&!mockSecurity.enabled){
    if(!/^[0-9]{6}$/.test(value.pin))return json(400,{error:'weak_pin'});
    mockSecurity.enabled=true;mockSecurity.pin=value.pin;
    return json(200,{enabled:true,session:mockSecurity.session,recovery_code:mockSecurity.recovery});
   }
   if(!mockSecurity.enabled)return json(409,{error:'not_enabled'});
   if(value.action==='unlock'){
    if(value.method==='pin'&&value.pin===mockSecurity.pin)return json(200,{session:mockSecurity.session});
    if(value.method==='biometric'&&mockSecurity.bio?.device_id===value.device_id&&mockSecurity.bio?.token===value.token)
      return json(200,{session:mockSecurity.session});
    if(value.method==='recovery'&&value.recovery===mockSecurity.recovery)
      return json(200,{session:mockSecurity.session,recovery_code:'b'.repeat(32)});
    return json(403,{error:'invalid_credential'});
   }
   if(req.headers['x-workshift-unlock']!==mockSecurity.session)return json(423,{error:'security_locked'});
   if(value.action==='biometric_add'){mockSecurity.bio={device_id:value.device_id,token:value.token};return json(200,{biometric_enrolled:true});}
   if(value.action==='biometric_remove'){mockSecurity.bio=null;return json(200,{biometric_enrolled:false});}
   if(value.action==='change_pin'){
    if(value.old_pin!==mockSecurity.pin)return json(403,{error:'invalid_credential'});
    mockSecurity.pin=value.new_pin;mockSecurity.bio=null;
    return json(200,{session:mockSecurity.session,recovery_code:'c'.repeat(32)});
   }
   if(value.action==='disable'&&value.pin===mockSecurity.pin){
    mockSecurity.enabled=false;mockSecurity.pin='';mockSecurity.bio=null;
    return json(200,{enabled:false});
   }
   if(value.action==='lock')return json(200,{locked:true});
   return json(403,{error:'invalid_credential'});
  }
  if(mockSecurity.enabled&&req.headers['x-workshift-unlock']!==mockSecurity.session)
    return json(423,{error:'security_locked'});
  if(route==='/mini/api/workspace'&&value.view==='payment_history'){
   workspaceReads++;
   return json(200,{payments:[
    {work_month:'2026-11',paid_on:'2026-11-20',period:2,amount_rub:9500,note:'Зарплата ноябрь'},
    {work_month:'2026-11',paid_on:'2026-11-15',period:1,amount_rub:8000,note:'Аванс ноябрь'},
    {work_month:'2026-10',paid_on:'2026-11-05',period:1,amount_rub:5000,note:'Аванс октябрь'},
    {work_month:'2026-09',paid_on:'2026-10-05',period:1,amount_rub:4500,note:'Сентябрь'}
   ],has_more:false});
  }
  if(route==='/mini/api/analytics'){
   if(scenario==='denied')return json(403,{error:'access_denied'});
   const months=Array.from({length:12},(_,i)=>({month:value.year+'-'+String(i+1).padStart(2,'0'),hours:i===6?20:0,shifts:i===6?2:0,earned:i===6?5600:0,total:i===6?5600:0,received:0}));
   return json(200,{year:value.year,months,hours:20,shifts:2,earned:5600,total:5600,received:0});
  }
  if(route==='/mini/api/state'){
   reads.push(value.month);
   if(scenario==='denied')return json(403,{error:'access_denied'});
   if(scenario==='failed')return json(503,{error:'Test connection unavailable'});
   const data=fixture(value.month);
   if(scenario==='slow')await new Promise(r=>setTimeout(r,value.month.endsWith('-12')?300:80));
   return json(200,data);
  }
  if(route==='/mini/api/action'){
   actions++;
   if(scenario==='uncertain'&&actions===1){operationResult=value;return json(500,{error:'Test response unavailable after commit'});}
   if(operationResult)assert.deepEqual(value,operationResult,'uncertain request must keep identical nonce and expected ids');
   assert.equal(value.shift_id,11,'action tied to the visible shift');
   return json(200,{success:true,event:value.action,id:11});
  }
  return json(404,{error:'not_found'});
 }
 const files={'/mini/':['index.html','text/html; charset=utf-8'],'/mini/app.js':['app.js','application/javascript; charset=utf-8'],'/mini/app.css':['app.css','text/css; charset=utf-8'],'/mini/logo.svg':['logo.svg','image/svg+xml'],'/mini/echarts.min.js':['vendor/echarts.min.js','application/javascript; charset=utf-8'],'/mini/telegram-web-app.js':['vendor/telegram-web-app.js','application/javascript; charset=utf-8']};
 if(!files[route]){res.writeHead(404);res.end();return;}
 const [file,type]=files[route];res.writeHead(200,{'Content-Type':type,'Content-Security-Policy':csp,'Cache-Control':'no-store'});res.end(fs.readFileSync(path.join(root,'miniapp',file)));
});
const sdk=`window.Telegram={WebApp:{initData:window.__signed?'fictional-ui-fixture':'',colorScheme:'dark',ready(){},expand(){},openTelegramLink(url){window.__links=(window.__links||[]);window.__links.push(url);},close(){window.__closed=(window.__closed||0)+1;},setHeaderColor(){},setBackgroundColor(){},HapticFeedback:{notificationOccurred(){},selectionChanged(){}},BiometricManager:{isInited:true,isBiometricAvailable:true,isAccessGranted:true,isBiometricTokenSaved:false,deviceId:'fixture-phone-0042',saved:'',init(cb){cb?.();},requestAccess(p,cb){this.isAccessGranted=true;cb(true);},updateBiometricToken(token,cb){this.saved=token;this.isBiometricTokenSaved=Boolean(token);cb(true);},authenticate(p,cb){cb(Boolean(this.saved),this.saved);}}}};`;
(async()=>{
 let browser;
 try{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const url=`http://127.0.0.1:${server.address().port}/mini/`;
  browser=await puppeteer.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-first-run','--disable-extensions',...(process.env.CI?['--no-sandbox']:[])]});
  async function pageFor(signed=false,width=390){
   const page=await browser.newPage();await page.setViewport({width,height:844,deviceScaleFactor:1,isMobile:true,hasTouch:true});
   await page.evaluateOnNewDocument(flag=>{
    window.__signed=flag;window.__closed=0;
    class FixtureDeviceMotionEvent extends Event{
     constructor(type,init={}){
      super(type);
      this.accelerationIncludingGravity=init.accelerationIncludingGravity||null;
      this.acceleration=init.acceleration||null;
     }
    }
    Object.defineProperty(window,'DeviceMotionEvent',{configurable:true,value:FixtureDeviceMotionEvent});
   },signed);
   await page.setRequestInterception(true);
   page.on('request',request=>(request.url().includes('/mini/telegram-web-app.js')||request.url().startsWith('https://telegram.org/'))?request.respond({status:200,contentType:'application/javascript',body:scenario==='sdkmissing'?'':sdk}):request.continue());
   page.on('pageerror',e=>errors.push(e.message));
   page.on('console',msg=>{if(msg.type()==='error'&&/content security|refused to/i.test(msg.text()))errors.push(msg.text());});
   await page.goto(url,{waitUntil:'networkidle0'});return page;
  }
  async function loaded(page){await page.waitForFunction(()=>document.getElementById('home-hours').textContent!=='—');}
  async function layout(page,label){
   const result=await page.evaluate(()=>({width:innerWidth,doc:document.documentElement.scrollWidth,body:document.body.scrollWidth,
    outside:[...document.querySelectorAll('.page.shown button,.bottom-nav button,.masthead button')].filter(x=>!x.hidden&&getComputedStyle(x).display!=='none').map(x=>({id:x.id||x.textContent.trim(),r:x.getBoundingClientRect()})).filter(x=>x.r.width>0&&(x.r.left< -1||x.r.right>innerWidth+1)).map(x=>x.id)}));
   assert.ok(result.doc<=result.width+1&&result.body<=result.width+1,`${label}: horizontal overflow ${JSON.stringify(result)}`);
   assert.deepEqual(result.outside,[],`${label}: outside controls`);checks++;
  }
  for(const width of [320,360,390,430,768,1024,1440]){
   scenario='normal';const page=await pageFor(false,width);await loaded(page);
   for(const theme of ['dark','light']){
    if(await page.$eval('body',e=>e.dataset.theme)!==theme)await page.click('#theme-switch');
    assert.equal(await page.$eval('body',e=>e.dataset.theme),theme);
    await new Promise(r=>setTimeout(r,300));
    for(const tab of ['home','calendar','salary','review']){
     await page.click(`.bottom-nav [data-nav="${tab}"]`);
     await new Promise(r=>setTimeout(r,220));
     await layout(page,`${width}/${theme}/${tab}`);
     if(tab==="review"&&width===390){assert.equal(await page.$eval("#issue-list",e=>e.textContent.includes("Офисный табель ещё не подтверждён")),false);checks++;}
     await page.screenshot({path:path.join(output,`${width}-${theme}-${tab}.png`),fullPage:true});
    }
   }
   await page.close();
  }

  // The compact 2026 home/review keeps ordinary flows prominent without deleting tools.
  scenario='normal';const compact=await pageFor(false);await loaded(compact);
  assert.ok(await compact.$$eval('#recent-shifts .shift-row',items=>items.length<=2),'home shows at most two recent shifts');checks++;
  await compact.click('.bottom-nav [data-nav="review"]');
  assert.equal(await compact.$$eval('#more-tools .secondary-tools button',items=>items.length),4,'all advanced tools preserved');checks++;
  assert.equal(await compact.$eval('#more-tools',e=>e.open),false,'advanced tools are initially folded');checks++;
  assert.equal(await compact.$eval('#extra-gestures',e=>e.open),false,'secondary gestures start folded');checks++;
  assert.equal(await compact.$eval('#time-settings',e=>e.open),false,'rare shift warnings start folded');checks++;
  assert.equal(await compact.$$eval('.primary-actions button',items=>items.length),3,'only three primary actions');checks++;
  await compact.click('#more-tools summary');
  assert.equal(await compact.$eval('#return-chat',e=>e.getClientRects().length>0),true,'chat fallback remains reachable');checks++;
  await compact.click('#extra-gestures summary');
  assert.equal(await compact.$eval('#phone-shake-hide',e=>e.getClientRects().length>0),true,'shake setting remains reachable');checks++;
  await compact.click('#time-settings summary');
  assert.equal(await compact.$eval('#long-shift-threshold',e=>e.getClientRects().length>0),true,'warning preference remains reachable');checks++;
  await compact.click('.primary-actions [data-nav="calendar"]');
  assert.equal(await compact.$eval('#table-panel',e=>e.hidden),false,'correction shortcut opens the full shift list');checks++;
  await compact.click('.bottom-nav [data-nav="salary"]');
  assert.equal(await compact.$eval('.period-shortcuts [data-chat="reports"]',e=>getComputedStyle(e).display), 'none','salary hides duplicate report shortcut');checks++;
  assert.equal(await compact.$eval('#salary-more-actions',e=>e.open),false,'rare salary actions start folded');checks++;
  await compact.click('#salary-more-actions summary');
  assert.equal(await compact.$eval('#salary-more-actions [data-workspace="adjustment"]',e=>e.getClientRects().length>0),true,'adjustments remain reachable');checks++;
  assert.equal(await compact.$eval('#salary-more-actions [data-workspace="money"]',e=>e.getClientRects().length>0),true,'finance history remains reachable');checks++;
  await compact.close();

  scenario='sdkmissing';const missing=await pageFor(false);const readsBefore=reads.length;
  await missing.goto(url+'?launch-check=1#tgWebAppData=fictional&tgWebAppVersion=9.0',{waitUntil:'networkidle0'});
  assert.equal(await missing.$eval('#demo-banner',e=>e.hidden),true);
  assert.equal(await missing.$eval('.shell',e=>e.classList.contains('auth-blocked')),true);checks++;
  assert.equal(reads.length,readsBefore,'Missing SDK must not send an unverified API request');checks++;
  await missing.close();scenario='normal';
  assert.equal(actions,0,'demo never writes');checks++;
  const page=await pageFor(true);await loaded(page);
  assert.equal(await page.$eval('#demo-banner',e=>e.hidden),true);
  await page.click('#shift-action');assert.equal(await page.evaluate(()=>window.__closed),0);assert.equal(actions,0);checks++;
  await page.click('.bottom-nav [data-nav="salary"]');
  assert.equal(await page.$$eval('#salary-periods article',x=>x.length),2);
  assert.equal(await page.$eval('#salary-total',e=>e.textContent),"•••• ₽");checks++;
  await page.click('.masthead [data-money-toggle]');
  assert.ok((await page.$eval('#salary-reimbursements',e=>e.textContent)).includes('200'));checks++;

  // Bank-style completed gesture: UP -> DOWN -> UP toggles visibility.
  // Neither the first face-down phase nor merely resting face-up can reveal money.
  let sensorReadsBefore=reads.length;const sensorActionsBefore=actions;
  await page.click('.bottom-nav [data-nav="review"]');
  assert.equal(await page.$eval('#phone-auto-visibility',e=>e.checked),false);
  await page.click('#phone-auto-visibility');
  await page.waitForFunction(()=>document.getElementById('phone-motion-state').textContent==='Ожидание сигнала');
  assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('workshift_phone_privacy_v1')).autoVisibility),true);checks++;
  const emitPose=async z=>page.evaluate(async value=>{
   for(let i=0;i<36;i++){
    window.dispatchEvent(new DeviceMotionEvent('devicemotion',{
     accelerationIncludingGravity:{x:0,y:0,z:value},acceleration:{x:0,y:0,z:0}
    }));
    await new Promise(resolve=>setTimeout(resolve,16));
   }
  },z);
  await page.click('.bottom-nav [data-nav="salary"]');
  await emitPose(9.8); // Initial face-up position only arms the sensor.
  assert.notEqual(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽','resting face-up does not hide money');checks++;
  await emitPose(-9.8);
  assert.notEqual(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽','first half of gesture does not toggle money');checks++;
  await emitPose(9.8);
  assert.equal(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽','first COMPLETE flip hides amounts');checks++;
  await emitPose(9.8);
  assert.equal(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽','holding face-up never exposes amounts');checks++;
  await emitPose(-9.8);
  assert.equal(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽','second half-gesture cannot reveal amounts');checks++;
  await emitPose(9.8);
  await page.waitForFunction(()=>document.getElementById('salary-total').textContent!=='•••• ₽');
  assert.ok((await page.$eval('#salary-total',e=>e.textContent)).includes('10'),'second COMPLETE flip reveals amounts');checks++;
  await emitPose(-9.8);
  assert.notEqual(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽','partial third flip does not hide on its own');checks++;
  await emitPose(9.8);
  assert.equal(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽','third complete flip hides again');checks++;
  await page.reload({waitUntil:'networkidle0'});await loaded(page);
  sensorReadsBefore=reads.length; // the page reload itself performs its normal state read
  await page.waitForFunction(()=>{
   const mode=document.getElementById('phone-motion-state').textContent;
   return document.getElementById('phone-auto-visibility').checked&&['Ожидание сигнала','Нет сигнала','Активен'].includes(mode);
  });
  await emitPose(9.8);
  await page.click('.bottom-nav [data-nav="salary"]');
  assert.equal(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽','relaunch starts with private balances');checks++;
  await emitPose(-9.8);
  assert.equal(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽','partial first flip after reload never reveals');checks++;
  await emitPose(9.8);
  assert.equal(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽','first complete flip after reload remains private');checks++;
  await emitPose(-9.8);
  assert.equal(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽','second partial flip after reload remains private');checks++;
  await emitPose(9.8);
  await page.waitForFunction(()=>document.getElementById('salary-total').textContent!=='•••• ₽');
  assert.ok((await page.$eval('#salary-total',e=>e.textContent)).includes('10'),'second complete flip after reload reveals');checks++;
  await page.click('.bottom-nav [data-nav="review"]');
  await page.click('#phone-auto-visibility');
  await page.waitForFunction(()=>document.getElementById('phone-motion-state').textContent==='Выключен');
  assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('workshift_phone_privacy_v1')).autoVisibility),false);checks++;
  await emitPose(-9.8);
  await page.click('.bottom-nav [data-nav="salary"]');
  assert.notEqual(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽','disabled sensor no longer changes visibility');checks++;
  assert.equal(reads.length,sensorReadsBefore,'sensor gestures make no API reads');
  assert.equal(actions,sensorActionsBefore,'sensor gestures never write shift or finance data');checks+=2;

  const payoutReadsBefore=workspaceReads;
  await page.click('#payment-history-load');
  await page.waitForFunction(()=>document.querySelectorAll('#payment-history-list .payment-history-item').length===4);
  assert.equal(workspaceReads,payoutReadsBefore+1,'history is loaded once on demand');checks++;
  await page.$eval('#payment-history-search',e=>{e.value='аванс ноябрь';e.dispatchEvent(new Event('input',{bubbles:true}));});
  assert.equal(await page.$$eval('#payment-history-list .payment-history-item',items=>items.length),1,'search matches date/month/notes');checks++;
  assert.ok((await page.$eval('#payment-history-note',e=>e.textContent)).includes('Показано 1 из 4'));checks++;
  await page.$eval('#payment-history-search',e=>{e.value='';e.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.select('#payment-history-month','2026-10');
  assert.equal(await page.$$eval('#payment-history-list .payment-history-item',items=>items.length),1,'month filter narrows the history');checks++;
  await page.select('#payment-history-month','2026-11');
  await page.select('#payment-history-period','2');
  assert.equal(await page.$$eval('#payment-history-list .payment-history-item',items=>items.length),1,'half-month filter narrows the history');checks++;
  await page.$eval('#payment-history-search',e=>{e.value='нет такой выплаты';e.dispatchEvent(new Event('input',{bubbles:true}));});
  assert.equal(await page.$eval('#payment-history-list',e=>e.textContent.includes('По фильтру записей нет')),true,'empty result has an explanation');checks++;
  await page.click('#payment-history-reset');
  assert.equal(await page.$$eval('#payment-history-list .payment-history-item',items=>items.length),4,'reset restores all records');checks++;
  assert.equal(workspaceReads,payoutReadsBefore+1,'client-side filters do not request more data');checks++;
  await page.click('.masthead [data-money-toggle]');
  assert.equal(await page.$eval('#payment-history-filters',e=>e.hidden),true,'hiding money hides payout filters');checks++;
  assert.equal(await page.$eval('#payment-history-list',e=>e.hidden),true,'hiding money clears payout rows');checks++;
  assert.equal(await page.$eval('#payment-history-search',e=>e.value),'','sensitive search text is cleared with the history');checks++;
  assert.equal(await page.evaluate(()=>Object.keys(localStorage).some(key=>/payment.?history/i.test(key))),false,'payout history and filters are never persisted');checks++;
  await page.click('.masthead [data-money-toggle]');
  assert.equal(await page.$eval('#payment-history-load',e=>e.textContent),'Загрузить историю','revealing money requires a fresh explicit history load');checks++;

  await page.click('#annual-toggle');await page.waitForFunction(()=>document.querySelectorAll('#annual-months button').length===12);
  assert.equal(await page.$eval('#annual-toggle',e=>e.getAttribute('aria-expanded')),'true');checks++;
  assert.ok((await page.$eval('#annual-metrics',e=>e.textContent)).includes('20 ч'));checks++;
  await page.$eval('#annual-toggle',e=>e.click());assert.equal(await page.$eval('#annual-panel',e=>e.hidden),true);checks++;
  await page.waitForFunction(()=>Boolean(window.echarts?.getInstanceByDom(document.getElementById('income-chart'))));
  assert.equal(await page.evaluate(()=>echarts.getInstanceByDom(document.getElementById('income-chart')).getOption().series[0].type),'line');checks++;
  await page.click('#chart-hours');
  assert.equal(await page.evaluate(()=>echarts.getInstanceByDom(document.getElementById('income-chart')).getOption().series[0].type),'bar');checks++;
  await page.screenshot({path:path.join(output,'390-interactive-chart.png'),fullPage:true});
  await page.$eval('#income-chart',e=>e.scrollIntoView({block:'center'}));
  const chartPoint=await page.evaluate(()=>{
    const element=document.getElementById('income-chart'),chart=echarts.getInstanceByDom(element),rect=element.getBoundingClientRect();
    const point=chart.convertToPixel({gridIndex:0},[9,5]);return {x:rect.left+point[0],y:rect.top+point[1]};
  });
  await page.mouse.click(chartPoint.x,chartPoint.y);
  await page.waitForFunction(()=>!document.getElementById('day-sheet').hidden);
  assert.ok((await page.$eval('#day-heading',e=>e.textContent)).startsWith('10.'));checks++;
  await page.click('#day-close');

  await page.click('.bottom-nav [data-nav="calendar"]');
  assert.equal(await page.$eval('#calendar-panel',e=>e.hidden),false,'calendar is the primary view');checks++;
  assert.equal(await page.$eval('#calendar-export',e=>e.disabled),false,'planned shifts can be exported');
  const exportReadCount=reads.length,exportActionCount=actions;
  await page.evaluate(()=>{
   Object.defineProperty(navigator,'share',{configurable:true,value:undefined});
   Object.defineProperty(navigator,'canShare',{configurable:true,value:undefined});
   window.__downloadCaptured=null;
   window.__calendarBlob=null;
   window.__originalCreateObjectURL=URL.createObjectURL;
   URL.createObjectURL=function(blob){
    window.__calendarBlob=blob;
    return window.__originalCreateObjectURL.call(URL,blob);
   };
   window.__originalAnchorClick=HTMLAnchorElement.prototype.click;
   HTMLAnchorElement.prototype.click=function(){
    if(this.download)window.__downloadCaptured={href:this.href,filename:this.download};
   };
  });
  await page.click('#calendar-export');
  await page.waitForFunction(()=>Boolean(window.__downloadCaptured&&window.__calendarBlob));
  const exported=await page.evaluate(async()=>{
   const download=window.__downloadCaptured;
   return {filename:download.filename,content:await window.__calendarBlob.text()};
  });
  const exportMonth=await page.$eval('#month-jump',e=>e.value);
  const moscowUtc=(day,clock)=>{
   const [year,month,date]=day.split('-').map(Number),[hour,minute]=clock.split(':').map(Number);
   const target=Date.UTC(year,month-1,date,hour,minute);let instant=target;
   const format=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
   for(let i=0;i<3;i++){
    const parts={};for(const part of format.formatToParts(new Date(instant)))if(part.type!=='literal')parts[part.type]=part.value;
    instant+=target-Date.UTC(Number(parts.year),Number(parts.month)-1,Number(parts.day),Number(parts.hour),Number(parts.minute));
   }
   return new Date(instant).toISOString().replace(/[-:]/g,'').replace(/\.\d{3}Z$/,'Z');
  };
  const exportStart=moscowUtc(exportMonth+'-18','16:00');
  const exportEnd=moscowUtc(exportMonth+'-19','02:00');
  assert.equal(exported.filename,'moi-smeny-'+exportMonth+'.ics');
  assert.ok(exported.content.startsWith('BEGIN:VCALENDAR\r\n'));
  assert.ok(exported.content.includes('DTSTART:'+exportStart));
  assert.ok(exported.content.includes('DTEND:'+exportEnd),'overnight shift must end on the next calendar day in Moscow time');
  assert.ok(exported.content.includes('X-WR-TIMEZONE:Europe/Moscow'));
  const unfoldedIcs=exported.content.replace(/\r\n[ \t]/g,'');
  const maxIcsLineBytes=Math.max(...exported.content.split('\r\n').filter(Boolean).map(line=>new TextEncoder().encode(line).length));
  assert.ok(maxIcsLineBytes<=75,'iCalendar lines must be folded at 75 UTF-8 octets');
  assert.ok(unfoldedIcs.includes('DESCRIPTION:Плановое время по Москве\\, не фактическая отметка.'),'folded description remains readable');
  assert.equal((exported.content.match(/BEGIN:VEVENT/g)||[]).length,1,'only work plans, not off days, become events');
  assert.ok(exported.content.includes('STATUS:TENTATIVE'));
  await page.evaluate(()=>{
   window.__sharedCalendar=null;
   Object.defineProperty(navigator,'canShare',{configurable:true,value:()=>true});
   Object.defineProperty(navigator,'share',{configurable:true,value:async payload=>{
    window.__sharedCalendar={title:payload.title,file:payload.files?.[0]?.name};
   }});
  });
  await page.click('#calendar-export');
  await page.waitForFunction(()=>Boolean(window.__sharedCalendar));
  assert.equal(await page.evaluate(()=>window.__sharedCalendar.file),exported.filename,'mobile share sheet receives the same .ics file');
  await page.evaluate(()=>{
   HTMLAnchorElement.prototype.click=window.__originalAnchorClick;
   URL.createObjectURL=window.__originalCreateObjectURL;
  });
  await page.click('#bulk-toggle');
  assert.equal(await page.$eval('#calendar-export',e=>e.disabled),true,'calendar export is disabled while selecting bulk-plan dates');
  assert.equal(reads.length,exportReadCount,'calendar export uses the already loaded plan without another API request');
  assert.equal(actions,exportActionCount,'calendar export never writes shift or finance data');checks+=14;
  await page.click('#bulk-toggle');
  await page.click('#mode-table');
  assert.equal(await page.$eval('#table-panel',e=>e.hidden),false);
  await page.select('#table-filter','worked');
  assert.equal(await page.$$eval('#ledger-body tr',rows=>rows.length),1);checks++;
  await page.type('#table-search','impossible-fixture');
  assert.equal(await page.$eval('#table-empty',e=>e.hidden),false);checks++;
  await page.$eval('#table-search',e=>{e.value='';e.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.screenshot({path:path.join(output,'390-filtered-table.png'),fullPage:true});
  await page.click('#ledger-body .day-link');
  assert.equal(await page.$eval('#day-sheet',e=>e.hidden),false);
  assert.ok((await page.$eval('#day-details',e=>e.textContent)).includes('10 ч 00 мин'));
  assert.ok((await page.$eval('#day-details',e=>e.textContent)).includes('Офисные часы не подтверждены'));
  await page.screenshot({path:path.join(output,'390-day-detail.png'),fullPage:true});checks++;
  await page.keyboard.press('Escape');assert.equal(await page.$eval('#day-sheet',e=>e.hidden),true);checks++;
  await page.click('#mode-calendar');assert.equal(await page.$eval('#calendar-panel',e=>e.hidden),false);checks++;
  await page.click('#calendar-grid button:nth-of-type(10)');
  assert.equal(await page.$eval('#day-sheet',e=>e.hidden),false);await page.click('#day-close');checks++;
  await page.click('#mode-table');await page.select('#table-filter','all');

  scenario='active';await page.reload({waitUntil:'networkidle0'});await loaded(page);
  const timerBefore=await page.$eval("#live-time",e=>e.textContent);
  await page.waitForFunction(before=>!document.getElementById("live-time").hidden&&document.getElementById("live-time").textContent!==before,{},timerBefore);
  assert.ok((await page.$eval('#shift-break',e=>e.textContent)).startsWith('0 мин'));checks++;
  assert.equal(await page.$eval('#break-action',e=>e.hidden),true); // auto break: no start action
  await page.screenshot({path:path.join(output,'390-active.png'),fullPage:true});checks++;
  scenario='break';await page.reload({waitUntil:'networkidle0'});await loaded(page);
  await new Promise(r=>setTimeout(r,1100));
  assert.ok((await page.$eval('#shift-break',e=>e.textContent)).includes('идёт'));
  await layout(page,'active-break');await page.screenshot({path:path.join(output,'390-break.png'),fullPage:true});checks++;
  scenario='long-active';await page.reload({waitUntil:'networkidle0'});await loaded(page);
  await page.waitForFunction(()=>!document.getElementById('shift-duration-warning').hidden);
  assert.ok((await page.$eval('#shift-duration-warning',e=>e.textContent)).includes('15 ч'));
  assert.equal(await page.$eval('#live-time',e=>e.hidden),false,'warning must not auto-close a shift');checks++;
  await page.click('.bottom-nav [data-nav="review"]');await page.select('#long-shift-threshold','16');
  assert.equal(await page.$eval('#shift-duration-warning',e=>e.hidden),true,'warning threshold is configurable without closing a shift');
  assert.equal(await page.evaluate(()=>localStorage.getItem('workshift_long_shift_hours_v1')),'16');checks++;
  await page.select('#long-shift-threshold','14');
  scenario='no-money';await page.reload({waitUntil:'networkidle0'});await loaded(page);await page.click('.bottom-nav [data-nav="salary"]');
  assert.equal(await page.$eval('#salary-received',e=>e.textContent),'•••• ₽');
  await page.click('.masthead [data-money-toggle]');
  assert.equal(await page.$eval('#salary-received',e=>e.textContent),'Пока нет');
  assert.ok(await page.$eval('.received-card',e=>e.classList.contains('is-empty')));
  assert.equal(await page.$eval('#forecast-card',e=>e.hidden),true);checks++;
  scenario='slow';await page.reload({waitUntil:'networkidle0'});await loaded(page);
  await page.click('.bottom-nav [data-nav="calendar"]');
  const initial=reads.at(-1);const d=new Date(initial+'-01T00:00:00Z');d.setUTCMonth(d.getUTCMonth()+3);const expected=d.toISOString().slice(0,7);
  await page.evaluate(()=>{for(let i=0;i<3;i++)document.getElementById('month-next').click();});
  await new Promise(r=>setTimeout(r,750));
  assert.equal(reads.at(-1),expected);
  assert.ok((await page.$eval('#calendar-month',e=>e.textContent)).includes(expected.slice(0,4)));
  assert.ok((await page.$eval('#day-heading',e=>e.textContent)).includes('01.'+expected.slice(5)));checks++;
  async function jump(month){
    await page.$eval('#month-jump',(input,value)=>{input.value=value;input.dispatchEvent(new Event('change',{bubbles:true}));},month);
    await page.waitForFunction(month=>document.getElementById('day-heading').textContent.includes('01.'+month.slice(5))&&document.getElementById('calendar-month').textContent.includes(month.slice(0,4)),{},month);
  }
  await jump('2024-02');assert.equal(await page.$$eval('#calendar-grid button',x=>x.length),29);checks++;
  await jump('2023-02');assert.equal(await page.$$eval('#calendar-grid button',x=>x.length),28);checks++;
  await jump('2030-12');await page.click('#month-next');await page.waitForFunction(()=>document.getElementById('month-jump').value==='2031-01');checks++;
  await jump('2100-12');await page.click('#month-next');assert.equal(await page.$eval('#month-jump',e=>e.value),'2100-12');checks++;
  scenario='denied';await page.reload({waitUntil:'networkidle0'});
  assert.equal(await page.$eval('#demo-banner',e=>e.hidden),true);
  assert.equal(await page.$eval('.shell',e=>e.classList.contains('auth-blocked')),true);
  assert.equal(await page.$eval('#connection-banner',e=>e.hidden),false);
  await page.screenshot({path:path.join(output,'390-denied.png'),fullPage:true});checks++;
  scenario='normal';await page.click('#retry-connection');await page.waitForFunction(()=>!document.querySelector('.shell').classList.contains('auth-blocked'));
  scenario='failed';await page.evaluate(()=>window.dispatchEvent(new Event('online')));await page.waitForFunction(()=>!document.getElementById('connection-banner').hidden);
  assert.ok((await page.$eval('#connection-error',e=>e.textContent)).includes('последние загруженные'));checks++;

  await page.click('.bottom-nav [data-nav="calendar"]');
  const preservedMonth=await page.$eval('#month-jump',e=>e.value);
  await page.click('#month-next');await new Promise(r=>setTimeout(r,250));
  assert.equal(await page.$eval('#month-jump',e=>e.value),preservedMonth,'failed month requests must not relabel old data');checks++;

  scenario='write';await page.reload({waitUntil:'networkidle0'});await loaded(page);
  await page.evaluate(()=>{document.getElementById('shift-action').click();document.getElementById('shift-action').click();});
  assert.equal(await page.$eval('#modal',e=>e.hidden),false);
  await page.keyboard.press('Escape');assert.equal(actions,0);checks++;
  await page.click('#shift-action');await page.click('#modal-confirm');await page.waitForFunction(()=>document.getElementById('modal').hidden);
  await new Promise(r=>setTimeout(r,200));assert.equal(actions,1);checks++;
  scenario='uncertain';actions=0;operationResult=null;
  await page.reload({waitUntil:'networkidle0'});await loaded(page);
  await page.click('#shift-action');await page.click('#modal-confirm');await new Promise(r=>setTimeout(r,300));
  await page.click('#shift-action');assert.equal(await page.$eval('#modal-title',e=>e.textContent),'Проверить сохранение');
  await page.click('#modal-confirm');await new Promise(r=>setTimeout(r,300));assert.equal(actions,2);checks++;

  // OFFLINE_EDIT_DRAFT_CHECKS: only local browser storage, never an API write.
  scenario='offline';actions=0;operationResult=null;await page.reload({waitUntil:'networkidle0'});await loaded(page);
  await page.click('.bottom-nav [data-nav="calendar"]');await page.click('#mode-table');await page.click('#ledger-body .day-link');
  await page.waitForSelector('.day-fact-edit');assert.equal(await page.$('#day-details .day-edit-link'),null,'direct edit mode removes the redundant chat link');
  await page.click('.day-fact-edit');await page.waitForSelector('#actual-form');
  await page.setOfflineMode(true);
  await page.$eval('#actual-start',e=>{e.value='15:30';e.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.click('#actual-save');
  await page.waitForFunction(()=>Boolean(localStorage.getItem('workshift_edit_draft_v1')));
  const offlineActual=await page.evaluate(()=>JSON.parse(localStorage.getItem('workshift_edit_draft_v1')));
  assert.equal(offlineActual.kind,'actual');assert.equal(offlineActual.start,'15:30');assert.equal(actions,0);checks++;
  await page.click('#day-close');await page.click('.bottom-nav [data-nav="review"]');
  const actualReload=page.waitForResponse(r=>r.url().includes('/mini/api/state')&&r.status()===200);await page.setOfflineMode(false);await actualReload;
  await page.waitForFunction(()=>navigator.onLine&&document.getElementById('pending-drafts').textContent.includes('Черновик исправления'));
  await page.click('#pending-drafts .main-action');await page.waitForSelector('#actual-form');
  assert.equal(await page.$eval('#actual-start',e=>e.value),'15:30');assert.equal(actions,0);checks++;
  await page.click('#actual-cancel');await page.click('#day-close');await page.click('.bottom-nav [data-nav="review"]');
  await page.click('#pending-drafts .offline-draft-card .text-button');
  assert.equal(await page.evaluate(()=>localStorage.getItem('workshift_edit_draft_v1')),null);checks++;

  // Late clock-out draft is restored as a chosen timestamp, never auto-sent.
  await page.click('.bottom-nav [data-nav="home"]');await page.click('#shift-correction-tools summary');
  await page.setOfflineMode(true);await page.click('#attendance-earlier');await page.waitForSelector('#recovery-form');
  await page.$eval('#recovery-time',e=>{e.value='20:15';e.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.click('#recovery-save');await page.waitForFunction(()=>Boolean(localStorage.getItem('workshift_edit_draft_v1')));
  const offlineRecovery=await page.evaluate(()=>JSON.parse(localStorage.getItem('workshift_edit_draft_v1')));
  assert.equal(offlineRecovery.kind,'recovery');assert.equal(offlineRecovery.at.slice(11),'20:15');assert.equal(actions,0);checks++;
  await page.click('#day-close');await page.click('.bottom-nav [data-nav="review"]');
  const recoveryReload=page.waitForResponse(r=>r.url().includes('/mini/api/state')&&r.status()===200);await page.setOfflineMode(false);await recoveryReload;
  await page.waitForFunction(()=>navigator.onLine&&document.getElementById('pending-drafts').textContent.includes('Черновик исправления'));
  await page.click('#pending-drafts .main-action');await page.waitForSelector('#recovery-form');
  assert.equal(await page.$eval('#recovery-time',e=>e.value),'20:15');assert.equal(actions,0);checks++;
  await page.click('#recovery-cancel');await page.click('.bottom-nav [data-nav="review"]');
  await page.click('#pending-drafts .offline-draft-card .text-button');
  assert.equal(await page.evaluate(()=>localStorage.getItem('workshift_edit_draft_v1')),null);checks++;

  // CALENDAR_EDITOR_CHECKS: no production data, no real Telegram messages.
  scenario='normal';actions=0;operationResult=null;
  await page.reload({waitUntil:'networkidle0'});await loaded(page);
  await page.click('.bottom-nav [data-nav="calendar"]');await jump('2099-07');
  await page.click('#mode-calendar');
  async function openPlan(){await page.click('#calendar-grid button:nth-of-type(18)');await page.waitForSelector('#plan-form');}
  async function fillTime(id,value){await page.$eval(id,(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));},value);}
  await openPlan();
  assert.equal(await page.$eval('#plan-work',e=>e.getAttribute('aria-pressed')),'true');checks++;
  assert.equal(await page.$eval('#plan-start',e=>e.value),'16:00');checks++;
  assert.ok((await page.$eval('#plan-preview',e=>e.textContent)).includes('следующий день'));checks++;
  await page.click('#plan-off');
  assert.equal(await page.$eval('#plan-times',e=>e.hidden),true);
  assert.equal(await page.$eval('#plan-start',e=>e.disabled),true);checks++;
  await page.click('#plan-submit');
  let urlSent=await page.evaluate(()=>window.__links.at(-1));
  assert.equal(new URL(urlSent).origin,'https://t.me');
  assert.equal(new URL(urlSent).pathname,'/FictionalShiftBot');
  assert.match(new URL(urlSent).searchParams.get('start'),/^p_20990718_o_0000_0000_[a-f0-9]{32}$/);checks++;
  assert.equal(actions,0,'calendar draft must not call web-write endpoint');checks++;
  await page.click('#plan-work');await fillTime('#plan-start','18:15');await fillTime('#plan-end','18:15');
  const sentBefore=await page.evaluate(()=>window.__links.length);
  await page.click('#plan-submit');assert.equal(await page.evaluate(()=>window.__links.length),sentBefore);
  assert.ok((await page.$eval('#plan-error',e=>e.textContent)).includes('разное время'));checks++;
  await fillTime('#plan-end','03:30');await page.click('#plan-submit');
  urlSent=await page.evaluate(()=>window.__links.at(-1));
  assert.match(new URL(urlSent).searchParams.get('start'),/^p_20990718_s_1815_0330_[a-f0-9]{32}$/);checks++;
  await page.screenshot({path:path.join(output,'390-calendar-plan-night.png'),fullPage:true});
  const beforeRefresh=reads.length;
  await page.evaluate(()=>window.dispatchEvent(new Event('online')));
  await page.waitForFunction(()=>document.getElementById('connection').textContent.includes('Подключено'));
  await new Promise(r=>setTimeout(r,200));
  assert.ok(reads.length>beforeRefresh);assert.equal(await page.$eval('#plan-start',e=>e.value),'18:15');checks++;
  planRevision='b'.repeat(32);
  await page.evaluate(()=>window.dispatchEvent(new Event('online')));await new Promise(r=>setTimeout(r,300));
  const oldLinks=await page.evaluate(()=>window.__links.length);
  await page.click('#plan-submit');assert.equal(await page.evaluate(()=>window.__links.length),oldLinks);
  assert.ok((await page.$eval('#plan-error',e=>e.textContent)).includes('План изменился'));checks++;
  await page.click('#day-close');await openPlan();
  assert.equal(await page.$eval('#plan-start',e=>e.value),'16:00');checks++;
  await page.click('#plan-submit');assert.ok((await page.evaluate(()=>window.__links.at(-1))).endsWith('b'.repeat(32)));checks++;
  await page.click('#plan-off');
  for(const width of [320,360,390,430,768,1024,1440]){
    await page.setViewport({width,height:640,deviceScaleFactor:1,isMobile:true,hasTouch:true});
    for(const theme of ['dark','light']){
      await page.$eval('body',(e,t)=>{e.dataset.theme=t;},theme);
      const bounds=await page.evaluate(()=>[...document.querySelectorAll('#day-sheet button,#day-sheet input:not(:disabled)')].filter(e=>e.getClientRects().length).map(e=>e.getBoundingClientRect()).every(r=>r.left>=0&&r.right<=innerWidth+1));
      assert.equal(bounds,true);checks++;
      await page.screenshot({path:path.join(output,`${width}-${theme}-calendar-editor.png`),fullPage:true});
    }
  }
  await page.setViewport({width:390,height:844,deviceScaleFactor:1,isMobile:true,hasTouch:true});
  await page.click('#plan-work');await page.focus('#plan-start');
  for(let i=0;i<5;i++){await page.keyboard.press('Tab');if(await page.evaluate(()=>document.activeElement.id)==='plan-end')break;}
  assert.equal(await page.evaluate(()=>document.activeElement.id),'plan-end');checks++;
  const linksOnline=await page.evaluate(()=>window.__links.length);
  await page.setOfflineMode(true);await page.click('#plan-submit');
  assert.equal(await page.evaluate(()=>window.__links.length),linksOnline);
  assert.ok((await page.$eval('#plan-error',e=>e.textContent)).includes('соединения'));checks++;
  await page.setOfflineMode(false);await new Promise(r=>setTimeout(r,250));
  scenario='denied';await page.evaluate(()=>window.dispatchEvent(new Event('online')));
  await page.waitForFunction(()=>document.querySelector('.shell').classList.contains('auth-blocked'));
  assert.equal(await page.$eval('#day-sheet',e=>e.hidden),true);assert.equal(await page.$('#plan-form'),null);checks++;
  scenario='normal';await page.reload({waitUntil:'networkidle0'});await loaded(page);
  await page.click('.bottom-nav [data-nav="calendar"]');await jump('2024-07');await page.click('#mode-calendar');
  await page.click('#calendar-grid button:nth-of-type(10)');
  assert.notEqual(await page.$('#plan-form'),null);assert.equal(await page.$eval('#day-plan-disclosure',e=>e.open),false);checks++;
  await page.close();
  const demo=await pageFor(false);await loaded(demo);
  await demo.click('.bottom-nav [data-nav="calendar"]');
  await demo.$eval('#month-jump',e=>{e.value='2099-07';e.dispatchEvent(new Event('change',{bubbles:true}));});
  await demo.click('#mode-calendar');await demo.click('#calendar-grid button:nth-of-type(18)');
  await demo.click('#plan-off');await demo.click('#plan-submit');
  assert.ok((await demo.$eval('#plan-error',e=>e.textContent)).includes('ничего не сохранено'));
  assert.equal(await demo.evaluate(()=>(window.__links||[]).length),0);checks++;
  await demo.close();
  scenario='normal';actions=0;
  const daily=await pageFor(true);await loaded(daily);
  const nextDay=agendaFixture().next_shift.day;
  assert.equal(await daily.$eval('#agenda-section',e=>e.hidden),false);checks++;
  const nearestLabel=await daily.$eval('#next-plan-title',e=>e.textContent);
  await daily.$eval('.agenda-week summary',e=>e.scrollIntoView({block:'center'}));await daily.click('.agenda-week summary');
  assert.equal(await daily.$$eval('#agenda-days button',rows=>rows.length),7);checks++;
  await daily.click('.bottom-nav [data-nav="calendar"]');
  await daily.$eval('#month-jump',e=>{e.value='2024-07';e.dispatchEvent(new Event('change',{bubbles:true}));});
  await daily.waitForFunction(()=>document.getElementById('month-jump').value==='2024-07'&&document.getElementById('calendar-month').textContent.includes('2024'));
  await daily.click('.bottom-nav [data-nav="home"]');
  assert.equal(await daily.$eval('#next-plan-title',e=>e.textContent),nearestLabel,'Upcoming plan independent of report month');checks++;
  await daily.click('.bottom-nav [data-nav="salary"]');
  await daily.$eval('.context-actions [data-chat="reports"]',e=>e.scrollIntoView({block:'center'}));await daily.click('.context-actions [data-chat="reports"]');
  assert.equal(new URL(await daily.evaluate(()=>window.__links.at(-1))).searchParams.get('start'),'q_reports_202407');checks++;
  await daily.click('.context-actions [data-chat="payment"]');
  assert.equal(new URL(await daily.evaluate(()=>window.__links.at(-1))).searchParams.get('start'),'q_payment_202407');checks++;
  assert.equal(actions,0,'Navigation is not a database write');checks++;
  await daily.click('.bottom-nav [data-nav="calendar"]');await daily.click('#mode-table');await daily.type('#table-search','unmatched-daily-fixture');
  assert.equal(await daily.$eval('#table-empty',e=>e.hidden),false);await daily.click('#table-reset');
  assert.equal(await daily.$eval('#table-search',e=>e.value),'');assert.equal(await daily.$eval('#table-reset',e=>e.hidden),true);checks++;
  await daily.click('.bottom-nav [data-nav="home"]');await daily.$eval('#next-plan',e=>e.scrollIntoView({block:'center'}));await daily.click('#next-plan');
  await daily.waitForFunction(()=>!document.getElementById('day-sheet').hidden);
  assert.equal(await daily.$eval('#day-heading',e=>e.textContent),nextDay.slice(8)+'.'+nextDay.slice(5,7)+'.'+nextDay.slice(0,4));checks++;
  await daily.$eval('.day-edit-link',e=>e.scrollIntoView({block:'center'}));await daily.click('.day-edit-link');
  assert.equal(new URL(await daily.evaluate(()=>window.__links.at(-1))).searchParams.get('start'),'q_day_'+nextDay.replaceAll('-',''));checks++;
  await daily.click('#day-close');await daily.click('#month-today');
  await daily.waitForFunction(month=>document.getElementById('month-jump').value===month,{},agendaFixture().today.slice(0,7));checks++;
  await daily.click('.bottom-nav [data-nav="review"]');await daily.$eval('#manual-refresh',e=>e.scrollIntoView({block:'center'}));
  const readsBeforeDaily=reads.length;await daily.click('#manual-refresh');await new Promise(r=>setTimeout(r,200));
  assert.ok(reads.length>readsBeforeDaily);assert.ok((await daily.$eval('#sync-status',e=>e.textContent)).includes('Обновлено'));checks++;
  const linksBeforeDaily=await daily.evaluate(()=>window.__links.length);await daily.setOfflineMode(true);await daily.$eval('[data-chat="plan"]',e=>e.scrollIntoView({block:'center'}));await daily.click('[data-chat="plan"]');
  assert.equal(await daily.evaluate(()=>window.__links.length),linksBeforeDaily);checks++;
  await daily.setOfflineMode(false);await new Promise(r=>setTimeout(r,300));
  await daily.click('.bottom-nav [data-nav="home"]');await daily.$eval('.agenda-week summary',e=>e.scrollIntoView({block:'center'}));
  for(const width of [320,390,1440]){await daily.setViewport({width,height:844,isMobile:width<700,hasTouch:width<700});await layout(daily,'daily-agenda-'+width);await daily.screenshot({path:path.join(output,'daily-agenda-'+width+'.png'),fullPage:true});}
  await daily.close();
  const previewDaily=await pageFor(false);await loaded(previewDaily);await previewDaily.click('.bottom-nav [data-nav="review"]');await previewDaily.$eval('[data-chat="plan"]',e=>e.scrollIntoView({block:'center'}));await previewDaily.click('[data-chat="plan"]');
  assert.equal(await previewDaily.evaluate(()=>(window.__links||[]).length),0);assert.equal(actions,0);checks++;await previewDaily.close();

  // Multi-date draft selection; no web writes and no real Telegram calls.
  scenario='normal';planRevision='c'.repeat(32);
  const bulk=await pageFor(true);await loaded(bulk);
  await bulk.click('.bottom-nav [data-nav="calendar"]');
  await bulk.$eval('#month-jump',e=>{e.value='2099-07';e.dispatchEvent(new Event('change',{bubbles:true}));});
  await bulk.waitForFunction(()=>document.getElementById('month-jump').value==='2099-07'&&document.querySelector('#calendar-grid [data-day="2099-07-31"]'));
  await bulk.click('#bulk-toggle');
  assert.equal(await bulk.$eval('#month-next',e=>e.disabled),true);assert.equal(await bulk.$eval('#bulk-submit',e=>e.disabled),true);checks++;
  for(const day of ['2099-07-10','2099-07-12','2099-07-31'])await bulk.click(`#calendar-grid [data-day="${day}"]`);
  assert.equal(await bulk.$$eval('#calendar-grid .bulk-picked',e=>e.length),3);assert.equal(await bulk.$eval('#day-sheet',e=>e.hidden),true);checks++;
  assert.ok((await bulk.$eval('#bulk-dates',e=>e.textContent)).includes('31.07'));checks++;
  await bulk.$eval('#bulk-off',e=>e.scrollIntoView({block:'center'}));await bulk.click('#bulk-off');
  assert.equal(await bulk.$eval('#bulk-start',e=>e.disabled),true);checks++;
  const baselineWrites=actions;
  await bulk.$eval('#bulk-submit',e=>e.scrollIntoView({block:'center'}));await bulk.click('#bulk-submit');
  let bulkLink=await bulk.evaluate(()=>window.__links.at(-1));
  assert.equal(new URL(bulkLink).searchParams.get('start'),'b_209907_40000a00_o_0000_0000_'+'c'.repeat(24));checks++;
  assert.equal(actions,baselineWrites,'bulk navigation cannot write web records');checks++;
  await bulk.click('#bulk-unset');await bulk.click('#bulk-submit');
  assert.match(await bulk.evaluate(()=>window.__links.at(-1)),/b_209907_40000a00_n_0000_0000_[a-f0-9]{24}$/);checks++;
  await bulk.click('#bulk-shift');
  await bulk.$eval('[data-bulk-preset="09:00/18:00"]',e=>e.scrollIntoView({block:'center'}));await bulk.click('[data-bulk-preset="09:00/18:00"]');
  assert.equal(await bulk.$eval('#bulk-start',e=>e.value),'09:00');assert.equal(await bulk.$eval('#bulk-end',e=>e.value),'18:00');checks++;
  await bulk.$eval('#bulk-end',e=>{e.value='09:00';e.dispatchEvent(new Event('input',{bubbles:true}));});
  let linkCount=await bulk.evaluate(()=>window.__links.length);await bulk.$eval('#bulk-submit',e=>e.scrollIntoView({block:'center'}));await bulk.click('#bulk-submit');
  assert.equal(await bulk.evaluate(()=>window.__links.length),linkCount);assert.ok((await bulk.$eval('#bulk-error',e=>e.textContent)).includes('разное'));checks++;
  await bulk.$eval('#bulk-end',e=>{e.value='18:00';e.dispatchEvent(new Event('input',{bubbles:true}));});
  planRevision='d'.repeat(32);await bulk.evaluate(()=>window.dispatchEvent(new Event('online')));await new Promise(r=>setTimeout(r,250));
  assert.equal(await bulk.$eval('#bulk-submit',e=>e.disabled),true);assert.ok((await bulk.$eval('#bulk-error',e=>e.textContent)).includes('изменился'));checks++;
  await bulk.$eval('#bulk-cancel',e=>e.scrollIntoView({block:'center'}));await bulk.click('#bulk-cancel');
  assert.equal(await bulk.$eval('#bulk-editor',e=>e.hidden),true);assert.equal(await bulk.$eval('#month-next',e=>e.disabled),false);checks++;
  await bulk.click('#bulk-toggle');await bulk.click('#calendar-grid [data-day="2099-07-31"]');
  for(const width of [320,390,768,1440]){
    await bulk.setViewport({width,height:844,deviceScaleFactor:1,isMobile:true,hasTouch:true});
    assert.equal(await bulk.$eval("#bulk-editor",e=>e.hidden),false);
    for(const theme of ['dark','light']){
      await bulk.$eval('body',(e,t)=>e.dataset.theme=t,theme);
      const bounds=await bulk.evaluate(()=>({w:innerWidth,scroll:document.documentElement.scrollWidth,out:[...document.querySelectorAll('#bulk-editor button,#bulk-editor input')].filter(e=>e.getClientRects().length).filter(e=>{const r=e.getBoundingClientRect();return r.left< -1||r.right>innerWidth+1;}).map(e=>e.id)}));
      assert.equal(bounds.w,width);assert.ok(bounds.scroll<=width+1);assert.deepEqual(bounds.out,[]);checks++;
      await bulk.screenshot({path:path.join(output,`${width}-${theme}-bulk-planning.png`),fullPage:true});
    }
  }
  await bulk.setViewport({width:390,height:844,deviceScaleFactor:1,isMobile:true,hasTouch:true});
  await bulk.setViewport({width:320,height:844,deviceScaleFactor:1,isMobile:true,hasTouch:true});
  await bulk.evaluate(()=>document.documentElement.style.fontSize='32px');
  await bulk.waitForFunction(()=>document.documentElement.classList.contains('large-reflow'));
  const large=await bulk.evaluate(()=>({w:innerWidth,scroll:document.documentElement.scrollWidth}));assert.equal(large.w,320);assert.ok(large.scroll<=321);checks++;
  await bulk.screenshot({path:path.join(output,'320-bulk-text-200.png'),fullPage:true});
  await bulk.evaluate(()=>document.documentElement.style.removeProperty('font-size'));
  await bulk.setViewport({width:390,height:844,deviceScaleFactor:1,isMobile:true,hasTouch:true});
  await bulk.setOfflineMode(true);await bulk.$eval('#bulk-submit',e=>e.scrollIntoView({block:'center'}));await bulk.click('#bulk-submit');assert.ok((await bulk.$eval('#bulk-error',e=>e.textContent)).includes('соединения'));checks++;
  await bulk.setOfflineMode(false);await new Promise(r=>setTimeout(r,250));scenario='denied';await bulk.evaluate(()=>window.dispatchEvent(new Event('online')));await bulk.waitForFunction(()=>document.querySelector('.shell').classList.contains('auth-blocked'));
  assert.equal(await bulk.$eval('#bulk-editor',e=>e.hidden),true);checks++;
  scenario='normal';await bulk.reload({waitUntil:'networkidle0'});await loaded(bulk);await bulk.click('.bottom-nav [data-nav="calendar"]');await bulk.click('#bulk-toggle');
  const pastCount=await bulk.$$eval('#calendar-grid button:disabled',els=>els.length);const todayNumber=Number(new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Moscow',day:'2-digit'}).format(new Date()));assert.equal(pastCount,todayNumber-1);checks++;
  await bulk.click('#bulk-toggle');await bulk.$eval('#month-jump',e=>{e.value='2099-07';e.dispatchEvent(new Event('change',{bubbles:true}));});await new Promise(r=>setTimeout(r,250));
  await bulk.click('#calendar-grid [data-day="2099-07-18"]');await bulk.click('#plan-unset');
  assert.equal(await bulk.$eval('#plan-start',e=>e.disabled),true);await bulk.click('#plan-submit');assert.match(await bulk.evaluate(()=>window.__links.at(-1)),/p_20990718_n_0000_0000_[a-f0-9]{32}$/);checks++;
  await bulk.close();
  const bulkDemo=await pageFor(false);await loaded(bulkDemo);await bulkDemo.click('.bottom-nav [data-nav="calendar"]');await bulkDemo.$eval('#month-jump',e=>{e.value='2099-07';e.dispatchEvent(new Event('change',{bubbles:true}));});await bulkDemo.click('#bulk-toggle');await bulkDemo.click('#calendar-grid [data-day="2099-07-10"]');
  await bulkDemo.$eval('#bulk-submit',e=>e.scrollIntoView({block:'center'}));await bulkDemo.click('#bulk-submit');assert.ok((await bulkDemo.$eval('#bulk-error',e=>e.textContent)).includes('ничего не сохранено'));assert.equal(await bulkDemo.evaluate(()=>(window.__links||[]).length),0);checks++;await bulkDemo.close();


  // Historical schedule corrections are separate from payroll-affecting actual edits.
  scenario='history';planRevision='a'.repeat(32);
  const history=await pageFor(true,390);await loaded(history);
  await history.click('.bottom-nav [data-nav="calendar"]');
  await history.$eval('#month-jump',e=>{e.value='2024-07';e.dispatchEvent(new Event('change',{bubbles:true}));});
  await history.waitForFunction(()=>document.getElementById('day-heading').textContent==='01.07.2024');
  await history.$eval('#calendar-grid [data-day="2024-07-10"]',e=>e.scrollIntoView({block:'center'}));
  await history.click('#calendar-grid [data-day="2024-07-10"]');
  assert.equal(await history.$eval('#day-details',e=>[...e.children].find(child=>!child.classList.contains('day-browse'))?.classList.contains('day-facts')),true);checks++;
  assert.equal(await history.$eval('#day-plan-disclosure',e=>e.open),false);checks++;
  assert.ok((await history.$eval('#day-plan-toggle',e=>e.textContent)).includes('выходной'));checks++;
  assert.equal(await history.$$eval('.day-fact-edit',els=>els.length),1);checks++;
  const historicalActions=actions;
  await history.$eval('.day-fact-edit',e=>e.scrollIntoView({block:'center'}));await history.click('.day-fact-edit');
  assert.equal(new URL(await history.evaluate(()=>window.__links.at(-1))).searchParams.get('start'),'q_fix_20240710_9');checks++;
  await history.$eval('#day-plan-toggle',e=>e.scrollIntoView({block:'center'}));await history.click('#day-plan-toggle');
  assert.equal(await history.$eval('#plan-off',e=>e.getAttribute('aria-pressed')),'true');checks++;
  assert.ok((await history.$eval('.plan-history-note',e=>e.textContent)).includes('реальные часы'));checks++;
  await history.$eval('#plan-work',e=>e.scrollIntoView({block:'center'}));await history.click('#plan-work');
  for(const [id,value] of [['#plan-start','16:00'],['#plan-end','02:00']])await history.$eval(id,(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));},value);
  await history.$eval('#plan-submit',e=>e.scrollIntoView({block:'center'}));await history.click('#plan-submit');
  assert.match(new URL(await history.evaluate(()=>window.__links.at(-1))).searchParams.get('start'),/^p_20240710_s_1600_0200_[a-f0-9]{32}$/);checks++;
  for(const choice of ['off','unset']){
    await history.$eval('#plan-'+choice,e=>e.scrollIntoView({block:'center'}));await history.click('#plan-'+choice);
    await history.$eval('#plan-submit',e=>e.scrollIntoView({block:'center'}));await history.click('#plan-submit');
    assert.ok(new URL(await history.evaluate(()=>window.__links.at(-1))).searchParams.get('start').startsWith('p_20240710_'+(choice==='off'?'o':'n')+'_0000_0000_'));checks++;
  }
  assert.equal(actions,historicalActions,'historical schedule controls never call a web-write API');checks++;
  for(const width of [320,390,1440]){
    // Resize the same session without toggling browser emulation (which reloads the page).
    await history.setViewport({width,height:844,deviceScaleFactor:1,isMobile:true,hasTouch:true});
    for(const theme of ['dark','light']){
      await history.$eval('body',(e,t)=>{e.dataset.theme=t;},theme);
      await history.$eval('#day-plan-disclosure',e=>{e.open=false;});await history.$eval('.day-sheet',e=>{e.scrollTop=0;});
      await history.screenshot({path:path.join(output,`${width}-${theme}-historical-facts.png`),fullPage:false});
      await history.$eval('#day-plan-disclosure',e=>{e.open=true;});
      await history.$eval('#day-plan-toggle',e=>e.scrollIntoView({block:'start'}));
      const fits=await history.evaluate(()=>innerWidth===document.documentElement.clientWidth&&[...document.querySelectorAll('#day-sheet button,#day-sheet input')].filter(e=>e.getClientRects().length).every(e=>{const r=e.getBoundingClientRect();return r.left>=-1&&r.right<=innerWidth+1;}));
      assert.equal(fits,true);checks++;
      await history.screenshot({path:path.join(output,`${width}-${theme}-historical-plan.png`),fullPage:false});
    }
  }
  await history.setViewport({width:390,height:844,deviceScaleFactor:1,isMobile:true,hasTouch:true});
  planRevision='c'.repeat(32);await history.evaluate(()=>window.dispatchEvent(new Event('online')));await new Promise(r=>setTimeout(r,350));
  const sentHistory=await history.evaluate(()=>window.__links.length);
  await history.$eval('#plan-submit',e=>e.scrollIntoView({block:'center'}));await history.click('#plan-submit');
  assert.equal(await history.evaluate(()=>window.__links.length),sentHistory);assert.ok((await history.$eval('#plan-error',e=>e.textContent)).includes('План изменился'));checks++;
  scenario='denied';await history.evaluate(()=>window.dispatchEvent(new Event('online')));await history.waitForFunction(()=>document.querySelector('.shell').classList.contains('auth-blocked'));
  assert.equal(await history.$eval('#day-sheet',e=>e.hidden),true);checks++;
  await history.close();scenario='normal';

  // App-level second lock: a PIN is never persisted in the browser, and
  // authenticated state reads are blocked until unlock after a fresh launch.
  mockSecurity={enabled:false,pin:"",session:"fixture-unlock-token",recovery:"a".repeat(32),bio:null};
  const securePage=await pageFor(true,390);await loaded(securePage);
  await securePage.evaluate(()=>{
   const manager={
    isInited:true,isBiometricAvailable:true,isAccessGranted:true,
    isBiometricTokenSaved:false,deviceId:"fixture-phone-0042",saved:"",
    init(callback){callback?.();},
    requestAccess(params,callback){this.isAccessGranted=true;callback(true);},
    updateBiometricToken(token,callback){this.saved=token;this.isBiometricTokenSaved=Boolean(token);callback(true);},
    authenticate(params,callback){callback(Boolean(this.saved),this.saved);}
   };
   window.Telegram.WebApp.BiometricManager=manager;
  });
  await securePage.click('.bottom-nav [data-nav="review"]');
  assert.equal(await securePage.$eval('#pin-setup-panel',e=>e.hidden),false,'owner may opt into PIN protection');checks++;
  await securePage.type('#pin-setup-code','628315');
  await securePage.type('#pin-setup-confirm','628315');
  await securePage.click('#pin-setup-form button[type="submit"]');
  await securePage.waitForFunction(()=>!document.getElementById('pin-recovery-card').hidden);
  assert.equal(mockSecurity.enabled,true,'server mock receives PIN enrollment');checks++;
  assert.equal(await securePage.evaluate(()=>Object.entries(localStorage).some(([key,value])=>/628315/.test(key+value))),false,'plaintext PIN is never stored in localStorage');checks++;
  assert.equal(await securePage.$eval('#pin-recovery-code',e=>e.textContent),'a'.repeat(32),'recovery shown once to owner');checks++;
  await securePage.click('#pin-recovery-saved');
  const beforeLockedReload=reads.length;
  await securePage.reload({waitUntil:'networkidle0'});
  await securePage.waitForFunction(()=>!document.getElementById('session-lock').hidden);
  assert.equal(reads.length,beforeLockedReload,'locked launch does not request private state');checks++;
  assert.equal(await securePage.$eval('#session-unlock',e=>e.hidden),true,'protected lock has no one-click bypass');checks++;
  assert.equal(await securePage.$eval('#pin-unlock-form',e=>e.hidden),false);
  await securePage.type('#pin-unlock-code','555555');
  await securePage.click('#pin-unlock-form button');
  await securePage.waitForFunction(()=>document.getElementById('pin-unlock-feedback').textContent.includes('Неверный PIN'));
  assert.equal(await securePage.$eval('#session-lock',e=>e.hidden),false,'incorrect PIN must not unlock');checks++;
  await securePage.$eval('#pin-unlock-code',e=>{e.value='';});
  await securePage.type('#pin-unlock-code','628315');
  await securePage.click('#pin-unlock-form button');
  await securePage.waitForFunction(()=>document.getElementById('session-lock').hidden);
  assert.ok(reads.length>beforeLockedReload,'correct PIN restores protected state reads');checks++;
  await securePage.click('.bottom-nav [data-nav="review"]');
  await securePage.click('#security-biometric-enable');
  await securePage.waitForFunction(()=>document.getElementById('security-biometric-status').textContent.includes('подключён'));
  assert.ok(mockSecurity.bio?.token,'native Telegram biometric token is enrolled');checks++;
  await securePage.click('#security-lock-now');
  await securePage.waitForFunction(()=>!document.getElementById('session-lock').hidden);
  await securePage.click('#security-biometric-unlock');
  await securePage.waitForFunction(()=>document.getElementById('session-lock').hidden);
  assert.equal(await securePage.$eval('#pin-manage-panel',e=>e.hidden),false,'native biometric auth restores access');checks++;
  await securePage.click('#pin-disable-details summary');
  await securePage.type('#pin-disable-code','628315');
  await securePage.click('#pin-disable-form button');
  await securePage.waitForFunction(()=>!document.getElementById('pin-setup-panel').hidden);
  assert.equal(mockSecurity.enabled,false,'PIN disable restores original Telegram-only mode');checks++;
  await securePage.close();

  // PERF_AUDIT: count redundant background state reads and idle clock mutations.
  const perfPage=await pageFor(true,390);await loaded(perfPage);
  const baseReads=reads.length;
  await perfPage.evaluate(()=>{for(let i=0;i<5;i++)document.dispatchEvent(new Event('visibilitychange'));});
  await new Promise(r=>setTimeout(r,350));
  const duplicateBackgroundReads=reads.length-baseReads;
  const beforeManual=reads.length;
  await perfPage.click('.bottom-nav [data-nav="review"]');await perfPage.click('#manual-refresh');
  await new Promise(r=>setTimeout(r,350));
  const forcedReads=reads.length-beforeManual;
  const beforeStale=reads.length;
  await perfPage.evaluate(()=>{const start=Date.now();Date.now=()=>start+20000;document.dispatchEvent(new Event("visibilitychange"));});
  await new Promise(r=>setTimeout(r,350));
  const staleBackgroundReads=reads.length-beforeStale;
  await perfPage.close();
  const idlePage=await pageFor(false,390);await loaded(idlePage);
  const idleMutations=await idlePage.evaluate(async()=>{
    let count=0;
    const observer=new MutationObserver(changes=>count+=changes.length);
    observer.observe(document.getElementById('shift-now'),{childList:true,characterData:true,subtree:true});
    await new Promise(resolve=>setTimeout(resolve,2250));
    observer.disconnect();return count;
  });
  await idlePage.close();
  console.log('PERF_AUDIT',JSON.stringify({duplicateBackgroundReads,forcedReads,staleBackgroundReads,idleMutations}));
  assert.equal(duplicateBackgroundReads,0,"rapid foreground events must not start redundant reads");
  assert.equal(forcedReads,1,"manual refresh must still issue a state request");
  assert.equal(staleBackgroundReads,1,"a truly stale foreground view must refresh");
  assert.equal(idleMutations,0,"the hidden clock must not repaint outside an active shift");checks+=4;

  assert.deepEqual(errors,[],'JavaScript/CSP errors');checks++;
  const report={status:'passed',checks,viewports:[320,360,390,430,768,1024,1440],screenshots:fs.readdirSync(output).filter(x=>x.endsWith('.png')).length,realTelegramClient:false,productionDataUsed:false};
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log('BROWSER_CHECKS',JSON.stringify(report));
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});