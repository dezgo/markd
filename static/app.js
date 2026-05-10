'use strict';

const VERSION = 'v25';

let todos = [];
let filter = 'active';

const list = document.getElementById('todo-list');
const emptyMsg = document.getElementById('empty-msg');
const addForm = document.getElementById('add-form');
const newTitle = document.getElementById('new-title');
const newDue = document.getElementById('new-due');
const newDueTime = document.getElementById('new-due-time');
const newNotes = document.getElementById('new-notes');
const newRecurInterval = document.getElementById('new-recur-interval');
const newRecurUnit = document.getElementById('new-recur-unit');
const newDayToggles = document.getElementById('new-day-toggles');

const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function formatRecurrence(todo) {
  if (!todo.recurrence_interval || !todo.recurrence_unit) return null;
  if (todo.recurrence_unit === 'weeks' && todo.recurrence_days) {
    const labels = todo.recurrence_days.split(',').map(d => DAY_LABELS[+d]);
    return `↻ ${labels.join(', ')}`;
  }
  return `↻ every ${todo.recurrence_interval} ${todo.recurrence_unit}`;
}

// Day-toggle helpers — work on any container with .day-toggle children
function getSelectedDays(container) {
  return [...container.querySelectorAll('.day-toggle.is-on')]
    .map(b => +b.dataset.day).sort((a, b) => a - b);
}
function setSelectedDays(container, daysCsv) {
  const set = new Set((daysCsv || '').split(',').filter(d => d !== '').map(Number));
  container.querySelectorAll('.day-toggle').forEach(b => {
    b.classList.toggle('is-on', set.has(+b.dataset.day));
  });
}
function wireDayToggles(container) {
  container.querySelectorAll('.day-toggle').forEach(b => {
    b.addEventListener('click', () => b.classList.toggle('is-on'));
  });
}
wireDayToggles(newDayToggles);

// Show/hide day toggles when unit changes
newRecurUnit.addEventListener('change', () => {
  newDayToggles.hidden = newRecurUnit.value !== 'weeks';
});
newDayToggles.hidden = newRecurUnit.value !== 'weeks';

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (res.status === 204) return null;
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.error || res.statusText);
  return body;
}

async function load() {
  const fresh = await api('/todos');
  // Skip re-render if nothing actually changed (avoids flicker during polling)
  if (JSON.stringify(fresh) === JSON.stringify(todos)) return;
  todos = fresh;
  render();
}

function isDueByEndOfToday(todo) {
  if (!todo.due_date) return false;
  const endOfToday = new Date();
  endOfToday.setHours(23, 59, 59, 999);
  const [y, m, d] = todo.due_date.split('-').map(Number);
  let due;
  if (todo.due_time) {
    const [h, mn] = todo.due_time.split(':').map(Number);
    due = new Date(Date.UTC(y, m - 1, d, h, mn));  // stored UTC
  } else {
    due = new Date(y, m - 1, d);  // local date
  }
  return due <= endOfToday;
}

function filtered() {
  if (filter === 'today') return todos.filter(t => !t.done && isDueByEndOfToday(t));
  if (filter === 'active') return todos.filter(t => !t.done);
  if (filter === 'done') return todos.filter(t => t.done);
  return todos;
}

function formatDue(isoDate, timeStr) {
  if (!isoDate && !timeStr) return null;
  let label = '';
  let overdue = false;

  if (isoDate && timeStr) {
    // Stored as UTC — convert to local for display
    const [y, m, d] = isoDate.split('-').map(Number);
    const [h, min] = timeStr.split(':').map(Number);
    const due = new Date(Date.UTC(y, m - 1, d, h, min));
    overdue = due < new Date();
    label = due.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    const timeLabel = due.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    label = `${label} at ${timeLabel}`;
  } else if (isoDate) {
    // Date-only — no timezone conversion
    const [y, m, d] = isoDate.split('-').map(Number);
    const due = new Date(y, m - 1, d);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    overdue = due < today;
    label = due.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } else if (timeStr) {
    const [h, min] = timeStr.split(':').map(Number);
    const t = new Date(2000, 0, 1, h, min);
    label = t.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }

  return { label, overdue };
}

