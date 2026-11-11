(()=>{
'use strict';
const API='https://vpn-staging.80-90-179-65.sslip.io';
const $=id=>document.getElementById(id);
let token='',clients=[],wg=[];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const msg=(t,bad=false)=>{$('status').textContent=t;$('status').className=bad?'m-error':'m-ok'};
const iso=()=>{const d=new Date();return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')};
const date=s=>s?new Date(s.slice(0,10)+'T12:00:00'):null;
const remaining=c=>c.end_date?Math.ceil((date(c.end_date)-date(iso()))/86400000):null;
async function call(path,opts={}){
 const r=await fetch(API+path,{...opts,headers:{'Content-Type':'application/json',...(token?{'Authorization':'Bearer '+token}:{}),...(opts.headers||{})}});
 let d;try{d=await r.json()}catch{throw Error('Некорректный ответ сервера: '+r.status)}
 if(!r.ok||d.ok===false){if(r.status===401){token='';$('workspace').classList.add('m-hide');$('loginPane').classList.remove('m-hide')}throw Error(d.error||'HTTP '+r.status)}
 return d;
}
const dataList=d=>{const x=d.clients??d.data??d; if(!Array.isArray(x))throw Error('Неизвестный формат списка клиентов');return x};
function render(){
 const q=$('search').value.trim().toLowerCase(),f=$('filter').value;
 const shown=clients.filter(c=>(!q||[c.name,c.phone,c.note].some(v=>String(v||'').toLowerCase().includes(q)))&&(f==='all'||(f==='lifetime'&&c.period==='lifetime')||(f==='active'&&(c.period==='lifetime'||remaining(c)>0))||(f==='expired'&&c.period!=='lifetime'&&remaining(c)<=0)));
 $('counts').textContent='Всего: '+clients.length+' · Активных: '+clients.filter(c=>c.period==='lifetime'||remaining(c)>0).length;
 const wgIds=new Set(wg.map(w=>w.id));
 $('rows').innerHTML=shown.map(c=>'<tr><td><b>'+esc(c.name)+'</b><div class="m-muted">'+esc(c.note||'')+'</div></td><td>'+esc(c.phone||'—')+'</td><td>'+esc(c.period)+'</td><td>'+esc(c.end_date||'∞')+'</td><td>'+esc(c.wg_client_id?(wgIds.has(c.wg_client_id)?'Привязан':'Не найден'):'Не привязан')+'</td><td><div class="m-actions"><button data-act="extend" data-id="'+c.id+'" class="secondary">Продлить</button><button data-act="history" data-id="'+c.id+'" class="secondary">История</button></div></td></tr>').join('');
 const current=$('referrer').value;
 $('referrer').innerHTML='<option value="">Никто</option>'+clients.map(c=>'<option value="'+c.id+'">'+esc(c.name)+'</option>').join('');
 $('referrer').value=current;
}
async function refresh(){
 const [a,b]=await Promise.all([call('/api/clients'),call('/api/wg/clients').catch(e=>({clients:[],error:e.message}))]);
 clients=dataList(a);wg=b.clients||[];render();
 msg(b.error?'Клиенты загружены, WireGuard недоступен: '+b.error:'Данные обновлены',!!b.error);
}
$('login').onsubmit=async e=>{
 e.preventDefault();try{
 const d=await call('/auth/login',{method:'POST',body:JSON.stringify({code:$('code').value})});
 token=d.access_token||d.token||'';if(!token)throw Error('Сервер не вернул токен');
 $('code').value='';await refresh();$('loginPane').classList.add('m-hide');$('workspace').classList.remove('m-hide');
 }catch(e){token='';msg('Ошибка входа: '+e.message,true)}
};
$('create').onsubmit=async e=>{
 e.preventDefault();if(!confirm('Создать запись клиента в SQLite? WireGuard-профиль пока не создаётся автоматически.'))return;
 const form=new FormData(e.currentTarget),p=Object.fromEntries(form);
 p.user_id='vpn-manager-admin';if(!p.referrer_id)delete p.referrer_id;
 try{const d=await call('/api/clients/create',{method:'POST',body:JSON.stringify(p)});msg('Клиент создан в SQLite (ID '+d.id+'). WireGuard пока не создан.');$('create').reset();$('create').elements.start_date.value=iso();await refresh()}catch(e){msg(e.message,true)}
};
$('rows').onclick=async e=>{
 const btn=e.target.closest('button[data-act]');if(!btn)return;
 const id=Number(btn.dataset.id),c=clients.find(x=>x.id===id);if(!c)return;
 try{
 if(btn.dataset.act==='history'){
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
};
$('closeHistory').onclick=()=>$('historyDialog').close();
$('reload').onclick=()=>refresh().catch(e=>msg(e.message,true));
$('search').oninput=render;$('filter').onchange=render;
$('logout').onclick=async()=>{try{await call('/auth/logout',{method:'POST'})}catch{}token='';clients=[];wg=[];$('workspace').classList.add('m-hide');$('loginPane').classList.remove('m-hide');msg('Выход выполнен')};
$('create').elements.start_date.value=iso();
})();