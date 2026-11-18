/* Real Chromium UI checks, only fictional fixtures; no production tokens or data. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const puppeteer=require(process.env.PUPPETEER_MODULE||'puppeteer');
const root=path.resolve(__dirname,'..');
const output=process.env.BROWSER_OUTPUT||path.join(require('node:os').tmpdir(),'workshift-browser-'+process.pid);
fs.mkdirSync(output,{recursive:true});
let scenario='normal', actions=0, reads=[], mode='demo', operationResult=null;
const errors=[];let checks=0;
const csp="default-src 'none'; script-src 'self' https://telegram.org; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'self' https://web.telegram.org https://*.web.telegram.org";
function fixture(month){
 const active=['active','break','write','uncertain'].includes(scenario);
 const paused=scenario==='break';
 return {month,now:new Date().toISOString(),summary:{hours:36.75,shifts:4,earned:10290,total:10790,received:6000,additional:{bonus:400,allowance:0,reimbursement:200,deduction:100,net_adjustment:500}},
  active:active?{id:11,start:new Date(Date.now()-7200000).toISOString()}:null,
  break:{active,on_break:paused,tracked:true,minutes:0,break_id:paused?12:null,break_started_utc:paused?new Date(Date.now()-600000).toISOString():null},
  shifts:[{id:9,day:month+'-10',start:'16:00',end:'02:00',next_day:true,break_minutes:0,hours:10,earnings:2800,source:'button'}],
  plan:[{day:month+'-18',status:'shift',start:'16:00',end:'02:00',source:'manual'},{day:month+'-19',status:'off',source:'manual'}],
  office:{},office_status:{confirmed_days:0,complete:false},issues:[],
  periods:[{period:1,hours:20,earnings:5600,shifts:2},{period:2,hours:16.75,earnings:4690,shifts:2}],
  weeks:[{label:'01.01',hours:0},{label:'08.01',hours:20},{label:'15.01',hours:15},{label:'22.01',hours:30},{label:'29.01',hours:25},{label:'05.02',hours:10}],
  capabilities:{writes:['write','uncertain'].includes(scenario),offline_submission:false},timezone:'Europe/Moscow'};
}
const server=http.createServer(async(req,res)=>{
 const route=new URL(req.url,'http://localhost').pathname;
 const json=(status,value)=>{if(res.destroyed)return;res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
 if(route.startsWith('/mini/api/')){
  let body='';for await(const chunk of req)body+=chunk;
  const value=JSON.parse(body||'{}');
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
 const files={'/mini/':['index.html','text/html; charset=utf-8'],'/mini/app.js':['app.js','application/javascript; charset=utf-8'],'/mini/app.css':['app.css','text/css; charset=utf-8'],'/mini/logo.svg':['logo.svg','image/svg+xml']};
 if(!files[route]){res.writeHead(404);res.end();return;}
 const [file,type]=files[route];res.writeHead(200,{'Content-Type':type,'Content-Security-Policy':csp,'Cache-Control':'no-store'});res.end(fs.readFileSync(path.join(root,'miniapp',file)));
});
const sdk=`window.Telegram={WebApp:{initData:window.__signed?'fictional-ui-fixture':'',colorScheme:'dark',ready(){},expand(){},close(){window.__closed=(window.__closed||0)+1;},setHeaderColor(){},setBackgroundColor(){},HapticFeedback:{notificationOccurred(){},selectionChanged(){}}}};`;
(async()=>{
 let browser;
 try{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const url=`http://127.0.0.1:${server.address().port}/mini/`;
  browser=await puppeteer.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-first-run','--disable-extensions',...(process.env.CI?['--no-sandbox']:[])]});
  async function pageFor(signed=false,width=390){
   const page=await browser.newPage();await page.setViewport({width,height:844,deviceScaleFactor:1,isMobile:true,hasTouch:true});
   await page.evaluateOnNewDocument(flag=>{window.__signed=flag;window.__closed=0;},signed);
   await page.setRequestInterception(true);
   page.on('request',request=>request.url().startsWith('https://telegram.org/')?request.respond({status:200,contentType:'application/javascript',body:sdk}):request.continue());
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
  for(const width of [360,390,430,768]){
   scenario='normal';const page=await pageFor(false,width);await loaded(page);
   for(const theme of ['dark','light']){
    if(await page.$eval('body',e=>e.dataset.theme)!==theme)await page.click('#theme-switch');
    assert.equal(await page.$eval('body',e=>e.dataset.theme),theme);
    await new Promise(r=>setTimeout(r,300));
    for(const tab of ['home','calendar','salary','review']){
     await page.click(`.bottom-nav [data-nav="${tab}"]`);
     await new Promise(r=>setTimeout(r,220));
     await layout(page,`${width}/${theme}/${tab}`);
     await page.screenshot({path:path.join(output,`${width}-${theme}-${tab}.png`),fullPage:true});
    }
   }
   await page.close();
  }
  assert.equal(actions,0,'demo never writes');checks++;
  const page=await pageFor(true);await loaded(page);
  assert.equal(await page.$eval('#demo-banner',e=>e.hidden),true);
  await page.click('#shift-action');assert.equal(await page.evaluate(()=>window.__closed),1);assert.equal(actions,0);checks++;
  await page.click('.bottom-nav [data-nav="salary"]');
  assert.equal(await page.$$eval('#salary-periods article',x=>x.length),2);
  assert.ok((await page.$eval('#salary-reimbursements',e=>e.textContent)).includes('200'));checks++;
  scenario='active';await page.reload({waitUntil:'networkidle0'});await loaded(page);
  assert.ok((await page.$eval('#shift-break',e=>e.textContent)).startsWith('0 мин'));
  assert.equal(await page.$eval('#break-action',e=>e.hidden),false);
  await page.screenshot({path:path.join(output,'390-active.png'),fullPage:true});checks++;
  scenario='break';await page.reload({waitUntil:'networkidle0'});await loaded(page);
  await new Promise(r=>setTimeout(r,1100));
  assert.ok((await page.$eval('#shift-break',e=>e.textContent)).includes('идёт'));
  await layout(page,'active-break');await page.screenshot({path:path.join(output,'390-break.png'),fullPage:true});checks++;
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
  scenario='write';await page.reload({waitUntil:'networkidle0'});await loaded(page);
  await page.evaluate(()=>{document.getElementById('shift-action').click();document.getElementById('shift-action').click();});
  assert.equal(await page.$eval('#modal',e=>e.hidden),false);
  await page.keyboard.press('Escape');assert.equal(actions,0);checks++;
  await page.click('#shift-action');await page.click('#modal-confirm');await page.waitForFunction(()=>document.getElementById('modal').hidden);
  await new Promise(r=>setTimeout(r,200));assert.equal(actions,1);checks++;
  scenario='uncertain';actions=0;operationResult=null;
  await page.reload({waitUntil:'networkidle0'});await loaded(page);
  await page.click('#shift-action');await page.click('#modal-confirm');await new Promise(r=>setTimeout(r,300));
  await page.click('#shift-action');assert.equal(await page.$eval('#modal-title',e=>e.textContent),'Проверить предыдущую отметку');
  await page.click('#modal-confirm');await new Promise(r=>setTimeout(r,300));assert.equal(actions,2);checks++;
  await page.close();
  assert.deepEqual(errors,[],'JavaScript/CSP errors');checks++;
  const report={status:'passed',checks,viewports:[360,390,430,768],screenshots:fs.readdirSync(output).filter(x=>x.endsWith('.png')).length,realTelegramClient:false,productionDataUsed:false};
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log('BROWSER_CHECKS',JSON.stringify(report));
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});