function render() {
  const visible = filtered();
  list.innerHTML = '';
  emptyMsg.hidden = visible.length > 0;

  for (const todo of visible) {
    const li = document.createElement('li');
    li.className = 'todo-item' + (todo.done ? ' is-done' : '');
    li.dataset.id = todo.id;

    const due = formatDue(todo.due_date, todo.due_time);
    const dueHtml = due
      ? `<div class="todo-due${due.overdue ? ' is-overdue' : ''}">${due.overdue ? '⚠ ' : ''}${due.label}</div>`
      : '';
    const recurLabel = formatRecurrence(todo);
    const recurHtml = recurLabel
      ? `<span class="todo-recur" title="Tap to edit">${recurLabel}</span>`
      : '';

    const showNotes = todo.notes || !todo.done;
    const notesHtml = showNotes
      ? `<div class="todo-notes${todo.notes ? '' : ' is-empty'}">${todo.notes ? escHtml(todo.notes) : 'Add note…'}</div>`
      : '';

    li.innerHTML = `
      <button class="todo-check" aria-label="${todo.done ? 'Mark incomplete' : 'Mark complete'}">
        <span class="todo-check-inner"></span>
      </button>
      <div class="todo-body">
        <div class="todo-title" title="Tap to edit">${escHtml(todo.title)}</div>
        ${notesHtml}
        <div class="todo-meta">${dueHtml}${recurHtml}</div>
      </div>
      <button class="todo-delete" aria-label="Delete todo">✕</button>
    `;

    li.querySelector('.todo-check').addEventListener('click', () => toggleDone(todo));
    li.querySelector('.todo-delete').addEventListener('click', () => remove(todo));
    li.querySelector('.todo-title').addEventListener('click', () => startEdit(li, todo));

    const notesEl = li.querySelector('.todo-notes');
    if (notesEl && !todo.done) notesEl.addEventListener('click', () => startNotesEdit(li, todo));

    const recurEl = li.querySelector('.todo-recur');
    if (recurEl) recurEl.addEventListener('click', () => startRecurEdit(li, todo));

    list.appendChild(li);
  }
}

function escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ---------------------------------------------------------------------------
// Inline edit — title
// ---------------------------------------------------------------------------

function startEdit(li, todo) {
  if (todo.done) return;
  const titleEl = li.querySelector('.todo-title');
  if (li.querySelector('.todo-edit-input')) return;

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'todo-edit-input';
  input.value = todo.title;
  input.maxLength = 500;
  titleEl.replaceWith(input);
  input.focus();
  input.select();

  let saved = false;

  async function save() {
    if (saved) return;
    saved = true;
    const val = input.value.trim();
    if (!val || val === todo.title) { cancelEdit(); return; }
    const updated = await api(`/todos/${todo.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ title: val }),
    });
    const idx = todos.findIndex(t => t.id === todo.id);
    if (idx !== -1) todos[idx] = updated;
    render();
  }

  function cancelEdit() {
    if (saved) return;
    saved = true;
    const restored = document.createElement('div');
    restored.className = 'todo-title';
    restored.title = 'Tap to edit';
    restored.textContent = todo.title;
    restored.addEventListener('click', () => startEdit(li, todo));
    input.replaceWith(restored);
  }

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); save(); }
    if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
  });
  input.addEventListener('blur', save);
}

// ---------------------------------------------------------------------------
// Inline edit — notes
// ---------------------------------------------------------------------------

function startNotesEdit(li, todo) {
  const notesEl = li.querySelector('.todo-notes');
  if (!notesEl || li.querySelector('.todo-notes-input')) return;

  const ta = document.createElement('textarea');
  ta.className = 'todo-notes-input';
  ta.value = todo.notes || '';
  ta.placeholder = 'Add a note…';
  ta.maxLength = 2000;
  ta.rows = 2;
  notesEl.replaceWith(ta);
  ta.focus();

  let saved = false;

  async function save() {
    if (saved) return;
    saved = true;
    const val = ta.value.trim() || null;
    if (val === (todo.notes || null)) { cancelNotesEdit(); return; }
    const updated = await api(`/todos/${todo.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ notes: val }),
    });
    const idx = todos.findIndex(t => t.id === todo.id);
    if (idx !== -1) todos[idx] = updated;
    render();
  }

  function cancelNotesEdit() {
    if (saved) return;
    saved = true;
    const restored = document.createElement('div');
    restored.className = 'todo-notes' + (todo.notes ? '' : ' is-empty');
    restored.textContent = todo.notes || 'Add note…';
    restored.addEventListener('click', () => startNotesEdit(li, todo));
    ta.replaceWith(restored);
  }

  ta.addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.preventDefault(); cancelNotesEdit(); }
    if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); save(); }
  });
  ta.addEventListener('blur', save);
}

