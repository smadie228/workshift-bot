/* Visual-privacy regression on a fictional local HTTP server. No production session. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const puppeteer=require(process.env.PUPPETEER_MODULE||'puppeteer');
const root=path.resolve(__dirname,'..'),output=process.env.PRIVACY_OUTPUT||path.join(require('node:os').tmpdir(),'money-privacy-'+process.pid);
fs.mkdirSync(output,{recursive:true});
const fakeInit='fake-telegram-session-for-isolated-browser-tests';
let actions=0,reports=0,reads=0,paymentHistoryCalls=0,checks=0,reportLinksCreatedWhileHidden=0,reportLinksRevokedOnHide=0;
const fakeToday=new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const fakeMonth=fakeToday.slice(0,7);
function fixture(month){
 const day=month+'-10';
 return {month,now:new Date().toISOString(),timezone:'Europe/Moscow',
  summary:{hours:19.5,shifts:2,earned:12000,total:12345.67,received:6000,additional:{bonus:445.67,allowance:0,reimbursement:0,deduction:100,net_adjustment:345.67}},
  active:null,break:{on_break:false,minutes:0,tracked:false},
  shifts:[{id:9,day,start:'16:00',end:'02:00',next_day:true,break_minutes:30,hours:9.5,earnings:6000,source:'button',revision:'a'.repeat(64)}],
  plan:[],office:{},office_status:{confirmed_days:0,complete:false},issues:[],
  periods:[{period:1,hours:19.5,earnings:12000,adjustments:345.67,total:12345.67,received:6000,shifts:2},{period:2,hours:0,earnings:0,adjustments:0,total:0,received:0,shifts:0}],
  rates:[{rate:615.38,hours:19.5,shifts:2,earned:12000}],
  weeks:[{label:'01.11',hours:19.5}],
  forecast:{future_shifts:0,future_hours:0,future_pay:0,assumed_break_minutes:30},
  capabilities:{writes:true,actual_edits:true,workspace:true,attendance_recovery:true},agenda:null,
  actual_editor:{default_break_minutes:30,revisions:{[day]:'a'.repeat(64)}},attendance_revision:'a'.repeat(64),
  attendance_hints:{in:{ask_time:false},out:{ask_time:false}},
  plan_editor:{mode:'chat_confirmation',direct_writes:true,bot_username:'FictionalShiftBot',revisions:{[day]:'a'.repeat(64)}}};
}
function workspace(month){
 return {month,today:fakeToday,revision:'a'.repeat(64),current_rate:600,default_break_minutes:30,
  payments:[{id:1,work_month:month,paid_on:fakeToday,period:1,amount_rub:6000,note:'Аванс 6000 рублей'}],
  adjustments:[{id:2,day:fakeToday,category:'bonus',amount_rub:445.67,note:'Бонус 445,67'}],
  archives:[],rates:[{effective_from:'2026-01-01',rate_rub:600}],series:[]};
}
function activity(){
 return {items:[{id:17,at:new Date().toISOString(),category:'money',title:'Поступление добавлено',day:fakeToday,
   fields:[{label:'Сумма',value:'6000,00 ₽'},{label:'Было',value:'445,67 ₽'}],manually_timed:false,estimated:false}],
   has_more:false,next_before_id:null,read_only:true};
}
const routes={'/mini/':'index.html','/mini/app.js':'app.js','/mini/app.css':'app.css',
 '/mini/logo.svg':'logo.svg','/mini/echarts.min.js':'vendor/echarts.min.js',
 '/mini/telegram-web-app.js':'vendor/telegram-web-app.js'};
const sdk="window.Telegram={WebApp:{initData:'"+fakeInit+"',colorScheme:'dark',ready(){},expand(){},openTelegramLink(){},setHeaderColor(){},setBackgroundColor(){},HapticFeedback:{notificationOccurred(){},selectionChanged(){}}}};";
const csp="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'";
async function installPrivacyClock(page){
 await page.evaluateOnNewDocument(()=>{
  Object.defineProperty(window,'DeviceMotionEvent',{configurable:true,value:class{static requestPermission(){return Promise.resolve('granted');}}});
  const nativeSet=window.setTimeout.bind(window),nativeClear=window.clearTimeout.bind(window),tasks=new Map(),tracked=new Set([15000,30000,60000,120000,180000,300000,600000,1800000]);let id=0,created={},cleared={};
  window.setTimeout=(fn,delay,...args)=>{if(tracked.has(delay)&&typeof fn==='function'){const key=++id;tasks.set(key,{fn,args,delay});created[delay]=(created[delay]||0)+1;return key;}return nativeSet(fn,delay,...args);};
  window.clearTimeout=key=>{const item=tasks.get(key);if(item){tasks.delete(key);cleared[item.delay]=(cleared[item.delay]||0)+1;return;}return nativeClear(key);};
  window.__moneyPrivacyClock={stats:(delay=30000)=>({pending:[...tasks.values()].filter(x=>x.delay===delay).length,created:created[delay]||0,cleared:cleared[delay]||0}),fire:(delay=30000)=>{const item=[...tasks.entries()].filter(x=>x[1].delay===delay).at(-1);if(item){tasks.delete(item[0]);item[1].fn(...item[1].args);}}};
 });
}
const server=http.createServer(async(req,res)=>{
 const url=new URL(req.url,'http://localhost'),name=url.pathname;
 const json=(status,object)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(object));};
 if(name.startsWith('/mini/api/')){
  let body='';for await(const block of req)body+=block;let input={};try{input=JSON.parse(body||'{}');}catch(e){}
  if(req.headers['x-telegram-init-data']!==fakeInit)return json(403,{error:'forbidden'});
  if(name==='/mini/api/state'){reads++;return json(200,fixture(input.month||fakeMonth));}
  if(name==='/mini/api/workspace'){
   if(input.view==='activity')return json(200,activity());
   if(input.view==='payment_history'){paymentHistoryCalls++;return json(200,{limit:60,has_more:false,payments:[
    {id:21,work_month:fakeMonth,period:1,paid_on:fakeToday,amount_rub:'6000.00',note:'Advance'},
    {id:20,work_month:fakeMonth,period:2,paid_on:fakeToday,amount_rub:'2500.50',note:'Second part'}
   ]});}
   return json(200,workspace(input.month||fakeMonth));
  }
  if(name==='/mini/api/analytics'){
   const year=Number(input.year)||2026,months=Array.from({length:12},(_,i)=>({month:year+'-'+String(i+1).padStart(2,'0'),hours:i===10?19.5:0,shifts:i===10?2:0,earned:i===10?12000:0,total:i===10?12345.67:0,received:i===10?6000:0}));
   return json(200,{year,months,hours:19.5,shifts:2,earned:12000,total:12345.67,received:6000});
  }
  if(name==='/mini/api/action'){actions++;return json(500,{error:'Not permitted in visual test'});}
  if(name==='/mini/api/report'){reports++;await new Promise(resolve=>setTimeout(resolve,450));return json(200,{base64:Buffer.from('FICTIONAL REPORT ONLY').toString('base64'),mime:'application/pdf',name:'fictional-report.pdf'});}
  return json(404,{error:'missing fixture'});
 }
 const file=routes[name];if(!file){res.writeHead(404);res.end();return;}
 const mime=file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':file.endsWith('.js')?'application/javascript':'text/html; charset=utf-8';
 res.writeHead(200,{'Content-Type':mime,'Content-Security-Policy':csp,'Cache-Control':'no-store'});
 res.end(fs.readFileSync(path.join(root,'miniapp',file)));
});
async function main(){
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const url='http://127.0.0.1:'+server.address().port+'/mini/';
 let browser;const errors=[];
 try{
  browser=await puppeteer.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-first-run','--disable-extensions']});
  for(const width of [320,390,1024,1440]){
   const context=await browser.createBrowserContext();
   const page=await context.newPage();
   await page.setViewport({width,height:width<700?844:900,isMobile:width<700,hasTouch:width<700});
   await installPrivacyClock(page);
   page.on('pageerror',e=>errors.push(e.message));
   await page.setRequestInterception(true);
   page.on('request',request=>request.url().endsWith('/mini/telegram-web-app.js')?
     request.respond({status:200,contentType:'application/javascript',body:sdk}):request.continue());
   await page.goto(url,{waitUntil:'networkidle0'});
   await page.waitForFunction(()=>document.getElementById('home-hours').textContent!=='—');
   assert.equal(await page.$eval('body',e=>e.dataset.moneyPrivate),'true');
   assert.equal(await page.$eval('#home-money',e=>e.textContent),'•••• ₽');checks+=2;
   assert.equal(await page.$eval('.masthead [data-money-toggle]',e=>e.getAttribute('aria-label')),'Показать денежные суммы');checks++;
   assert.ok((await page.$eval('#money-privacy-tip',e=>e.textContent)).includes('разделе «Ещё»'));checks++;
   await page.click('.bottom-nav [data-nav="salary"]');
   assert.equal(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽');
   assert.equal(await page.$eval('#salary-received',e=>e.textContent),'•••• ₽');
   assert.equal(await page.$eval('#salary-balance',e=>e.textContent),'•••• ₽');
   assert.equal(await page.$eval('#cashflow-progress-track',e=>e.getAttribute('aria-valuenow')),'0');
   assert.equal(await page.$eval('#chart-money',e=>e.disabled),true);
   assert.equal(await page.$eval('#payment-history-load',e=>e.disabled),true);checks+=6;
   const hiddenType=await page.evaluate(()=>window.echarts.getInstanceByDom(document.getElementById('income-chart')).getOption().series[0].type);
   assert.equal(hiddenType,'bar');checks++;
   await page.screenshot({path:path.join(output,width+'-private-salary.png'),fullPage:true});
   await page.click('.masthead [data-money-toggle]');
   assert.equal(await page.$eval('body',e=>e.dataset.moneyPrivate),'false');
   assert.match((await page.$eval('#salary-total',e=>e.textContent)).replace(/\s/g,''),/12345,67/);
   assert.match((await page.$eval('#salary-balance',e=>e.textContent)).replace(/\s/g,''),/6345,67/);
   assert.ok((await page.$eval('#cashflow-progress-label',e=>e.textContent)).includes('49%'));
   assert.equal(await page.$eval('#cashflow-progress-track',e=>e.getAttribute('aria-valuenow')),'49');
   assert.equal(await page.$$eval('#salary-periods .metric',x=>x.length),2);
   assert.ok((await page.$eval('#salary-periods',e=>e.textContent)).includes('Получено'));
   assert.equal(await page.evaluate(()=>window.echarts.getInstanceByDom(document.getElementById('income-chart')).getOption().series[0].type),'line');
   await page.click('#payment-history-load');
   await page.waitForFunction(()=>document.querySelectorAll('#payment-history-list .payment-history-item').length===2);
   assert.ok((await page.$eval('#payment-history-list',e=>e.textContent)).includes('Advance'));
   assert.ok((await page.$eval('#payment-history-list',e=>e.textContent)).includes('Second part'));
   assert.equal(await page.$eval('#payment-history-load',e=>e.getAttribute('aria-expanded')),'true');checks+=4;
   await page.click('.masthead [data-money-toggle]');
   assert.equal(await page.$eval('#payment-history-list',e=>e.hidden),true);
   assert.ok(!(await page.$eval('#payment-history-list',e=>e.textContent)).includes('6000'));checks+=2;
   await page.click('.masthead [data-money-toggle]');
   assert.equal(await page.$eval('#payment-history-load',e=>e.disabled),false);checks++;
   await page.screenshot({path:path.join(output,width+'-revealed-salary.png'),fullPage:true});
   await page.click('#annual-toggle');await page.waitForFunction(()=>document.querySelectorAll('#annual-months button').length===12);
   assert.equal(await page.$$eval('#annual-metrics .metric',x=>x.length),4);
   assert.equal(await page.$eval('#annual-finance-chart',e=>e.hidden),false);
   assert.equal(await page.evaluate(()=>window.echarts.getInstanceByDom(document.getElementById('annual-finance-chart')).getOption().series.length),2);checks+=3;
   const annualText=await page.$eval('#annual-months',e=>e.textContent);assert.ok(annualText.replaceAll(' ','').replaceAll('\u00a0','').replaceAll('\u202f','').includes('6000'));checks++;
   await page.click('.masthead [data-money-toggle]');
   assert.equal(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽');
   assert.equal(await page.$eval('#chart-money',e=>e.disabled),true);
   assert.equal(await page.$eval('#annual-finance-chart',e=>e.hidden),true);
   assert.ok(!(await page.$eval('#annual-months',e=>e.textContent)).includes('6000'));checks+=4;
   if(width===390){
    await page.click('.bottom-nav [data-nav="review"]');
    await page.click('#phone-face-down');await page.click('#phone-shake-hide');await page.click('#phone-haptics');
    await page.evaluate(()=>{window.__vibrations=0;Object.defineProperty(navigator,'vibrate',{configurable:true,value:()=>{window.__vibrations++;return true;}});});
    await page.click('#phone-motion-enable');
    await page.waitForFunction(()=>document.getElementById('phone-motion-status').textContent.includes('подключён'));
    await page.click('#phone-motion-calibrate');
    await page.evaluate(async()=>{for(let i=0;i<9;i++){const e=new Event('devicemotion');Object.defineProperties(e,{accelerationIncludingGravity:{value:{x:0,y:0,z:9.8}},acceleration:{value:null}});window.dispatchEvent(e);await new Promise(r=>setTimeout(r,30));}});
    await page.waitForFunction(()=>localStorage.getItem('workshift_phone_face_up_sign_v1')!==null);
    await page.click('.bottom-nav [data-nav="salary"]');await page.click('.masthead [data-money-toggle]');
    await page.evaluate(async()=>{for(let i=0;i<8;i++){const e=new Event('devicemotion');Object.defineProperties(e,{accelerationIncludingGravity:{value:{x:0,y:0,z:-9.8}},acceleration:{value:null}});window.dispatchEvent(e);await new Promise(r=>setTimeout(r,110));}});
    await page.waitForFunction(()=>document.body.dataset.moneyPrivate==='true');
    assert.equal(await page.$eval('#salary-total',e=>e.textContent),'•••• ₽');checks++;
    await page.click('.bottom-nav [data-nav="review"]');await page.click('#phone-face-down');
    await page.click('.bottom-nav [data-nav="salary"]');await page.click('.masthead [data-money-toggle]');
    await page.evaluate(async()=>{for(let i=0;i<2;i++){const e=new Event('devicemotion');Object.defineProperties(e,{acceleration:{value:{x:18,y:0,z:0}},accelerationIncludingGravity:{value:null}});window.dispatchEvent(e);await new Promise(r=>setTimeout(r,260));}});
    await page.waitForFunction(()=>document.body.dataset.moneyPrivate==='true');
    assert.ok(await page.evaluate(()=>window.__vibrations>0));checks+=2;
    await page.click('.masthead [data-money-toggle]');await page.keyboard.down('Alt');await page.keyboard.down('Shift');await page.keyboard.press('H');await page.keyboard.up('Shift');await page.keyboard.up('Alt');
    assert.equal(await page.$eval('body',e=>e.dataset.moneyPrivate),'true');checks++;
   }
   await page.click('.bottom-nav [data-nav="calendar"]');await page.click('#mode-table');
   assert.ok(!(await page.$eval('#ledger-body',e=>e.textContent)).includes('6 000'));checks++;
   await page.click('#ledger-body .day-link');await page.waitForFunction(()=>!document.getElementById('day-sheet').hidden);
   assert.ok(!(await page.$eval('#day-details',e=>e.textContent)).includes('6 000'));checks++;
   await page.click('#day-sheet [data-money-toggle]');
   assert.ok((await page.$eval('#day-details',e=>e.textContent)).replace(/\s/g,'').includes('6000'));checks++;
   await page.click('#day-sheet [data-money-toggle]');
   assert.ok(!(await page.$eval('#day-details',e=>e.textContent)).replace(/\s/g,'').includes('6000'));checks++;
   await page.click('#day-close');await page.click('.masthead [data-money-toggle]');
   await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
   assert.equal(await page.$eval('#home-money',e=>e.textContent),'•••• ₽');
   assert.equal(await page.$eval('#session-lock',e=>e.hidden),false);checks+=2;
   await page.reload({waitUntil:'networkidle0'});
   assert.equal(await page.$eval('body',e=>e.dataset.moneyPrivate),'true');checks++;
   await page.click('.masthead [data-money-toggle]');
   let clock=await page.evaluate(()=>window.__moneyPrivacyClock.stats());
   assert.deepEqual(clock,{pending:1,created:1,cleared:0});
   await page.evaluate(()=>document.dispatchEvent(new Event('pointerdown',{bubbles:true})));
   clock=await page.evaluate(()=>window.__moneyPrivacyClock.stats());
   assert.deepEqual(clock,{pending:1,created:2,cleared:1});
   await page.evaluate(()=>window.__moneyPrivacyClock.fire());
   assert.equal(await page.$eval('body',e=>e.dataset.moneyPrivate),'true');checks+=4;
   await page.click('.bottom-nav [data-nav="review"]');
   await page.select('#money-privacy-timeout','60');
   assert.equal(await page.evaluate(()=>localStorage.getItem('workshift_money_privacy_timeout_v1')),'60000');checks++;
   await page.click('.bottom-nav [data-nav="salary"]');await page.click('.masthead [data-money-toggle]');
   assert.equal((await page.evaluate(()=>window.__moneyPrivacyClock.stats(60000))).pending,1);checks++;
   await page.click('.masthead [data-money-toggle]');
   await page.click('.bottom-nav [data-nav="review"]');
   await page.select('#money-privacy-timeout','30');
   await page.select('#session-lock-timeout','3');
   assert.equal(await page.evaluate(()=>localStorage.getItem('workshift_session_lock_minutes_v1')),'3');
   assert.equal((await page.evaluate(()=>window.__moneyPrivacyClock.stats(180000))).pending,1);checks+=2;
   await page.evaluate(()=>window.__moneyPrivacyClock.fire(180000));
   assert.equal(await page.$eval('#session-lock',e=>e.hidden),false);
   assert.equal(await page.$eval('main',e=>e.inert),true);checks+=2;
   await page.click('#session-unlock');
   await page.waitForFunction(()=>document.getElementById('session-lock').hidden);
   assert.equal(await page.$eval('body',e=>e.dataset.sessionLocked),'false');
   assert.equal(await page.$eval('#home-money',e=>e.textContent),'•••• ₽');checks+=2;
   await context.close();
  }
  const page=await browser.newPage();await page.setViewport({width:390,height:844,isMobile:true,hasTouch:true});await installPrivacyClock(page);
  page.on('pageerror',e=>errors.push(e.message));
  await page.setRequestInterception(true);page.on('request',r=>r.url().endsWith('/mini/telegram-web-app.js')?
   r.respond({status:200,contentType:'application/javascript',body:sdk}):r.continue());
  await page.goto(url,{waitUntil:'networkidle0'});await page.waitForFunction(()=>document.getElementById('home-hours').textContent!=='—');
  await page.click('.bottom-nav [data-nav="salary"]');
  await page.click('#page-salary [data-workspace="money"]');
  await page.waitForSelector('#workspace-content .ws-record');
  assert.equal(await page.$eval('#workspace-content .ws-record-amount',e=>e.textContent),'•••• ₽');
  assert.ok((await page.$eval('#workspace-content',e=>e.textContent)).includes('Комментарий скрыт'));checks+=2;
  await page.click('#workspace-content .ws-record .small-switch');
  assert.equal(await page.$('#workspace-form'),null,'financial editor must not open with sensitive value while hidden');checks++;
  await page.click('#workspace-content .main-action');
  await page.waitForSelector('#workspace-form');
  assert.equal(await page.$eval('#workspace-form [name="amount"]',e=>String(e.value)),'6000');checks++;
  await page.$eval('#workspace-form [name="amount"]',e=>{e.value='7000';e.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.click('#workspace-sheet [data-money-toggle]');
  assert.equal(await page.$eval('#workspace-sheet',e=>e.classList.contains('privacy-locked')),true);
  assert.equal(await page.$eval('#workspace-privacy-lock',e=>e.hidden),false);checks+=2;
  await page.click('#workspace-sheet [data-money-toggle]');
  assert.equal(await page.$eval('#workspace-form [name="amount"]',e=>e.value),'7000');checks++;
  await page.click('#workspace-close');await page.waitForSelector('#modal-confirm');await page.click('#modal-confirm');
  await page.waitForFunction(()=>document.getElementById('workspace-sheet').hidden);
  await page.click('.masthead [data-money-toggle]'); // hide again before inspecting history
  await page.click('.bottom-nav [data-nav="review"]');await page.click('#page-review [data-activity]');
  await page.waitForFunction(()=>document.querySelectorAll('#activity-list .activity-item').length>0);
  assert.ok(!(await page.$eval('#activity-list',e=>e.textContent)).includes('6000,00'));checks++;
  await page.click('#workspace-sheet [data-money-toggle]');
  assert.ok((await page.$eval('#activity-list',e=>e.textContent)).includes('6000,00'));checks++;
  await page.click('#workspace-close');
  await page.click('.masthead [data-money-toggle]');
  await page.click('.bottom-nav [data-nav="salary"]');
  await page.click('#page-salary [data-chat="reports"]');
  await page.waitForSelector('#ws-report-build');await page.click('#ws-report-build');
  assert.ok((await page.$eval('#workspace-error',e=>e.textContent)).includes('Суммы скрыты'));
  assert.equal(reports,0);assert.equal(actions,0);checks+=3;
  await page.click('#workspace-sheet [data-money-toggle]');
  await page.click('#ws-report-build');await new Promise(resolve=>setTimeout(resolve,80));
  await page.click('#workspace-sheet [data-money-toggle]');
  await page.waitForFunction(()=>document.getElementById('workspace-error').textContent.includes('Суммы скрыты'));
  const hiddenReportLink=await page.$('#ws-download');if(hiddenReportLink)reportLinksCreatedWhileHidden++;
  assert.equal(hiddenReportLink,null);checks++;
  await page.click('#workspace-sheet [data-money-toggle]');
  await page.click('#ws-report-build');await page.waitForSelector('#ws-download');
  assert.equal(reports,2);checks++;
  await page.click('#workspace-sheet [data-money-toggle]');
  const revokedReportLink=await page.$('#ws-download');if(!revokedReportLink)reportLinksRevokedOnHide++;
  assert.equal(revokedReportLink,null);
  assert.ok((await page.$eval('#ws-report-result',e=>e.textContent)).includes('Ссылка на отчёт закрыта'));checks+=2;
  assert.equal(actions,0);
  await page.close();
  assert.deepEqual(errors,[]);
  const report={status:'passed',checks,viewports:[320,390,1024,1440],privateActions:actions,reportCalls:reports,paymentHistoryCalls,
    reportLinksCreatedWhileHidden,reportLinksRevokedOnHide,
    productionDataUsed:false,realTelegramClientTested:false,errors,
    screenshots:fs.readdirSync(output).filter(n=>n.endsWith('.png')).length};
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
  console.log('MONEY_PRIVACY_CHECKS',JSON.stringify(report));
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(e=>{console.error(e.stack);process.exitCode=1;});
