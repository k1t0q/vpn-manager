import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../server/connect.js',import.meta.url),'utf8');
function page(pilot){
 const nodes=new Map();
 const node=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,disabled:false,value:'',textContent:'',addEventListener(name,cb){this[name]=cb},setAttribute(){},removeAttribute(){}});return nodes.get(id)};
 const win={location:{href:''}};let imports=0;
 vm.runInNewContext(source,{
  document:{getElementById:node,visibilityState:'visible',title:''},window:win,location:{pathname:'/'+'a'.repeat(22),hash:'#'+'b'.repeat(43)},Date,setInterval(){},setTimeout(){},
  fetch:async path=>({ok:true,json:async()=>path==='/api/clash-import'?(imports++,{url:'clashmi://install-config?url=https%3A%2F%2Fexample.invalid%2Fticket'}):({profile:{label:'CAESAR VPN #93',available:true,enabled:true,lifetime:true,progress:100,import_pilot:pilot}})})
 });return {node,win,imports:()=>imports};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('pilot prepares import in advance and launches Clash Mi synchronously on click',async()=>{
 const p=page(true);await tick();await tick();assert.equal(p.imports(),1);assert.equal(p.node('openApp').hidden,false);
 p.node('openApp').click();assert.ok(p.win.location.href.startsWith('clashmi://install-config?'));
});
test('normal clients keep download and QR without issuing import capabilities',async()=>{
 const p=page(false);await tick();assert.equal(p.node('openApp').hidden,true);assert.equal(p.imports(),0);assert.equal(p.node('download').disabled,false);assert.equal(p.node('qr').disabled,false);
});
