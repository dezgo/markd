'use strict';

const VERSION = 'v28';

let todos = [];
let filter = 'active';
let editingId = null;
let showUpcoming = false;
const TODAY_GROUPS = new Set(['Overdue', 'Today']);

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
const submitBtn = document.getElementById('submit-btn');
const cancelEditBtn = document.getElementById('cancel-edit-btn');

const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function formatRecurrence(todo) {
  if (!todo.recurrence_interval || !todo.recurrence_unit) return null;
  if (todo.recurrence_unit === 'weeks' && todo.recurrence_days) {
    const labels = todo.recurrence_days.split(',').map(d => DAY_LABELS[+d]);
    return `↻ ${labels.join(', ')}`;
  }
  return `↻ every ${todo.recurrence_interval} ${todo.recurrence_unit}`;
}

// Day-toggle helpers
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
    due = new Date(Date.UTC(y, m - 1, d, h, mn));
  } else {
    due = new Date(y, m - 1, d);
  }
  return due <= endOfToday;
}

function dueDateTime(t) {
  if (!t.due_date) return null;
  const [y, m, d] = t.due_date.split('-').map(Number);
  if (t.due_time) {
    const [h, mn] = t.due_time.split(':').map(Number);
    return new Date(Date.UTC(y, m - 1, d, h, mn));  // stored UTC
  }
  return new Date(y, m - 1, d);
}

function compareByDue(a, b) {
  const da = dueDateTime(a), db = dueDateTime(b);
  if (!da && !db) return 0;
  if (!da) return 1;   // undated tasks sink
  if (!db) return -1;
  return da - db;
}

function filtered() {
  let result;
  if (filter === 'active') result = todos.filter(t => !t.done);
  else if (filter === 'done') result = todos.filter(t => t.done);
  else result = [...todos];

  if (filter === 'active') {
    result = [...result].sort(compareByDue);
  }
  return result;
}

