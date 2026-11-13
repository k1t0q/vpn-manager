(()=>{
 'use strict';
 const download=document.getElementById('download'),share=document.getElementById('share'),status=document.getElementById('status');
 const handle=location.pathname.slice(1),token=location.hash.slice(1);
 let file=null;
 if(/^[A-Za-z0-9_-]{22}$/.test(handle)&&/^[A-Za-z0-9_-]{43}$/.test(token)){
  download.disabled=false;status.textContent='Нажмите «Скачать профиль». Затем импортируйте файл в DefaultVPN.';
 }
 download.addEventListener('click',async()=>{
  download.disabled=true;status.textContent='Получаем профиль…';
  try{
   const response=await fetch('/api/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({handle,token}),cache:'no-store',referrerPolicy:'no-referrer'});
   if(!response.ok){const data=await response.json();throw Error(data.error||'Не удалось получить профиль.')}
   file=new File([await response.blob()],'CAESAR-BOT.conf',{type:'application/octet-stream'});
   const url=URL.createObjectURL(file),link=document.createElement('a');link.href=url;link.download=file.name;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
   share.hidden=!(navigator.share&&navigator.canShare&&navigator.canShare({files:[file]}));
   status.textContent='Файл скачивается. Откройте его в «Файлах» и выберите «Поделиться → DefaultVPN», либо импортируйте из приложения.';
  }catch(error){status.textContent=error.message}finally{download.disabled=false}
 });
 share.addEventListener('click',async()=>{
  if(!file)return;
  try{await navigator.share({files:[file],title:'VPN-профиль'});status.textContent='Завершите импорт и включите VPN в DefaultVPN.'}
  catch(error){if(error.name!=='AbortError')status.textContent='Откройте скачанный файл в приложении «Файлы» и выберите DefaultVPN через меню «Поделиться».'}
 });
})();
