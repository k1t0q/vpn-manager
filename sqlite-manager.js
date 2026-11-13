(()=>{
'use strict';
const API=window.VPN_MANAGER_CONFIG.API_BASE;
const $=id=>document.getElementById(id);
const TOKEN_KEY='vpn_manager_sqlite_session_v1';
let token=sessionStorage.getItem(TOKEN_KEY)||'',clients=[],wg=[],wgAvailable=false,capabilities={};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const msg=(t,bad=false)=>{$('status').textContent=t;$('status').className=bad?'m-error':'m-ok'};
const iso=()=>{const d=new Date();return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')};
const date=s=>s?new Date(s.slice(0,10)+'T12:00:00'):null;
const remaining=c=>c.period==='lifetime'?null:(c.end_date?Math.ceil((c.end_date.includes('T')?Date.parse(c.end_date)-Date.now():date(c.end_date)-date(iso()))/86400000):null);
const tariff={'trial':'Пробный — 24 часа','14d':'14 дней','2m':'2 месяца','1m':'1 месяц','6m':'6 месяцев','12m':'12 месяцев','lifetime':'Навсегда ∞','imported':'Импорт','1':'1 месяц','6':'6 месяцев','12':'12 месяцев'};
const formatDate=v=>!v?'—':String(v).includes('T')?new Date(v).toLocaleString('ru-RU',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):String(v).slice(0,10).split('-').reverse().join('.');
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
function wgDisplay(c,mobile=false){
 if(!wgAvailable)return '<span class="wg-status none">Статус VPN недоступен</span>';
 if(!c.wg_client_id)return '<span class="wg-status none">Не привязан</span>';
 const w=wg.find(x=>String(x.id)===String(c.wg_client_id));
 if(!w)return '<span class="wg-status off">Не найден</span>';
 const profile='<small class="vpn-profile-name">'+esc(peerLabel(c))+'</small>';
 const status='<span class="wg-status '+(w.enabled?'on':'off')+'">'+(w.enabled?'● VPN включён':'● VPN отключён')+'</span>';
 if(mobile)return '<span class="mobile-vpn-line">'+status+'<span class="mobile-vpn-meta">'+(w.address?' · IP: '+esc(w.address):'')+' · '+esc(lastSeen(w))+'</span></span>';
 return profile+status+(w.address?'<small>IP: '+esc(w.address)+' / активность: '+esc(lastSeen(w))+'</small>':'<small>Активность: '+esc(lastSeen(w))+'</small>');
}

async function call(path,opts={}){
 const mutation=['POST','PATCH'].includes(opts.method)&&(path.startsWith('/api/clients/')||path.startsWith('/api/wg/client/'));
 let pendingId='',requestKey='';
 if(mutation){
  const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify([path,opts.body||''])));
  pendingId='vpn_manager_pending_'+Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('');
  requestKey=sessionStorage.getItem(pendingId)||crypto.randomUUID();sessionStorage.setItem(pendingId,requestKey);
 }
 const r=await fetch(API+path,{...opts,headers:{'Content-Type':'application/json',...(token?{'Authorization':'Bearer '+token}:{}),...(requestKey?{'Idempotency-Key':requestKey}:{}),...(opts.headers||{})}});
 let d;try{d=await r.json()}catch{throw Error('Некорректный ответ сервера: '+r.status)}
 if(!r.ok||d.ok===false){if(r.status===401){token='';sessionStorage.removeItem(TOKEN_KEY);$('workspace').classList.add('m-hide');$('loginPane').classList.remove('m-hide')}throw Error(d.error||'HTTP '+r.status)}
 if(pendingId)sessionStorage.removeItem(pendingId);
 return d;
}
const dataList=d=>{const x=d.clients??d.data??d; if(!Array.isArray(x))throw Error('Неизвестный формат списка клиентов');return x};
function renderAlerts(){
 const box=$('siteNotifications');if(!box)return;
 const expired=clients.filter(c=>c.period!=='lifetime'&&remaining(c)!==null&&remaining(c)<=0).sort((a,b)=>remaining(a)-remaining(b));
 const soon=clients.filter(c=>c.period!=='lifetime'&&remaining(c)>0&&remaining(c)<=3).sort((a,b)=>remaining(a)-remaining(b));
 const section=(type,title,items)=>'<details name="vpn-attention" class="attention-group '+type+'"><summary>'+title+' <span class="attention-count">'+items.length+'</span><span class="attention-chevron" aria-hidden="true"></span></summary><div class="attention-list">'+(items.length?items.map(c=>'<button type="button" class="attention-person" data-jump-client="'+c.id+'"><div><b>'+esc(c.name)+'</b><small>До '+esc(formatDate(c.end_date))+'</small></div><strong>'+(type==='expired'?(remaining(c)===0?'Сегодня':Math.abs(remaining(c))+' дн. назад'):remaining(c)+' дн.')+'</strong></button>').join(''):'<div class="attention-empty">Нет клиентов</div>')+'</div></details>';
 box.classList.remove('hidden');
 const open=box.querySelector('.attention-group[open]')?.classList.contains('expired')?'expired':box.querySelector('.attention-group[open]')?.classList.contains('soon')?'soon':null;
 box.innerHTML=section('expired','Истекли',expired)+section('soon','До 3 дней',soon);
 if(open)box.querySelector('.attention-group.'+open)?.setAttribute('open','');
}
function render(){
 const q=$('search').value.trim().toLowerCase(),f=$('filter').value;
 const shown=clients.filter(c=>(!q||[c.name,c.phone,c.note].some(v=>String(v||'').toLowerCase().includes(q)))&&(f==='all'||(f==='lifetime'&&c.period==='lifetime')||(f==='active'&&(c.period==='lifetime'||remaining(c)>0))||(f==='expired'&&c.period!=='lifetime'&&remaining(c)<=0)||(f==='soon'&&c.period!=='lifetime'&&remaining(c)>0&&remaining(c)<=7)||(f==='referrals'&&!!c.referrer_id)||(f==='wg_on'&&wg.some(w=>String(w.id)===String(c.wg_client_id)&&w.enabled))||(f==='wg_off'&&wgAvailable&&wg.some(w=>String(w.id)===String(c.wg_client_id)&&!w.enabled))));
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
 +(capabilities.profile_delivery?'<button data-act="delivery" data-id="'+c.id+'" class="secondary referral-action">'+(c.delivered_at?'Передан ✓':'Отметить передачу')+'</button>':'')
 +'</div>';
};
 const remain=c=>c.period==='lifetime'?'∞':remaining(c)===null?'—':String(remaining(c));
 const label=c=>esc(tariff[c.period]||c.period||'—');
 const referred=c=>esc(clients.find(x=>String(x.id)===String(c.referrer_id))?.name||'—');
 const inviteCount=c=>clients.filter(x=>String(x.referrer_id||'')===String(c.id)).length;
 const deliveryBadge=c=>c.delivered_at?'<small class="green">Профиль передан: '+esc(formatDate(c.delivered_at))+'</small>':'';
 const inviteBadge=c=>inviteCount(c)?'<span class="invite-count">Пригласил: '+inviteCount(c)+'</span>':'';
 $('rows').innerHTML=shown.map(c=>'<tr data-client-id="'+c.id+'"><td><b>'+esc(c.name)+'</b>'+inviteBadge(c)+deliveryBadge(c)+(c.note?'<small>'+esc(c.note)+'</small>':'')+'</td><td>'+esc(c.phone||'—')+'</td><td>'+referred(c)+'</td><td>'+wgDisplay(c)+'</td><td>'+label(c)+'</td><td>'+formatDate(c.start_date)+'</td><td>'+ (c.period==='lifetime'?'Навсегда':formatDate(c.end_date))+'</td><td><b>'+remain(c)+'</b></td><td>'+subscriptionStatus(c)+'</td><td>'+actions(c)+'</td></tr>').join('');
 $('cards').innerHTML=shown.map(c=>'<article class="card" data-client-id="'+c.id+'"><div class="card-top"><div><h3 class="card-vpn-name">'+esc(peerLabel(c))+'</h3><div class="card-client-name">'+esc(c.name)+'</div>'+inviteBadge(c)+deliveryBadge(c)+(c.phone?'<small>'+esc(c.phone)+'</small>':'')+'</div>'+subscriptionStatus(c)+'</div><div class="info"><div><small>Тариф</small><b>'+label(c)+'</b></div><div><small>До</small><b>'+(c.period==='lifetime'?'Навсегда':formatDate(c.end_date))+'</b></div><div><small>Осталось</small><b>'+remain(c)+' дн.</b></div></div>'+(c.referrer_id?'<div class="note">Пригласил: '+referred(c)+'</div>':'')+'<div class="note mobile-vpn-note">'+wgDisplay(c,true)+'</div><details class="manage"><summary>Управление подпиской</summary><div class="manage-body">'+actions(c)+'</div></details></article>').join('');
 if(capabilities.read_only)document.querySelectorAll('#rows button,#cards button').forEach(button=>button.disabled=true);
 $('create').querySelector('[type=submit]').disabled=!!capabilities.read_only;
 $('empty').classList.toggle('hidden',shown.length>0);
 const current=$('referrer').value;
 $('referrer').innerHTML='<option value="">Никто</option>'+clients.map(c=>'<option value="'+c.id+'">'+esc(c.name)+'</option>').join('');
 $('referrer').value=current;
}
async function refresh(){
 const [a,b,caps]=await Promise.all([call('/api/clients'),call('/api/wg/clients').catch(e=>({clients:[],error:e.message})),call('/health').catch(()=>({}))]);
 capabilities=caps;
 let importer=$('importProfiles');
 if(!importer){importer=document.createElement('button');importer.id='importProfiles';importer.className='secondary';importer.textContent='Импорт профилей';$('exportCsv').insertAdjacentElement('afterend',importer);importer.onclick=importProfiles}
 importer.hidden=!capabilities.profile_import;
 importer.style.display=capabilities.profile_import?'':'none';
 clients=dataList(a);
 // IDs are assigned monotonically by atomic provisioning. Historic/imported
 // timestamps can be missing or in the future; they must not hide new clients.
 clients.sort((a,b)=>Number(b.id||0)-Number(a.id||0));
 wg=b.clients||[];wgAvailable=!b.error;render();
 msg(b.error?'Клиенты загружены, AmneziaWG недоступен: '+b.error:capabilities.read_only?'Тестовая панель доступна только для просмотра':'Данные обновлены',!!b.error);
}
$('toggleCode').onclick=()=>{const field=$('code'),visible=field.type==='password';field.type=visible?'text':'password';$('toggleCode').textContent=visible?'Скрыть':'Показать';$('toggleCode').setAttribute('aria-label',visible?'Скрыть код':'Показать код')};
$('login').onsubmit=async e=>{
 e.preventDefault();try{
 const d=await call('/auth/login',{method:'POST',body:JSON.stringify({code:$('code').value.trim()})});
 token=d.access_token||d.token||'';if(!token)throw Error('Сервер не вернул токен');sessionStorage.setItem(TOKEN_KEY,token);
 $('code').value='';await refresh();$('loginPane').classList.add('m-hide');$('workspace').classList.remove('m-hide');
 }catch(e){token='';sessionStorage.removeItem(TOKEN_KEY);msg('Ошибка входа: '+e.message,true)}
};
async function importProfiles(){
 const button=$('importProfiles');if(button)button.disabled=true;
 try{
  const result=await call('/api/clients/import');const candidates=result.candidates||[];
  if(!candidates.length){msg('Все найденные профили AmneziaWG уже учтены');return}
  const names=candidates.map(c=>c.name+' · '+c.address).join('\n');
  if(!confirm('Импортировать существующие профили ('+candidates.length+')?\n\n'+names+'\n\nКлючи, IP и конфигурации сохранятся.'))return;
  const imported=await call('/api/clients/import',{method:'POST',body:JSON.stringify({client_ids:candidates.map(c=>c.id).sort((a,b)=>a-b)})});
  await refresh();msg('Импортировано профилей: '+imported.imported.length);
 }catch(err){msg('Импорт не выполнен: '+err.message,true)}finally{if(button)button.disabled=false}
}
function peerLabel(c){
 const peer=wg.find(w=>String(w.id)===String(c.wg_client_id));
 const raw=peer?.profile_name||peer?.name||'';
 const match=String(raw).trim().match(/^(?:CAESAR|CEASER)\s+BOT\s*#\s*(\d+)(?=\s|$)/i);
 return match?'CAESAR BOT #'+Number(match[1]):'CAESAR BOT #'+c.id;
}
function addMonthsISO(base,months){
 const parts=String(base).split('-').map(Number);
 const date=new Date(Date.UTC(parts[0],parts[1]-1,1));
 date.setUTCMonth(date.getUTCMonth()+months);
 const last=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0)).getUTCDate();
 date.setUTCDate(Math.min(parts[2],last));
 return date.toISOString().slice(0,10);
}
function localDateTime(value){
 const d=new Date(value);if(!Number.isFinite(d.getTime()))return '';
 return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')+'T'+[d.getHours(),d.getMinutes(),d.getSeconds()].map(n=>String(n).padStart(2,'0')).join(':');
}
function syncEditEndDate(){
 const f=$('editForm'),period=f.elements.period.value,end=f.elements.end_date;
 if(period==='lifetime'){end.disabled=true;end.value='';return}
 end.disabled=false;
 const previous=end.value;
 end.type=period==='trial'?'datetime-local':'date';end.step=period==='trial'?'1':'1';
 if(previous)end.value=period==='trial'?(previous.includes('T')?previous:previous+'T12:00:00'):previous.slice(0,10);
 if(!end.value){const months={'1m':1,'2m':2,'6m':6,'12m':12}[period]||1;end.value=period==='trial'?localDateTime(Date.now()+86400000):period==='14d'?dateOffsetISO(iso(),14):addMonthsISO(iso(),months)}
}
function dateOffsetISO(base,days){
 const date=new Date(base+'T00:00:00Z');
 date.setUTCDate(date.getUTCDate()+days);
 return date.toISOString().slice(0,10);
}
function dateDaysRemaining(end){
 if(!end)return null;
 if(end.includes('T'))return Math.ceil((new Date(end).getTime()-Date.now())/86400000);
 return Math.round((Date.parse(end+'T00:00:00Z')-Date.parse(iso()+'T00:00:00Z'))/86400000);
}
function syncEditDays(){
 const f=$('editForm'),days=$('editDays'),hint=$('editDaysHint'),end=f.elements.end_date;
 const lifetime=f.elements.period.value==='lifetime';
 days.disabled=lifetime;
 if(lifetime){days.value='';hint.textContent='Для ручного изменения дней выберите срочный тариф.';return}
 const count=dateDaysRemaining(end.value);
 days.value=count===null?'':String(count);
 hint.textContent=count===null?'Укажите количество дней. Дата окончания рассчитается автоматически.':'Дата окончания: '+end.value+' · Осталось '+count+' дн.';
}
function nextPeerName(){
 const numbers=wg.map(w=>/^(?:CAESAR|CEASER) BOT #(\d+)(?=\s|$)/i.exec(String(w.name||'').trim())).filter(Boolean).map(m=>Number(m[1]));
 return 'CAESAR BOT #'+(Math.max(0,...numbers)+1);
}
$('create').onsubmit=async e=>{
 e.preventDefault();
 const form=new FormData(e.currentTarget),p=Object.fromEntries(form);
 if(!p.referrer_id)delete p.referrer_id;else p.referrer_id=Number(p.referrer_id);
 const testOnly=capabilities.managed_test_only!==false;
 if(testOnly&&!p.name.startsWith('STAGING TEST '))p.name='STAGING TEST '+p.name;
 if(!confirm('Создать отдельный '+(testOnly?'тестовый ':'')+'профиль «'+p.name+'»?'))return;
 const submit=e.currentTarget.querySelector('[type="submit"]');if(submit)submit.disabled=true;
 try{
  const d=await call('/api/clients/provision',{method:'POST',body:JSON.stringify(p)});
  const id=d.id||d.client?.id;
  if(!id)throw Error('Сервер не подтвердил ID. Повторите тот же запрос без изменения полей.');
  $('create').reset();$('create').elements.start_date.value=iso();
  await refresh();msg('Создан клиент и отдельный профиль «'+p.name+'»');
 }catch(err){msg(err.message,true);alert(err.message)}
 finally{if(submit)submit.disabled=false}
};
async function handleClientAction(e){
 const btn=e.target.closest('button[data-act]');if(!btn)return;
 const id=Number(btn.dataset.id),c=clients.find(x=>Number(x.id)===id);if(!c)return;
 if(btn.dataset.act==='delete'){
  if(!confirm('Удалить клиента «'+c.name+'»?\\n\\nУдаление привязанного VPN-профиля требует отдельной проверки на сервере.'))return;
  btn.disabled=true;
  try{
   await call('/api/clients/'+id+'/delete',{method:'POST'});
   const history=await call('/api/clients/'+id+'/history').catch(()=>null);
   await refresh();msg('Клиент «'+c.name+'» удалён'+(history?.history?' · Сохранено записей истории: '+history.history.length:''));
  }
  catch(err){const message='Не удалось удалить клиента: '+err.message;msg(message,true);alert(message)}
  finally{btn.disabled=false}
  return;
 }
 try{
 if(btn.dataset.act==='delivery'){
 if(c.delivered_at&&!confirm('Снять отметку о передаче профиля «'+c.name+'»?'))return;
 btn.disabled=true;
 try{await call('/api/clients/'+id+'/delivery',{method:'POST',body:JSON.stringify({delivered:!c.delivered_at})});await refresh();msg(c.delivered_at?'Отметка снята':'Передача профиля отмечена')}finally{btn.disabled=false}
 }else if(btn.dataset.act==='referral'){
 const ref=clients.find(x=>String(x.id)===String(c.referrer_id));
 if(!ref)throw Error('Пригласивший клиент не найден');
 const dialog=$('referralDialog');
 dialog.dataset.clientId=String(id);
 $('referralInfo').textContent=c.name+' → '+ref.name;
 $('referralBonusPeriod').value=['1m','6m','12m'].includes(c.period)?c.period:'1m';
 dialog.showModal();
 dialog.focus({preventScroll:true});
 }else if(btn.dataset.act==='edit'){
 const f=$('editForm');f.elements.end_date.type=c.period==='trial'?'datetime-local':'date';f.elements.end_date.step='1';
 for(const key of ['id','name','phone','note','start_date','period','end_date'])f.elements[key].value=key==='end_date'?(c.period==='trial'?localDateTime(c[key]):(c[key]||'').slice(0,10)):(c[key]??'');
 const inviter=$('editReferrer');
 inviter.innerHTML='<option value="">Никто</option>'+clients.filter(x=>String(x.id)!==String(c.id)).map(x=>'<option value="'+esc(x.id)+'">'+esc(x.name)+'</option>').join('');
 inviter.value=c.referrer_id==null?'':String(c.referrer_id);
 syncEditEndDate();
 syncEditDays();
 f.dataset.original=JSON.stringify(Object.fromEntries(new FormData(f)));
 $('editDialog').showModal();
 requestAnimationFrame(()=>{if(document.activeElement?.blur)document.activeElement.blur()});
 }else if(btn.dataset.act==='wg-toggle'){
 const peer=wg.find(w=>String(w.id)===String(c.wg_client_id));
 if(!peer)throw Error('Профиль не подтверждён сервером. Обновите список.');
 const action=peer.enabled?'disable':'enable';
 if(!confirm((action==='disable'?'Отключить':'Включить')+' AmneziaWG для '+c.name+'?'))return;
 await call('/api/wg/client/'+encodeURIComponent(c.wg_client_id)+'/'+action,{method:'POST'});
 await refresh();msg('AmneziaWG '+(action==='disable'?'отключён':'включён'));
 }else if(btn.dataset.act==='wg-qr'||btn.dataset.act==='wg-config'){
 const isQr=btn.dataset.act==='wg-qr';
 const response=await fetch(API+'/api/wg/client/'+encodeURIComponent(c.wg_client_id)+'/'+(isQr?'qr':'config'),{headers:{Authorization:'Bearer '+token}});
 if(!response.ok)throw Error('Не удалось получить '+(isQr?'QR':'конфигурацию')+': HTTP '+response.status);
 const blob=await response.blob();
 if(isQr){
  const url=URL.createObjectURL(blob);
  const qr=$('qrImage');qr.src=url;
  $('qrTitle').textContent=peerLabel(c).replace(/^CEASER\s+BOT/i,'CAESAR BOT');
  $('qrDialog').dataset.peerName=peerLabel(c)||'AmneziaWG';
  $('qrDialog').dataset.tariff=tariff[c.period]||c.period||'—';
  $('qrDialog').dataset.expires=c.period==='lifetime'?'Бессрочно ∞':formatDate(c.end_date);
  $('qrDialog').showModal();
  $('qrDialog').dataset.objectUrl=url;
  return;
 }
 const peerName=peerLabel(c)||'AmneziaWG';
 const fileName=peerName.replace(/[\\/:*?"<>|\x00-\x1f]/g,'_').trim()+'.conf';
 const url=URL.createObjectURL(blob);
 const link=document.createElement('a');link.href=url;link.download=fileName;document.body.appendChild(link);link.click();link.remove();
 setTimeout(()=>URL.revokeObjectURL(url),60000);
 msg('Конфигурация скачивается: '+fileName);
 }else if(btn.dataset.act==='wg-create'){
 if(!confirm('Создать AmneziaWG-профиль для '+c.name+' и привязать к SQLite?'))return;
 const result=await call('/api/wg/client',{method:'POST',body:JSON.stringify({name:nextPeerName()})});
 const peer=result.client||result;
 const wid=result.id||result.client_id||result.wg_client_id||peer.id;
 if(!wid)throw Error('AmneziaWG создал профиль, но не вернул ID. Проверьте AmneziaWG перед повтором.');
 try{await call('/api/clients/'+id+'/wireguard',{method:'POST',body:JSON.stringify({wg_client_id:wid})})}
 catch(err){throw Error('AmneziaWG профиль '+wid+' создан, но привязка не удалась: '+err.message+'. НЕ повторяйте создание.')}
 await refresh();msg('AmneziaWG создан и привязан к '+c.name);
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
 requestAnimationFrame(()=>{dialog.focus({preventScroll:true})});
 }
 }catch(e){msg(e.message,true)}
}
$('rows').addEventListener('click',handleClientAction);
$('cards').addEventListener('click',handleClientAction);
$('closeQr').onclick=()=>$('qrDialog').close();
$('shareQr').onclick=async()=>{
 const dialog=$('qrDialog'),img=$('qrImage'),button=$('shareQr');
 if(!img.complete||!img.naturalWidth){msg('QR-код ещё загружается',true);return}
 button.disabled=true;
 try{
  const name=dialog.dataset.peerName||'AmneziaWG';
  const canvas=document.createElement('canvas');
  canvas.width=900;canvas.height=1210;
  const ctx=canvas.getContext('2d');
  const round=(x,y,w,h,r,fill,stroke)=>{
   ctx.beginPath();ctx.roundRect(x,y,w,h,r);
   if(fill){ctx.fillStyle=fill;ctx.fill()}
   if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=2;ctx.stroke()}
  };
  const text=(str,x,y,font,color,align='left')=>{ctx.font=font;ctx.fillStyle=color;ctx.textAlign=align;ctx.fillText(str,x,y)};
  const bg=ctx.createLinearGradient(0,0,900,1210);
  bg.addColorStop(0,'#192537');bg.addColorStop(1,'#0b121e');
  ctx.fillStyle=bg;ctx.fillRect(0,0,900,1210);
  ctx.strokeStyle='rgba(70,224,178,.12)';ctx.lineWidth=2;
  ctx.beginPath();ctx.arc(870,40,260,0,Math.PI*2);ctx.stroke();
  ctx.beginPath();ctx.arc(870,40,330,0,Math.PI*2);ctx.stroke();
  round(56,54,8,62,4,'#49d7ad');
  const label=name.length>34?name.slice(0,33)+'…':name;
  text(label.replace(/^CEASER\s+BOT/i,'CAESAR BOT'),88,104,'bold 44px Arial,sans-serif','#f5f9ff');
  round(56,146,788,784,24,'#ffffff');
  ctx.imageSmoothingEnabled=false;
  const qrSize=732;
  const qrX=56+(788-qrSize)/2;
  const qrY=146+(784-qrSize)/2;
  ctx.drawImage(img,qrX,qrY,qrSize,qrSize);
  ctx.imageSmoothingEnabled=true;
  round(56,954,382,126,18,'#202e40','#34465a');
  round(462,954,382,126,18,'#202e40','#34465a');
  text('ТАРИФ',82,997,'bold 18px Arial,sans-serif','#9caec3');
  text('ДЕЙСТВУЕТ ДО',488,997,'bold 18px Arial,sans-serif','#9caec3');
  const tariffLabel=dialog.dataset.tariff||'—',expires=dialog.dataset.expires||'—';
  text(tariffLabel.length>22?tariffLabel.slice(0,21)+'…':tariffLabel,82,1045,'bold 29px Arial,sans-serif','#f5f9ff');
  text(expires,488,1045,'bold 29px Arial,sans-serif','#f5f9ff');
  text('Сканируйте код в приложении AmneziaWG',450,1140,'22px Arial,sans-serif','#b7c6d9','center');
  const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('Не удалось создать изображение')),'image/png'));
  const file=new File([blob],'CAESAR-BOT-QR.png',{type:'image/png'});
  const match=name.match(/(?:CAESAR|CEASER)\s+BOT\s*#\s*(\d+)/i);
  const botId=match?'CAESAR BOT #'+match[1]:name;
  const shareText=botId+'\nВаш QR-код для подключения VPN через AmneziaWG.\nТариф: '+(dialog.dataset.tariff||'—')+'\nДействует до: '+(dialog.dataset.expires||'—');
  if(navigator.share&&(!navigator.canShare||navigator.canShare({files:[file]}))){
   try{await navigator.share({files:[file],title:'CAESAR BOT #',text:shareText});return}
   catch(e){if(e.name==='AbortError')return}
  }
  const url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=file.name;document.body.appendChild(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),30000);
  try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(shareText);msg('QR сохранён, текст CAESAR BOT # скопирован. Отправьте оба получателю.')}else msg('QR сохранён. Добавьте текст CAESAR BOT # при отправке.')}catch{msg('QR сохранён. Добавьте текст CAESAR BOT # при отправке.')}
 }catch(e){msg('Не удалось отправить QR-код: '+e.message,true)}
 finally{button.disabled=false}
};

