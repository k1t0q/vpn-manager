(()=>{
 'use strict';
 const byId=id=>document.getElementById(id),enable=byId('enablePush'),test=byId('testPush'),disable=byId('disablePush'),status=byId('pushStatus');
 if(!enable)return;
 let registration=null,deviceId='',busy=false;
 const session=()=>sessionStorage.getItem('vpn_manager_sqlite_session_v1');
 const show=text=>{status.textContent=text};
 const standalone=()=>window.matchMedia('(display-mode: standalone)').matches||navigator.standalone===true;
 const ios=/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
 const supported=()=>('serviceWorker' in navigator)&&('PushManager' in window)&&('Notification' in window);
 async function api(path,body){
  const response=await fetch(window.VPN_MANAGER_CONFIG.API_BASE+'/api/push/'+path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+session(),'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const data=await response.json();
  if(!response.ok||!data.ok)throw Error(response.status===401?'Войдите в панель снова':response.status===429?'Подождите 30 секунд и повторите':'Сервис уведомлений недоступен. Повторите позже.');
  return data;
 }
 const base64Bytes=value=>Uint8Array.from(atob(value.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-value.length%4)%4)),c=>c.charCodeAt(0));
 function buttons(active){enable.hidden=active;test.hidden=!active;disable.hidden=!active}
 async function worker(){if(!registration)registration=await navigator.serviceWorker.register('./sw.js?v=push1',{scope:'./'});return navigator.serviceWorker.ready}
 async function initialize(){
  if(!session()||busy)return;
  if(ios&&!standalone()){enable.disabled=true;show('На iPhone: Safari → Поделиться → На экран «Домой». Откройте панель через новый значок и включите уведомления.');return}
  if(!supported()){enable.disabled=true;show('Этот браузер не поддерживает push. На iPhone нужна iOS 16.4 или новее и значок панели на экране «Домой».');return}
  if(Notification.permission==='denied'){enable.disabled=true;show('Уведомления запрещены. Разрешите их для VPN MANAGER в настройках телефона.');return}
  busy=true;
  try{
   const reg=await worker(),sub=await reg.pushManager.getSubscription();
   if(sub&&Notification.permission==='granted'){
    const data=await api('subscribe',sub.toJSON());deviceId=data.device_id;buttons(true);show('Push включены на этом устройстве: за 7, 3 и 1 день, а также после окончания подписки.');
   }
  }catch(error){show(error.message)}finally{busy=false}
 }
 enable.addEventListener('click',async()=>{
  if(busy)return;
  // Ask permission directly from the user's tap, before network awaits (iOS).
  const permission=Notification.requestPermission();
  busy=true;enable.disabled=true;
  try{
   if(await permission!=='granted')throw Error('Разрешите уведомления, чтобы получать напоминания при закрытой панели.');
   const key=await api('key'),reg=await worker();
   const sub=await reg.pushManager.getSubscription()||await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:base64Bytes(key.public_key)});
   const result=await api('subscribe',sub.toJSON());deviceId=result.device_id;buttons(true);
   show('Push включены. Нажмите «Проверить push» и убедитесь, что уведомление пришло.');
  }catch(error){show(error.message)}finally{busy=false;enable.disabled=false}
 });
 test.addEventListener('click',async()=>{
  if(busy||!deviceId)return;busy=true;test.disabled=true;
  try{await api('test',{device_id:deviceId});show('Тест принят push-сервисом. Проверьте уведомления телефона, в том числе при свёрнутой панели.');}
  catch(error){show(error.message)}finally{busy=false;test.disabled=false}
 });
 disable.addEventListener('click',async()=>{
  if(busy)return;busy=true;disable.disabled=true;
  try{await api('unsubscribe',{device_id:deviceId});const sub=await (await worker()).pushManager.getSubscription();if(sub)await sub.unsubscribe();deviceId='';buttons(false);show('Push на этом устройстве выключены.');}
  catch(error){show(error.message)}finally{busy=false;disable.disabled=false}
 });
 new MutationObserver(()=>{if(!byId('workspace').classList.contains('m-hide'))initialize()}).observe(byId('workspace'),{attributes:true,attributeFilter:['class']});
 initialize();
})();
