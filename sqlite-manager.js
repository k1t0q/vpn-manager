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
 return '<span class="wg-status '+(w.enabled?'on':'off')+'">'+(w.enabled?'● VPN включён':'● VPN отключён')+'</span><small>Последнее подключение: '+esc(lastSeen(w))+'</small>'+(w.address?'<small>IP: '+esc(w.address)+'</small>':'');
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
 const alerts=clients.filter(c=>c.period!=='lifetime'&&remaining(c)!==null&&remaining(c)<=7).sort((a,b)=>remaining(a)-remaining(b));
 box.classList.toggle('hidden',alerts.length===0);
 box.innerHTML=alerts.length?'<div class="site-notifications-head">Требуют внимания <span>'+alerts.length+'</span></div>'+alerts.map(c=>{
 const d=remaining(c),expired=d<=0;
 const label=expired?'Подписка истекла'+(d<0?' '+Math.abs(d)+' дн. назад':' сегодня'):'До окончания '+d+' дн.';
 return '<div class="site-notification '+(expired?'expired':'warning')+'"><b>'+esc(c.name)+'</b> — '+esc(label)+' · '+esc(formatDate(c.end_date))+'</div>';
 }).join(''):'';
}
function render(){
 const q=$('search').value.trim().toLowerCase(),f=$('filter').value;
 const shown=clients.filter(c=>(!q||[c.name,c.phone,c.note].some(v=>String(v||'').toLowerCase().includes(q)))&&(f==='all'||(f==='lifetime'&&c.period==='lifetime')||(f==='active'&&(c.period==='lifetime'||remaining(c)>0))||(f==='expired'&&c.period!=='lifetime'&&remaining(c)<=0)||(f==='soon'&&c.period!=='lifetime'&&remaining(c)>0&&remaining(c)<=7)||(f==='wg_on'&&wg.some(w=>String(w.id)===String(c.wg_client_id)&&w.enabled))||(f==='wg_off'&&!!c.wg_client_id&&!wg.some(w=>w.id===c.wg_client_id&&w.enabled))));
 $('counts').textContent='Всего: '+clients.length+' · Активных: '+clients.filter(c=>c.period==='lifetime'||remaining(c)>0).length;
 renderAlerts();
 const wgIds=new Set(wg.map(w=>w.id));
 const active=clients.filter(c=>c.period==='lifetime'||remaining(c)>0).length;
 $('sTotal').textContent=clients.length;$('sActive').textContent=active;
 $('sSoon').textContent=clients.filter(c=>c.period!=='lifetime'&&remaining(c)>0&&remaining(c)<=7).length;
 $('sExpired').textContent=clients.filter(c=>c.period!=='lifetime'&&remaining(c)!==null&&remaining(c)<=0).length;
 const actions=c=>'<div class="m-actions"><button data-act="extend" data-id="'+c.id+'" class="secondary">Продлить</button><button data-act="edit" data-id="'+c.id+'" class="secondary">Изменить</button><button data-act="history" data-id="'+c.id+'" class="secondary">История</button>'+(c.referrer_id?'<button data-act="referral" data-id="'+c.id+'" class="secondary">Бонус рефералу</button>':'')+(!c.wg_client_id?'<button data-act="wg-create" data-id="'+c.id+'">Создать VPN</button>':('<button data-act="wg-toggle" data-id="'+c.id+'" class="secondary">'+(wg.find(w=>String(w.id)===String(c.wg_client_id))?.enabled?'Отключить VPN':'Включить VPN')+'</button><button data-act="wg-qr" data-id="'+c.id+'" class="secondary">QR</button><button data-act="wg-config" data-id="'+c.id+'" class="secondary">Конфиг</button>'))+'</div>';
 const remain=c=>c.period==='lifetime'?'∞':remaining(c)===null?'—':String(remaining(c));
 const label=c=>esc(tariff[c.period]||c.period||'—');
 const referred=c=>esc(clients.find(x=>String(x.id)===String(c.referrer_id))?.name||'—');
 $('rows').innerHTML=shown.map(c=>'<tr><td><b>'+esc(c.name)+'</b>'+(c.note?'<small>'+esc(c.note)+'</small>':'')+'</td><td>'+esc(c.phone||'—')+'</td><td>'+referred(c)+'</td><td>'+wgDisplay(c)+'</td><td>'+label(c)+'</td><td>'+formatDate(c.start_date)+'</td><td>'+ (c.period==='lifetime'?'Навсегда':formatDate(c.end_date))+'</td><td><b>'+remain(c)+'</b></td><td>'+subscriptionStatus(c)+'</td><td>'+actions(c)+'</td></tr>').join('');
 $('cards').innerHTML=shown.map(c=>'<article class="card"><div class="card-top"><div><h3>'+esc(c.name)+'</h3><small>'+esc(c.phone||'')+'</small></div>'+subscriptionStatus(c)+'</div><div class="info"><div><small>Тариф</small><b>'+label(c)+'</b></div><div><small>До</small><b>'+(c.period==='lifetime'?'Навсегда':formatDate(c.end_date))+'</b></div><div><small>Осталось</small><b>'+remain(c)+' дн.</b></div></div>'+(c.referrer_id?'<div class="note">Пригласил: '+referred(c)+'</div>':'')+'<div class="note">'+wgDisplay(c)+'</div><details class="manage" open><summary>Управление подпиской</summary><div class="manage-body">'+actions(c)+'</div></details></article>').join('');
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
$('create').onsubmit=async e=>{
 e.preventDefault();if(!confirm('Создать запись клиента в SQLite? WireGuard-профиль пока не создаётся автоматически.'))return;
 const form=new FormData(e.currentTarget),p=Object.fromEntries(form);
 p.user_id='vpn-manager-admin';if(!p.referrer_id)delete p.referrer_id;
 try{const d=await call('/api/clients/create',{method:'POST',body:JSON.stringify(p)});msg('Клиент создан в SQLite (ID '+d.id+'). WireGuard пока не создан.');$('create').reset();$('create').elements.start_date.value=iso();
await refresh()}catch(e){msg(e.message,true)}
};
async function handleClientAction(e){
 const btn=e.target.closest('button[data-act]');if(!btn)return;
 const id=Number(btn.dataset.id),c=clients.find(x=>Number(x.id)===id);if(!c)return;
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
 $('editDialog').showModal();
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
 const blob=await response.blob(),url=URL.createObjectURL(blob);
 if(isQr){
  const qr=$('qrImage');qr.src=url;
  $('qrTitle').textContent='QR — '+c.name;
  $('qrDialog').showModal();
  $('qrDialog').dataset.objectUrl=url;
  return;
 }else{const link=document.createElement('a');link.href=url;link.download=(c.name||'wireguard').replace(/[^a-z0-9_-]/gi,'_')+'.conf';link.click()}
 setTimeout(()=>URL.revokeObjectURL(url),60000);
 }else if(btn.dataset.act==='wg-create'){
 if(!confirm('Создать WireGuard-профиль для '+c.name+' и привязать к SQLite?'))return;
 const result=await call('/api/wg/client',{method:'POST',body:JSON.stringify({name:c.name})});
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
 const period=prompt('Продлить на: 1m, 6m, 12m или lifetime','1m');
 if(period===null)return;if(!['1m','6m','12m','lifetime'].includes(period)){msg('Неверный тариф',true);return}
 if(!confirm('Продлить '+c.name+' на '+period+'?'))return;
 await call('/api/clients/'+id+'/extend',{method:'POST',body:JSON.stringify({period})});await refresh();msg('Подписка продлена');
 }
 }catch(e){msg(e.message,true)}
}
$('rows').addEventListener('click',handleClientAction);
$('cards').addEventListener('click',handleClientAction);
$('closeQr').onclick=()=>$('qrDialog').close();
$('qrDialog').addEventListener('close',()=>{const d=$('qrDialog');if(d.dataset.objectUrl){URL.revokeObjectURL(d.dataset.objectUrl);delete d.dataset.objectUrl;$('qrImage').removeAttribute('src')}});
$('closeHistory').onclick=()=>$('historyDialog').close();
$('closeEdit').onclick=()=>$('editDialog').close();
$('editForm').onsubmit=async e=>{
 e.preventDefault();const d=Object.fromEntries(new FormData(e.currentTarget));const id=Number(d.id);
 if(d.period==='lifetime')d.end_date=null;
 try{await call('/api/clients/'+id,{method:'PATCH',body:JSON.stringify(d)});$('editDialog').close();await refresh();msg('Клиент обновлён')}
 catch(err){msg('Редактирование пока недоступно на сервере: '+err.message,true)}
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
})();