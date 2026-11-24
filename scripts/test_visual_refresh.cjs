/* Accessibility/layout regression on fictional demo data only. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const puppeteer=require(process.env.PUPPETEER_MODULE||'puppeteer');
const root=path.resolve(__dirname,'..'),dir=process.env.VISUAL_OUTPUT||path.join(require('node:os').tmpdir(),'visual-refresh-'+process.pid);
fs.mkdirSync(dir,{recursive:true});
const routes={'/mini/':'index.html','/mini/app.js':'app.js','/mini/app.css':'app.css','/mini/logo.svg':'logo.svg',
'/mini/telegram-web-app.js':'vendor/telegram-web-app.js','/mini/echarts.min.js':'vendor/echarts.min.js'};
const server=http.createServer((req,res)=>{const item=routes[new URL(req.url,'http://localhost').pathname];if(!item){res.writeHead(404);return res.end();}
res.writeHead(200,{'Content-Type':item.endsWith('.js')?'application/javascript':item.endsWith('.css')?'text/css':item.endsWith('.svg')?'image/svg+xml':'text/html; charset=utf-8','Cache-Control':'no-store'});res.end(fs.readFileSync(path.join(root,'miniapp',item)));});
(async()=>{let browser,checks=0;const errors=[],privateRequests=[],metrics=[];try{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const url='http://127.0.0.1:'+server.address().port+'/mini/';
 browser=await puppeteer.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-first-run','--disable-extensions']});
 for(const width of [320,390,1024,1440]){
  const page=await browser.newPage();await page.setViewport({width,height:width<700?844:950,isMobile:width<700,hasTouch:width<700});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.url().includes('/mini/api/'))privateRequests.push(r.url());});
  const res=await page.goto(url,{waitUntil:'networkidle0'});assert.equal(res.status(),200);
  await page.waitForFunction(()=>document.getElementById('home-hours').textContent!=='—');
  await page.click('.bottom-nav [data-nav="calendar"]');
  const m=await page.evaluate(()=>{
   const s=e=>{const b=e.getBoundingClientRect();return {x:b.x,y:b.y,w:b.width,h:b.height};};
   const grid=document.getElementById('calendar-grid'),one=grid.querySelector('button[data-day]'),card=document.querySelector('.calendar-card');
   return {width:innerWidth,scroll:document.documentElement.scrollWidth,card:s(card),day:s(one),
   nav:s(document.querySelector('.bottom-nav')),navStyle:getComputedStyle(document.querySelector('.bottom-nav')).position,
   legend:document.querySelector('.legend').textContent,
   row:document.querySelector('[data-day$="-03"] small')?.textContent,
   group:document.querySelector('.segmented').getAttribute('role'),
   aria:one.getAttribute('aria-label'),
   weekHidden:document.querySelector('.weekday-grid').getAttribute('aria-hidden'),
   duplicateTableAria:document.getElementById('table-summary').getAttributeNames().filter(x=>x==='aria-live').length};
  });
  assert.ok(m.scroll<=width+1,m.scroll);assert.equal(m.group,'group');assert.equal(m.weekHidden,'true');
  assert.equal(m.duplicateTableAria,1);assert.ok(m.aria.length>15);
  assert.ok(m.legend.includes('Отработано')&&m.legend.includes('По плану'));checks+=5;
  assert.match(m.row,/^\d+ч\d{2}$/);checks++;
  if(width===390){assert.ok(m.card.y<365,m.card.y);checks++;}
  if(width>=1024){assert.equal(m.navStyle,'sticky');assert.ok(m.day.h<=80,m.day.h);checks+=2;}
  if(width<700){assert.ok(m.day.h>=50,m.day.h);checks++;}
  await page.screenshot({path:path.join(dir,width+'-dark-calendar.png'),fullPage:true});
  await page.click('#theme-switch');await page.screenshot({path:path.join(dir,width+'-light-calendar.png'),fullPage:true});
  if(width===390){
   await page.evaluate(()=>{document.documentElement.style.setProperty('--tg-content-safe-area-inset-bottom','34px');document.documentElement.style.setProperty('--tg-safe-area-inset-bottom','34px');document.documentElement.style.setProperty('--tg-safe-area-inset-top','54px');document.documentElement.style.setProperty('--tg-safe-area-inset-left','20px');});
   const safe=await page.evaluate(()=>({bottom:parseFloat(getComputedStyle(document.querySelector('.bottom-nav')).paddingBottom),top:parseFloat(getComputedStyle(document.querySelector('.shell')).paddingTop),left:parseFloat(getComputedStyle(document.querySelector('.bottom-nav')).paddingLeft)}));
   assert.ok(safe.bottom>=42&&safe.top>=54&&safe.left>=20,JSON.stringify(safe));checks++;
  }
  if(width===320){
   await page.evaluate(()=>document.documentElement.style.fontSize='32px');
   await new Promise(r=>setTimeout(r,160));
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));checks++;
   const zoomNav=await page.evaluate(()=>{const links=[...document.querySelectorAll('.bottom-nav .nav-label')];return {cols:getComputedStyle(document.querySelector('.bottom-nav')).gridTemplateColumns.split(' ').length,labels:links.map(l=>({label:l.textContent,h:l.getBoundingClientRect().height,line:parseFloat(getComputedStyle(l).lineHeight)}))};});
   assert.equal(zoomNav.cols,2,JSON.stringify(zoomNav));
   assert.ok(zoomNav.labels.every(l=>l.h<=l.line*1.15),JSON.stringify(zoomNav));checks+=2;
   await page.screenshot({path:path.join(dir,'320-large-calendar.png'),fullPage:true});
  }
  metrics.push(m);await page.close();
 }
 assert.deepEqual(privateRequests,[]);assert.deepEqual(errors,[]);checks+=2;
 const report={status:'passed',checks,viewports:[320,390,1024,1440],productionDataUsed:false,realPhoneTested:false,errors,privateRequests,screenshots:fs.readdirSync(dir).filter(n=>n.endsWith('.png')).length,metrics};
 fs.writeFileSync(path.join(dir,'report.json'),JSON.stringify(report,null,2));
 console.log('VISUAL_REFRESH',JSON.stringify({status:report.status,checks,viewports:report.viewports,screenshots:report.screenshots,mobileCalendarTop:metrics[1].card.y,desktopTileHeight:metrics[2].day.h}));
}finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}})().catch(e=>{console.error(e.stack);process.exitCode=1;});
