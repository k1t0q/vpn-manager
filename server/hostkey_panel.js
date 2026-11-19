/* CAESAR VPN HOSTKEY: read-only pilot of the legacy VPN MANAGER layout.
 * Never load config.js/sqlite-manager.js: those scripts control old AWG hosts.
 */
(() => {
  'use strict';
  const API = '/manager-test';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[ch]);
  const state = {token: sessionStorage.getItem('caesarPanelPilot') || '', clients: []};
  const status = (message, error = false) => {
    $('status').textContent = message;
    $('status').className = error ? 'm-error' : 'm-muted';
  };

  async function request(path, options = {}) {
    const headers = {'Accept': 'application/json'};
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    if (options.body) headers['Content-Type'] = 'application/json';
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
    sessionStorage.removeItem('caesarPanelPilot');
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
    if (!device.allowed) return {text: 'выключен', cls: 'wait'};
    return device.deployed ? {text: 'работает', cls: 'ok'} : {text: 'ожидает запуска', cls: 'wait'};
  }
  function devices(c) {
    return ['phone','pc'].map(kind => {
      const v = deviceState(c,kind);
      return `<span class="pilot-device-${v.cls}">${kind === 'phone' ? '📱 Телефон' : '💻 ПК'}: ${v.text}</span>`;
    }).join('');
  }
  function actions(c) {
    return `<div class="m-actions client-actions">
       ${c.has_portal ? `<button type="button" data-link="${c.id}">Личный кабинет</button>` : '<span class="m-muted pilot-copy">Ключи пока не выданы</span>'}
       <button type="button" data-history="${c.id}" class="secondary">История</button>
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
    if (filter === 'referrals') clients = clients.filter(c => !!c.referrer_id);
    if (filter === 'expiry') clients = [...clients].sort((a,b) =>
      (daysRemaining(a) ?? Infinity) - (daysRemaining(b) ?? Infinity));
    return clients;
  }
  function render() {
    const clients = searched();
    const stats = state.clients.map(stateOf);
    $('sTotal').textContent = state.clients.length;
    $('sActive').textContent = stats.filter(s => s.name === 'active' || s.name === 'soon').length;
    $('sSoon').textContent = stats.filter(s => s.name === 'soon').length;
    $('sExpired').textContent = stats.filter(s => s.name === 'expired').length;
    $('empty').classList.toggle('hidden', clients.length > 0);
    $('rows').innerHTML = clients.map(c => {
      const state = stateOf(c);
      const days = daysRemaining(c);
      const remaining = days === Infinity ? '∞' : (days === null ? '—' : Math.max(0,days));
      return `<tr><td><strong>${esc(c.name || 'Без имени')}</strong><small>№ ${c.id}</small></td>
        <td>${esc(c.phone || '—')}</td><td>${esc(findReferrer(c.referrer_id))}</td>
        <td><div class="pilot-devices">${devices(c)}</div></td>
        <td>${esc(periodLabel(c.period))}</td><td>${esc(c.start_date || '—')}</td>
        <td>${lifetime(c) ? '∞' : esc(c.end_date || '—')}</td><td>${remaining}</td>
        <td><span class="status ${state.name}">${state.label}</span></td><td>${actions(c)}</td></tr>`;
    }).join('');
    $('cards').innerHTML = clients.map(c => {
      const s = stateOf(c);
      const days = daysRemaining(c);
      return `<article class="card"><div class="card-top"><div><h3 class="card-client-name">${esc(c.name || 'Без имени')}</h3><small class="m-muted">№ ${c.id} · ${esc(c.phone || 'Без телефона')}</small></div><span class="status ${s.name}">${s.label}</span></div>
        <div class="info"><p>${esc(periodLabel(c.period))} · ${lifetime(c) ? 'Бессрочно' : (days === null ? 'Дата не указана' : `осталось ${Math.max(0,days)} дн.`)}</p>
        <p>${esc(c.start_date || '—')} — ${lifetime(c) ? '∞' : esc(c.end_date || '—')}</p>
        <div class="pilot-devices">${devices(c)}</div></div><div class="manage"><div class="manage-body">${actions(c)}</div></div></article>`;
    }).join('');
    status(`Показано ${clients.length} из ${state.clients.length} клиентов · режим только чтение`);
  }

  async function loadClients() {
    status('Загружаю клиентов…');
    try {
      const data = await request('/api/clients');
      state.clients = data.clients;
      showPanel();
      render();
    } catch (e) { status('Ошибка загрузки: ' + e.message, true); }
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

  $('login').addEventListener('submit', async event => {
    event.preventDefault();
    const code = $('code').value.trim();
    $('code').value = '';
    status('Проверяю код…');
    try {
      const data = await request('/auth/login', {method:'POST',body:{code}});
      state.token = data.access_token;
      sessionStorage.setItem('caesarPanelPilot', state.token);
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
    if (link) showLink(link.dataset.link);
    if (history) showHistory(history.dataset.history);
  });
  $('closeConnectLink').addEventListener('click', () => $('connectLinkDialog').close());
  $('closeHistory').addEventListener('click', () => $('historyDialog').close());
  $('copyConnectLink').addEventListener('click', async () => {
    try {await navigator.clipboard.writeText($('connectLinkValue').value); status('Ссылка скопирована');}
    catch { $('connectLinkValue').select(); status('Скопируйте выделенную ссылку'); }
  });
  // The HTML contains legacy forms. They do not have handlers or server write routes.
  // Keep their controls disabled to make the pilot's read-only nature obvious.
  ['create','editForm','referralForm','extendForm'].forEach(id => {
    $(id).addEventListener('submit', e => e.preventDefault());
    $(id).querySelectorAll('input,select,button').forEach(control => {control.disabled = true;});
  });
  ['copyConnectCode','mobileExport','exportCsv'].forEach(id => { $(id).disabled = true; });
  $('connectLinkDialog').querySelector('details').hidden = true;
  if (state.token) loadClients();
})();
