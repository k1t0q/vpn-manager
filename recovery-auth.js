(()=>{
 let snapshot=null,session=null,listener=()=>{};
 const $=id=>document.getElementById(id);
 const form=$('loginForm');
 $('email').type='hidden';$('email').required=false;$('email').value='recovery@local.invalid';$('email').parentElement.style.display='none';
 $('password').required=false;$('password').autocomplete='off';$('password').placeholder=localStorage.getItem('vpn_manager_api_token')?'Сохранённый API-токен будет использован автоматически':'API-токен VPN Manager';
 $('password').parentElement.firstChild.textContent='API-токен VPN Manager';
 const label=document.createElement('label');label.textContent='Файл с клиентами';const input=document.createElement('input');input.type='file';input.accept='.json,application/json';input.required=true;input.id='recoveryFile';label.append(input);form.prepend(label);
 $('authScreen').querySelector('p').textContent='Временный вход без Supabase';
 const banner=document.createElement('div');banner.className='warn';banner.id='recoveryNotice';banner.textContent='Временный режим: сроки, тарифы и данные клиентов доступны для просмотра. Включение и отключение VPN, QR и конфиги работают через сервер.';$('app').prepend(banner);
 const allowed=new Set(['wg-enable','wg-disable','wg-qr','wg-share-qr','wg-config','wg-share-config']);
 document.addEventListener('click',e=>{const b=e.target.closest('[data-a],#importWgBtn');if(b&&(!b.dataset.a||!allowed.has(b.dataset.a))){e.preventDefault();e.stopImmediatePropagation();$('message').textContent='Изменение данных и история недоступны во временном режиме';}},true);
 document.addEventListener('submit',e=>{if(['addForm','editForm'].includes(e.target.id)){e.preventDefault();e.stopImmediatePropagation();}},true);
 function lockControls(){for(const b of document.querySelectorAll('[data-a]'))if(!allowed.has(b.dataset.a)){b.disabled=true;b.title='Недоступно во временном режиме'}for(const b of document.querySelectorAll('[data-sel],[data-link],#importWgBtn'))b.disabled=true;}
 new MutationObserver(lockControls).observe($('app'),{childList:true,subtree:true});
 $('addForm').closest('aside').hidden=true;document.querySelector('.layout').style.gridTemplateColumns='1fr';document.querySelector('.push-bar').hidden=true;
 window.supabase={createClient(){return {
  from:table=>VPNRecovery.createQuery(table,()=>snapshot?.clients||[]),
  auth:{onAuthStateChange(fn){listener=fn},async getSession(){return {data:{session}}},async signOut(){snapshot=null;session=null;await listener('SIGNED_OUT',null);return {error:null}},async signInWithPassword({password}){
   try{
    const file=input.files[0];if(!file||file.size>10*1024*1024)throw Error('Выбери файл с сохранёнными клиентами');
    const parsed=VPNRecovery.parseSnapshot(JSON.parse(await file.text()));
    const token=(password||localStorage.getItem('vpn_manager_api_token')||'').trim();if(!token)throw Error('Введи API-токен VPN Manager');
    const r=await fetch('https://80-90-179-65.sslip.io/api/wg/clients',{headers:{'X-VPN-Token':token},signal:AbortSignal.timeout(15000)});
    if(!r.ok)throw Error(r.status===401?'Неверный API-токен':'Сервер VPN недоступен: '+r.status);
    const health=await r.json();if(!health.ok||!Array.isArray(health.clients))throw Error('Сервер не подтвердил доступ');
    snapshot=parsed;session={user:{id:parsed.clients[0].user_id}};localStorage.setItem('vpn_manager_api_token',token);$('password').value='';
    banner.textContent='Список клиентов на '+new Date(parsed.created_at).toLocaleString('ru-RU')+'. Данные и сроки — только просмотр. VPN, QR и конфиги — через работающий сервер.';
    await listener('SIGNED_IN',session);lockControls();return {data:{session},error:null};
   }catch(e){return {data:{session:null},error:{message:e.message}}}
  }}
 }}};
})();
