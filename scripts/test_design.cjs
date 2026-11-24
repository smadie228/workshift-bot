/* Visual/accessibility regression checks on fictional demo data only. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const puppeteer=require(process.env.PUPPETEER_MODULE||'puppeteer');
const root=path.resolve(__dirname,'..'),out=process.env.DESIGN_OUTPUT||path.join(require('node:os').tmpdir(),'workshift-design-'+process.pid);
fs.mkdirSync(out,{recursive:true});
const routes={'/mini/':'index.html','/mini/app.js':'app.js','/mini/app.css':'app.css','/mini/logo.svg':'logo.svg','/mini/telegram-web-app.js':'vendor/telegram-web-app.js','/mini/echarts.min.js':'vendor/echarts.min.js'};
const server=http.createServer((req,res)=>{const file=routes[new URL(req.url,'http://localhost').pathname];if(!file){res.writeHead(404);return res.end();}
const mime=file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html';res.writeHead(200,{'Content-Type':mime+'; charset=utf-8','Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'"});res.end(fs.readFileSync(path.join(root,'miniapp',file)));});
const checks=[],warnings=[],errors=[];
function luminance(hex){hex=hex.replace("#","");if(hex.length===3)hex=[...hex].map(x=>x+x).join("");return hex.match(/[a-f0-9]{2}/gi).map(x=>parseInt(x,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);}
function contrast(a,b){let x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
(async()=>{let browser;try{
await new Promise(r=>server.listen(0,'127.0.0.1',r));const url=`http://127.0.0.1:${server.address().port}/mini/`;
browser=await puppeteer.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-first-run','--disable-extensions',...(process.env.CI?['--no-sandbox']:[])]});
const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/mini/api/'))errors.push('Demo accessed private API');});
async function inspect(label){const result=await page.evaluate(()=>{const visible=e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length)&&getComputedStyle(e).visibility!=='hidden';
const outside=[...document.querySelectorAll('.page.shown button,.bottom-nav button,.period-control input,.day-sheet button,.day-sheet input')].filter(visible).map(e=>({name:e.id||e.textContent.trim().slice(0,35),r:e.getBoundingClientRect()})).filter(e=>e.r.left< -1||e.r.right>innerWidth+1).map(e=>e.name);
const clipped=[...document.querySelectorAll('.page.shown h2,.page.shown h3,.nav-label,.day-sheet h2')].filter(visible).filter(e=>e.scrollWidth>e.clientWidth+2).map(e=>e.id||e.textContent);
return {width:innerWidth,scroll:document.documentElement.scrollWidth,height:document.body.scrollHeight,outside,clipped};});const requested=page.viewport().width;checks.push({label,requested,...result});if(result.width>requested+1||result.scroll>requested+1||result.outside.length||result.clipped.length)warnings.push({label,...result});}
for(const width of [320,390,768,1024,1440]){await page.setViewport({width,height:width<701?844:1000,deviceScaleFactor:1,isMobile:width<701,hasTouch:width<701});await page.goto(url,{waitUntil:'networkidle0'});await page.waitForFunction(()=>document.getElementById('home-hours').textContent!=='—');
for(const theme of ['dark','light']){if(await page.$eval('body',e=>e.dataset.theme)!==theme)await page.click('#theme-switch');
for(const tab of ['home','calendar','salary','review']){await page.click(`.bottom-nav [data-nav="${tab}"]`);await new Promise(r=>setTimeout(r,120));await inspect(`${width}/${theme}/${tab}`);await page.screenshot({path:path.join(out,`${width}-${theme}-${tab}.png`),fullPage:true});}
await page.click('.bottom-nav [data-nav="salary"]');
await page.evaluate(()=>{document.getElementById('payment-history-filters').hidden=false;});
await inspect(`${width}/${theme}/payment-history-filters`);
await page.screenshot({path:path.join(out,`${width}-${theme}-payment-filters.png`),fullPage:true});
await page.evaluate(()=>{document.getElementById('payment-history-filters').hidden=true;});
await page.click('.bottom-nav [data-nav="calendar"]');await page.click('#mode-table');await inspect(`${width}/${theme}/list`);await page.screenshot({path:path.join(out,`${width}-${theme}-list.png`),fullPage:true});await page.click('#mode-calendar');
const palette=await page.evaluate(()=>{const s=getComputedStyle(document.body);return Object.fromEntries(['ink','muted','accent','positive','warning','bg','surface','surface2'].map(k=>[k,s.getPropertyValue('--'+k).trim()]));});
for(const fg of ['ink','muted','accent','positive','warning'])for(const bg of ['bg','surface','surface2']){const ratio=contrast(palette[fg],palette[bg]);checks.push({label:`${theme}/contrast/${fg}/${bg}`,ratio});if(ratio<4.5)warnings.push({label:`${theme}/contrast/${fg}/${bg}`,ratio});}
}
// The reading preference changes display only and persists across refresh.
await page.click('.bottom-nav [data-nav="review"]');await page.$eval('#text-size-switch',e=>e.scrollIntoView({block:'center',behavior:'instant'}));await page.click('#text-size-switch');assert.equal(await page.$eval('#text-size-switch',e=>e.getAttribute('aria-pressed')),'true');await inspect(`${width}/large-preference`);await page.reload({waitUntil:'networkidle0'});assert.equal(await page.$eval('html',e=>e.dataset.reading),'large');
for(const tab of ['home','calendar','salary','review']){await page.click(`.bottom-nav [data-nav="${tab}"]`);await inspect(`${width}/large/${tab}`);}
await page.$eval('#text-size-switch',e=>e.scrollIntoView({block:'center',behavior:'instant'}));await page.click('#text-size-switch');
// Emulate 200% text, in addition to narrow reflow, without changing page data.
if([320,390,1440].includes(width)){await page.evaluate(()=>document.documentElement.style.fontSize='32px');for(const tab of ['home','calendar','salary','review']){await new Promise(r=>setTimeout(r,180));try{await page.click(`.bottom-nav [data-nav="${tab}"]`);}catch(e){const detail=await page.evaluate(()=>({inert:document.querySelector('main').inert,dayHidden:document.getElementById('day-sheet').hidden,modalHidden:document.getElementById('modal').hidden,activePage:document.body.dataset.page,scroll:scrollY,viewport:{width:innerWidth,height:innerHeight,visual:visualViewport.height,offset:visualViewport.offsetTop,scale:visualViewport.scale},nav:[...document.querySelectorAll('.nav-item')].map(e=>{const r=e.getBoundingClientRect();return {name:e.dataset.nav,x:r.x,y:r.y,w:r.width,h:r.height,display:getComputedStyle(e).display,hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML.slice(0,300)};})}));await page.screenshot({path:path.join(out,'click-failure.png'),fullPage:false});warnings.push({label:`${width}/text-200/${tab}/click`,error:e.message,detail});await page.$eval(`.bottom-nav [data-nav="${tab}"]`,e=>e.click());}await inspect(`${width}/text-200/${tab}`);}await page.screenshot({path:path.join(out,`${width}-text-200.png`),fullPage:true});await page.evaluate(()=>document.documentElement.style.removeProperty('font-size'));}
}
// Keyboard focus must stay in the day dialog and return after Escape.
await page.setViewport({width:390,height:844});await page.goto(url,{waitUntil:'networkidle0'});await page.click('.bottom-nav [data-nav="calendar"]');
await page.$eval('#month-jump',e=>{e.value='2099-07';e.dispatchEvent(new Event('change',{bubbles:true}));});await page.click('#calendar-grid button:nth-of-type(18)');
assert.equal(await page.$eval('#day-sheet',e=>e.hidden),false);await inspect('390/day-editor');await page.screenshot({path:path.join(out,'390-day-editor.png'),fullPage:true});
await page.$eval('#day-close',e=>e.focus());await page.keyboard.down('Shift');await page.keyboard.press('Tab');await page.keyboard.up('Shift');assert.equal(await page.evaluate(()=>document.getElementById('day-sheet').contains(document.activeElement)),true);await page.keyboard.press('Escape');assert.equal(await page.$eval('#day-sheet',e=>e.hidden),true);checks.push({label:'keyboard-dialog',passed:true});
await page.click('.bottom-nav [data-nav="salary"]');
assert.equal(await page.$eval('#salary-more-actions',e=>e.open),false);
await page.click('#salary-more-actions summary');
await inspect('390/salary-extra-actions');
await page.screenshot({path:path.join(out,'390-salary-extra-actions.png'),fullPage:true});
checks.push({label:'secondary-finance-actions-reachable',passed:true});
await page.click('.bottom-nav [data-nav="review"]');
const automaticPrivacy=await page.evaluate(()=>({
 master:Boolean(document.getElementById('phone-auto-visibility')),
 oldManualButton:Boolean(document.getElementById('phone-motion-enable')),
 oldCalibration:Boolean(document.getElementById('phone-motion-calibrate')),
 oldFaceDownOnly:Boolean(document.getElementById('phone-face-down')),
 copy:document.querySelector('label[for="phone-auto-visibility"]')?.textContent||''
}));
assert.equal(automaticPrivacy.master,true);
assert.equal(automaticPrivacy.oldManualButton,false);
assert.equal(automaticPrivacy.oldCalibration,false);
assert.equal(automaticPrivacy.oldFaceDownOnly,false);
assert.match(automaticPrivacy.copy,/вниз.*обратно вверх.*повтори.*показать/is);
checks.push({label:'automatic-motion-privacy-control',passed:true});
const secureControls=await page.evaluate(()=>({
 setup:document.getElementById('pin-setup-form'),
 lock:document.getElementById('pin-unlock-form'),
 bio:document.getElementById('security-biometric-unlock'),
 recovery:document.getElementById('security-recovery-form')
}));
assert.ok(secureControls.setup&&secureControls.lock&&secureControls.bio&&secureControls.recovery);
checks.push({label:'pin-biometry-recovery-forms-present',passed:true});
await page.evaluate(()=>{document.getElementById('pin-setup-panel').hidden=false;});
await inspect('390/pin-setup');
await page.screenshot({path:path.join(out,'390-pin-setup.png'),fullPage:true});
await page.evaluate(()=>{
 document.getElementById('pin-setup-panel').hidden=true;
 document.getElementById('session-lock').hidden=false;
 document.getElementById('pin-unlock-form').hidden=false;
 document.getElementById('session-unlock').hidden=true;
 document.getElementById('security-recovery-details').hidden=false;
});
await inspect('390/pin-locked-overlay');
await page.screenshot({path:path.join(out,'390-pin-locked-overlay.png'),fullPage:false});
await page.evaluate(()=>{
 document.getElementById('session-lock').hidden=true;
 document.getElementById('pin-unlock-form').hidden=true;
 document.getElementById('security-recovery-details').hidden=true;
 document.getElementById('session-unlock').hidden=false;
});
checks.push({label:'pin-locked-overlay-reflow',passed:true});
assert.equal(await page.$eval('#more-tools',e=>e.open),false,'advanced actions start collapsed');
assert.equal(await page.$eval('#extra-gestures',e=>e.open),false,'secondary gestures start collapsed');
assert.equal(await page.$eval('#time-settings',e=>e.open),false,'rare settings start collapsed');
assert.equal(await page.$$eval('.primary-actions>button',buttons=>buttons.length),3);
checks.push({label:'compact-review-default',passed:true});
await page.click('#more-tools summary');await inspect('390/expanded-tools');
await page.screenshot({path:path.join(out,'390-expanded-tools.png'),fullPage:true});
assert.equal(await page.$$eval('#more-tools .secondary-tools button',buttons=>buttons.length),4);
checks.push({label:'tools-remain-reachable',passed:true});
await page.click('#time-settings summary');await inspect('390/expanded-time-settings');
await page.click('#extra-gestures summary');await inspect('390/expanded-phone-gestures');
await page.screenshot({path:path.join(out,'390-expanded-phone-gestures.png'),fullPage:true});
assert.equal(await page.$eval('#phone-shake-hide',e=>e.type),'checkbox');
checks.push({label:'advanced-gestures-remain-reachable',passed:true});
await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);checks.push({label:'reduced-motion',passed:await page.evaluate(()=>matchMedia('(prefers-reduced-motion: reduce)').matches)});
assert.deepEqual(errors,[]);const report={status:warnings.length?'needs-fixes':'passed',checks:checks.length,warnings,errors,screenshots:fs.readdirSync(out).filter(f=>f.endsWith('.png')).length,productionDataUsed:false,realPhoneTested:false,screenReaderManuallyTested:false};fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));fs.writeFileSync(path.join(out,'metrics.json'),JSON.stringify(checks,null,2));console.log('DESIGN_CHECKS',JSON.stringify(report));if(warnings.length)process.exitCode=1;
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}})().catch(e=>{console.error(e.stack);process.exitCode=1;});