// ---------------------------------------------------------------------------
// Inline edit — recurrence
// ---------------------------------------------------------------------------

function startRecurEdit(li, todo) {
  const recurEl = li.querySelector('.todo-recur');
  if (!recurEl || li.querySelector('.todo-recur-edit')) return;

  const wrap = document.createElement('span');
  wrap.className = 'todo-recur-edit';

  const numInput = document.createElement('input');
  numInput.type = 'number';
  numInput.min = 1;
  numInput.max = 99;
  numInput.className = 'recur-edit-num';
  numInput.value = todo.recurrence_interval || '';

  const unitSel = document.createElement('select');
  unitSel.className = 'recur-edit-unit';
  for (const u of ['days', 'weeks', 'months', 'years']) {
    const opt = document.createElement('option');
    opt.value = u;
    opt.textContent = u;
    if (u === todo.recurrence_unit) opt.selected = true;
    unitSel.appendChild(opt);
  }

  wrap.append(numInput, ' ', unitSel);
  // Day toggles (only used when unit is "weeks")
  const dayWrap = document.createElement('span');
  dayWrap.className = 'day-toggles recur-edit-days';
  for (let i = 0; i < 7; i++) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'day-toggle';
    b.dataset.day = i;
    b.title = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][i];
    b.textContent = ['S','M','T','W','T','F','S'][i];
    dayWrap.appendChild(b);
  }
  wireDayToggles(dayWrap);
  setSelectedDays(dayWrap, todo.recurrence_days);
  dayWrap.style.display = unitSel.value === 'weeks' ? '' : 'none';
  unitSel.addEventListener('change', () => {
    dayWrap.style.display = unitSel.value === 'weeks' ? '' : 'none';
  });
  wrap.appendChild(dayWrap);

  recurEl.replaceWith(wrap);
  numInput.focus();
  numInput.select();

  let saved = false;

  async function save() {
    if (saved) return;
    saved = true;
    const days = unitSel.value === 'weeks' ? getSelectedDays(dayWrap) : [];
    let body;
    if (days.length > 0) {
      body = { recurrence_unit: 'weeks', recurrence_days: days.join(',') };
    } else {
      const interval = parseInt(numInput.value, 10);
      if (!interval || interval < 1) { cancel(); return; }
      body = { recurrence_interval: interval, recurrence_unit: unitSel.value, recurrence_days: null };
    }
    const updated = await api(`/todos/${todo.id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
    const idx = todos.findIndex(t => t.id === todo.id);
    if (idx !== -1) todos[idx] = updated;
    render();
  }

  function cancel() {
    if (saved) return;
    saved = true;
    const badge = document.createElement('span');
    badge.className = 'todo-recur';
    badge.title = 'Tap to edit';
    badge.textContent = formatRecurrence(todo) || '';
    badge.addEventListener('click', () => startRecurEdit(li, todo));
    wrap.replaceWith(badge);
  }

  function onBlur() {
    setTimeout(() => { if (!wrap.contains(document.activeElement)) save(); }, 200);
  }

  numInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); save(); }
    if (e.key === 'Escape') { e.preventDefault(); cancel(); }
  });
  unitSel.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); save(); }
    if (e.key === 'Escape') { e.preventDefault(); cancel(); }
  });
  numInput.addEventListener('blur', onBlur);
  unitSel.addEventListener('blur', onBlur);
}

// ---------------------------------------------------------------------------
// Toast helper
// ---------------------------------------------------------------------------

function makeToast(message) {
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.innerHTML = `<span>${message}</span><button class="toast-undo">Undo</button>`;
  document.body.appendChild(toast);
  return toast;
}

function dismissToastEl(toastEl) {
  toastEl.classList.add('toast-out');
  setTimeout(() => toastEl.remove(), 250);
}

// ---------------------------------------------------------------------------
// Mark done with undo
// ---------------------------------------------------------------------------

let pendingToggle = null;

function commitToggle() {
  if (!pendingToggle) return;
  const { todo, timeoutId, toastEl } = pendingToggle;
  clearTimeout(timeoutId);
  dismissToastEl(toastEl);
  pendingToggle = null;
  api(`/todos/${todo.id}`, {
    method: 'PATCH',
    body: JSON.stringify({ done: true }),
  }).then(updated => {
    const idx = todos.findIndex(t => t.id === todo.id);
    if (idx !== -1) todos[idx] = updated;
    if (updated.recurrence_interval) {
      return api('/todos').then(all => { todos = all; });
    }
  }).then(() => render()).catch(() => {});
}

function toggleDone(todo) {
  if (pendingToggle) commitToggle();

  if (todo.done) {
    // Un-completing — do immediately, no toast; backend deletes spawned child
    api(`/todos/${todo.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ done: false }),
    }).then(updated => {
      // Reload list — backend may have deleted the spawned child
      return api('/todos').then(all => { todos = all; });
    }).then(() => render());
    return;
  }

  const idx = todos.findIndex(t => t.id === todo.id);
  if (idx !== -1) todos[idx] = { ...todos[idx], done: true };
  render();

  const toastEl = makeToast('Task done');
  const timeoutId = setTimeout(commitToggle, 4000);
  pendingToggle = { todo, timeoutId, toastEl };

  toastEl.querySelector('.toast-undo').addEventListener('click', () => {
    clearTimeout(pendingToggle.timeoutId);
    dismissToastEl(pendingToggle.toastEl);
    const i = todos.findIndex(t => t.id === todo.id);
    if (i !== -1) todos[i] = { ...todos[i], done: false };
    pendingToggle = null;
    render();
  });
}