$('qrDialog').addEventListener('close',()=>{const d=$('qrDialog');if(d.dataset.objectUrl){URL.revokeObjectURL(d.dataset.objectUrl);delete d.dataset.objectUrl;$('qrImage').removeAttribute('src')}});
$('closeHistory').onclick=()=>$('historyDialog').close();
$('closeEdit').onclick=()=>$('editDialog').close();
$('editForm').elements.period.addEventListener('change',()=>{syncEditEndDate();syncEditDays()});
$('editForm').elements.end_date.addEventListener('change',syncEditDays);
$('editDays').addEventListener('input',()=>{
 const f=$('editForm'),input=$('editDays');
 if(f.elements.period.value==='lifetime'||input.value==='')return;
 const days=Number(input.value);
 if(!Number.isSafeInteger(days)||days<0||days>36500){$('editDaysHint').textContent='Укажите от 0 до 36500 дней';return}
 f.elements.end_date.value=f.elements.period.value==='trial'?localDateTime(Date.now()+days*86400000):dateOffsetISO(iso(),days);
 $('editDaysHint').textContent='Дата окончания: '+f.elements.end_date.value+' · Осталось '+days+' дн.';
});
$('editForm').onsubmit=async e=>{
 e.preventDefault();const d=Object.fromEntries(new FormData(e.currentTarget));const id=Number(d.id);
 const selectedPeriod=d.period;
 delete d.id;
 if(!('note' in d))d.note=clients.find(c=>Number(c.id)===id)?.note||'';
 d.referrer_id=d.referrer_id?Number(d.referrer_id):null;
 if(d.period==='lifetime')d.end_date=null;
 else {
  if(!['trial','14d','1m','2m','6m','12m','imported'].includes(d.period)){alert('Некорректный тариф. Изменения не сохранены.');return}
  if(!d.end_date){alert('Укажите дату окончания подписки. Изменения не сохранены.');return}
  if(d.period==='trial'?!Number.isFinite(new Date(d.end_date).getTime()):(!/^\d{4}-\d{2}-\d{2}$/.test(d.end_date)||!Number.isFinite(Date.parse(d.end_date+'T00:00:00Z')))){alert('Некорректная дата окончания.');return}
 }
 const original=JSON.parse(e.currentTarget.dataset.original||'{}');
 for(const key of Object.keys(d)){
  const old=key==='referrer_id'?(original[key]?Number(original[key]):null):(original[key]??'');
  if(d[key]===old || (key==='end_date'&&d.period==='lifetime'&&!old))delete d[key];
 }
 if(!Object.keys(d).length){$('editDialog').close();return}
 if(selectedPeriod==='trial'&&d.end_date)d.end_date=new Date(d.end_date).toISOString().replace(/Z$/,'+00:00');
 try{await call('/api/clients/'+id,{method:'PATCH',body:JSON.stringify(d)});$('editDialog').close();await refresh();msg('Клиент обновлён')}
 catch(err){const message='Не удалось сохранить изменения: '+err.message;msg(message,true);alert(message)}
};

