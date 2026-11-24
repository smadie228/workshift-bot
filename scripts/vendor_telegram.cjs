/* Fetch the pinned, MIT-licensed official SDK at build time, never at runtime. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
(async()=>{
 const out=path.resolve(__dirname,'../miniapp/vendor');
 const manifest=JSON.parse(fs.readFileSync(path.join(out,'telegram-manifest.json'),'utf8'));
 for(const name of ['telegram-web-app.js','TELEGRAM-LICENSE.txt']){
  const entry=manifest.files[name],url=new URL(entry.source);
  if(url.protocol!=='https:'||url.hostname!=='raw.githubusercontent.com'||!url.pathname.startsWith('/TelegramMessenger/TGMiniAppsJsSDK/'+manifest.commit+'/'))throw Error('Unexpected SDK origin');
  const response=await fetch(url,{signal:AbortSignal.timeout(15000)});if(!response.ok)throw Error('SDK source fetch failed');
  const bytes=Buffer.from(await response.arrayBuffer());if(bytes.length>2000000)throw Error('SDK too large');
  if(crypto.createHash('sha256').update(bytes).digest('hex')!==entry.sha256)throw Error('SDK integrity mismatch');
  fs.writeFileSync(path.join(out,name),bytes);
 }
 console.log('OFFICIAL_TELEGRAM_SDK_READY',manifest.commit);
})().catch(e=>{console.error(e.message);process.exitCode=1;});
