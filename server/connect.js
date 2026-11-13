(()=>{
 'use strict';
 const $=id=>document.getElementById(id),handle=location.pathname.slice(1),token=location.hash.slice(1);
 const valid=/^[A-Za-z0-9_-]{22}$/.test(handle)&&/^[A-Za-z0-9_-]{43}$/.test(token);
 let profile=null,file=null,qrUrl=null;
 const status=text=>{$('status').textContent=text};
 async function api(path,extra={}){
  const response=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({handle,token,...extra}),cache:'no-store',credentials:'same-origin',referrerPolicy:'no-referrer'});
  if(!response.ok){const data=await response.json();if(response.status===401){$('account').hidden=true;$('loginPane').hidden=false;file=null}throw Error(data.error||'Не удалось выполнить запрос.')}
  return response;
 }
 function show(data){
  profile=data;$('loginPane').hidden=true;$('account').hidden=false;$('profileName').textContent=data.label;document.title=data.label+' — личный кабинет';
  $('days').textContent=data.lifetime?'Навсегда ∞':data.days_left+' дн.';
  $('end').textContent=data.lifetime?'Без срока':data.end_date?new Date(data.end_date.includes('T')?data.end_date:data.end_date+'T00:00:00+03:00').toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'}):'Не указана';
  $('progress').value=data.progress;$('progress').setAttribute('aria-valuetext',data.lifetime?'Без ограничения срока':data.days_left+' дней осталось');
  $('subscriptionHint').textContent=data.lifetime?'Бессрочная подписка.':data.days_left===0?'Срок подписки истёк. Обратитесь к администратору для продления.':'Продление отобразится здесь автоматически.';
  $('vpnState').textContent=data.available?'Доступ включён':!data.enabled?'Доступ выключен':'Подписка истекла';
  for(const id of ['download','qr','share'])$(id).disabled=!data.available;
  if(!(navigator.share&&navigator.canShare)){$('share').disabled=true;$('shareHint').textContent='В этом браузере передача файла в приложение недоступна. Используйте скачивание .conf или QR-код.'}
  status('');
 }
 async function busy(button,action){if(button.disabled)return;button.disabled=true;button.setAttribute('aria-busy','true');try{await action()}catch(error){status(error.message)}finally{button.removeAttribute('aria-busy');button.disabled=!profile?.available;if(button.id==='login')button.disabled=false}}
 $('loginForm').addEventListener('submit',e=>{e.preventDefault();busy($('login'),async()=>{status('Проверяем код…');const data=await(await api('login',{code:$('code').value})).json();$('code').value='';show(data.profile)})});
 async function getFile(){const response=await api('download');return new File([await response.blob()],profile.label.replaceAll(' ','-')+'.conf',{type:'application/octet-stream'})}
 $('download').addEventListener('click',()=>busy($('download'),async()=>{status('Получаем профиль…');file=await getFile();const url=URL.createObjectURL(file),a=document.createElement('a');a.href=url;a.download=file.name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);status('Профиль скачан. Импортируйте его в VPN-приложение.')}));
 $('qr').addEventListener('click',()=>busy($('qr'),async()=>{status('Получаем QR-код…');const response=await api('qr');if(qrUrl)URL.revokeObjectURL(qrUrl);qrUrl=URL.createObjectURL(await response.blob());$('qrImage').src=qrUrl;$('qrTitle').textContent=profile.label;$('qrDialog').showModal();status('')}));
 $('closeQr').addEventListener('click',()=>{$('qrDialog').close();$('qrImage').removeAttribute('src');if(qrUrl)URL.revokeObjectURL(qrUrl);qrUrl=null});
 $('share').addEventListener('click',()=>busy($('share'),async()=>{
  if(!file){file=await getFile();status('Профиль готов. Нажмите «Передать в приложение» ещё раз, чтобы выбрать приложение.');return}
  if(!navigator.canShare?.({files:[file]})){status('Браузер не поддерживает передачу этого файла. Скачайте .conf и импортируйте его из приложения.');return}
  try{await navigator.share({files:[file],title:profile.label});status('Завершите импорт и включите VPN в выбранном приложении.')}catch(error){if(error.name!=='AbortError')throw error}
 }));
 $('logout').addEventListener('click',async()=>{await api('logout');file=null;profile=null;$('account').hidden=true;$('loginPane').hidden=false;document.title='CAESAR VPN — личный кабинет';status('Вы вышли из кабинета.')});
 if(!valid){$('login').disabled=true;status('Откройте полную персональную ссылку, полученную от администратора.')}else api('profile').then(r=>r.json()).then(data=>show(data.profile)).catch(()=>status('Введите код доступа для входа.'));
 setInterval(()=>{if(profile&&document.visibilityState==='visible')api('profile').then(r=>r.json()).then(data=>show(data.profile)).catch(error=>status(error.message))},60000);
})();