// ---------------------------------------------------------------------------
// Delete with undo
// ---------------------------------------------------------------------------

let pendingDelete = null;

function commitDelete() {
  if (!pendingDelete) return;
  const { todo, timeoutId, toastEl } = pendingDelete;
  clearTimeout(timeoutId);
  dismissToastEl(toastEl);
  pendingDelete = null;
  api(`/todos/${todo.id}`, { method: 'DELETE' }).catch(() => {});
}

function remove(todo) {
  if (pendingDelete) commitDelete();

  todos = todos.filter(t => t.id !== todo.id);
  render();

  const toastEl = makeToast('Task deleted');
  const timeoutId = setTimeout(commitDelete, 4000);
  pendingDelete = { todo, timeoutId, toastEl };

  toastEl.querySelector('.toast-undo').addEventListener('click', () => {
    clearTimeout(pendingDelete.timeoutId);
    dismissToastEl(pendingDelete.toastEl);
    todos = [todo, ...todos];
    todos.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    pendingDelete = null;
    render();
  });
}

// ---------------------------------------------------------------------------
// Add todo
// ---------------------------------------------------------------------------

addForm.addEventListener('submit', async e => {
  e.preventDefault();
  const title = newTitle.value.trim();
  if (!title) return;
  const payload = { title };
  if (newDue.value && newDueTime.value) {
    // Convert local date+time to UTC for storage
    const [y, m, d] = newDue.value.split('-').map(Number);
    const [h, min] = newDueTime.value.split(':').map(Number);
    const local = new Date(y, m - 1, d, h, min);
    payload.due_date = local.toISOString().slice(0, 10);
    payload.due_time = local.toISOString().slice(11, 16);
  } else if (newDue.value) {
    payload.due_date = newDue.value;
  } else if (newDueTime.value) {
    payload.due_time = newDueTime.value;
  }
  if (newNotes.value.trim()) payload.notes = newNotes.value.trim();
  const selectedDays = newRecurUnit.value === 'weeks' ? getSelectedDays(newDayToggles) : [];
  if (selectedDays.length > 0) {
    payload.recurrence_unit = 'weeks';
    payload.recurrence_days = selectedDays.join(',');
  } else if (newRecurInterval.value) {
    payload.recurrence_interval = parseInt(newRecurInterval.value, 10);
    payload.recurrence_unit = newRecurUnit.value;
  }
  const created = await api('/todos', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  todos.unshift(created);
  newTitle.value = '';
  newDue.value = '';
  newDueTime.value = '';
  newNotes.value = '';
  newRecurInterval.value = '';
  setSelectedDays(newDayToggles, '');
  if (filter === 'done') filter = 'active';
  document.querySelector('.tab.active').classList.remove('active');
  document.querySelector('[data-filter="active"]').classList.add('active');
  render();
  newTitle.focus();
});

// ---------------------------------------------------------------------------
// Push notifications
// ---------------------------------------------------------------------------

function urlBase64ToUint8Array(b64) {
  const padding = '='.repeat((4 - b64.length % 4) % 4);
  const base64 = (b64 + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

const notifBtn = document.getElementById('notif-btn');

async function setupPush() {
  if (!('Notification' in window) || !('serviceWorker' in navigator)) return;
  if (Notification.permission === 'denied') return;

  let reg;
  try { reg = await navigator.serviceWorker.ready; } catch { return; }
  if (!reg.pushManager) return;

  let { publicKey } = await api('/push/vapid-public-key').catch(() => ({}));
  if (!publicKey) return;

  const keyBytes = urlBase64ToUint8Array(publicKey);

  // If permission already granted, subscribe directly with the current key.
  // This returns the existing sub if the key matches, or throws if it changed
  // (e.g. VAPID keys regenerated on server) — in which case we unsubscribe
  // the stale sub and fall through to show the bell for re-subscription.
  if (Notification.permission === 'granted') {
    try {
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes });
      await api('/push/subscribe', { method: 'POST', body: JSON.stringify(sub.toJSON()) }).catch(() => {});
      notifBtn.hidden = true;
      return;
    } catch {
      const stale = await reg.pushManager.getSubscription().catch(() => null);
      if (stale) await stale.unsubscribe().catch(() => {});
    }
  }

  // Show the bell — user must tap to trigger the permission dialog
  notifBtn.hidden = false;
  notifBtn.onclick = async () => {
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') return;
    try {
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes });
      await api('/push/subscribe', { method: 'POST', body: JSON.stringify(sub.toJSON()) });
      notifBtn.classList.add('is-on');
      notifBtn.title = 'Notifications on';
      notifBtn.onclick = null;
    } catch (e) {
      console.error('Push subscribe failed', e);
    }
  };
}

document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    filter = btn.dataset.filter;
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    render();
  });
});

const versionEl = document.getElementById('version');
if (versionEl) versionEl.textContent = VERSION;

// ---------------------------------------------------------------------------
// Polling — keep the list fresh across devices
// ---------------------------------------------------------------------------

const POLL_MS = 10000;
let pollTimer = null;

function isUserBusy() {
  return !!document.querySelector(
    '.todo-edit-input, .todo-notes-input, .todo-recur-edit, .toast'
  );
}

async function poll() {
  if (document.hidden || isUserBusy() || pendingToggle || pendingDelete) return;
  try { await load(); } catch {}
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(poll, POLL_MS);
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  } else {
    poll();
    startPolling();
  }
});

load();
setupPush();
startPolling();
