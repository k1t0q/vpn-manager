/* CAESAR VPN HOSTKEY: guarded billing and VLESS device management.
 * Never load config.js/sqlite-manager.js: those scripts control old AWG hosts.
 */
(() => {
  'use strict';
  const API = '/manager';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[ch]);
  const state = {token: sessionStorage.getItem('caesarManagerSession') || '', clients: [], pilotEnabled: false,
    fullAccess: false, fastSync: false, writePending: false};
  const finishBoot = () => document.documentElement.classList.add('caesar-panel-ready');
  const status = (message, error = false) => {
    $('status').textContent = message;
    $('status').className = error ? 'm-error' : 'm-muted';
  };

  async function request(path, options = {}) {
    const headers = {'Accept': 'application/json'};
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    if (options.body) headers['Content-Type'] = 'application/json';
    if (options.requestId) headers['Idempotency-Key'] = options.requestId;
    const response = await fetch(`${API}${path}`, {
      method: options.method || 'GET',
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
      cache: 'no-store',
      credentials: 'same-origin'
    });
    let result;
    try { result = await response.json(); } catch { throw new Error(`HTTP ${response.status}`); }
    if (!response.ok) {
      if (response.status === 401 && path !== '/auth/login') signOut(false);
      if (response.status === 401) {
        throw new Error(path === '/auth/login' ? 'Неверный код администратора' : 'Нужно заново войти');
      }
      throw new Error(result.error || `HTTP ${response.status}`);
    }
    return result;
  }

  function showPanel() {
    $('loginPane').classList.add('m-hide');
    $('workspace').classList.remove('m-hide');
    $('mobileHeaderActions').hidden = false;
    $('desktopHeaderActions').hidden = false;
  }
  function signOut(sendRequest = true) {
    if (sendRequest && state.token) request('/auth/logout', {method:'POST'}).catch(() => {});
    state.token = '';
    state.clients = [];
    sessionStorage.removeItem('caesarManagerSession');
    $('loginPane').classList.remove('m-hide');
    $('workspace').classList.add('m-hide');
    $('mobileHeaderActions').hidden = true;
    $('desktopHeaderActions').hidden = true;
    status('');
  }

  const moscowToday = () => new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit'
  }).format(new Date());
  const msPerDay = 86400000;
  const dateMs = input => Date.parse(String(input).slice(0, 10) + 'T00:00:00Z');
  const periodLabel = period => ({
    lifetime: 'Навсегда ∞', trial: 'Пробный', '1m': '1 месяц', '6m': '6 месяцев',
    '12m': '12 месяцев'
  })[period] || String(period || '—');
  function lifetime(c) { return c.period === 'lifetime'; }
  function daysRemaining(c) {
    if (lifetime(c)) return Infinity;
    if (!c.end_date || !/^\d{4}-\d{2}-\d{2}/.test(c.end_date)) return null;
    const value = dateMs(c.end_date);
    return Number.isFinite(value) ? Math.round((value - dateMs(moscowToday())) / msPerDay) : null;
  }
  function stateOf(c) {
    const days = daysRemaining(c);
    if (days === null) return {label: 'Уточнить', name: 'unknown'};
    if (days < 0) return {label: 'Истекла', name: 'expired'};
    if (days <= 7) return {label: 'Скоро', name: 'soon'};
    return {label: 'Активна', name: 'active'};
  }
  function deviceState(c, kind) {
    const device = c.devices && c.devices[kind];
    if (!device) return {text: 'не выдан', cls: 'none'};
    if (!c.subscription_active) return device.deployed
      ? {text: 'отключается (срок истёк)', cls: 'wait'} : {text: 'отключён (срок истёк)', cls: 'none'};
    if (!device.allowed) return device.deployed
      ? {text: 'отключается', cls: 'wait'} : {text: 'отключён', cls: 'none'};
    return device.deployed ? {text: 'ключ активен', cls: 'ok'} : {text: 'включается', cls: 'wait'};
  }
  function vpnAccess(c) {
    if (c.archived) return {text: 'Архив', cls: 'off'};
    if (c.vpn_state === 'pending') {
      const text = c.vpn_target_keys < c.vpn_deployed_keys ? 'Отключается…' :
        c.vpn_target_keys > c.vpn_deployed_keys ? 'Включается…' : 'Синхронизация…';
      return {text: `⏳ ${text}`, cls: 'pending'};
    }
    if (c.vpn_state === 'on') return {text: '🟢 Включён · 2/2', cls: 'on'};
    if (c.vpn_state === 'partial') return {text: '🟡 Включён · 1/2', cls: 'partial'};
    if (c.vpn_state === 'expired') return {text: '🔴 Отключён · срок истёк', cls: 'expired'};
    if (c.vpn_state === 'off') return {text: '🔴 Заблокирован', cls: 'off'};
    return {text: '⚪ Ключи не выданы', cls: 'none'};
  }
  function vpnBadge(c) {
    const a = vpnAccess(c);
    return `<span class="vpn-access ${a.cls}" title="Доступность ключей в Xray, не онлайн-статус устройства">${a.text}</span>`;
  }
  function devices(c, compact = false) {
    return ['phone','pc'].map(kind => {
      const v = deviceState(c,kind);
      const description = compact ? v.text.replace('отключён (срок истёк)', 'срок истёк')
        .replace('отключается (срок истёк)', 'отключается') : v.text;
      return `<span class="pilot-device-${v.cls}">${kind === 'phone' ? '📱 Телефон' : '💻 ПК'}: ${description}</span>`;
    }).join('');
  }
  function actions(c) {
    const locked = c.can_manage ? '' : ' disabled title="Управление недоступно"';
    if (c.archived) return `<div class="m-actions client-actions">
      <button type="button" data-action="restore" data-id="${c.id}"${locked}>Восстановить</button>
      <button type="button" data-history="${c.id}" class="secondary">История</button></div>`;
    const phoneEnabled = !!c.devices?.phone?.allowed;
    const pcEnabled = !!c.devices?.pc?.allowed;
    const enabled = Object.values(c.devices || {}).some(d => d.allowed);
    const accessLocked = !c.has_portal || (!enabled && !c.subscription_active);
    return `<div class="m-actions client-actions">
      <button type="button" data-action="edit" data-id="${c.id}"${locked}>Редактировать</button>
      <button type="button" data-action="extend" data-id="${c.id}"${locked}>Продлить</button>
      ${c.has_portal ? `<button type="button" data-link="${c.id}">Личный кабинет</button>` :
        `<button type="button" data-action="provision" data-id="${c.id}"${locked}>Выдать ключи</button>`}
      <button type="button" data-action="access" data-id="${c.id}" data-enabled="${!enabled}"
        class="${enabled ? 'danger' : 'secondary'}"${locked}${accessLocked ? ' disabled title="Сначала продлите подписку или выдайте ключи"' : ''}>${enabled ? '⛔ Заблокировать VPN' : '✅ Включить VPN'}</button>
      <button type="button" data-action="toggle" data-kind="phone" data-id="${c.id}" data-enabled="${!phoneEnabled}"${locked}${(!c.has_portal || (!phoneEnabled && !c.subscription_active)) ? ' disabled' : ''}>📱 ${phoneEnabled ? 'Отключить' : 'Включить'}</button>
      <button type="button" data-action="toggle" data-kind="pc" data-id="${c.id}" data-enabled="${!pcEnabled}"${locked}${(!c.has_portal || (!pcEnabled && !c.subscription_active)) ? ' disabled' : ''}>💻 ${pcEnabled ? 'Отключить' : 'Включить'}</button>
      <button type="button" data-history="${c.id}" class="secondary">История</button>
      <button type="button" data-action="archive" data-id="${c.id}" class="danger"${locked}>Удалить</button>
    </div>`;
  }
  function findReferrer(id) {
    const found = state.clients.find(c => c.id === id);
    return found ? found.name : (id ? `№${id}` : '—');
  }
  function searched() {
    const query = $('search').value.trim().toLocaleLowerCase('ru');
    const filter = $('filter').value;
    let clients = state.clients.filter(c =>
      !query || [c.name, c.phone, c.id, c.note].some(v => String(v ?? '').toLocaleLowerCase('ru').includes(query)));
    if (filter !== 'archive') clients = clients.filter(c => !c.archived);
    else clients = clients.filter(c => c.archived);
    if (filter === 'referrals') clients = clients.filter(c => !!c.referrer_id);
    if (filter === 'expiry') clients = [...clients].sort((a,b) =>
      (daysRemaining(a) ?? Infinity) - (daysRemaining(b) ?? Infinity));
    if (filter === 'vpn_on') clients = clients.filter(c => ['on','partial'].includes(c.vpn_state));
    if (filter === 'vpn_off') clients = clients.filter(c => ['off','expired','none'].includes(c.vpn_state));
    if (filter === 'vpn_pending') clients = clients.filter(c => c.vpn_state === 'pending');
    return clients;
  }
  function render() {
    const clients = searched();
    const activeClients = state.clients.filter(c => !c.archived);
    const stats = activeClients.map(stateOf);
    $('sTotal').textContent = activeClients.length;
    $('sActive').textContent = stats.filter(s => s.name === 'active' || s.name === 'soon').length;
    $('sSoon').textContent = stats.filter(s => s.name === 'soon').length;
    $('sExpired').textContent = stats.filter(s => s.name === 'expired').length;
    $('empty').classList.toggle('hidden', clients.length > 0);
    $('rows').innerHTML = clients.map(c => {
      const days = daysRemaining(c);
      const remaining = days === Infinity ? '∞' : (days === null ? '—' : Math.max(0,days));
      return `<tr><td><strong>${esc(c.name || 'Без имени')}</strong><small>№ ${c.id}</small></td>
        <td>${esc(c.phone || '—')}</td><td>${esc(findReferrer(c.referrer_id))}</td>
        <td><div class="pilot-devices">${devices(c)}</div></td>
        <td>${esc(periodLabel(c.period))}</td><td>${esc(c.start_date || '—')}</td>
        <td>${lifetime(c) ? '∞' : esc(c.end_date || '—')}</td>
        <td><strong class="remaining-days">${remaining}</strong></td>
        <td>${vpnBadge(c)}</td><td>${actions(c)}</td></tr>`;
    }).join('');
    $('cards').innerHTML = clients.map(c => {
      const s = c.archived ? {label:'Архив',name:'expired'} : stateOf(c);
      const days = daysRemaining(c);
      return `<article class="card"><div class="card-top"><div><h3 class="card-client-name">${esc(c.name || 'Без имени')}</h3><small class="m-muted">№ ${c.id} · ${esc(c.phone || 'Без телефона')}</small></div><span class="status ${s.name}">${s.label}</span></div>
        <div class="info"><p>${esc(periodLabel(c.period))} · ${lifetime(c) ? 'Бессрочно' : (days === null ? 'Дата не указана' : `осталось ${Math.max(0,days)} дн.`)}</p>
        <p>${esc(c.start_date || '—')} — ${lifetime(c) ? '∞' : esc(c.end_date || '—')}</p>
        <p>${vpnBadge(c)}</p><div class="pilot-devices pilot-devices-mobile">${devices(c, true)}</div></div>
        <div class="manage"><div class="manage-body">${actions(c)}</div></div></article>`;
    }).join('');
    status(`Показано ${clients.length} клиентов · ${state.fullAccess ? 'полное управление' : 'режим просмотра'}`);
  }

  async function loadClients() {
    status('Загружаю клиентов…');
    try {
      const data = await request('/api/clients');
      state.clients = data.clients;
      state.pilotEnabled = !!data.pilot_enabled;
      state.fullAccess = !!data.full_access;
      state.fastSync = !!data.fast_sync;
      showPanel();
      setCreateMode();
      populateReferrers();
      render();
      const note = document.querySelector('.pilot-note');
      if (note && state.fullAccess) note.textContent = '✅ Управление CAESAR VPN активно. '
        + 'Создание, продление и отключение клиентов выполняются через эту панель. '
        + 'VPN-статус показывает доступность ключей, а не факт подключения устройства. '
        + (state.fastSync ? 'Изменения передаются Xray автоматически сразу; ' : 'Изменения применяются Xray в течение пяти минут; ')
        + 'перед каждым изменением сохраняется резервная копия.';
    } catch (e) {
      // A temporary API outage should leave the refresh/logout controls available.
      if (state.token) showPanel();
      status('Ошибка загрузки: ' + e.message, true);
    }
  }
  async function showLink(id) {
    const client = state.clients.find(c => c.id === Number(id));
    if (!client) return;
    try {
      const data = await request(`/api/clients/${id}/link`);
      $('connectClientLabel').textContent = client.name || `Клиент №${id}`;
      $('connectLinkValue').value = data.url;
      $('connectCodeValue').value = 'Код не требуется';
      $('connectCodeValue').readOnly = true;
      $('connectLinkDialog').showModal();
    } catch (e) {status('Не удалось получить ссылку: ' + e.message, true);}
  }
  async function showHistory(id) {
    try {
      const data = await request(`/api/clients/${id}/history`);
      $('history').replaceChildren();
      const entries = data.history;
      if (!entries.length) $('history').textContent = 'История пока пуста';
      for (const event of entries) {
        const entry = document.createElement('p');
        entry.className = 'm-muted';
        entry.textContent = `${event.created_at || ''} · ${event.event_type || ''} · ${event.description || ''}`;
        $('history').append(entry);
      }
      $('historyDialog').showModal();
    } catch (e) {status('Ошибка истории: ' + e.message, true);}
  }

  function setCreateMode() {
    const form = $('create');
    form.querySelectorAll('input,select,button').forEach(el => {el.disabled = !state.fullAccess;});
    const trial = form.querySelector('[value="trial"]');
    trial.disabled = true; // Exact 24h trial support will be released separately.
    const periodSelect = form.elements.namedItem('period');
    if (periodSelect.value === 'trial') periodSelect.value = '1m';
    const section = form.closest('.create-client-section');
    const heading = section.querySelector('h2');
    heading.textContent = 'Добавить клиента';
    form.querySelector('[name="name"]').placeholder = 'Имя клиента';
    if (!section.querySelector('.pilot-create-hint')) {
      const tip = document.createElement('p');
      tip.className = 'm-muted pilot-create-hint';
      section.append(tip);
    }
    section.querySelector('.pilot-create-hint').textContent = state.fullAccess
      ? 'При создании автоматически выдаются два индивидуальных ключа: телефон и ПК.'
      : 'Управление пока недоступно. Ожидается завершение настройки сервера.';
  }

  function populateReferrers() {
    for (const id of ['referrer', 'editReferrer']) {
      const select = $(id);
      const current = select.value;
      select.replaceChildren(new Option('Никто', ''));
      state.clients.filter(c => !c.archived).forEach(c => {
        select.append(new Option(`${c.name || 'Без имени'} · №${c.id}`, String(c.id)));
      });
      select.value = current;
    }
  }

  let statusRefreshPending = false;
  async function refreshPendingStatuses() {
    if (!state.token || state.writePending || statusRefreshPending || document.hidden ||
        !state.clients.some(c => c.vpn_state === 'pending')) return;
    statusRefreshPending = true;
    try {
      const data = await request('/api/clients');
      if (!state.token) return;
      state.clients = data.clients;
      render();
    } catch { /* Manual refresh remains available if the API is temporarily unavailable. */ }
    finally { statusRefreshPending = false; }
  }

  // The Xray fallback timer runs every five minutes. Keep checking until it
  // confirms the requested state, even after the initial fast-sync window.
  setInterval(refreshPendingStatuses, 10000);

  async function mutate(action, id, payload, label) {
    if (state.writePending) {
      status('Подожди завершения предыдущего действия');
      return false;
    }
    state.writePending = true;
    try {
      // Retries of one request must use the same UUID. One click = one operation.
      const requestId = crypto.randomUUID();
      const path = action === 'create' ? '/api/clients' : `/api/clients/${id}/${action}`;
      const result = await request(path, {method: 'POST', body: payload, requestId});
      await loadClients();
      status(`${label}. ${result.fast_sync_requested ? 'Сервер получил запрос на быструю синхронизацию.' : 'Синхронизация VPN — до пяти минут.'}`);
      if (result.sync_pending) setTimeout(refreshPendingStatuses, 2500);
      return true;
    } catch (e) {
      status(`Ошибка: ${e.message}`, true);
      return false;
    } finally {
      state.writePending = false;
    }
  }

  function editClient(id) {
    const client = state.clients.find(c => c.id === Number(id));
    if (!client || !client.can_manage || client.archived) return;
    const form = $('editForm');
    form.dataset.clientId = String(id);
    const fill = (key,value) => { form.elements.namedItem(key).value = value ?? ''; };
    fill('id', id);
    fill('name', client.name);
    fill('phone', client.phone);
    fill('start_date', String(client.start_date || '').slice(0,10));
    fill('period', client.period);
    fill('end_date', String(client.end_date || '').slice(0,10));
    fill('referrer_id', client.referrer_id || '');
    form.querySelectorAll('input,select,button').forEach(el => {el.disabled = false;});
    $('editDays').disabled = true; // Date arithmetic is server-owned, via Extend.
    $('editDaysHint').textContent = 'Для добавления дней используйте «Продлить». Даты можно исправлять вручную.';
    $('editDialog').showModal();
  }

  function extendClient(id) {
    const client = state.clients.find(c => c.id === Number(id));
    if (!client || !client.can_manage || client.archived) return;
    $('extendForm').dataset.clientId = String(id);
    $('extendClientName').textContent = `${client.name} · №${id}`;
    $('extendPeriod').value = client.period === 'lifetime' ? 'lifetime' : '1m';
    $('extendPeriod').querySelector('[value="trial"]').disabled = true;
    $('extendDialog').showModal();
  }

  async function runAction(button) {
    if (button.disabled) return;
    const id = Number(button.dataset.id);
    const action = button.dataset.action;
    const client = state.clients.find(c => c.id === id);
    if (!client || !client.can_manage) return;
    if (action === 'edit') return editClient(id);
    if (action === 'extend') return extendClient(id);
    if (action === 'provision') {
      if (!confirm(`Создать два ключа для «${client.name}»? Они активируются после проверки Xray.`)) return;
      return mutate('provision', id, {}, 'Ключи зарегистрированы');
    }
    if (action === 'toggle') {
      const enabled = button.dataset.enabled === 'true';
      const kind = button.dataset.kind;
      if (!confirm(`${enabled ? 'Включить' : 'Отключить'} ${kind === 'phone' ? 'телефон' : 'ПК'} клиента «${client.name}»?`)) return;
      return mutate('toggle', id, {kind,enabled}, `Доступ ${enabled ? 'разрешён' : 'отключён'}`);
    }
    if (action === 'access') {
      const enabled = button.dataset.enabled === 'true';
      const explanation = enabled ? 'Восстановить доступ обоим устройствам' :
        'Заблокировать оба VPN-ключа, сохранив подписку и персональную ссылку';
      if (!confirm(`${explanation} для «${client.name}»?`)) return;
      return mutate('access', id, {enabled}, enabled ? 'VPN-доступ разрешён' : 'VPN-доступ заблокирован');
    }
    if (action === 'archive') {
      const typed = prompt(`Архивирование отзовёт ссылки и оба VLESS-ключа клиента.\nДля подтверждения введите его имя точно:\n${client.name}`);
      if (typed !== client.name) return;
      return mutate('archive', id, {confirm_name: typed}, 'Клиент архивирован, ключи отозваны');
    }
    if (action === 'restore') {
      if (!confirm(`Восстановить «${client.name}»? Устройства останутся выключены до ручного включения.`)) return;
      return mutate('restore', id, {}, 'Клиент восстановлен');
    }
  }

  $('login').addEventListener('submit', async event => {
    event.preventDefault();
    const code = $('code').value.trim();
    $('code').value = '';
    status('Проверяю код…');
    try {
      const data = await request('/auth/login', {method:'POST',body:{code}});
      state.token = data.access_token;
      sessionStorage.setItem('caesarManagerSession', state.token);
      await loadClients();
    } catch (e) {status('Ошибка входа: ' + e.message, true);}
  });
  $('toggleCode').addEventListener('click', () => {
    $('code').type = $('code').type === 'password' ? 'text' : 'password';
  });
  ['reload','mobileReload'].forEach(id => $(id).addEventListener('click', loadClients));
  ['logout','mobileLogout'].forEach(id => $(id).addEventListener('click', () => signOut()));
  $('search').addEventListener('input', render);
  $('filter').addEventListener('change', render);
  document.addEventListener('click', e => {
    const link = e.target.closest('[data-link]');
    const history = e.target.closest('[data-history]');
    const action = e.target.closest('[data-action]');
    if (link) showLink(link.dataset.link);
    if (history) showHistory(history.dataset.history);
    if (action && !action.closest('#connectLinkDialog')) runAction(action);
  });
  $('closeConnectLink').addEventListener('click', () => $('connectLinkDialog').close());
  $('closeHistory').addEventListener('click', () => $('historyDialog').close());
  $('copyConnectLink').addEventListener('click', async () => {
    try {await navigator.clipboard.writeText($('connectLinkValue').value); status('Ссылка скопирована');}
    catch { $('connectLinkValue').select(); status('Скопируйте выделенную ссылку'); }
  });
  $('create').addEventListener('submit', async e => {
    e.preventDefault();
    if (!state.fullAccess) return;
    const form = e.currentTarget;
    const data = new FormData(form);
    const payload = {
      name: String(data.get('name') || '').trim(),
      phone: String(data.get('phone') || ''),
      start_date: String(data.get('start_date') || ''),
      period: String(data.get('period') || ''),
      referrer_id: data.get('referrer_id') ? Number(data.get('referrer_id')) : null
    };
    if (await mutate('create', null, payload, 'Тестовый клиент и два устройства созданы')) form.reset();
  });
  $('editForm').addEventListener('submit', async e => {
    e.preventDefault();
    const form = e.currentTarget;
    const id = Number(form.dataset.clientId);
    const old = state.clients.find(c => c.id === id);
    if (!old || !old.can_manage) return;
    const value = name => form.elements.namedItem(name).value;
    const inputs = {name: value('name').trim(), phone: value('phone'),
      start_date: value('start_date'), period: value('period'),
      end_date: value('end_date'), referrer_id: value('referrer_id') ? Number(value('referrer_id')) : null};
    const changed = {};
    for (const [field,newValue] of Object.entries(inputs)) {
      const original = field === 'end_date' ? String(old.end_date || '').slice(0,10) : old[field];
      if ((original ?? (field === 'referrer_id' ? null : '')) !== newValue) changed[field] = newValue;
    }
    if (!Object.keys(changed).length) {form.closest('dialog').close();return;}
    if (await mutate('edit', id, changed, 'Данные клиента сохранены')) form.closest('dialog').close();
  });
  $('extendForm').addEventListener('submit', async e => {
    e.preventDefault();
    const form = e.currentTarget;
    const id = Number(form.dataset.clientId);
    if (await mutate('extend', id, {period: $('extendPeriod').value}, 'Подписка продлена')) form.closest('dialog').close();
  });
  $('closeEdit').addEventListener('click', () => $('editDialog').close());
  $('closeExtend').addEventListener('click', () => $('extendDialog').close());
  $('referralForm').addEventListener('submit', e => e.preventDefault());
  $('confirmReferral').disabled = true;
  $('copyConnectCode').disabled = true;
  const exportClients = () => {
    if (!state.clients.length) return;
    const columns = ['id','name','phone','period','start_date','end_date','referrer_id'];
    const cell = value => {
      let text = String(value ?? '');
      if (/^[=+@\-\t\r]/.test(text)) text = "'" + text;
      return `"${text.replace(/"/g,'""')}"`;
    };
    const csv = '\ufeff' + [columns.join(';'), ...state.clients.filter(c => !c.archived)
      .map(c => columns.map(key => cell(c[key])).join(';'))].join('\r\n');
    const url = URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'CAESAR_VPN_clients.csv';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  };
  ['mobileExport','exportCsv'].forEach(id => $(id).addEventListener('click', exportClients));
  $('filter').append(new Option('VPN включён', 'vpn_on'));
  $('filter').append(new Option('VPN отключён', 'vpn_off'));
  $('filter').append(new Option('VPN — ожидает применения', 'vpn_pending'));
  $('filter').append(new Option('Архив', 'archive'));
  $('connectLinkDialog').querySelector('details').hidden = true;
  if (state.token) loadClients().finally(finishBoot);
  else finishBoot();
})();
