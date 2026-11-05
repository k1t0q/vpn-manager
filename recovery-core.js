(()=>{
 const blocked={data:null,error:{message:'Временный доступ: изменение данных клиентов отключено'}};
 function parseSnapshot(value){
  if(value?.format!=='vpn-manager-recovery/v1'||!Array.isArray(value.clients)||value.clients.length>10000)throw Error('Выбери файл данных VPN Manager');
  const owners=new Set(),ids=new Set();
  for(const c of value.clients){
   if(!Number.isSafeInteger(c.id)||c.id<1||ids.has(c.id)||typeof c.user_id!=='string'||typeof c.name!=='string'||!['trial','14d','1m','2m','6m','12m','lifetime','imported'].includes(c.period))throw Error('Некорректная запись клиента');
   ids.add(c.id);owners.add(c.user_id);
   for(const key of ['start_date','end_date'])if(c[key]!=null&&!/^\d{4}-\d{2}-\d{2}$/.test(c[key]))throw Error('Некорректная дата клиента');
  }
  if(owners.size!==1)throw Error('Файл должен содержать клиентов одного аккаунта');
  if(!Number.isFinite(Date.parse(value.created_at)))throw Error('Некорректная дата снимка');
  return structuredClone(value);
 }
 function createQuery(table,getClients){
  let action='select',filters=[],sort=null,single=false;
  const q={select(){return q},eq(k,v){filters.push([k,v]);return q},order(k,o={}){sort=[k,o.ascending!==false];return q},single(){single=true;return q},insert(){action='write';return q},update(){action='write';return q},delete(){action='write';return q},then(resolve,reject){
   let result;
   if(action!=='select')result=blocked;
   else if(table!=='clients')result={data:null,error:{message:'История недоступна во временном режиме'}};
   else {let rows=structuredClone(getClients()).filter(c=>filters.every(([k,v])=>c[k]===v));if(sort){const [k,asc]=sort;rows.sort((a,b)=>String(a[k]??'').localeCompare(String(b[k]??''))*(asc?1:-1))}result={data:single?(rows[0]||null):rows,error:null};}
   return Promise.resolve(result).then(resolve,reject);
  }};return q;
 }
 globalThis.VPNRecovery={parseSnapshot,createQuery};
})();
