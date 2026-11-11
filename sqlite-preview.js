(()=>{
'use strict';
const $=id=>document.getElementById(id);
let token='',base='',clients=[];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function status(s,error=false){$('status').textContent=s;$('status').classList.toggle('staging-error',error)}
function normalize(raw){const d=raw?.clients??raw?.data??raw; if(!Array.isArray(d))throw Error('API вернул неожиданный формат списка');return d}
async function call(path,options={}){
 const response=await fetch(base+path,{...options,headers:{'Content-Type':'application/json',...(token?{'Authorization':'Bearer '+token}:{})}});
 let data;try{data=await response.json()}catch{throw Error('API вернул не JSON (HTTP '+response.status+')')}
 if(!response.ok||data?.ok===false)throw Error(data?.error||data?.message||'HTTP '+response.status);
 return data;
}
function render(){
 const q=$('search').value.trim().toLocaleLowerCase('ru');
 const visible=clients.filter(c=>!q||String(c.name||'').toLocaleLowerCase('ru').includes(q)||String(c.phone||'').includes(q));
 $('total').textContent=clients.length;$('lifetime').textContent=clients.filter(c=>c.period==='lifetime').length;
 $('dated').textContent=clients.filter(c=>c.period!=='lifetime').length;
 $('rows').innerHTML=visible.map(c=>'<tr><td>'+esc(c.name)+'</td><td>'+esc(c.phone)+'</td><td>'+esc(c.period)+'</td><td>'+esc(c.start_date)+'</td><td>'+esc(c.end_date||'∞')+'</td><td>'+esc(c.wg_client_id||'—')+'</td></tr>').join('');
}
async function refresh(){
 try{status('Загрузка…');clients=normalize(await call('/api/clients'));render();$('result').hidden=false;status('Данные загружены. Режим только для чтения.')}
 catch(e){status(e.message,true)}
}
$('connect').addEventListener('submit',async e=>{
 e.preventDefault();base=$('endpoint').value.trim().replace(/\/+$/,'');token='';
 try{
  const d=await call('/auth/login',{method:'POST',body:JSON.stringify({code:$('code').value})});
  token=d.access_token||d.token||'';
  $('code').value='';
  if(!token)throw Error('API не вернул токен доступа');
  await refresh();
 }catch(e){token='';status(e.message,true)}
});
$('refresh').onclick=refresh;
$('search').oninput=render;
$('logout').onclick=async()=>{try{if(token)await call('/auth/logout',{method:'POST'})}catch{}token='';clients=[];$('result').hidden=true;status('Сеанс завершён')};
})();