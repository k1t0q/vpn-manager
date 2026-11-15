self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('push',event=>{
 let data={};try{data=event.data?event.data.json():{}}catch{}
 event.waitUntil(self.registration.showNotification(data.title||'VPN MANAGER',{body:data.body||'Проверьте сроки подписок в панели.',tag:data.tag||'vpn-manager',data:{url:self.registration.scope}}));
});
self.addEventListener('notificationclick',event=>{
 event.notification.close();
 const url=self.registration.scope;
 event.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(async list=>{
  for(const client of list){if(client.url.startsWith(url)&&'focus' in client)return client.focus()}
  return self.clients.openWindow(url);
 }));
});
