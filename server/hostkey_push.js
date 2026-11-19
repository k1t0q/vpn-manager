/* CAESAR VPN MANAGER: administrator-only Web Push, scoped to /manager/. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const enable = $('enablePush');
  const disable = $('disablePush');
  const test = $('testPush');
  const message = $('pushStatus');
  if (!enable || !disable || !test || !message) return;
  let registration;
  let deviceId = '';
  let busy = false;

  const session = () => sessionStorage.getItem('caesarManagerSession');
  const note = text => { message.textContent = text; };
  const standalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const ios = () => /iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const supported = () => window.isSecureContext && 'serviceWorker' in navigator &&
    'PushManager' in window && 'Notification' in window;
  const buttons = active => {
    enable.hidden = active;
    test.hidden = !active;
    disable.hidden = !active;
  };
  const bytes = value => Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/') +
    '='.repeat((4 - value.length % 4) % 4)), char => char.charCodeAt(0));

  async function api(path, body) {
    const token = session();
    if (!token) throw new Error('Войдите в VPN MANAGER заново.');
    const response = await fetch('/manager/api/push/' + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: {'Authorization': `Bearer ${token}`, 'Accept': 'application/json',
        ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
      ...(body === undefined ? {} : {body: JSON.stringify(body)}),
      credentials: 'same-origin', cache: 'no-store'
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      if (response.status === 401) throw new Error('Сессия закончилась. Войдите в панель снова.');
      if (response.status === 503) throw new Error('Push-сервис ещё не настроен на сервере.');
      throw new Error(data.error || `Ошибка push: HTTP ${response.status}`);
    }
    return data;
  }

  async function worker() {
    if (!registration) registration = await navigator.serviceWorker.register('/manager/sw.js', {scope: '/manager/'});
    return registration;
  }

  async function initialize() {
    if (busy || !session() || $('workspace').classList.contains('m-hide')) return;
    if (ios() && !standalone()) {
      enable.disabled = true;
      note('На iPhone: откройте VPN MANAGER в Safari → Поделиться → На экран «Домой». Затем откройте значок и включите уведомления.');
      return;
    }
    if (!supported()) {
      enable.disabled = true;
      note('Этот браузер не поддерживает push. Для iPhone нужна iOS 16.4+ и приложение на экране «Домой».');
      return;
    }
    if (Notification.permission === 'denied') {
      enable.disabled = true;
      note('Уведомления запрещены в настройках телефона. Разрешите их для CAESAR VPN MANAGER.');
      return;
    }
    enable.disabled = false;
    busy = true;
    try {
      const active = await (await worker()).pushManager.getSubscription();
      if (active && Notification.permission === 'granted') {
        const result = await api('subscribe', active.toJSON());
        deviceId = result.device_id;
        buttons(true);
        note('Push включены на этом устройстве: напоминания за 7, 3 и 1 день и об окончании подписки.');
      } else {
        buttons(false);
        note('Включите push, чтобы получать уведомления, даже когда панель закрыта.');
      }
    } catch (error) {
      note(error.message);
    } finally { busy = false; }
  }

  enable.addEventListener('click', async () => {
    if (busy || !supported()) return;
    // Permission prompt must start synchronously in response to this tap (iOS).
    const permission = Notification.requestPermission();
    busy = true;
    enable.disabled = true;
    try {
      if (await permission !== 'granted') throw new Error('Разрешите уведомления в настройках браузера.');
      const key = await api('key');
      const reg = await worker();
      const current = await reg.pushManager.getSubscription();
      const subscription = current || await reg.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: bytes(key.public_key)
      });
      const result = await api('subscribe', subscription.toJSON());
      deviceId = result.device_id;
      buttons(true);
      note('Push включены. Нажмите «Проверить push», чтобы отправить тест на телефон.');
    } catch (error) {
      note(error.message);
    } finally { busy = false; enable.disabled = false; }
  });

  test.addEventListener('click', async () => {
    if (busy || !deviceId) return;
    busy = true; test.disabled = true;
    try {
      const {ticket} = await api('test', {device_id: deviceId});
      note('Тест отправлен в очередь. Уведомление придёт примерно в течение минуты.');
      for (let attempt = 0; attempt < 20; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 5000));
        const result = await api('test-status?ticket=' + encodeURIComponent(ticket));
        if (result.state === 'sent') { note('Тест отправлен push-сервисом. Проверьте уведомления телефона.'); break; }
        if (result.state === 'failed') throw new Error('Не удалось доставить push. Проверьте настройки и повторите.');
      }
    } catch (error) { note(error.message); }
    finally { busy = false; test.disabled = false; }
  });

  disable.addEventListener('click', async () => {
    if (busy || !deviceId) return;
    busy = true; disable.disabled = true;
    try {
      await api('unsubscribe', {device_id: deviceId});
      const subscription = await (await worker()).pushManager.getSubscription();
      if (subscription) await subscription.unsubscribe();
      deviceId = '';
      buttons(false);
      note('Push на этом устройстве выключены.');
    } catch (error) { note(error.message); }
    finally { busy = false; disable.disabled = false; }
  });

  new MutationObserver(() => initialize()).observe($('workspace'),
    {attributes: true, attributeFilter: ['class']});
  initialize();
})();
