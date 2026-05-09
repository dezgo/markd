'use strict';

let todos = [];
let filter = 'active';

const list = document.getElementById('todo-list');
const emptyMsg = document.getElementById('empty-msg');
const addForm = document.getElementById('add-form');
const newTitle = document.getElementById('new-title');
const newDue = document.getElementById('new-due');

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

    li.innerHTML = `
      <button class="todo-check" aria-label="${todo.done ? 'Mark incomplete' : 'Mark complete'}">
        <span class="todo-check-inner"></span>
      </button>
      <div class="todo-body">
        <div class="todo-title">${escHtml(todo.title)}</div>
        ${dueHtml}
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
  render();
}

async function remove(todo) {
  await api(`/todos/${todo.id}`, { method: 'DELETE' });
  todos = todos.filter(t => t.id !== todo.id);
  render();
}

addForm.addEventListener('submit', async e => {
  e.preventDefault();
  const title = newTitle.value.trim();
  if (!title) return;
  const payload = { title };
  if (newDue.value) payload.due_date = newDue.value;
  const created = await api('/todos', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  todos.unshift(created);
  newTitle.value = '';
  newDue.value = '';
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
