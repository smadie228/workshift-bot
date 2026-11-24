/* Reproducible self-hosted chart assets. Only npm's pinned, verified package. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),destination=path.join(root,'miniapp','vendor');
const manifest=JSON.parse(fs.readFileSync(path.join(destination,'manifest.json'),'utf8'));
const source=path.dirname(require.resolve('echarts/package.json'));
if(JSON.parse(fs.readFileSync(path.join(source,'package.json'),'utf8')).version!==manifest.version)throw Error('Unexpected chart library version');
const mappings=[['dist/echarts.min.js','echarts.min.js'],['LICENSE','ECHARTS-LICENSE.txt'],['NOTICE','ECHARTS-NOTICE.txt']];
for(const [input,output] of mappings){
 const bytes=fs.readFileSync(path.join(source,input));
 const digest=crypto.createHash('sha256').update(bytes).digest('hex');
 if(digest!==manifest.files[output])throw Error('Chart asset integrity mismatch: '+output);
 fs.writeFileSync(path.join(destination,output),bytes);
}
console.log('SELF_HOSTED_CHARTS_READY',manifest.version);
