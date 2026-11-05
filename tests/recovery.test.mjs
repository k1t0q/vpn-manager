import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const context={structuredClone};vm.createContext(context);vm.runInContext(fs.readFileSync(new URL('../recovery-core.js',import.meta.url),'utf8'),context);
const {parseSnapshot,createQuery}=context.VPNRecovery;
const snapshot={format:'vpn-manager-recovery/v1',created_at:'2026-11-05T08:00:00Z',clients:[{id:1,user_id:'owner',name:'Example',period:'lifetime',start_date:'2026-01-01',end_date:null}]};
test('snapshot preserves lifetime date and rejects mixed owners or duplicates',()=>{
 const result=parseSnapshot(snapshot);assert.equal(result.clients[0].end_date,null);assert.equal(result.clients[0].period,'lifetime');
 assert.throws(()=>parseSnapshot({...snapshot,clients:[...snapshot.clients,{...snapshot.clients[0],id:2,user_id:'other'}]}));
 assert.throws(()=>parseSnapshot({...snapshot,clients:[...snapshot.clients,...snapshot.clients]}));
 result.clients[0].name='Changed';assert.equal(snapshot.clients[0].name,'Example');
});
test('all data mutations are rejected and leave imported records unchanged',async()=>{
 for(const operation of ['insert','update','delete']){const r=await createQuery('clients',()=>snapshot.clients)[operation]({name:'Changed'}).eq('id',1).select('id').single();assert.ok(r.error);assert.equal(r.data,null)}
 assert.equal(snapshot.clients[0].name,'Example');
});
test('read query filters and returns copies; absent history is reported explicitly',async()=>{
 const result=await createQuery('clients',()=>snapshot.clients).select('*').eq('id',1).order('end_date');assert.equal(result.data.length,1);result.data[0].name='Changed';assert.equal(snapshot.clients[0].name,'Example');
 assert.ok((await createQuery('client_history',()=>[]).select('*')).error);
});