function csvCell(value){const v=String(value??'');return '"'+(/^[=+@\-\t\r]/.test(v)?"'"+v:v).replace(/"/g,'""')+'"'}
$('exportCsv').onclick=()=>{
 const cols=['ID','Имя','Телефон','Тариф','Начало','Окончание','Осталось дней','WG ID','WG статус','Пригласил','Заметка'];
 const data=clients.map(c=>[c.id,c.name,c.phone,c.period,c.start_date,c.end_date,remaining(c),c.wg_client_id,!wgAvailable?'Статус недоступен':c.wg_client_id?(wg.find(w=>String(w.id)===String(c.wg_client_id))?.enabled?'Включён':'Выключен или не найден'):'Нет',clients.find(x=>String(x.id)===String(c.referrer_id))?.name||'',c.note]);
 if(capabilities.profile_delivery){cols.push('Профиль передан');data.forEach((row,i)=>row.push(clients[i].delivered_at||''))}
 const csv='\uFEFF'+[cols,...data].map(row=>row.map(csvCell).join(';')).join('\r\n');
 const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
 const a=document.createElement('a');a.href=url;a.download='vpn-manager-clients-'+iso()+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
 msg('Экспортировано клиентов: '+clients.length);
};
$('reload').onclick=()=>window.location.reload();
$('search').oninput=render;$('filter').onchange=render;
$('siteNotifications').addEventListener('toggle',e=>{if(e.target.matches('.attention-group')&&e.target.open){$('siteNotifications').querySelectorAll('.attention-group').forEach(group=>{if(group!==e.target)group.open=false})}},true);
$('siteNotifications').addEventListener('click',e=>{
 const button=e.target.closest('button[data-jump-client]');if(!button)return;
 const id=button.dataset.jumpClient;
 $('search').value='';const targetClient=clients.find(c=>String(c.id)===id);$('filter').value=targetClient&&targetClient.period!=='lifetime'&&remaining(targetClient)<=0?'expired':'active';render();
 const mobile=window.matchMedia('(max-width:900px)').matches;
 const container=mobile?$('cards'):$('rows');
 const target=Array.from(container.querySelectorAll('[data-client-id]')).find(el=>el.dataset.clientId===id);
 if(!target){msg('Клиент не найден в списке',true);return}
 target.scrollIntoView({behavior:'smooth',block:'center'});
 target.classList.add('client-jump-highlight');
 setTimeout(()=>target.classList.remove('client-jump-highlight'),2500);
});
$('logout').onclick=async()=>{try{await call('/auth/logout',{method:'POST'})}catch{}token='';sessionStorage.removeItem(TOKEN_KEY);clients=[];wg=[];$('workspace').classList.add('m-hide');$('loginPane').classList.remove('m-hide');msg('Выход выполнен')};
$('create').elements.start_date.value=iso();
if(token){
 $('loginPane').classList.add('m-hide');
 $('workspace').classList.remove('m-hide');
 refresh().catch(err=>{
   $('workspace').classList.add('m-hide');
   $('loginPane').classList.remove('m-hide');
   msg(/Unauthorized|401|expired|ист[её]к/i.test(err.message)?'Сеанс истёк. Войдите снова — код можно подставить из сохранённых паролей.':'Не удалось восстановить сеанс: '+err.message,true);
 });
}
$('cancelReferral').onclick=()=>$('referralDialog').close();
$('referralForm').addEventListener('submit',async e=>{
 e.preventDefault();
 const dialog=$('referralDialog'),id=Number(dialog.dataset.clientId),period=$('referralBonusPeriod').value;
 const bonus={'1m':14,'6m':30,'12m':60}[period];
 const btn=$('confirmReferral');
 if(!bonus)return;
 if(!confirm('Начислить +'+bonus+' дней пригласившему? Повторное начисление за одного клиента запрещено.'))return;
 btn.disabled=true;
 try{
  const referred=clients.find(c=>Number(c.id)===id);
  if(!referred?.referrer_id)throw Error('Пригласивший клиент не найден');
  await call('/api/clients/'+referred.referrer_id+'/referral',{method:'POST',body:JSON.stringify({client_id:id,bonus_period:period})});
  dialog.close();await refresh();msg('Реферальный бонус обработан: +'+bonus+' дней; бессрочные подписки сохраняют ∞');
 }catch(err){msg('Ошибка бонуса: '+err.message,true);alert('Ошибка бонуса: '+err.message)}
 finally{btn.disabled=false}
});
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

// Mobile header actions reuse the existing desktop handlers.
for(const [mobile,desktop] of [['mobileReload','reload'],['mobileExport','exportCsv'],['mobileLogout','logout']]){
 const button=$(mobile);
 if(button)button.addEventListener('click',()=>{if(mobile==='mobileReload'){window.location.reload();return}$(desktop).click()});
}
const mobileActions=$('mobileHeaderActions');
if(mobileActions){
 const syncMobileActions=()=>{mobileActions.hidden=$('workspace').classList.contains('m-hide')};
 new MutationObserver(syncMobileActions).observe($('workspace'),{attributes:true,attributeFilter:['class']});
 syncMobileActions();
}
})();

