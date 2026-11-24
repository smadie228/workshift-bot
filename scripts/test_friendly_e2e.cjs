/* Browser -> actual Python fixture -> disposable SQLite. Never production. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const puppeteer=require(process.env.PUPPETEER_MODULE||'puppeteer');
const base=process.env.WRITE_FIXTURE_URL||'http://127.0.0.1:19867';
const out=process.env.FRIENDLY_E2E_OUTPUT||path.join(require('node:os').tmpdir(),'friendly-e2e-'+process.pid);fs.mkdirSync(out,{recursive:true});
const fields={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:77})};
const secret=crypto.createHmac('sha256','WebAppData').update('fictional-write-e2e-token').digest();
fields.hash=crypto.createHmac('sha256',secret).update(Object.entries(fields).sort().map(([k,v])=>k+'='+v).join('\n')).digest('hex');
const init=new URLSearchParams(fields).toString();
const sdk=`window.Telegram={WebApp:{initData:${JSON.stringify(init)},colorScheme:'dark',ready(){},expand(){},close(){window.__closed=(window.__closed||0)+1;},openTelegramLink(){window.__links=(window.__links||0)+1;},setHeaderColor(){},setBackgroundColor(){},HapticFeedback:{selectionChanged(){},notificationOccurred(){}},BackButton:{show(){},hide(){},onClick(){}}}};`;
let checks=0,dropAck=false,dropped,requests=[];const errors=[];
async function api(payload,route='state'){
 const response=await fetch(base+'/mini/api/'+route,{method:'POST',headers:{'Content-Type':'application/json','Origin':base,'X-Telegram-Init-Data':init},body:JSON.stringify(payload)});
 const data=await response.json();assert.equal(response.status,200,JSON.stringify(data));return data;
}
(async()=>{let browser;try{
 browser=await puppeteer.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-first-run','--disable-extensions']});
 const page=await browser.newPage();await page.setViewport({width:390,height:844,isMobile:true,hasTouch:true,deviceScaleFactor:1});
 page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);
 page.on('request',async r=>{try{
  if(r.url().endsWith('/telegram-web-app.js'))return r.respond({status:200,contentType:'application/javascript',body:sdk});
  if(r.url().endsWith('/mini/api/action')){requests.push(JSON.parse(r.postData()));if(dropAck){dropAck=false;dropped=JSON.parse(r.postData());await api(dropped,'action');return r.abort('connectionfailed');}}
  return r.continue();
 }catch(e){errors.push(e.message);try{await r.abort();}catch(x){}}});
 async function click(sel){await page.$eval(sel,e=>e.scrollIntoView({block:'center',behavior:'instant'}));await page.click(sel);}
 async function fill(sel,value){await page.$eval(sel,(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));},value);}
 async function jump(month){await page.$eval('#month-jump',(e,v)=>{e.value=v;e.dispatchEvent(new Event('change',{bubbles:true}));},month);await page.waitForFunction(m=>document.querySelector('#calendar-grid [data-day="'+m+'-01"]')&&document.getElementById('connection').textContent.includes('Подключено'),{},month);await new Promise(r=>setTimeout(r,120));}
 async function confirm(){await page.waitForFunction(()=>!document.getElementById('modal').hidden);await click('#modal-confirm');await page.waitForFunction(()=>document.getElementById('modal').hidden&&!document.getElementById('shift-action').disabled);}
 await page.goto(base+'/mini/',{waitUntil:'networkidle0'});await page.waitForFunction(()=>document.getElementById('connection').textContent.includes('Подключено'));
 assert.equal(await page.$eval('#alerts-preview',e=>e.hidden),true);checks++;
 await page.click('.bottom-nav [data-nav="calendar"]');await jump('2024-07');await click('#calendar-grid [data-day="2024-07-20"]');
 await click('#actual-off-action');await confirm();await page.waitForSelector('.actual-off-status');
 let state=await api({month:'2024-07'});assert.equal(state.ledger[19].actual_off,true);assert.equal(state.ledger[19].hours,0);checks+=2;
 await click('#actual-off-action');await confirm();await page.waitForFunction(()=>!document.querySelector('.actual-off-status'));checks++;
 await click('.day-add-fact');await fill('#actual-start','09:00');await fill('#actual-end','18:00');await fill('#actual-break','30');await click('#actual-save');await confirm();await page.waitForFunction(()=>!document.getElementById('actual-form'));
 state=await api({month:'2024-07'});const original=state.shifts.find(r=>r.day==='2024-07-20'),pay=state.summary.earned;
 await click('#actual-off-action');assert.ok((await page.$eval('#modal-text',e=>e.textContent)).includes('из расчёта'));await click('#modal-cancel');assert.equal((await api({month:'2024-07'})).summary.earned,pay);checks+=2;
 await click('#actual-off-action');await confirm();await page.waitForSelector('.actual-off-status');state=await api({month:'2024-07'});
 assert.equal(await page.$('.day-add-fact'),null);checks++;assert.equal(state.ledger[19].archived_shifts,1);assert.equal(state.summary.earned,pay-original.earnings);checks+=2;
 await page.screenshot({path:path.join(out,'390-actual-day-off.png')});
 await click('#actual-off-action');await confirm();await page.waitForSelector('.day-fact-edit');state=await api({month:'2024-07'});
 assert.equal(state.summary.earned,pay);assert.equal(state.shifts.find(r=>r.id===original.id).start,original.start);checks+=2;
 await click('#day-plan-toggle');await click('#plan-work');await fill('#plan-start','16:00');await fill('#plan-end','02:00');
 assert.equal(await page.$eval('#plan-submit',e=>e.textContent),'Сохранить план');checks++;
 // Unsaved plan survives an attempted close.
 await click('#day-close');await page.waitForFunction(()=>!document.getElementById('modal').hidden);assert.match(await page.$eval('#modal-title',e=>e.textContent),/без сохранения/);await click('#modal-cancel');assert.equal(await page.$eval('#plan-start',e=>e.value),'16:00');checks+=2;
 // Lose an already committed single-day response; replay from the inline recovery button.
 dropAck=true;await click('#plan-submit');await click('#modal-confirm');await page.waitForFunction(()=>document.getElementById('plan-retry')&&!document.getElementById('plan-retry').hidden&&!document.getElementById('plan-retry').disabled);
 assert.equal(await page.$eval('#plan-fields',e=>e.disabled),true);checks++;
 await click('#plan-retry');await confirm();await page.waitForFunction(()=>document.getElementById('write-recovery').hidden);
 assert.deepEqual(requests.at(-1),dropped);state=await api({month:'2024-07'});assert.equal(state.plan.find(r=>r.day==='2024-07-20').start,'16:00');assert.equal(state.summary.earned,pay);checks+=3;
 for(const type of ['off','unset']){if(!(await page.$eval('#day-plan-disclosure',e=>e.open)))await click('#day-plan-toggle');await click('#plan-'+type);await click('#plan-submit');await confirm();}
 state=await api({month:'2024-07'});assert.equal(state.ledger[19].plan,null);assert.equal(state.summary.earned,pay);checks+=2;
 await click('#day-close');await jump('2099-07');await click('#bulk-toggle');
 for(const day of ['10','12','31'])await click('#calendar-grid [data-day="2099-07-'+day+'"]');
 await click('#bulk-off');await click('#bulk-submit');assert.ok((await page.$eval('#modal-text',e=>e.textContent)).includes('31'));await click('#modal-cancel');assert.equal((await api({month:'2099-07'})).plan.length,0);checks+=2;
 for(const width of [320,390,1440]){await page.setViewport({width,height:844,isMobile:true,hasTouch:true,deviceScaleFactor:1});for(const theme of ['dark','light']){
  await page.$eval('body',(e,t)=>{e.dataset.theme=t;},theme);await click('#bulk-submit');
  const metrics=await page.evaluate(()=>({w:innerWidth,s:document.documentElement.scrollWidth,controls:[...document.querySelectorAll('#modal button')].map(e=>e.getBoundingClientRect()).every(r=>r.left>=0&&r.right<=innerWidth+1)}));assert.equal(metrics.w,width);assert.ok(metrics.s<=width+1);assert.equal(metrics.controls,true);checks++;
  await page.screenshot({path:path.join(out,`${width}-${theme}-plan-confirmation.png`)});await click('#modal-cancel');
 }}
 await page.setViewport({width:390,height:844,isMobile:true,hasTouch:true,deviceScaleFactor:1});dropAck=true;await click('#bulk-submit');await click('#modal-confirm');await page.waitForFunction(()=>!document.getElementById('bulk-retry').hidden&&!document.getElementById('bulk-retry').disabled);
 await click('#bulk-retry');await confirm();await page.waitForFunction(()=>document.getElementById('bulk-editor').hidden);
 assert.deepEqual(requests.at(-1),dropped);state=await api({month:'2099-07'});assert.equal(state.plan.length,3);assert.ok(state.plan.every(r=>r.status==='off'));checks+=3;
 // A second writer changes a day while its form remains open.
 await click('#calendar-grid [data-day="2099-07-10"]');await click('#plan-work');await fill('#plan-start','09:00');await fill('#plan-end','18:00');
 await api({action:'set_plan',nonce:'f'.repeat(32),day:'2099-07-10',status:'unset',expected_revision:state.plan_editor.revisions['2099-07-10']},'action');
 await click('#plan-submit');await click('#modal-confirm');await page.waitForFunction(()=>document.getElementById('plan-error').textContent.includes('изменился'));state=await api({month:'2099-07'});assert.equal(state.ledger[9].plan,null);checks++;
 assert.equal(await page.evaluate(()=>window.__links||0),0);assert.equal(await page.evaluate(()=>window.__closed||0),0);assert.deepEqual(errors,[]);checks+=3;
 const report={status:'passed',checks,realPythonBackend:true,temporarySQLite:true,productionDataUsed:false,realTelegramClient:false,directPlans:true,actualDayOffRestore:true,lossReplay:true,noChatRedirects:true,screenshots:fs.readdirSync(out).filter(n=>n.endsWith('.png')).length};
 fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log('FRIENDLY_E2E',JSON.stringify(report));
}finally{if(browser)await browser.close();}})().catch(e=>{console.error(e.stack);process.exitCode=1;});
