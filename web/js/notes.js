/* ---------------------------------------------------------------------------
 * notes.js
 *
 * Notes and to-do panel.
 *
 * Extracted from the single 4000-line script.js by /data/p9_3b.py.
 * [M14 P9.3]
 * ------------------------------------------------------------------------- */

import { API, PANEL_AUTO_CLOSE_MS } from './config.js';
import { el } from './dom.js';
import { svgIcon } from './icons.js';
import { render as renderMarkdown } from './markdown.js';
import { armAutoClose } from './panels.js';
// ── Notes Panel ──
export const notesPanel = document.getElementById('notes-panel');
export const notesClose = document.getElementById('notes-close');
export const notesMinimize = document.getElementById('notes-minimize');
export const notesList = document.getElementById('notes-list');
export const notesEmpty = document.getElementById('notes-empty');
export const todoLists = document.getElementById('todo-lists');
export const todoEmpty = document.getElementById('todo-empty');
export const notesBtn = document.getElementById('notes-btn');
export const notesPanelHeader = document.getElementById('notes-panel-header');
export const notesTabs = document.querySelectorAll('.notes-tab');
export const notesTabContents = document.querySelectorAll('.notes-tab-content');

export async function fetchNotes(search = null) {
    try {
        let url = `${API}/api/notes`;
        if (search) url += `?search=${encodeURIComponent(search)}`;
        const res = await fetch(url);
        if (!res.ok) return [];
        const data = await res.json();
        return data.notes || [];
    } catch { return []; }
}

export async function fetchTodos() {
    try {
        const res = await fetch(`${API}/api/todos`);
        if (!res.ok) return [];
        const data = await res.json();
        return data.lists || [];
    } catch { return []; }
}

