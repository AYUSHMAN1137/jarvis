/* ---------------------------------------------------------------------------
 * headermenu.js - the header overflow menu.
 *
 * The header carried eight icon buttons in a row. At 480px they collided with
 * the logo, and even on a wide screen a row of eight undifferentiated glyphs
 * is a lookup puzzle rather than a toolbar. The four things used constantly -
 * history, activity, search, new chat - stay visible. The three occasional
 * ones move behind a single overflow button.
 *
 * The buttons themselves are not recreated here; the existing elements are
 * simply nested inside the menu in index.html. Every click handler that binds
 * by id keeps working untouched, so there is no second implementation of
 * "open settings" to fall out of step.
 *
 * Keyboard contract, per the menu button pattern: Enter/Space/ArrowDown open
 * and land on the first item, ArrowUp opens on the last, arrows cycle, Home
 * and End jump, Escape closes and returns focus to the trigger, Tab or a click
 * outside closes without swallowing the click.
 *   [M14 P10.1]
 * ------------------------------------------------------------------------- */

import { $ } from './dom.js';
let trigger = null;
let menu = null;

function items() {
    return menu ? [...menu.querySelectorAll('[role="menuitem"]:not([hidden])')] : [];
}

export function isHeaderMenuOpen() {
    return !!menu && !menu.hidden;
}

export function openHeaderMenu(focusIndex = 0) {
    if (!menu || !trigger) return;
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    const list = items();
    const i = focusIndex < 0 ? list.length - 1 : focusIndex;
    if (list[i]) list[i].focus();
}

export function closeHeaderMenu({ restoreFocus = true } = {}) {
    if (!isHeaderMenuOpen()) return;
    menu.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    if (restoreFocus) trigger.focus();
}

function move(delta) {
    const list = items();
    if (!list.length) return;
    const at = list.indexOf(document.activeElement);
    const next = (at + delta + list.length) % list.length;
    list[next].focus();
}

export function initHeaderMenu() {
    trigger = $('header-more-btn');
    menu = $('header-more-menu');
    if (!trigger || !menu) return;

    trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        if (isHeaderMenuOpen()) closeHeaderMenu();
        else openHeaderMenu(0);
    });

    trigger.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openHeaderMenu(0);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            openHeaderMenu(-1);
        }
    });

    menu.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
        else if (e.key === 'Home') { e.preventDefault(); (items()[0] || trigger).focus(); }
        else if (e.key === 'End') { e.preventDefault(); (items().pop() || trigger).focus(); }
        else if (e.key === 'Escape') {
            // Stop here: Escape also stops a running turn.
            e.preventDefault();
            e.stopPropagation();
            closeHeaderMenu();
        } else if (e.key === 'Tab') {
            // Tabbing out is a dismissal, but the focus move itself must stand.
            closeHeaderMenu({ restoreFocus: false });
        }
    });

    // An item does its own work through its existing handler; all this adds is
    // dismissal, so the menu is never left hanging over the panel it opened.
    menu.addEventListener('click', (e) => {
        if (e.target.closest('[role="menuitem"]')) closeHeaderMenu();
    });

    document.addEventListener('click', (e) => {
        if (!isHeaderMenuOpen()) return;
        if (menu.contains(e.target) || trigger.contains(e.target)) return;
        closeHeaderMenu({ restoreFocus: false });
    });
}
