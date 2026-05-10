'use strict';

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
  todos = await api('/todos');
  render();
}

function filtered() {
  if (filter === 'active') return todos.filter(t => !t.done);
  if (filter === 'done') return todos.filter(t => t.done);
  return todos;
}

function formatDue(isoDate, timeStr) {
  if (!isoDate && !timeStr) return null;
  let label = '';
  let overdue = false;

  if (isoDate) {
    const [y, m, d] = isoDate.split('-').map(Number);
    const due = new Date(y, m - 1, d);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    overdue = due < today;
    label = due.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  if (timeStr) {
    const [h, min] = timeStr.split(':').map(Number);
    const t = new Date(2000, 0, 1, h, min);
    const timeLabel = t.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    label = label ? `${label} at ${timeLabel}` : timeLabel;
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
    const recurHtml = todo.recurrence_interval && todo.recurrence_unit
      ? `<span class="todo-recur" title="Tap to edit">↻ every ${todo.recurrence_interval} ${todo.recurrence_unit}</span>`
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
  recurEl.replaceWith(wrap);
  numInput.focus();
  numInput.select();

  let saved = false;

  async function save() {
    if (saved) return;
    saved = true;
    const interval = parseInt(numInput.value, 10);
    if (!interval || interval < 1) { cancel(); return; }
    const updated = await api(`/todos/${todo.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ recurrence_interval: interval, recurrence_unit: unitSel.value }),
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
    badge.textContent = `↻ every ${todo.recurrence_interval} ${todo.recurrence_unit}`;
    badge.addEventListener('click', () => startRecurEdit(li, todo));
    wrap.replaceWith(badge);
  }

  function onBlur() {
    setTimeout(() => { if (!wrap.contains(document.activeElement)) save(); }, 100);
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
  if (newDue.value) payload.due_date = newDue.value;
  if (newDueTime.value) payload.due_time = newDueTime.value;
  if (newNotes.value.trim()) payload.notes = newNotes.value.trim();
  if (newRecurInterval.value) {
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

async function setupPush() {
  if (!('Notification' in window) || !('serviceWorker' in navigator)) return;
  if (Notification.permission === 'denied') return;

  let reg;
  try { reg = await navigator.serviceWorker.ready; } catch { return; }
  if (!reg.pushManager) return;

  try {
    const { publicKey } = await api('/push/vapid-public-key');
    if (!publicKey) return;

    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') return;
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
    }

    await api('/push/subscribe', { method: 'POST', body: JSON.stringify(sub.toJSON()) });
  } catch { /* permission denied or not supported — silently skip */ }
}

document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    filter = btn.dataset.filter;
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    render();
  });
});

load();
setupPush();
