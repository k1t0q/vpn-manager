(()=>{
'use strict';
const API='https://vpn-staging.80-90-179-65.sslip.io';
const $=id=>document.getElementById(id);
const TOKEN_KEY='vpn_manager_sqlite_session_v1';
let token=localStorage.getItem(TOKEN_KEY)||'',clients=[],wg=[];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const msg=(t,bad=false)=>{$('status').textContent=t;$('status').className=bad?'m-error':'m-ok'};
const iso=()=>{const d=new Date();return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')};
const date=s=>s?new Date(s.slice(0,10)+'T12:00:00'):null;
const remaining=c=>c.period==='lifetime'?null:(c.end_date?Math.ceil((date(c.end_date)-date(iso()))/86400000):null);
const tariff={'trial':'Пробный — 24 часа','1m':'1 месяц','6m':'6 месяцев','12m':'12 месяцев','lifetime':'Навсегда ∞','imported':'Импорт','1':'1 месяц','6':'6 месяцев','12':'12 месяцев'};
const formatDate=v=>v?String(v).slice(0,10).split('-').reverse().join('.'):'—';
function lastSeen(w){
 const raw=w?.latestHandshakeAt ?? w?.latestHandshake ?? w?.latest_handshake_at;
 if(!raw)return 'Никогда';
 const d=new Date(typeof raw==='number'?(raw<1e12?raw*1000:raw):raw);if(!Number.isFinite(d.getTime())||d.getTime()<=0)return 'Никогда';
 const mins=Math.max(0,Math.floor((Date.now()-d.getTime())/60000));
 if(mins<1)return 'только что';
 if(mins<60)return mins+' мин. назад';
 if(mins<1440)return Math.floor(mins/60)+' ч. назад';
 return Math.floor(mins/1440)+' дн. назад';
}
function subscriptionStatus(c){
 if(c.period==='lifetime')return '<span class="status active">Навсегда ∞</span>';
 const d=remaining(c);
 if(d===null)return '<span class="status unknown">Без срока</span>';
 return d<=0?'<span class="status expired">Истекла</span>':d<=7?'<span class="status soon">Скоро</span>':'<span class="status active">Активна</span>';
}
function wgDisplay(c){
 if(!c.wg_client_id)return '<span class="wg-status none">Не привязан</span>';
 const w=wg.find(x=>String(x.id)===String(c.wg_client_id));
 if(!w)return '<span class="wg-status off">Не найден</span>';
 return '<span class="wg-status '+(w.enabled?'on':'off')+'">'+(w.enabled?'● VPN включён':'● VPN отключён')+'</span>'+(w.address?'<small>IP: '+esc(w.address)+' / активность: '+esc(lastSeen(w))+'</small>':'<small>Активность: '+esc(lastSeen(w))+'</small>');
}

async function call(path,opts={}){
 const r=await fetch(API+path,{...opts,headers:{'Content-Type':'application/json',...(token?{'Authorization':'Bearer '+token}:{}),...(opts.headers||{})}});
 let d;try{d=await r.json()}catch{throw Error('Некорректный ответ сервера: '+r.status)}
 if(!r.ok||d.ok===false){if(r.status===401){token='';localStorage.removeItem(TOKEN_KEY);$('workspace').classList.add('m-hide');$('loginPane').classList.remove('m-hide')}throw Error(d.error||'HTTP '+r.status)}
 return d;
}
const dataList=d=>{const x=d.clients??d.data??d; if(!Array.isArray(x))throw Error('Неизвестный формат списка клиентов');return x};
function renderAlerts(){
 const box=$('siteNotifications');if(!box)return;
 const expired=clients.filter(c=>c.period!=='lifetime'&&remaining(c)!==null&&remaining(c)<=0).sort((a,b)=>remaining(a)-remaining(b));
 const soon=clients.filter(c=>c.period!=='lifetime'&&remaining(c)>0&&remaining(c)<7).sort((a,b)=>remaining(a)-remaining(b));
 const section=(type,title,items)=>'<details name="vpn-attention" class="attention-group '+type+'"><summary>'+title+' <span class="attention-count">'+items.length+'</span><span class="attention-chevron" aria-hidden="true"></span></summary><div class="attention-list">'+(items.length?items.map(c=>'<button type="button" class="attention-person" data-jump-client="'+c.id+'"><div><b>'+esc(c.name)+'</b><small>До '+esc(formatDate(c.end_date))+'</small></div><strong>'+(type==='expired'?(remaining(c)===0?'Сегодня':Math.abs(remaining(c))+' дн. назад'):remaining(c)+' дн.')+'</strong></button>').join(''):'<div class="attention-empty">Нет клиентов</div>')+'</div></details>';
 box.classList.remove('hidden');
 const open=box.querySelector('.attention-group[open]')?.classList.contains('expired')?'expired':box.querySelector('.attention-group[open]')?.classList.contains('soon')?'soon':null;
 box.innerHTML=section('expired','Истекли',expired)+section('soon','Менее 7 дней',soon);
 if(open)box.querySelector('.attention-group.'+open)?.setAttribute('open','');
}
function render(){
 const q=$('search').value.trim().toLowerCase(),f=$('filter').value;
 const shown=clients.filter(c=>(!q||[c.name,c.phone,c.note].some(v=>String(v||'').toLowerCase().includes(q)))&&(f==='all'||(f==='lifetime'&&c.period==='lifetime')||(f==='active'&&(c.period==='lifetime'||remaining(c)>0))||(f==='expired'&&c.period!=='lifetime'&&remaining(c)<=0)||(f==='soon'&&c.period!=='lifetime'&&remaining(c)>0&&remaining(c)<7)||(f==='wg_on'&&wg.some(w=>String(w.id)===String(c.wg_client_id)&&w.enabled))||(f==='wg_off'&&!!c.wg_client_id&&!wg.some(w=>w.id===c.wg_client_id&&w.enabled))));
 $('counts').textContent='Всего: '+clients.length+' · Активных: '+clients.filter(c=>c.period==='lifetime'||remaining(c)>0).length;
 renderAlerts();
 const wgIds=new Set(wg.map(w=>w.id));
 const active=clients.filter(c=>c.period==='lifetime'||remaining(c)>0).length;
 $('sTotal').textContent=clients.length;$('sActive').textContent=active;
 $('sSoon').textContent=clients.filter(c=>c.period!=='lifetime'&&remaining(c)>0&&remaining(c)<=7).length;
 $('sExpired').textContent=clients.filter(c=>c.period!=='lifetime'&&remaining(c)!==null&&remaining(c)<=0).length;
 const actions=c=>{
 const peer=wg.find(w=>String(w.id)===String(c.wg_client_id));
 const hasWg=!!c.wg_client_id;
 return '<div class="m-actions client-actions">'
 +'<button data-act="extend" data-id="'+c.id+'" class="secondary">Продлить</button>'
 +'<button data-act="edit" data-id="'+c.id+'" class="secondary">Изменить</button>'
 +(hasWg?'<button data-act="wg-qr" data-id="'+c.id+'" class="secondary">QR</button><button data-act="wg-config" data-id="'+c.id+'" class="secondary">Конфиг</button>':'<button data-act="wg-create" data-id="'+c.id+'">Создать VPN</button><span></span>')
 +(hasWg?'<button data-act="wg-toggle" data-id="'+c.id+'" class="'+(peer?.enabled?'warning':'success-toggle')+'">'+(peer?.enabled?'Отключить':'Включить')+'</button>':'<span></span>')
 +'<button data-act="delete" data-id="'+c.id+'" class="danger">Удалить</button>'
 +(c.referrer_id?'<button data-act="referral" data-id="'+c.id+'" class="secondary referral-action">Бонус рефералу</button>':'')
 +'</div>';
};
 const remain=c=>c.period==='lifetime'?'∞':remaining(c)===null?'—':String(remaining(c));
 const label=c=>esc(tariff[c.period]||c.period||'—');
 const referred=c=>esc(clients.find(x=>String(x.id)===String(c.referrer_id))?.name||'—');
 $('rows').innerHTML=shown.map(c=>'<tr data-client-id="'+c.id+'"><td><b>'+esc(c.name)+'</b>'+(c.note?'<small>'+esc(c.note)+'</small>':'')+'</td><td>'+esc(c.phone||'—')+'</td><td>'+referred(c)+'</td><td>'+wgDisplay(c)+'</td><td>'+label(c)+'</td><td>'+formatDate(c.start_date)+'</td><td>'+ (c.period==='lifetime'?'Навсегда':formatDate(c.end_date))+'</td><td><b>'+remain(c)+'</b></td><td>'+subscriptionStatus(c)+'</td><td>'+actions(c)+'</td></tr>').join('');
 $('cards').innerHTML=shown.map(c=>'<article class="card" data-client-id="'+c.id+'"><div class="card-top"><div><h3 class="card-vpn-name">'+esc(peerLabel(c))+'</h3><div class="card-client-name">'+esc(c.name)+'</div>'+(c.phone?'<small>'+esc(c.phone)+'</small>':'')+'</div>'+subscriptionStatus(c)+'</div><div class="info"><div><small>Тариф</small><b>'+label(c)+'</b></div><div><small>До</small><b>'+(c.period==='lifetime'?'Навсегда':formatDate(c.end_date))+'</b></div><div><small>Осталось</small><b>'+remain(c)+' дн.</b></div></div>'+(c.referrer_id?'<div class="note">Пригласил: '+referred(c)+'</div>':'')+'<div class="note">'+wgDisplay(c)+'</div><details class="manage" open><summary>Управление подпиской</summary><div class="manage-body">'+actions(c)+'</div></details></article>').join('');
 $('empty').classList.toggle('hidden',shown.length>0);
 const current=$('referrer').value;
 $('referrer').innerHTML='<option value="">Никто</option>'+clients.map(c=>'<option value="'+c.id+'">'+esc(c.name)+'</option>').join('');
 $('referrer').value=current;
}
async function refresh(){
 const [a,b]=await Promise.all([call('/api/clients'),call('/api/wg/clients').catch(e=>({clients:[],error:e.message}))]);
 clients=dataList(a);
 clients.sort((a,b)=>{
  const ad=Date.parse(a.created_at||'')||0,bd=Date.parse(b.created_at||'')||0;
  return bd-ad || Number(b.id||0)-Number(a.id||0);
 });
 wg=b.clients||[];render();
 msg(b.error?'Клиенты загружены, WireGuard недоступен: '+b.error:'Данные обновлены',!!b.error);
}
$('login').onsubmit=async e=>{
 e.preventDefault();try{
 const d=await call('/auth/login',{method:'POST',body:JSON.stringify({code:$('code').value})});
 token=d.access_token||d.token||'';if(!token)throw Error('Сервер не вернул токен');localStorage.setItem(TOKEN_KEY,token);
 $('code').value='';await refresh();$('loginPane').classList.add('m-hide');$('workspace').classList.remove('m-hide');
 }catch(e){token='';localStorage.removeItem(TOKEN_KEY);msg('Ошибка входа: '+e.message,true)}
};
function peerLabel(c){
 const raw=wg.find(w=>String(w.id)===String(c.wg_client_id))?.name||'';
 const match=String(raw).trim().match(/^((?:CAESAR|CEASER) BOT #\d+)(?=\s|$)/i);
 return match?match[1]:(raw||c.name);
}
function addMonthsISO(base,months){
 const parts=String(base).split('-').map(Number);
 const date=new Date(Date.UTC(parts[0],parts[1]-1,1));
 date.setUTCMonth(date.getUTCMonth()+months);
 const last=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0)).getUTCDate();
 date.setUTCDate(Math.min(parts[2],last));
 return date.toISOString().slice(0,10);
}
function syncEditEndDate(){
 const f=$('editForm'),period=f.elements.period.value,end=f.elements.end_date;
 if(period==='lifetime'){end.disabled=true;end.value='';return}
 end.disabled=false;
 if(!end.value){const months={'1m':1,'6m':6,'12m':12}[period]||1;end.value=addMonthsISO(f.elements.start_date.value||iso(),months)}
}
function nextPeerName(){
 const numbers=wg.map(w=>/^(?:CAESAR|CEASER) BOT #(\d+)(?=\s|$)/i.exec(String(w.name||'').trim())).filter(Boolean).map(m=>Number(m[1]));
 return 'CAESAR BOT #'+(Math.max(0,...numbers)+1);
}
$('create').onsubmit=async e=>{
 e.preventDefault();
 const form=new FormData(e.currentTarget),p=Object.fromEntries(form);
 p.user_id='vpn-manager-admin';if(!p.referrer_id)delete p.referrer_id;
 const peerName=nextPeerName();
 if(!confirm('Создать клиента «'+p.name+'» и VPN-профиль «'+peerName+'»?'))return;
 const submit=e.currentTarget.querySelector('[type="submit"]');if(submit)submit.disabled=true;
 try{
  const d=await call('/api/clients/create',{method:'POST',body:JSON.stringify(p)});
  const id=d.id||d.client?.id;
  if(!id)throw Error('Запись клиента создана, но сервер не вернул ID. Проверьте список перед повтором.');
  let wid;
  try{
   const result=await call('/api/wg/client',{method:'POST',body:JSON.stringify({name:peerName})});
   const peer=result.client||result;
   wid=result.id||result.client_id||result.wg_client_id||peer.id;
   if(!wid)throw Error('wg-easy не вернул ID созданного профиля');
  }catch(err){await refresh();throw Error('Клиент «'+p.name+'» создан (ID '+id+'), но VPN-профиль не создан или не подтверждён: '+err.message+'. Проверьте wg-easy, не создавайте клиента повторно.')}
  try{await call('/api/clients/'+id+'/wireguard',{method:'POST',body:JSON.stringify({wg_client_id:wid})})}
  catch(err){await refresh();throw Error('Клиент и VPN-профиль «'+peerName+'» созданы, но привязка не удалась: '+err.message+'. Не создавайте их повторно.')}
  $('create').reset();$('create').elements.start_date.value=iso();
  await refresh();msg('Создан клиент «'+p.name+'» — '+peerName);
 }catch(err){msg(err.message,true);alert(err.message)}
 finally{if(submit)submit.disabled=false}
};
async function handleClientAction(e){
 const btn=e.target.closest('button[data-act]');if(!btn)return;
 const id=Number(btn.dataset.id),c=clients.find(x=>Number(x.id)===id);if(!c)return;
 if(btn.dataset.act==='delete'){
  if(!confirm('Удалить клиента «'+c.name+'»?\\n\\nБудут удалены запись из VPN MANAGER и привязанный профиль WireGuard.'))return;
  btn.disabled=true;
  try{await call('/api/clients/'+id+'/delete',{method:'POST'});await refresh();msg('Клиент «'+c.name+'» удалён вместе с WireGuard-профилем')}
  catch(err){const message='Не удалось удалить клиента: '+err.message;msg(message,true);alert(message)}
  finally{btn.disabled=false}
  return;
 }
 try{
 if(btn.dataset.act==='referral'){
 const ref=clients.find(x=>String(x.id)===String(c.referrer_id));
 const bonus={'1m':14,'1':14,'6m':30,'6':30,'12m':60,'12':60}[c.period];
 if(!ref||!bonus)throw Error('Для этого клиента бонус не предусмотрен');
 if(!confirm('Начислить '+bonus+' дней клиенту '+ref.name+' за приглашение '+c.name+'? Начисление допускается один раз. WireGuard не изменяется.'))return;
 const r=await call('/api/referrals/award-safe',{method:'POST',body:JSON.stringify({client_id:id})});
 await refresh();msg('Начислено +'+r.bonus_days+' дней клиенту '+ref.name);
 }else if(btn.dataset.act==='edit'){
 const f=$('editForm');for(const key of ['id','name','phone','start_date','period','end_date','note'])f.elements[key].value=c[key]??'';
 syncEditEndDate();
 $('editDialog').showModal();
 requestAnimationFrame(()=>{if(document.activeElement?.blur)document.activeElement.blur()});
 }else if(btn.dataset.act==='wg-toggle'){
 const peer=wg.find(w=>String(w.id)===String(c.wg_client_id));
 const action=peer?.enabled?'disable':'enable';
 if(!confirm((action==='disable'?'Отключить':'Включить')+' WireGuard для '+c.name+'?'))return;
 await call('/api/wg/client/'+encodeURIComponent(c.wg_client_id)+'/'+action,{method:'POST'});
 await refresh();msg('WireGuard '+(action==='disable'?'отключён':'включён'));
 }else if(btn.dataset.act==='wg-qr'||btn.dataset.act==='wg-config'){
 const isQr=btn.dataset.act==='wg-qr';
 const response=await fetch(API+'/api/wg/client/'+encodeURIComponent(c.wg_client_id)+'/'+(isQr?'qr':'config'),{headers:{Authorization:'Bearer '+token}});
 if(!response.ok)throw Error('Не удалось получить '+(isQr?'QR':'конфигурацию')+': HTTP '+response.status);
 const blob=await response.blob();
 if(isQr){
  const url=URL.createObjectURL(blob);
  const qr=$('qrImage');qr.src=url;
  $('qrTitle').textContent='QR — '+c.name;
  $('qrDialog').showModal();
  $('qrDialog').dataset.objectUrl=url;
  return;
 }
 const peerName=peerLabel(c)||'WireGuard';
 const fileName=peerName.replace(/[\\/:*?"<>|\x00-\x1f]/g,'_').trim()+'.conf';
 const file=new File([blob],fileName,{type:'application/octet-stream'});
 if(typeof navigator.share==='function'&&typeof navigator.canShare==='function'&&navigator.canShare({files:[file]})){
  try{
   await navigator.share({files:[file]});
   msg('Конфигурация передана в меню отправки');
  }catch(err){
   if(err?.name!=='AbortError')throw Error('Не удалось открыть отправку файла: '+err.message);
  }
 }else{
  if(!confirm('Это устройство не поддерживает отправку файла через меню «Поделиться». Скачать конфигурацию вместо этого?'))return;
  const url=URL.createObjectURL(blob);
  const link=document.createElement('a');link.href=url;link.download=fileName;document.body.appendChild(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),60000);
 }
 }else if(btn.dataset.act==='wg-create'){
 if(!confirm('Создать WireGuard-профиль для '+c.name+' и привязать к SQLite?'))return;
 const result=await call('/api/wg/client',{method:'POST',body:JSON.stringify({name:nextPeerName()})});
 const peer=result.client||result;
 const wid=result.id||result.client_id||result.wg_client_id||peer.id;
 if(!wid)throw Error('WireGuard создал профиль, но не вернул ID. Проверьте wg-easy перед повтором.');
 try{await call('/api/clients/'+id+'/wireguard',{method:'POST',body:JSON.stringify({wg_client_id:wid})})}
 catch(err){throw Error('WireGuard профиль '+wid+' создан, но привязка не удалась: '+err.message+'. НЕ повторяйте создание.')}
 await refresh();msg('WireGuard создан и привязан к '+c.name);
 }else if(btn.dataset.act==='history'){
 const d=await call('/api/clients/'+id+'/history');
 $('history').innerHTML=(d.history||[]).map(h=>'<p><b>'+esc(h.event_type)+'</b> — '+esc(h.description)+'<small> '+esc(h.created_at)+'</small></p>').join('')||'<p>Записей пока нет</p>';
 $('historyDialog').showModal();
 }else{
 const dialog=$('extendDialog');
 $('extendClientName').textContent=c.name;
 $('extendPeriod').value=['1m','6m','12m','lifetime'].includes(c.period)?c.period:'1m';
 dialog.dataset.clientId=String(id);
 dialog.showModal();
 }
 }catch(e){msg(e.message,true)}
}
$('rows').addEventListener('click',handleClientAction);
$('cards').addEventListener('click',handleClientAction);
$('closeQr').onclick=()=>$('qrDialog').close();
$('qrDialog').addEventListener('close',()=>{const d=$('qrDialog');if(d.dataset.objectUrl){URL.revokeObjectURL(d.dataset.objectUrl);delete d.dataset.objectUrl;$('qrImage').removeAttribute('src')}});
$('closeHistory').onclick=()=>$('historyDialog').close();
$('closeEdit').onclick=()=>$('editDialog').close();
$('editForm').elements.period.addEventListener('change',()=>{const f=$('editForm');if(f.elements.period.value!=='lifetime'&&!f.elements.end_date.value)syncEditEndDate();else if(f.elements.period.value==='lifetime')syncEditEndDate()});
$('editForm').onsubmit=async e=>{
 e.preventDefault();const d=Object.fromEntries(new FormData(e.currentTarget));const id=Number(d.id);
 delete d.id;
 if(d.period==='lifetime')d.end_date=null;
 else if(!d.end_date){d.end_date=addMonthsISO(d.start_date||iso(),{'1m':1,'6m':6,'12m':12}[d.period]||1)}
 try{await call('/api/clients/'+id,{method:'PATCH',body:JSON.stringify(d)});$('editDialog').close();await refresh();msg('Клиент обновлён')}
 catch(err){const message='Не удалось сохранить изменения: '+err.message;msg(message,true);alert(message)}
};

function csvCell(value){const v=String(value??'');return '"'+(/^[=+@\-\t\r]/.test(v)?"'"+v:v).replace(/"/g,'""')+'"'}
$('exportCsv').onclick=()=>{
 const cols=['ID','Имя','Телефон','Тариф','Начало','Окончание','Осталось дней','WG ID','WG статус','Пригласил','Заметка'];
 const data=clients.map(c=>[c.id,c.name,c.phone,c.period,c.start_date,c.end_date,remaining(c),c.wg_client_id,c.wg_client_id?(wg.find(w=>String(w.id)===String(c.wg_client_id))?.enabled?'Включён':'Выключен или не найден'):'Нет',clients.find(x=>String(x.id)===String(c.referrer_id))?.name||'',c.note]);
 const csv='\uFEFF'+[cols,...data].map(row=>row.map(csvCell).join(';')).join('\r\n');
 const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
 const a=document.createElement('a');a.href=url;a.download='vpn-manager-clients-'+iso()+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
 msg('Экспортировано клиентов: '+clients.length);
};
$('reload').onclick=()=>refresh().catch(e=>msg(e.message,true));
$('search').oninput=render;$('filter').onchange=render;
$('siteNotifications').addEventListener('toggle',e=>{if(e.target.matches('.attention-group')&&e.target.open){$('siteNotifications').querySelectorAll('.attention-group').forEach(group=>{if(group!==e.target)group.open=false})}},true);
$('siteNotifications').addEventListener('click',e=>{
 const button=e.target.closest('button[data-jump-client]');if(!button)return;
 const id=button.dataset.jumpClient;
 $('search').value='';$('filter').value='all';render();
 const mobile=window.matchMedia('(max-width:900px)').matches;
 const container=mobile?$('cards'):$('rows');
 const target=Array.from(container.querySelectorAll('[data-client-id]')).find(el=>el.dataset.clientId===id);
 if(!target){msg('Клиент не найден в списке',true);return}
 target.scrollIntoView({behavior:'smooth',block:'center'});
 target.classList.add('client-jump-highlight');
 setTimeout(()=>target.classList.remove('client-jump-highlight'),2500);
});
$('logout').onclick=async()=>{try{await call('/auth/logout',{method:'POST'})}catch{}token='';localStorage.removeItem(TOKEN_KEY);clients=[];wg=[];$('workspace').classList.add('m-hide');$('loginPane').classList.remove('m-hide');msg('Выход выполнен')};
$('create').elements.start_date.value=iso();
if(token){
 $('loginPane').classList.add('m-hide');
 $('workspace').classList.remove('m-hide');
 refresh().catch(err=>{
   $('workspace').classList.add('m-hide');
   $('loginPane').classList.remove('m-hide');
   msg('Не удалось восстановить сеанс: '+err.message,true);
 });
}
$('closeExtend').onclick=()=>$('extendDialog').close();
$('extendForm').addEventListener('submit',async e=>{
 e.preventDefault();
 const dialog=$('extendDialog'),id=Number(dialog.dataset.clientId),period=$('extendPeriod').value;
 const button=$('confirmExtend');
 button.disabled=true;
 try{
  await call('/api/clients/'+id+'/extend',{method:'POST',body:JSON.stringify({period})});
  dialog.close();await refresh();msg('Подписка продлена');
 }catch(err){msg('Не удалось продлить: '+err.message,true);alert('Не удалось продлить: '+err.message)}
 finally{button.disabled=false}
});
})();