export function renderNotes(notes) {
    if (!notesList || !notesEmpty) return;
    if (!notes || notes.length === 0) {
        notesList.innerHTML = '';
        notesEmpty.style.display = 'block';
        return;
    }
    notesEmpty.style.display = 'none';
    const frag = document.createDocumentFragment();
    notes.forEach(n => {
        const body = n.markdown_body || n.body || '';
        const preview = body.length > 120 ? body.slice(0, 120) + '...' : body;
        const time = n.updated_at || n.created_at || '';
        let timeStr = '';
        try { timeStr = new Date(time).toLocaleDateString('en-IN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch (_) {}

        const title = el('span', { class: 'note-card-title' });
        if (n.pinned) {
            const pin = svgIcon('pin', { label: 'Pinned', class: 'note-card-pin' });
            title.appendChild(pin);
            title.appendChild(document.createTextNode(' '));
        }
        title.appendChild(document.createTextNode(String(n.title == null ? '' : n.title)));

        // The preview stays plain text - markdown of a 120-character fragment
        // is meaningless - and the expanded view renders the real thing. The
        // field is literally called markdown_body. [M14 P6]
        const bodyEl = el('div', { class: 'note-card-body', text: preview });
        bodyEl._mdSource = body;
        bodyEl._mdPreview = preview;

        const delBtn = el('button', {
            type: 'button',
            class: 'note-card-btn delete',
            dataset: { noteDelete: String(n.id) },
            title: 'Delete',
            'aria-label': 'Delete note'
        }, [svgIcon('trash')]);

        frag.appendChild(el('div', { class: 'note-card', dataset: { noteId: String(n.id) } }, [
            el('div', { class: 'note-card-header' }, [
                title,
                el('div', { class: 'note-card-actions' }, [delBtn])
            ]),
            bodyEl,
            el('div', { class: 'note-card-time', text: timeStr })
        ]));
    });
    notesList.replaceChildren(frag);

    // Bind delete buttons
    notesList.querySelectorAll('[data-note-delete]').forEach(btn => {
        btn.addEventListener('click', async e => {
            e.stopPropagation();
            const id = parseInt(btn.dataset.noteDelete);
            await fetch(`${API}/api/notes/${id}`, { method: 'DELETE' });
            openNotesPanel('notes');
        });
    });

    // Expand/collapse on click
    notesList.querySelectorAll('.note-card-body').forEach(body => {
        body.addEventListener('click', () => {
            const expanded = body.classList.toggle('expanded');
            if (expanded && body._mdSource) {
                const host = document.createElement('span');
                host.className = 'msg-stream-text note-card-md';
                body.textContent = '';
                body.appendChild(host);
                if (typeof renderMarkdown === 'function') {
                    renderMarkdown(host, body._mdSource, false);
                } else {
                    host.textContent = body._mdSource;
                }
            } else if (!expanded) {
                body.textContent = body._mdPreview == null ? '' : body._mdPreview;
            }
        });
    });
}

export function renderTodos(lists) {
    if (!todoLists || !todoEmpty) return;
    if (!lists || lists.length === 0) {
        todoLists.innerHTML = '';
        todoEmpty.style.display = 'block';
        return;
    }
    todoEmpty.style.display = 'none';
    const frag = document.createDocumentFragment();
    lists.forEach(lst => {
        const items = lst.items || [];
        const done = items.filter(i => i.done).length;
        const total = items.length;
        const listId = String(lst.id);

        const card = el('div', { class: 'todo-list-card', dataset: { listId: listId } }, [
            el('div', { class: 'todo-list-header' }, [
                el('span', { class: 'todo-list-title', text: String(lst.title == null ? '' : lst.title) }),
                el('span', { class: 'todo-list-progress', text: done + '/' + total }),
                el('div', { class: 'todo-list-actions' }, [
                    el('button', {
                        type: 'button',
                        class: 'note-card-btn delete',
                        dataset: { todoDelete: listId },
                        title: 'Delete list',
                        'aria-label': 'Delete list'
                    }, [svgIcon('trash')])
                ])
            ])
        ]);

        items.forEach(item => {
            const itemId = String(item.id);
            card.appendChild(el('div', {
                class: 'todo-item' + (item.done ? ' done' : ''),
                dataset: { itemId: itemId }
            }, [
                /* A real <button role="checkbox">, not a <div> with a click
                 * handler. The div was unreachable by keyboard and announced
                 * as nothing at all; a button brings focusability, Space and
                 * Enter activation, and a name for free.  [M14 P10.7] */
                el('button', {
                    type: 'button',
                    class: 'todo-checkbox' + (item.done ? ' checked' : ''),
                    role: 'checkbox',
                    'aria-checked': item.done ? 'true' : 'false',
                    'aria-label': (item.done ? 'Mark not done: ' : 'Mark done: ')
                        + String(item.text == null ? 'item' : item.text),
                    dataset: { todoToggle: itemId, done: item.done ? '1' : '0' }
                }),
                el('span', { class: 'todo-item-text', text: String(item.text == null ? '' : item.text) }),
                el('button', {
                    type: 'button',
                    class: 'todo-item-delete',
                    dataset: { todoItemDelete: itemId },
                    'aria-label': 'Delete item',
                    text: '×'
                })
            ]));
        });

        card.appendChild(el('div', { class: 'todo-add-input' }, [
            el('input', {
                type: 'text',
                placeholder: 'Add item...',
                'aria-label': 'Add item',
                dataset: { todoAddInput: listId }
            }),
            el('button', { type: 'button', class: 'todo-add-btn', dataset: { todoAddBtn: listId }, 'aria-label': 'Add item', text: '+' })
        ]));

        frag.appendChild(card);
    });
    todoLists.replaceChildren(frag);

    // Bind todo checkboxes
    todoLists.querySelectorAll('[data-todo-toggle]').forEach(cb => {
        cb.addEventListener('click', async () => {
            const id = parseInt(cb.dataset.todoToggle);
            const currentDone = cb.dataset.done === '1';
            await fetch(`${API}/api/todos/items/${id}/done`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ done: !currentDone })
            });
            openNotesPanel('todo');
        });
    });

    // Bind delete item
    todoLists.querySelectorAll('[data-todo-item-delete]').forEach(btn => {
        btn.addEventListener('click', async e => {
            e.stopPropagation();
            const id = parseInt(btn.dataset.todoItemDelete);
            await fetch(`${API}/api/todos/items/${id}`, { method: 'DELETE' });
            openNotesPanel('todo');
        });
    });

    // Bind delete list
    todoLists.querySelectorAll('[data-todo-delete]').forEach(btn => {
        btn.addEventListener('click', async e => {
            e.stopPropagation();
            const id = parseInt(btn.dataset.todoDelete);
            await fetch(`${API}/api/todos/${id}`, { method: 'DELETE' });
            openNotesPanel('todo');
        });
    });

    // Bind add item
    todoLists.querySelectorAll('[data-todo-add-btn]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const listId = parseInt(btn.dataset.todoAddBtn);
            // listId comes from the API: CSS.escape keeps it a value, not selector syntax.
            const input = todoLists.querySelector('[data-todo-add-input="' + CSS.escape(String(listId)) + '"]');
            if (!input || !input.value.trim()) return;
            await fetch(`${API}/api/todos/${listId}/items`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items: [input.value.trim()] })
            });
            input.value = '';
            openNotesPanel('todo');
        });
    });

    // Enter key to add item
    todoLists.querySelectorAll('[data-todo-add-input]').forEach(input => {
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter') {
                const listId = input.dataset.todoAddInput;
                const btn = todoLists.querySelector(`[data-todo-add-btn="${listId}"]`);
                if (btn) btn.click();
            }
        });
    });
}

