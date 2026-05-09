'use strict';

let todos = [];
let filter = 'active';

const list = document.getElementById('todo-list');
const emptyMsg = document.getElementById('empty-msg');
const addForm = document.getElementById('add-form');
const newTitle = document.getElementById('new-title');
const newDue = document.getElementById('new-due');
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

function formatDue(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.split('-').map(Number);
  const due = new Date(y, m - 1, d);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((due - today) / 86400000);
  const label = due.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  return { label, overdue: diff < 0 };
}

function render() {
  const visible = filtered();
  list.innerHTML = '';
  emptyMsg.hidden = visible.length > 0;

  for (const todo of visible) {
    const li = document.createElement('li');
    li.className = 'todo-item' + (todo.done ? ' is-done' : '');
    li.dataset.id = todo.id;

    const due = formatDue(todo.due_date);
    const dueHtml = due
      ? `<div class="todo-due${due.overdue ? ' is-overdue' : ''}">${due.overdue ? '⚠ ' : ''}${due.label}</div>`
      : '';
    const recurHtml = todo.recurrence_interval && todo.recurrence_unit
      ? `<span class="todo-recur">↻ every ${todo.recurrence_interval} ${todo.recurrence_unit}</span>`
      : '';

    li.innerHTML = `
      <button class="todo-check" aria-label="${todo.done ? 'Mark incomplete' : 'Mark complete'}">
        <span class="todo-check-inner"></span>
      </button>
      <div class="todo-body">
        <div class="todo-title" title="Tap to edit">${escHtml(todo.title)}</div>
        <div class="todo-meta">${dueHtml}${recurHtml}</div>
      </div>
      <button class="todo-delete" aria-label="Delete todo">✕</button>
    `;

    li.querySelector('.todo-check').addEventListener('click', () => toggleDone(todo));
    li.querySelector('.todo-delete').addEventListener('click', () => remove(todo));
    li.querySelector('.todo-title').addEventListener('click', () => startEdit(li, todo));

    list.appendChild(li);
  }
}

function escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ---------------------------------------------------------------------------
// Inline edit
// ---------------------------------------------------------------------------

function startEdit(li, todo) {
  if (todo.done) return;
  const titleEl = li.querySelector('.todo-title');
  if (li.querySelector('.todo-edit-input')) return; // already editing

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
    if (!val || val === todo.title) {
      cancelEdit();
      return;
    }
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
  // Commit any pending toggle before acting
  if (pendingToggle) commitToggle();

  if (todo.done) {
    // Un-completing — do immediately, no toast
    api(`/todos/${todo.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ done: false }),
    }).then(updated => {
      const idx = todos.findIndex(t => t.id === todo.id);
      if (idx !== -1) todos[idx] = updated;
      render();
    });
    return;
  }

  // Optimistically mark done in UI
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
  // Commit any previous pending delete immediately
  if (pendingDelete) commitDelete();

  // Optimistically remove from UI
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
  newRecurInterval.value = '';
  if (filter === 'done') filter = 'active';
  document.querySelector('.tab.active').classList.remove('active');
  document.querySelector('[data-filter="active"]').classList.add('active');
  render();
  newTitle.focus();
});

document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    filter = btn.dataset.filter;
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    render();
  });
});

load();
