/* Full browser -> real Python HTTP -> temporary SQLite tests. Fictional credentials only. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const puppeteer=require(process.env.PUPPETEER_MODULE||'puppeteer');
const base=process.env.WRITE_FIXTURE_URL||'http://127.0.0.1:19866',out=process.env.WRITE_E2E_OUTPUT||path.join(require('node:os').tmpdir(),'write-e2e-'+process.pid);
fs.mkdirSync(out,{recursive:true});
const fields={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:77})};
const secret=crypto.createHmac('sha256','WebAppData').update('fictional-write-e2e-token').digest();
fields.hash=crypto.createHmac('sha256',secret).update(Object.entries(fields).sort().map(([k,v])=>k+'='+v).join('\n')).digest('hex');
const init=new URLSearchParams(fields).toString();
const sdk=`window.Telegram={WebApp:{initData:${JSON.stringify(init)},colorScheme:'dark',ready(){},expand(){},close(){window.__closed=(window.__closed||0)+1;},openTelegramLink(){window.__links=(window.__links||0)+1;},setHeaderColor(){},setBackgroundColor(){},HapticFeedback:{selectionChanged(){},notificationOccurred(){}},BackButton:{show(){},hide(){},onClick(){}}}};`;
let checks=0,dropAck=false,dropped=null,forwarded=[];
const errors=[];
async function api(payload,route='state'){
 const r=await fetch(base+'/mini/api/'+route,{method:'POST',headers:{'Content-Type':'application/json','Origin':base,'X-Telegram-Init-Data':init},body:JSON.stringify(payload)});
 const data=await r.json();assert.equal(r.status,200,JSON.stringify(data));return data;
}
(async()=>{let browser;try{
 browser=await puppeteer.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-first-run','--disable-extensions']});
 const page=await browser.newPage();await page.setViewport({width:390,height:844,isMobile:true,hasTouch:true,deviceScaleFactor:1});
 page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);
 page.on('request',async r=>{
  try{
   if(r.url().endsWith('/telegram-web-app.js'))return r.respond({status:200,contentType:'application/javascript',body:sdk});
   if(r.url().endsWith('/mini/api/action')){
    forwarded.push(JSON.parse(r.postData()));
    if(dropAck){dropAck=false;dropped=JSON.parse(r.postData());const response=await fetch(r.url(),{method:'POST',headers:{'Content-Type':'application/json','Origin':base,'X-Telegram-Init-Data':init},body:r.postData()});assert.equal(response.status,200);await response.text();return r.abort('connectionfailed');}
   }
   return r.continue();
  }catch(e){errors.push(e.message);try{await r.abort();}catch(x){}}
 });
 await page.goto(base+'/mini/',{waitUntil:'networkidle0'});await page.waitForFunction(()=>document.getElementById('connection').textContent.includes('Подключено'));
 assert.equal(await page.$eval('#shift-action',e=>e.textContent),'Пришёл');checks++;
 const before=await api({});
 await page.evaluate(()=>{const b=document.getElementById('shift-action');b.click();b.click();});
 await page.waitForFunction(()=>document.getElementById('shift-action').textContent==='Ушёл'&&!document.getElementById('shift-action').disabled);
 const started=await api({});assert.ok(started.active?.id);assert.equal(forwarded.filter(p=>p.action==='in').length,1);checks+=2;
 await page.screenshot({path:path.join(out,'390-active.png')});
 await page.click('#shift-action');await page.click('#modal-cancel');assert.equal((await api({})).active.id,started.active.id);checks++;
 // Commit on the real server, but lose its response. Retry must retrieve one receipt.
 dropAck=true;await page.click('#shift-action');await page.click('#modal-confirm');
 await page.waitForFunction(()=>!document.getElementById('write-recovery').hidden&&!document.getElementById('write-retry').disabled);
 const closed=await api({});assert.equal(closed.active,null);checks++;
 await page.reload({waitUntil:'networkidle0'});await page.waitForFunction(()=>!document.getElementById('write-retry').disabled);
 await page.$eval('#write-retry',e=>e.scrollIntoView({block:'center'}));await page.click('#write-retry');await page.click('#modal-confirm');
 await page.waitForFunction(()=>document.getElementById('write-recovery').hidden);
 assert.deepEqual(forwarded.at(-1),dropped);assert.equal((await api({})).shifts.length,closed.shifts.length);checks+=2;
 async function click(selector){await page.$eval(selector,e=>e.scrollIntoView({block:'center',behavior:'instant'}));await page.click(selector);}
 async function fill(selector,value){await page.$eval(selector,(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));},value);}
 await page.click('.bottom-nav [data-nav="calendar"]');await fill('#month-jump','2024-07');await page.$eval('#month-jump',e=>e.dispatchEvent(new Event('change',{bubbles:true})));
 await page.waitForFunction(()=>document.getElementById('month-jump').value==='2024-07'&&document.querySelector('#calendar-grid [data-day="2024-07-10"]'));
 await click('#calendar-grid [data-day="2024-07-10"]');await click('.day-fact-edit');
 assert.equal(await page.$eval('#actual-start',e=>e.value),'16:00');assert.equal(await page.$eval('#actual-break',e=>e.value),'30');checks+=2;
 await fill('#actual-start','17:30');await fill('#actual-end','01:30');await fill('#actual-break','15');await fill('#actual-reason','Проверка вымышленных данных');
 await click('#actual-save');
 if(await page.$eval('#modal',e=>e.hidden)){
  console.log('EDITOR_DIAGNOSTIC',JSON.stringify(await page.evaluate(()=>({error:document.getElementById('actual-error')?.textContent,toast:document.getElementById('toast').textContent,dayHidden:document.getElementById('day-sheet').hidden,focus:document.activeElement.id,fields:[...document.querySelectorAll('#actual-form input')].map(e=>({id:e.id,value:e.value,valid:e.checkValidity(),message:e.validationMessage,disabled:e.disabled})),button:document.getElementById('actual-save')?.outerHTML}))));
  await page.screenshot({path:path.join(out,'editor-failure.png')});
 }
 assert.equal(await page.$eval('#modal',e=>e.hidden),false);assert.ok((await page.$eval('#modal-text',e=>e.textContent)).includes('Было:'));checks+=2;
 await page.keyboard.press('Escape');assert.equal(await page.$eval('#day-sheet',e=>e.hidden),false);assert.ok(await page.$('#actual-form'));checks+=2;
 assert.equal((await api({month:'2024-07'})).shifts[0].start,'16:00');checks++;
 for(const width of [320,390,1440]){
  await page.setViewport({width,height:844,isMobile:true,hasTouch:true,deviceScaleFactor:1});
  for(const theme of ['dark','light']){
   await page.$eval('body',(e,t)=>{e.dataset.theme=t;},theme);
   const fits=await page.evaluate(()=>({w:innerWidth,s:document.documentElement.scrollWidth,out:[...document.querySelectorAll('#actual-form input,#actual-form button')].filter(e=>e.getClientRects().length).some(e=>{const r=e.getBoundingClientRect();return r.left<0||r.right>innerWidth+1;})}));
   assert.equal(fits.w,width);assert.ok(fits.s<=width+1);assert.equal(fits.out,false);checks++;
   await page.screenshot({path:path.join(out,`${width}-${theme}-actual-editor.png`)});
  }
 }
 await page.setViewport({width:320,height:844,isMobile:true,hasTouch:true,deviceScaleFactor:1});
 await page.evaluate(()=>document.documentElement.style.fontSize='32px');await new Promise(r=>setTimeout(r,150));
 assert.equal(await page.evaluate(()=>innerWidth),320);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=321));
 assert.equal(await page.evaluate(()=>{const t=document.getElementById('day-heading').getBoundingClientRect(),b=document.getElementById('day-close').getBoundingClientRect();return t.right<=b.left||t.top>=b.bottom||t.bottom<=b.top;}),true);checks++;
 await page.screenshot({path:path.join(out,'320-actual-text-200.png')});await page.evaluate(()=>document.documentElement.style.removeProperty('font-size'));
 await page.setViewport({width:390,height:844,isMobile:true,hasTouch:true,deviceScaleFactor:1});
 dropAck=true;await click('#actual-save');await page.click('#modal-confirm');
 await page.waitForFunction(()=>document.getElementById('actual-retry')&&!document.getElementById('actual-retry').hidden&&!document.getElementById('actual-retry').disabled);
 assert.equal(await page.$eval('#actual-form fieldset',e=>e.disabled),true);checks++;
 await click('#actual-retry');await page.click('#modal-confirm');await page.waitForFunction(()=>!document.getElementById('actual-form'));
 assert.deepEqual(forwarded.at(-1),dropped);checks++;
 let state=await api({month:'2024-07'});assert.equal(state.shifts[0].hours,7.75);assert.equal(state.shifts[0].earnings,2325);checks+=2;
 assert.equal(await page.$eval('#day-sheet',e=>e.hidden),false);checks++;
 await page.screenshot({path:path.join(out,'390-saved-fact.png')});
 // Another authenticated writer updates the same record after the form was opened.
 await click('.day-fact-edit');await fill('#actual-end','02:30');
 const rec=state.shifts[0];await api({action:'correct_shift',nonce:'c'.repeat(32),shift_id:rec.id,day:rec.day,start:rec.start,end:rec.end,break_minutes:20,expected_revision:rec.revision},'action');
 await click('#actual-save');await page.click('#modal-confirm');await page.waitForFunction(()=>document.getElementById('actual-error')?.textContent.includes('изменилась'));
 state=await api({month:'2024-07'});assert.equal(state.shifts[0].end,'01:30');assert.equal(state.shifts[0].break_minutes,20);checks+=2;
 await click('#actual-cancel');await page.click('#day-close');
 await page.evaluate(()=>window.dispatchEvent(new Event('online')));await new Promise(r=>setTimeout(r,300));
 // Missing actual time is added separately, without creating or changing a plan.
 await click('#calendar-grid [data-day="2024-07-12"]');await click('.day-add-fact');
 await fill('#actual-start','09:00');await fill('#actual-end','18:00');await fill('#actual-break','30');
 await click('#actual-save');await page.click('#modal-confirm');await page.waitForFunction(()=>!document.getElementById('actual-form'));
 state=await api({month:'2024-07'});assert.equal(state.shifts.length,2);assert.equal(state.plan.length,0);checks+=2;
 assert.equal(await page.evaluate(()=>window.__links||0),0);assert.equal(await page.evaluate(()=>window.__closed||0),0);checks+=2;
 assert.deepEqual(errors,[]);checks++;
 const report={status:'passed',checks,realPythonBackend:true,temporarySQLite:true,realHmacValidation:true,realTelegramClient:false,productionDataUsed:false,responseLossReplay:true,concurrentEditRejected:true,chatRedirects:0,screenshots:fs.readdirSync(out).filter(n=>n.endsWith('.png')).length};
 fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log('ACTUAL_WRITE_E2E',JSON.stringify(report));
}finally{if(browser)await browser.close();}})().catch(e=>{console.error(e.stack);process.exitCode=1;});
