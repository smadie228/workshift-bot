/* Fictional browser -> real Python -> temporary SQLite. Never use a production URL. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const puppeteer=require(process.env.PUPPETEER_MODULE||'puppeteer');
const base=process.env.WRITE_FIXTURE_URL||'http://127.0.0.1:19830';
assert.match(base,/^http:\/\/127\.0\.0\.1:\d+$/);
const out=process.env.COMFORT_OUTPUT||path.join(require('node:os').tmpdir(),'comfort-'+process.pid);fs.mkdirSync(out,{recursive:true});
const fields={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:77})};
const secret=crypto.createHmac('sha256','WebAppData').update('fictional-write-e2e-token').digest();fields.hash=crypto.createHmac('sha256',secret).update(Object.entries(fields).sort().map(([k,v])=>k+'='+v).join('\n')).digest('hex');
const init=new URLSearchParams(fields).toString(),headers={'Content-Type':'application/json','Origin':base,'X-Telegram-Init-Data':init};
const sdk=`window.Telegram={WebApp:{initData:${JSON.stringify(init)},colorScheme:'dark',ready(){},expand(){},close(){window.__closed=(window.__closed||0)+1;},openTelegramLink(){window.__links=(window.__links||0)+1;},setHeaderColor(){},setBackgroundColor(){},HapticFeedback:{selectionChanged(){},notificationOccurred(k){(window.__haptics||=[]).push(k);}},BackButton:{show(){},hide(){},onClick(){}}}};`;
let checks=0,dropHTML=false,lastDropped=null;const errors=[],requests=[];
async function api(body,route='workspace'){const r=await fetch(base+'/mini/api/'+route,{method:'POST',headers,body:JSON.stringify(body)});const d=await r.json();assert.equal(r.status,200,JSON.stringify(d));return d;}
(async()=>{let browser;try{
 browser=await puppeteer.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-first-run','--disable-extensions']});
 const page=await browser.newPage();await page.setViewport({width:390,height:844,isMobile:true,hasTouch:true,deviceScaleFactor:1});
 page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);
 page.on('request',async r=>{try{
  if(r.url().endsWith('/telegram-web-app.js'))return r.respond({status:200,contentType:'application/javascript',body:sdk});
  if(r.url().endsWith('/mini/api/action')){requests.push(JSON.parse(r.postData()));if(dropHTML){dropHTML=false;lastDropped=JSON.parse(r.postData());const response=await fetch(r.url(),{method:'POST',headers,body:r.postData()});assert.equal(response.status,200);await response.text();return r.respond({status:502,contentType:'text/html',body:'<h1>temporary gateway</h1>'});}}
  return r.continue();
 }catch(e){errors.push(e.message);try{await r.abort();}catch(_){}}});
 async function click(selector){await page.$eval(selector,e=>e.scrollIntoView({block:'center',behavior:'instant'}));await page.click(selector);}
 async function label(text,scope='#workspace-content'){const selector=await page.evaluate((scope,text)=>{const e=[...document.querySelectorAll(scope+' button')].find(x=>x.textContent.trim()===text);if(!e)throw Error('Missing '+text);e.dataset.testClick='target';return '[data-test-click="target"]';},scope,text);await click(selector);await page.evaluate(()=>document.querySelector('[data-test-click]')?.removeAttribute('data-test-click'));}
 async function fill(selector,value){await page.$eval(selector,(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));},value);}
 async function ready(){await page.waitForFunction(()=>!document.getElementById('workspace-close').disabled&&document.getElementById('workspace-heading').textContent!=='Загрузка…');}
 await page.goto(base+'/mini/',{waitUntil:'networkidle0'});await page.waitForFunction(()=>document.getElementById('connection').textContent.includes('Подключено'));
 await page.click('.bottom-nav [data-nav="review"]');await click('[data-workspace="settings"]');await ready();
 assert.equal(await page.$eval('#workspace-context',e=>e.textContent),'Настройки');checks++;
 await label('Изменить перерыв');await fill('#ws-minutes','45');await label('Отмена');await page.click('#modal-cancel');assert.equal(await page.$eval('#ws-minutes',e=>e.value),'45');checks++;
 await label('Отмена');await page.click('#modal-confirm');assert.equal(await page.$eval('#workspace-heading',e=>e.textContent),'Ставка и перерыв');assert.equal((await api({month:'2024-07'})).default_break_minutes,30);checks+=2;
 await click('#workspace-nav [data-section="money"]');await label('+ Поступление');await fill('#ws-amount','123');await click('#workspace-nav [data-section="reports"]');
 assert.equal(await page.$eval('#modal',e=>e.hidden),false);checks++;await page.click('#modal-cancel');assert.equal(await page.$eval('#ws-amount',e=>e.value),'123');checks++;
 await click('#workspace-nav [data-section="reports"]');await page.click('#modal-confirm');assert.ok(await page.$('#ws-report-build'));assert.equal(await page.$eval('#workspace-nav [data-section="reports"]',e=>e.getAttribute('aria-current')),'page');checks+=2;
 await click('#workspace-nav [data-section="money"]');await label('+ Поступление');
 await fill('#ws-amount','0');await click('#ws-save');assert.equal(await page.$eval('#ws-amount',e=>e.checkValidity()),false);assert.equal(await page.$eval('#modal',e=>e.hidden),true);checks+=2;
 await fill('#ws-amount','1\u00a0234,56');await fill('#ws-work_month','2024-07');await fill('#ws-paid_on','2024-07-20');await fill('#ws-note','Вымышленный платёж');
 assert.equal(await page.$eval('#ws-amount',e=>e.checkValidity()),true);checks++;
 // Header/navigation stay visible while the form scrolls. Each viewport is exercised independently.
 for(const width of [320,390,768,1440]){
  await page.setViewport({width,height:844,isMobile:true,hasTouch:true,deviceScaleFactor:1});
  for(const theme of ['dark','light']){
   await page.$eval('body',(e,t)=>e.dataset.theme=t,theme);await page.$eval('.workspace-scroll',e=>e.scrollTop=e.scrollHeight);
   const boxes=await page.evaluate(()=>{const h=document.querySelector('.workspace-panel>.day-sheet-header').getBoundingClientRect(),b=document.getElementById('workspace-close').getBoundingClientRect(),n=document.getElementById('workspace-nav').getBoundingClientRect();return {width:innerWidth,scroll:document.documentElement.scrollWidth,header:h.top>=0&&b.bottom<=innerHeight,nav:n.top>=0&&n.bottom<=innerHeight,fields:[...document.querySelectorAll('#workspace-sheet input,#workspace-sheet select')].every(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1;})};});
   assert.ok(boxes.scroll<=boxes.width+1);assert.ok(boxes.header&&boxes.nav&&boxes.fields);checks+=2;
   await page.$eval('.workspace-scroll',e=>e.scrollTop=0);await page.screenshot({path:path.join(out,`${width}-${theme}-form.png`)});
  }
 }
 // Emulated small visible viewport, not a physical phone keyboard claim.
 await page.setViewport({width:390,height:420,isMobile:true,hasTouch:true,deviceScaleFactor:1});await click('#ws-amount');
 let bounds=await page.$eval('#ws-amount',e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom};});assert.ok(bounds.top>=0&&bounds.bottom<=420);checks++;
 await page.screenshot({path:path.join(out,'390-short-viewport.png')});
 await page.setViewport({width:320,height:844,isMobile:true,hasTouch:true,deviceScaleFactor:1});await page.evaluate(()=>document.documentElement.style.fontSize='32px');await new Promise(r=>setTimeout(r,120));
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));checks++;
 await click('#ws-save');assert.equal(await page.$eval('#modal',e=>e.hidden),false);await page.click('#modal-cancel');checks++;
 await page.screenshot({path:path.join(out,'320-text-200.png')});await page.evaluate(()=>document.documentElement.style.removeProperty('font-size'));
 await page.setViewport({width:390,height:844,isMobile:true,hasTouch:true,deviceScaleFactor:1});
 // Server commits; gateway response is HTML 502. It must not show a JS parsing error or resubmit as new.
 dropHTML=true;await click('#ws-save');await page.click('#modal-confirm');await page.waitForFunction(()=>!document.getElementById('workspace-retry').hidden&&!document.getElementById('workspace-retry').disabled);
 const message=await page.$eval('#workspace-error',e=>e.textContent);assert.ok(message.includes('Сервер временно недоступен'));assert.ok(!message.includes('Unexpected token'));assert.equal(await page.evaluate(()=>window.__haptics.at(-1)),'error');checks+=3;
 await click('#workspace-retry');await page.click('#modal-confirm');await page.waitForFunction(()=>document.getElementById('workspace-heading').textContent==='Записи денег');await ready();
 assert.deepEqual(requests.at(-1),lastDropped);assert.equal(await page.$eval('#month-jump',e=>e.value),'2024-07');checks+=2;
 const data=await api({month:'2024-07'});assert.equal(data.payments.filter(r=>r.note==='Вымышленный платёж').length,1);checks++;
 assert.ok((await page.$eval('.ws-record-amount',e=>e.textContent)).includes('1'));assert.ok((await page.$eval('.ws-record-meta',e=>e.textContent)).includes('июл'));checks+=2;
 await page.screenshot({path:path.join(out,'390-money-records.png')});
 // Back button warns on unsaved edit, then returns to the same list without changing the receipt.
 await label('Изменить');await fill('#ws-amount','2000');await click('#workspace-back');await page.click('#modal-cancel');assert.equal(await page.$eval('#ws-amount',e=>e.value),'2000');checks++;
 await click('#workspace-back');await page.click('#modal-confirm');assert.equal(await page.$eval('#workspace-heading',e=>e.textContent),'Записи денег');checks++;
 await page.keyboard.press('Escape');assert.equal(await page.$eval('#workspace-sheet',e=>e.hidden),true);assert.equal(await page.$eval('main',e=>e.inert),false);checks+=2;
 assert.equal(await page.evaluate(()=>window.__links||0),0);assert.equal(await page.evaluate(()=>window.__closed||0),0);assert.deepEqual(errors,[]);checks+=3;
 const report={status:'passed',checks,realPythonBackend:true,temporarySQLite:true,productionDataUsed:false,realTelegramClient:false,sectionNavigation:true,contextCancel:true,spacedAmount:true,gatewayLossReplay:true,viewportKeyboardEmulated:true,screenReaderManuallyTested:false,screenshots:fs.readdirSync(out).filter(x=>x.endsWith('.png')).length};
 fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log('COMFORT_E2E',JSON.stringify(report));
}finally{if(browser)await browser.close();}})().catch(e=>{console.error(e.stack);process.exitCode=1;});