/* Holds a CANCEL FUNCTION now, not a timer id.  [M14 P10.6] */
export let _notesPanelTimer = null;
export async function openNotesPanel(tab = 'notes', opts = {}) {
    if (!notesPanel) return;
    notesPanel.setAttribute('aria-hidden', 'false');
    if (notesBtn) notesBtn.classList.add('active');

    // Switch to correct tab
    notesTabs.forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tab);
    });
    notesTabContents.forEach(c => c.classList.remove('active'));
    const content = document.getElementById(tab === 'notes' ? 'notes-content' : 'todo-content');
    if (content) content.classList.add('active');

    // Load data
    if (tab === 'notes') {
        const notes = await fetchNotes();
        renderNotes(notes);
    } else {
        const lists = await fetchTodos();
        renderTodos(lists);
    }
    /* See reminders.js: only an agent-opened panel closes itself.
         [M14 P10.6] */
    if (_notesPanelTimer) { _notesPanelTimer(); _notesPanelTimer = null; }
    if (opts.auto) {
        _notesPanelTimer = armAutoClose(notesPanel, PANEL_AUTO_CLOSE_MS, closeNotesPanel);
    }
}

export function closeNotesPanel() {
    if (!notesPanel) return;
    notesPanel.setAttribute('aria-hidden', 'true');
    if (notesBtn) notesBtn.classList.remove('active');
    if (_notesPanelTimer) { _notesPanelTimer(); _notesPanelTimer = null; }
}


/* Event wiring that used to sit at the top level of script.js. Under ES
 * modules it cannot stay there: it touches elements owned by other
 * modules, and in an import cycle those bindings are still in their
 * temporal dead zone while this module is being evaluated. main.js calls
 * this once every module exists, in the original source order.
 *   [M14 P9.3] */
export function initWiring() {
    /* ─────────────────────────────────────────
       Notes & To-Do Panel Logic
       ───────────────────────────────────────── */

    // Tab switching
    notesTabs.forEach(tab => {
        tab.addEventListener('click', () => {
            notesTabs.forEach(t => t.classList.remove('active'));
            notesTabContents.forEach(c => c.classList.remove('active'));
            tab.classList.add('active');
            const tabName = tab.dataset.tab;
            const content = document.getElementById(tabName === 'notes' ? 'notes-content' : 'todo-content');
            if (content) content.classList.add('active');
        });
    });

    // Toggle on button click
    if (notesBtn) {
        notesBtn.addEventListener('click', () => {
            const isOpen = notesPanel && notesPanel.getAttribute('aria-hidden') === 'false';
            if (isOpen) closeNotesPanel();
            else openNotesPanel();
        });
    }
    if (notesClose) notesClose.addEventListener('click', closeNotesPanel);
    if (notesMinimize) notesMinimize.addEventListener('click', closeNotesPanel);
}
