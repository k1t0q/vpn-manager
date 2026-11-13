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


function warningContext(){
 const source=fs.readFileSync(new URL('../sqlite-manager.js',import.meta.url),'utf8');
 const storage=new Map(),now=Date.parse('2026-11-13T12:00:00Z');
 const sandbox={Date:{now:()=>now,parse:Date.parse},clients:[],sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)}};
 vm.createContext(sandbox);
 vm.runInContext(source.match(/const remaining=c=>[^\n]+/)[0]+source.slice(source.indexOf('function alertWindow'),source.indexOf('function renderAlerts'))+source.slice(source.indexOf('function notificationSummary'),source.indexOf('function render(){')),sandbox);
 return {sandbox,now};
}
test('warning windows are exclusive and date-only expiry uses Moscow midnight',()=>{
 const {sandbox:c,now}=warningContext();
 for(const [days,kind] of [[8,null],[7,'seven_days'],[4,'seven_days'],[3,'three_days'],[2,'three_days'],[1,'one_day'],[0,'expired']]){
  assert.equal(c.alertWindow({period:'trial',end_date:new Date(now+days*86400000).toISOString()}),kind);
 }
 assert.equal(c.alertWindow({period:'lifetime',end_date:'2000-01-01'}),null);
 assert.equal(vm.runInContext("remaining({period:'1m',end_date:'2026-11-14'})",c),1);
});
test('on-site notices deduplicate and ignore obsolete deadlines and warning stages',()=>{
 const {sandbox:c,now}=warningContext();
 const end=new Date(now+2*86400000).toISOString();
 c.clients=[{id:7,period:'trial',end_date:end}];
 const notice={client_id:7,event_type:'three_days',end_date:end};
 assert.equal(c.notificationSummary({notifications:[notice,notice]}),'До 3 дней: 1');
 assert.equal(c.notificationSummary({notifications:[notice]}),'');
 c.clients[0].end_date=new Date(now+86400000).toISOString();
 assert.equal(c.notificationSummary({notifications:[notice]}),'');
 assert.equal(c.notificationSummary({notifications:[{...notice,end_date:c.clients[0].end_date,event_type:'one_day'}]}),'До 1 дня: 1');
});

