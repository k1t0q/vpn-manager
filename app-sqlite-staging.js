(() => {
const cfg=window.VPN_MANAGER_CONFIG||{};
const configured=true;
const $=id=>document.getElementById(id);
if(!configured){$("setupWarning").classList.remove("hidden");return}

const STAGING="https://vpn-staging.80-90-179-65.sslip.io";
let sqliteToken="";
async function sqlite(path,method="GET",payload){
  const res=await fetch(STAGING+path,{method,headers:{"Authorization":"Bearer "+sqliteToken,...(payload===undefined?{}:{"Content-Type":"application/json"})},body:payload===undefined?undefined:JSON.stringify(payload),cache:"no-store"});
  const data=await res.json().catch(()=>({}));
  if(!res.ok||data.ok===false)throw Error(data.error||"SQLite HTTP "+res.status);
  return data;
}

const BACKEND=STAGING;
let session=null,clients=[],wgClients=[],attentionOnly=false,clientFilter="all";

const labels={"trial":"Пробный — 24 часа","14d":"14 дней","1m":"1 месяц","2m":"2 месяца","6m":"6 месяцев","12m":"12 месяцев","lifetime":"Навсегда","imported":"Импорт"};
const referralBonus={"1m":14,"6m":30,"12m":60};
const pad=n=>String(n).padStart(2,"0");
const today=()=>{const d=new Date();return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`};
const parse=s=>{const[y,m,d]=s.split("-").map(Number);return new Date(y,m-1,d,12)};
const iso=d=>`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
const fmt=s=>{if(!s)return"";const[y,m,d]=s.split("-");return `${d}.${m}.${y}`};
const esc=v=>String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;");
const days=s=>s?Math.round((parse(s)-parse(today()))/86400000):null;
const status=(s,period)=>{if(period==="lifetime")return["Навсегда ∞","active"];const d=days(s);return d===null?["Срок не указан","unknown"]:d<=0?["Истекла","expired"]:d<=7?["Скоро","soon"]:["Активна","active"]};

function addPeriod(start,p){
  const d=parse(start);
  if(p==="trial"){d.setDate(d.getDate()+1);return iso(d)}
  if(p==="14d"){d.setDate(d.getDate()+14);return iso(d)}
  const map={"1m":1,"2m":2,"6m":6,"12m":12};
  if(!(p in map))throw new Error("Неизвестный тариф: "+p);
  const day=d.getDate(),mm=map[p];
  d.setDate(1);d.setMonth(d.getMonth()+mm);
  const last=new Date(d.getFullYear(),d.getMonth()+1,0).getDate();
  d.setDate(Math.min(day,last));return iso(d)
}
function addDaysToDate(s,n){const d=parse(s);d.setDate(d.getDate()+n);return iso(d)}
function msg(t="",bad=false){$("message").textContent=t;$("message").style.color=bad?"var(--red)":"var(--green)"}

function attentionNeeded(c){
  if(c.period==="lifetime"){const w=wgFor(c);return !!c.wg_client_id&&(!w||!w.enabled)}
  const d=days(c.end_date),w=wgFor(c);
  return d===null || d<=7 || (c.wg_client_id && (!w || !w.enabled));
}
function handshakeText(w){
  if(!w?.latestHandshakeAt)return "Никогда";
  const t=new Date(w.latestHandshakeAt);
  if(Number.isNaN(t.getTime()))return "Неизвестно";
  const diff=Math.max(0,Date.now()-t.getTime()),min=Math.floor(diff/60000);
  if(min<1)return "только что";
  if(min<60)return `${min} мин. назад`;
  const h=Math.floor(min/60); if(h<24)return `${h} ч. назад`;
  const d=Math.floor(h/24); return `${d} дн. назад`;
}
async function logHistory(){ /* Server records create/update/extend events. */ }
async function showHistory(c){
  $("historyTitle").textContent="История — "+c.name;
  $("historyList").innerHTML='<div class="history-empty">Загрузка…</div>';
  $("historyDialog").showModal();
  try{
    const data=await sqlite("/api/clients/"+c.id+"/history");
    $("historyList").innerHTML=data.history.length?data.history.map(x=>'<div class="history-item"><div><b>'+esc(x.description||x.event_type)+'</b><small>'+new Date(x.created_at).toLocaleString("ru-RU")+'</small></div></div>').join(""):'<div class="history-empty">История пуста</div>';
  }catch(e){$("historyList").textContent=e.message}
}

async function api(path,opts={}){
  if(!sqliteToken)throw Error("Сначала войдите в SQLite");
  const headers={...(opts.headers||{}),"Authorization":"Bearer "+sqliteToken};
  if(typeof opts.body==="string")headers["Content-Type"]="application/json";
  const r=await fetch(STAGING+path,{...opts,headers,cache:"no-store"});
  if(!r.ok){let error="HTTP "+r.status;try{error=(await r.json()).error||error}catch{}throw Error(error)}
  return r;
}
async function apiJson(path,opts={}){return (await api(path,opts)).json()}
const urlBase64ToUint8Array=s=>{const p="=".repeat((4-s.length%4)%4),b=(s+p).replace(/-/g,"+").replace(/_/g,"/"),r=atob(b);return Uint8Array.from([...r].map(c=>c.charCodeAt(0)))};
async function registerPushWorker(){
  if(!("serviceWorker" in navigator)||!("PushManager" in window)){if($("pushStatus"))$("pushStatus").textContent="Push не поддерживается";return null}
  try{const reg=await navigator.serviceWorker.register("./sw.js?v=5.16");const sub=await reg.pushManager.getSubscription();if($("pushStatus"))$("pushStatus").textContent=sub?"Push включён":"Push выключен";if($("pushBtn"))$("pushBtn").textContent=sub?"Push-уведомления включены":"Включить push-уведомления";return reg}catch(e){if($("pushStatus"))$("pushStatus").textContent="Ошибка Service Worker";console.warn(e);return null}
}
async function enablePush(){
  try{
    const reg=await registerPushWorker();if(!reg)throw new Error("Push не поддерживается на этом устройстве");
    const permission=await Notification.requestPermission();if(permission!=="granted")throw new Error("Разрешение на уведомления не выдано");
    const key=cfg.VAPID_PUBLIC_KEY;if(!key||key.includes("PASTE_"))throw new Error("На сервере ещё не создан VAPID-ключ");
    let sub=await reg.pushManager.getSubscription();if(!sub)sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:urlBase64ToUint8Array(key)});
    const r=await api("/api/push/subscribe",{method:"POST",body:JSON.stringify(sub.toJSON())});
    if(!r.ok)throw new Error("Не удалось сохранить push-подписку");
    $("pushStatus").textContent="Push включён";$("pushBtn").textContent="Push-уведомления включены";msg("Push-уведомления включены");
  }catch(e){msg("Push: "+e.message,true)}
}


function fillReferrers(){
  const sel=$("referrer"),cur=sel.value;
  sel.innerHTML='<option value="">Никто / не указан</option>'+clients.slice().sort((a,b)=>(a.name||"").localeCompare(b.name||"","ru")).map(c=>`<option value="${c.id}">${esc(c.name)}${c.phone?` — ${esc(c.phone)}`:""}</option>`).join("");
  if([...sel.options].some(o=>o.value===cur))sel.value=cur;
}
function updateReferralHint(){
  const bonus=referralBonus[$("period").value]||0,who=$("referrer").value;
  $("referralHint").textContent=who?(bonus?`Пригласившему будет начислено +${bonus} дней.`:"Для этого тарифа бонус не начисляется."):"";
}
function preview(){const s=$("startDate").value;if(s)$("preview").textContent="Подписка до: "+fmt(addPeriod(s,$("period").value))}
function wgFor(c){return c.wg_client_id?wgClients.find(w=>w.id===c.wg_client_id):null}
function linkOptions(){
  const used=new Set(clients.map(c=>c.wg_client_id).filter(Boolean));
  return '<option value="">Выбрать существующий VPN</option>'+wgClients.filter(w=>!used.has(w.id)).map(w=>`<option value="${w.id}">${esc(w.name)} — ${esc(w.address||"")}</option>`).join("");
}
async function loadWG(){
  try{const x=await apiJson("/api/wg/clients");wgClients=x.clients||[]}
  catch(e){wgClients=[];msg("WireGuard недоступен: "+e.message,true)}
}
async function load(){
  try{const data=await sqlite("/api/clients");clients=(data.clients||[]).sort((a,b)=>(a.end_date||"9999").localeCompare(b.end_date||"9999"));fillReferrers();await loadWG();render()}catch(e){msg(e.message,true)}
}
function sortClientsNewestFirst(){
  clients.sort((a,b)=>{
    const at=a.created_at?new Date(a.created_at).getTime():0;
    const bt=b.created_at?new Date(b.created_at).getTime():0;
    return bt-at;
  });
}

function renderNotifications(){
  const box=$("siteNotifications");
  if(!box)return;
  const alerts=[];
  clients.forEach(c=>{
    if(c.period==="lifetime")return;
    const d=days(c.end_date);
    if(d===3)alerts.push({type:"warning",text:`У клиента ${esc(c.name)} осталось 3 дня — до ${fmt(c.end_date)}.`});
    else if(d!==null&&d<=0)alerts.push({type:"expired",text:`У клиента ${esc(c.name)} закончилась подписка${c.wg_client_id?" — VPN отключён":""}.`});
  });
  box.classList.toggle("hidden",alerts.length===0);
  box.innerHTML=alerts.length?`<div class="site-notifications-head">Уведомления <span>${alerts.length}</span></div>`+alerts.map(a=>`<div class="site-notification ${a.type}">${a.text}</div>`).join(""):"";
}

function renderStats(){
  let a=0,s=0,e=0;
  clients.forEach(c=>{if(c.period==="lifetime"){a++;return}const d=days(c.end_date);if(d===null)return;if(d<=0)e++;else{a++;if(d<=7)s++}});
  $("sTotal").textContent=clients.length;$("sActive").textContent=a;$("sSoon").textContent=s;$("sExpired").textContent=e;
}
function filtered(){
  const q=$("search").value.trim().toLowerCase();

  let base=attentionOnly?clients.filter(attentionNeeded):clients;

  if(clientFilter!=="all"&&clientFilter!=="days_asc"){
    base=base.filter(c=>{
      const d=days(c.end_date),w=wgFor(c);
      if(clientFilter==="active")return c.period==="lifetime"||(d!==null&&d>7);
      if(clientFilter==="soon")return d!==null&&d>0&&d<=7;
      if(clientFilter==="expired")return d!==null&&d<=0;
      if(clientFilter==="unknown")return c.period!=="lifetime"&&d===null;
      if(clientFilter==="wg_on")return !!(w&&w.enabled);
      if(clientFilter==="wg_off")return !!c.wg_client_id&&(!w||!w.enabled);
      return true;
    });
  }

  if(q){
    base=base.filter(c=>{
      const w=wgFor(c);
      return (c.name||"").toLowerCase().includes(q)
        ||(c.phone||"").toLowerCase().includes(q)
        ||(c.note||"").toLowerCase().includes(q)
        ||(w?.name||"").toLowerCase().includes(q)
        ||(w?.address||"").toLowerCase().includes(q);
    });
  }

  if(clientFilter==="days_asc"){
    base=base.slice().sort((a,b)=>{
      const ad=a.period==="lifetime"?Infinity:days(a.end_date);
      const bd=b.period==="lifetime"?Infinity:days(b.end_date);
      const av=ad===null?Infinity:ad;
      const bv=bd===null?Infinity:bd;
      return av-bv || (a.name||"").localeCompare(b.name||"","ru");
    });
  }

  return base;
}
function wgStatus(c){
  if(!c.wg_client_id)return '<span class="wg-status none">Не привязан</span>';
  const w=wgFor(c);
  if(!w)return '<span class="wg-status off">Не найден</span>';
  return `<span class="wg-status ${w.enabled?"on":"off"}">${w.enabled?"● VPN включён":"● VPN отключён"}</span>${w.address?`<small>IP: ${esc(w.address)}</small>`:""}<small>Последнее подключение: ${esc(handshakeText(w))}</small>`;
}
function wgControls(c){
  if(!c.wg_client_id){
    return `<div class="wg-controls"><button data-a="wg-create" data-id="${c.id}">Создать VPN</button><select data-link="${c.id}">${linkOptions()}</select><button class="secondary" data-a="wg-link" data-id="${c.id}">Привязать</button></div>`;
  }
  const w=wgFor(c);
  if(!w)return `<div class="wg-controls"><button class="secondary" data-a="wg-unlink" data-id="${c.id}">Сбросить привязку</button></div>`;
  return `<div class="wg-controls">
    <button data-a="${w.enabled?"wg-disable":"wg-enable"}" data-id="${c.id}">${w.enabled?"Отключить VPN":"Включить VPN"}</button>
    <div class="share-row">
      <button class="secondary" data-a="wg-qr" data-id="${c.id}">QR</button>
      <button class="secondary" data-a="wg-config" data-id="${c.id}">Конфиг</button>
    </div>
    <div class="share-row">
      <button class="secondary" data-a="wg-share-qr" data-id="${c.id}">Отправить QR</button>
      <button class="secondary" data-a="wg-share-config" data-id="${c.id}">Отправить config</button>
    </div>
  </div>`;
}
function subControls(c){
  return `<select data-sel="${c.id}"><option value="trial">24 часа</option><option value="14d">14 дней</option><option value="1m">1 месяц</option><option value="2m">2 месяца</option><option value="6m">6 месяцев</option><option value="12m">12 месяцев</option><option value="lifetime">Навсегда ∞</option></select><button data-a="extend" data-id="${c.id}">Продлить</button><button class="secondary" data-a="edit" data-id="${c.id}">Изменить</button><button class="secondary" data-a="history" data-id="${c.id}">История</button><button class="danger" data-a="delete" data-id="${c.id}">Удалить</button>`;
}
function render(){sortClientsNewestFirst();
  renderStats();renderNotifications();const ac=clients.filter(attentionNeeded).length;if($("attentionCount"))$("attentionCount").textContent=ac;const list=filtered();$("empty").classList.toggle("hidden",list.length>0);
  $("rows").innerHTML=list.map(c=>{const[st,cl]=status(c.end_date,c.period);return `<tr>
    <td><b>${esc(c.name)}</b>${c.note?`<small>${esc(c.note)}</small>`:""}</td>
    <td>${esc(c.phone||"")}</td>
    <td>${esc((clients.find(x=>x.id===c.referrer_id)||{}).name||"—")}</td>
    <td>${wgStatus(c)}${wgControls(c)}</td>
    <td>${esc(labels[c.period]||c.period||"—")}</td>
    <td>${fmt(c.start_date)}</td><td>${c.period==="lifetime"?"Навсегда":(c.end_date?fmt(c.end_date):"—")}</td><td>${c.period==="lifetime"?"∞":(days(c.end_date)===null?"—":days(c.end_date))}</td>
    <td><span class="status ${cl}">${st}</span></td>
    <td><div class="actions">${subControls(c)}</div></td>
  </tr>`}).join("");
  $("cards").innerHTML=list.map(c=>{const[st,cl]=status(c.end_date,c.period);return `<article class="card">
    <div class="card-top"><div><h3>${esc(c.name)}</h3>${c.phone?`<a class="phone" href="tel:${esc(c.phone)}">${esc(c.phone)}</a>`:""}</div><span class="status ${cl}">${st}</span></div>
    <div class="info"><div><small>Тариф</small><b>${esc(labels[c.period]||c.period||"—")}</b></div><div><small>До</small><b>${c.period==="lifetime"?"Навсегда":(c.end_date?fmt(c.end_date):"—")}</b></div><div><small>Осталось</small><b>${c.period==="lifetime"?"∞":(days(c.end_date)===null?"—":days(c.end_date)+" дн.")}</b></div></div>
    ${c.referrer_id?`<div class="note">Пригласил: <b>${esc((clients.find(x=>x.id===c.referrer_id)||{}).name||"—")}</b></div>`:""}
    ${c.note?`<div class="note">${esc(c.note)}</div>`:""}
    <div class="note"><b>WireGuard:</b> ${wgStatus(c)}${wgControls(c)}</div>
    <details class="manage"><summary>Управление подпиской</summary><div class="manage-body">${subControls(c)}</div></details>
  </article>`}).join("");
}

async function shareBlob(blob,filename,title){
  const file=new File([blob],filename,{type:blob.type||"application/octet-stream"});
  if(navigator.canShare && navigator.canShare({files:[file]}) && navigator.share){
    await navigator.share({title,files:[file]});
    return;
  }
  const url=URL.createObjectURL(blob),a=document.createElement("a");
  a.href=url;a.download=filename;a.click();
  setTimeout(()=>URL.revokeObjectURL(url),30000);
  msg("Системная отправка файлов недоступна — файл скачан.");
}


function parseDateFromWgName(name){
  const s=String(name||"");
  const m=s.match(/(?:^|[\s(])(\d{1,2})[.\-](\d{1,2})[.\-](\d{2,4})(?:\)|\s|$)/);
  if(!m)return null;
  let y=Number(m[3]); if(y<100)y+=2000;
  const d=Number(m[1]),mo=Number(m[2]);
  const dt=new Date(y,mo-1,d,12);
  if(dt.getFullYear()!==y||dt.getMonth()!==mo-1||dt.getDate()!==d)return null;
  return iso(dt);
}

function wgCreatedDate(c){
  const raw=c.createdAt||c.updatedAt;
  if(raw){
    const d=new Date(raw);
    if(!Number.isNaN(d.getTime()))return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
  }
  return today();
}

async function importWireGuardClients(){msg("Импорт в SQLite временно заблокирован до проверки безопасного импорта.",true)}

$("addForm").addEventListener("submit",async e=>{
  e.preventDefault();
  const start=$("startDate").value,p=$("period").value,referrerId=$("referrer").value?Number($("referrer").value):null;
  let wgClientId=null;
  try{
    const createVpnEl=$("createVpn");
    if(!createVpnEl || createVpnEl.checked){
      const x=await apiJson("/api/wg/client",{method:"POST",body:JSON.stringify({name:$("name").value.trim()})});
      wgClientId=x.client?.id||null;
      if(!wgClientId)throw new Error("Не получен ID WireGuard");
    }
    const payload={user_id:"sqlite-admin",name:$("name").value.trim(),phone:$("phone").value.trim(),start_date:start,end_date:p==="lifetime"?null:addPeriod(start,p),period:p,note:$("note").value.trim(),referrer_id:referrerId,wg_client_id:wgClientId};
    const created=await sqlite("/api/clients/create","POST",payload);

    const bonus=referralBonus[p]||0;
    if(referrerId&&bonus>0){
      const ref=clients.find(x=>x.id===referrerId);
      if(ref){
        const base=days(ref.end_date)>0?ref.end_date:today();
        await sqlite("/api/referrals/award","POST",{referrer_id:referrerId,referred_id:created.id,days:bonus});
        if(ref.wg_client_id){try{await api(`/api/wg/client/${ref.wg_client_id}/enable`,{method:"POST"})}catch{}}
      }
    }
    e.target.reset();$("startDate").value=today();$("period").value="1m";if($("createVpn"))$("createVpn").checked=true;preview();updateReferralHint();msg("Клиент добавлен");await load();
  }catch(err){msg("Ошибка добавления: "+err.message,true)}
});

document.addEventListener("click",async e=>{
  const b=e.target.closest("[data-a]");if(!b)return;
  const id=Number(b.dataset.id),c=clients.find(x=>x.id===id);if(!c)return;
  try{
    const a=b.dataset.a;
    if(a==="history"){return showHistory(c)}
    if(a==="delete"){
      if(!confirm(`Удалить клиента «${c.name}» из VPN MANAGER? WireGuard-профиль останется без изменений.`))return;
      await sqlite("/api/clients/"+id,"DELETE");
      msg("Клиент удалён из VPN MANAGER. WireGuard сохранён.");
      return load();
    }
    if(a==="edit"){
      $("editId").value=c.id;$("editName").value=c.name||"";$("editPhone").value=c.phone||"";$("editStart").value=c.start_date;$("editDaysLeft").value=c.period==="lifetime"?"":(days(c.end_date)===null?"":Math.max(0,days(c.end_date)));if($("editPeriod"))$("editPeriod").value=c.period||"1m";$("editNote").value=c.note||"";$("editDialog").showModal();return;
    }
    if(a==="extend"){
      const scope=b.closest(".actions,.manage-body"),sel=scope.querySelector(`[data-sel="${id}"]`),p=sel.value;
      const base=(days(c.end_date)!==null&&days(c.end_date)>0)?c.end_date:today(),newEnd=p==="lifetime"?null:addPeriod(base,p);
      await sqlite("/api/clients/"+id+"/extend","POST",{period:p});
      if(c.wg_client_id)await api(`/api/wg/client/${c.wg_client_id}/enable`,{method:"POST"});
      await logHistory(id,"extended",`Подписка продлена: ${labels[p]||p}${p==="lifetime"?" ∞":", до "+fmt(newEnd)}; VPN включён`);msg("Подписка продлена, VPN включён");return load();
    }
    if(a==="wg-create"){
      const x=await apiJson("/api/wg/client",{method:"POST",body:JSON.stringify({name:c.name})});
      const wid=x.client?.id;if(!wid)throw new Error("Не получен ID WireGuard");
      await sqlite("/api/clients/"+id,"PATCH",{wg_client_id:wid});await logHistory(id,"wg_created","Создан WireGuard-профиль");msg("WireGuard создан");return load();
    }
    if(a==="wg-link"){
      const sel=document.querySelector(`[data-link="${id}"]`),wid=sel?.value;if(!wid)throw new Error("Выбери WireGuard-клиента");
      await sqlite("/api/clients/"+id,"PATCH",{wg_client_id:wid});await logHistory(id,"wg_linked","Привязан существующий WireGuard-профиль");msg("WireGuard привязан");return load();
    }
    if(a==="wg-unlink"){
      await sqlite("/api/clients/"+id,"PATCH",{wg_client_id:null});await logHistory(id,"wg_unlinked","Привязка WireGuard сброшена (профиль не удалён)");msg("Привязка сброшена");return load();
    }
    if(a==="wg-enable"||a==="wg-disable"){
      await api(`/api/wg/client/${c.wg_client_id}/${a==="wg-enable"?"enable":"disable"}`,{method:"POST"});
      await logHistory(id,a==="wg-enable"?"wg_enabled":"wg_disabled",a==="wg-enable"?"VPN включён":"VPN отключён");
      msg(a==="wg-enable"?"VPN включён":"VPN отключён");return load();
    }
    if(a==="wg-qr"||a==="wg-share-qr"){
      const r=await api(`/api/wg/client/${c.wg_client_id}/qr`),blob=await r.blob();
      if(a==="wg-share-qr")return shareBlob(blob,`${c.name||"client"}-qr.svg`,`WireGuard QR — ${c.name}`);
      const url=URL.createObjectURL(blob);window.open(url,"_blank");setTimeout(()=>URL.revokeObjectURL(url),60000);return;
    }
    if(a==="wg-config"||a==="wg-share-config"){
      const r=await api(`/api/wg/client/${c.wg_client_id}/config`),blob=await r.blob();
      if(a==="wg-share-config")return shareBlob(blob,`${c.name||"wireguard"}.conf`,`WireGuard config — ${c.name}`);
      const url=URL.createObjectURL(blob),ln=document.createElement("a");ln.href=url;ln.download=`${c.name||"wireguard"}.conf`;ln.click();setTimeout(()=>URL.revokeObjectURL(url),30000);return;
    }
  }catch(err){msg(err.message,true)}
});

$("editForm").addEventListener("submit",async e=>{
  e.preventDefault();
  const id=Number($("editId").value),c=clients.find(x=>x.id===id),left=Math.max(0,parseInt($("editDaysLeft").value||"0",10)),end=parse(today());
  end.setDate(end.getDate()+left);
  const editPeriod=$("editPeriod")?.value||c.period;try{await sqlite("/api/clients/"+id,"PATCH",{name:$("editName").value.trim(),phone:$("editPhone").value.trim(),start_date:$("editStart").value,end_date:editPeriod==="lifetime"?null:iso(end),period:editPeriod,note:$("editNote").value.trim()})}catch(e){return msg(e.message,true)}
  try{if(c?.wg_client_id){await api(`/api/wg/client/${c.wg_client_id}/${left<=0?"disable":"enable"}`,{method:"POST"})}}catch{}
  await logHistory(id,"edited",`Данные клиента изменены; ${editPeriod==="lifetime"?"тариф Навсегда ∞":"осталось дней: "+left}`);$("editDialog").close();msg("Изменения сохранены");load();
});

if($("pushBtn"))$("pushBtn").onclick=enablePush;registerPushWorker();
$("closeEdit").onclick=()=>$("editDialog").close();
if($("editPeriod"))$("editPeriod").onchange=()=>{const life=$("editPeriod").value==="lifetime";$("editDaysLeft").disabled=life;if(life)$("editDaysLeft").value=""};
$("closeHistory").onclick=()=>$("historyDialog").close();
$("attentionBtn").onclick=()=>{attentionOnly=!attentionOnly;$("attentionBtn").classList.toggle("attention-active",attentionOnly);$("attentionBtn").firstChild.textContent=attentionOnly?"← Все клиенты ":"⚠ Требуют внимания ";render()};$("search").oninput=render;if($("clientFilter"))$("clientFilter").onchange=e=>{clientFilter=e.target.value;render()};$("refreshBtn").onclick=load;if($("importWgBtn"))$("importWgBtn").onclick=importWireGuardClients;$("startDate").onchange=preview;$("period").onchange=()=>{preview();updateReferralHint()};$("referrer").onchange=updateReferralHint;$("logoutBtn").onclick=async()=>{try{await sqlite("/auth/logout","POST")}catch{}sqliteToken="";session=null;$("app").classList.add("hidden");$("authScreen").classList.remove("hidden")};
$("loginForm").addEventListener("submit",async e=>{e.preventDefault();try{const x=await fetch(STAGING+"/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:$("password").value})});const data=await x.json();if(!x.ok)throw Error(data.error||"Ошибка входа");sqliteToken=data.access_token;session={user:{id:"sqlite-admin"}};$("authScreen").classList.add("hidden");$("app").classList.remove("hidden");$("startDate").value=today();$("period").value="1m";preview();await load();updateReferralHint();$("password").value="";$("authMessage").textContent=""}catch(err){$("authMessage").textContent=err.message}});
})();