function formatDue(isoDate, timeStr) {
  if (!isoDate && !timeStr) return null;
  let label = '';
  let overdue = false;

  if (isoDate && timeStr) {
    const [y, m, d] = isoDate.split('-').map(Number);
    const [h, min] = timeStr.split(':').map(Number);
    const due = new Date(Date.UTC(y, m - 1, d, h, min));
    overdue = due < new Date();
    label = due.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    const timeLabel = due.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    label = `${label} at ${timeLabel}`;
  } else if (isoDate) {
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

function renderTodoItem(todo) {
  const li = document.createElement('li');
  li.className = 'todo-item' + (todo.done ? ' is-done' : '') + (todo.id === editingId ? ' is-editing' : '');
  li.dataset.id = todo.id;

  const due = formatDue(todo.due_date, todo.due_time);
  const dueHtml = due
    ? `<div class="todo-due${due.overdue ? ' is-overdue' : ''}">${due.overdue ? '⚠ ' : ''}${due.label}</div>`
    : (todo.done ? '' : `<div class="todo-due is-empty">+ due</div>`);
  const recurLabel = formatRecurrence(todo);
  const recurHtml = recurLabel
    ? `<span class="todo-recur">${recurLabel}</span>`
    : (todo.done ? '' : `<span class="todo-recur is-empty">+ repeat</span>`);

  const showNotes = todo.notes || !todo.done;
  const notesHtml = showNotes
    ? `<div class="todo-notes${todo.notes ? '' : ' is-empty'}">${todo.notes ? escHtml(todo.notes) : 'Add note…'}</div>`
    : '';

  li.innerHTML = `
    <button class="todo-check" aria-label="${todo.done ? 'Mark incomplete' : 'Mark complete'}">
      <span class="todo-check-inner"></span>
    </button>
    <div class="todo-body" title="${todo.done ? '' : 'Tap to edit'}">
      <div class="todo-title">${escHtml(todo.title)}</div>
      ${notesHtml}
      <div class="todo-meta">${dueHtml}${recurHtml}</div>
    </div>
    <button class="todo-delete" aria-label="Delete todo">✕</button>
  `;

  li.querySelector('.todo-check').addEventListener('click', () => toggleDone(todo));
  li.querySelector('.todo-delete').addEventListener('click', () => remove(todo));
  if (!todo.done) {
    li.querySelector('.todo-body').addEventListener('click', () => startEditingTask(todo));
  }
  return li;
}

const GROUP_ORDER = ['Overdue', 'Today', 'Tomorrow', 'This week', 'Later', 'No date'];

function groupForTodo(t) {
  if (!t.due_date) return 'No date';
  const [y, m, d] = t.due_date.split('-').map(Number);
  let dueLocalDate;
  if (t.due_time) {
    const [h, mn] = t.due_time.split(':').map(Number);
    const dueDt = new Date(Date.UTC(y, m - 1, d, h, mn));
    dueLocalDate = new Date(dueDt.getFullYear(), dueDt.getMonth(), dueDt.getDate());
  } else {
    dueLocalDate = new Date(y, m - 1, d);
  }
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = Math.round((dueLocalDate - today) / 86400000);
  if (days < 0) return 'Overdue';
  if (days === 0) return 'Today';
  if (days === 1) return 'Tomorrow';
  if (days < 7) return 'This week';
  return 'Later';
}

function render() {
  const visible = filtered();
  list.innerHTML = '';

  if (filter !== 'active') {
    emptyMsg.hidden = visible.length > 0;
    visible.forEach(t => list.appendChild(renderTodoItem(t)));
    return;
  }

  // Group active by due-date bucket
  const groups = Object.fromEntries(GROUP_ORDER.map(g => [g, []]));
  visible.forEach(t => groups[groupForTodo(t)].push(t));

  const todayCount = groups['Overdue'].length + groups['Today'].length;
  const upcomingCount = visible.length - todayCount;

  // Decide which groups to show
  const groupsToShow = showUpcoming ? GROUP_ORDER : GROUP_ORDER.filter(g => TODAY_GROUPS.has(g));

  let renderedItems = 0;
  for (const name of groupsToShow) {
    if (groups[name].length === 0) continue;
    const header = document.createElement('li');
    header.className = 'todo-group-header' + (name === 'Overdue' ? ' is-overdue' : '');
    header.textContent = name;
    list.appendChild(header);
    groups[name].forEach(t => list.appendChild(renderTodoItem(t)));
    renderedItems += groups[name].length;
  }

  // Empty state — only when default view (today) has nothing AND no upcoming hidden
  if (renderedItems === 0 && upcomingCount === 0) {
    emptyMsg.hidden = false;
  } else {
    emptyMsg.hidden = true;
  }

  // Friendly nudge if today is empty but upcoming exist
  if (renderedItems === 0 && upcomingCount > 0 && !showUpcoming) {
    const empty = document.createElement('li');
    empty.className = 'todo-group-empty';
    empty.textContent = 'Nothing due today.';
    list.appendChild(empty);
  }

  // Toggle to expand/collapse upcoming
  if (upcomingCount > 0) {
    const toggle = document.createElement('li');
    toggle.className = 'todo-upcoming-toggle';
    toggle.innerHTML = showUpcoming
      ? `<button type="button">Hide upcoming</button>`
      : `<button type="button">Show ${upcomingCount} upcoming →</button>`;
    toggle.querySelector('button').addEventListener('click', () => {
      showUpcoming = !showUpcoming;
      render();
    });
    list.appendChild(toggle);
  }
}

function escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ---------------------------------------------------------------------------
// Edit mode — populates the top form with a task's data
// ---------------------------------------------------------------------------

function startEditingTask(todo) {
  editingId = todo.id;
  newTitle.value = todo.title;

  // Convert UTC date+time back to local for editing
  if (todo.due_date && todo.due_time) {
    const [y, m, d] = todo.due_date.split('-').map(Number);
    const [h, mn] = todo.due_time.split(':').map(Number);
    const local = new Date(Date.UTC(y, m - 1, d, h, mn));
    newDue.value = `${local.getFullYear()}-${String(local.getMonth() + 1).padStart(2, '0')}-${String(local.getDate()).padStart(2, '0')}`;
    newDueTime.value = `${String(local.getHours()).padStart(2, '0')}:${String(local.getMinutes()).padStart(2, '0')}`;
  } else if (todo.due_date) {
    newDue.value = todo.due_date;
    newDueTime.value = '';
  } else if (todo.due_time) {
    newDue.value = '';
    newDueTime.value = todo.due_time;
  } else {
    newDue.value = '';
    newDueTime.value = '';
  }

  newNotes.value = todo.notes || '';
  newRecurUnit.value = todo.recurrence_unit || 'weeks';
  newRecurInterval.value = todo.recurrence_interval || '';
  newDayToggles.hidden = newRecurUnit.value !== 'weeks';
  setSelectedDays(newDayToggles, todo.recurrence_days);

  document.body.classList.add('is-editing');
  submitBtn.textContent = '✓';
  submitBtn.title = 'Save changes';
  cancelEditBtn.hidden = false;

  render();  // re-highlight the editing row
  newTitle.focus();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function cancelEditing() {
  editingId = null;
  newTitle.value = '';
  newDue.value = '';
  newDueTime.value = '';
  newNotes.value = '';
  newRecurInterval.value = '';
  setSelectedDays(newDayToggles, '');
  newRecurUnit.value = 'weeks';
  newDayToggles.hidden = false;

  document.body.classList.remove('is-editing');
  submitBtn.textContent = '＋';
  submitBtn.title = 'Add todo';
  cancelEditBtn.hidden = true;
  render();
}

cancelEditBtn.addEventListener('click', () => cancelEditing());
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && editingId !== null && document.activeElement !== document.body) {
    // only cancel if focus is on the form
    if (addForm.contains(document.activeElement)) {
      e.preventDefault();
      cancelEditing();
    }
  }
});

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
    api(`/todos/${todo.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ done: false }),
    }).then(updated => {
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
  if (todo.id === editingId) cancelEditing();

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
// Form submit — add or edit
// ---------------------------------------------------------------------------

function buildPayload({ forUpdate }) {
  const payload = { title: newTitle.value.trim() };

  if (newDue.value && newDueTime.value) {
    const [y, m, d] = newDue.value.split('-').map(Number);
    const [h, min] = newDueTime.value.split(':').map(Number);
    const local = new Date(y, m - 1, d, h, min);
    payload.due_date = local.toISOString().slice(0, 10);
    payload.due_time = local.toISOString().slice(11, 16);
  } else if (newDue.value) {
    payload.due_date = newDue.value;
    if (forUpdate) payload.due_time = null;
  } else if (newDueTime.value) {
    payload.due_time = newDueTime.value;
    if (forUpdate) payload.due_date = null;
  } else if (forUpdate) {
    payload.due_date = null;
    payload.due_time = null;
  }

  if (newNotes.value.trim()) {
    payload.notes = newNotes.value.trim();
  } else if (forUpdate) {
    payload.notes = null;
  }

  const selectedDays = newRecurUnit.value === 'weeks' ? getSelectedDays(newDayToggles) : [];
  if (selectedDays.length > 0) {
    payload.recurrence_unit = 'weeks';
    payload.recurrence_days = selectedDays.join(',');
    if (forUpdate) payload.recurrence_interval = null;
  } else if (newRecurInterval.value) {
    payload.recurrence_interval = parseInt(newRecurInterval.value, 10);
    payload.recurrence_unit = newRecurUnit.value;
    if (forUpdate) payload.recurrence_days = null;
  } else if (forUpdate) {
    payload.recurrence_interval = null;
    payload.recurrence_unit = null;
    payload.recurrence_days = null;
  }

  return payload;
}

addForm.addEventListener('submit', async e => {
  e.preventDefault();
  if (!newTitle.value.trim()) return;

  if (editingId !== null) {
    const payload = buildPayload({ forUpdate: true });
    const updated = await api(`/todos/${editingId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
    const idx = todos.findIndex(t => t.id === editingId);
    if (idx !== -1) todos[idx] = updated;
    cancelEditing();
    return;
  }

  const payload = buildPayload({ forUpdate: false });
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
  return editingId !== null || !!document.querySelector('.toast');
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
