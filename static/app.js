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
        <div class="todo-title">${escHtml(todo.title)}</div>
        <div class="todo-meta">${dueHtml}${recurHtml}</div>
      </div>
      <button class="todo-delete" aria-label="Delete todo">✕</button>
    `;

    li.querySelector('.todo-check').addEventListener('click', () => toggle(todo));
    li.querySelector('.todo-delete').addEventListener('click', () => remove(todo));

    list.appendChild(li);
  }
}

function escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function toggle(todo) {
  const updated = await api(`/todos/${todo.id}`, {
    method: 'PATCH',
    body: JSON.stringify({ done: !todo.done }),
  });
  const idx = todos.findIndex(t => t.id === todo.id);
  if (idx !== -1) todos[idx] = updated;
  // Completing a recurring todo spawns a new one server-side — reload the list
  if (updated.done && updated.recurrence_interval) {
    todos = await api('/todos');
  }
  render();
}

let pendingDelete = null;

function remove(todo) {
  // Commit any previous pending delete immediately
  if (pendingDelete) {
    clearTimeout(pendingDelete.timeoutId);
    api(`/todos/${pendingDelete.todo.id}`, { method: 'DELETE' }).catch(() => {});
    dismissToast();
  }

  // Optimistically remove from UI
  todos = todos.filter(t => t.id !== todo.id);
  render();

  // Show undo toast
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.innerHTML = `<span>Task deleted</span><button class="toast-undo">Undo</button>`;
  document.body.appendChild(toast);

  const timeoutId = setTimeout(() => {
    api(`/todos/${todo.id}`, { method: 'DELETE' }).catch(() => {});
    dismissToast();
  }, 4000);

  pendingDelete = { todo, timeoutId, toastEl: toast };

  toast.querySelector('.toast-undo').addEventListener('click', () => {
    clearTimeout(pendingDelete.timeoutId);
    dismissToast();
    todos = [todo, ...todos];
    todos.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    pendingDelete = null;
    render();
  });
}

function dismissToast() {
  if (!pendingDelete) return;
  const el = pendingDelete.toastEl;
  el.classList.add('toast-out');
  setTimeout(() => el.remove(), 250);
  pendingDelete = null;
}

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
