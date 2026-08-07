/* ---------------------------------------------------------------------------
 * geometry.js - remembering where the floating panels were put.
 *
 * The camera, reminders and notes panels can be dragged and resized, and until
 * now every reload threw that away. Worse, a panel dragged to the right of a
 * wide monitor and reopened on a laptop was simply gone: its saved position
 * was off screen with no way to reach it.
 *
 * So three things happen here, in this order, and the order is the point:
 *
 *   1. Geometry is written on drag END, not during the drag. A mousemove
 *      handler fires dozens of times a second and localStorage writes are
 *      synchronous - persisting per frame would make dragging stutter.
 *
 *   2. Everything is clamped to the viewport before it is applied, on restore
 *      AND on window resize. A stored position is a hint, never an
 *      instruction; the viewport always wins. The clamp keeps a slice of the
 *      panel's header reachable rather than merely keeping the box inside, so
 *      a panel can always be grabbed again.
 *
 *   3. A panel released near an edge snaps flush to it. Without this, "put it
 *      in the corner" leaves a two pixel gap that looks like a mistake.
 *
 * Storage is one JSON object under a single key, and every read is wrapped:
 * corrupt or foreign data must degrade to default placement, never to a
 * console full of exceptions on boot.
 *   [M14 P10.5]
 * ------------------------------------------------------------------------- */

import { FLOAT_PANEL_SELECTOR } from './config.js';
export const PANEL_GEOMETRY_KEY = 'jarvisPanels';

/** Distance from an edge, in px, within which a panel snaps flush to it. */
export const SNAP_PX = 12;

/** Smallest strip of a panel that must stay on screen, so it can be grabbed. */
const KEEP_VISIBLE = 48;

/** Read the whole store. Never throws: bad data behaves like no data. */
export function loadPanelGeometry() {
    try {
        const raw = localStorage.getItem(PANEL_GEOMETRY_KEY);
        if (!raw) return {};
        const parsed = JSON.parse(raw);
        // A JSON array or string would pass JSON.parse and then break every
        // caller that expects a lookup table.
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
        return parsed;
    } catch (e) {
        console.warn('[geometry] ignoring unreadable panel geometry', e);
        return {};
    }
}

function writeStore(store) {
    try {
        localStorage.setItem(PANEL_GEOMETRY_KEY, JSON.stringify(store));
    } catch (e) {
        // Private mode and quota errors are not worth breaking a drag over.
        console.warn('[geometry] could not save panel geometry', e);
    }
}

function keyFor(panel) {
    // The id is the only stable identity these panels have; without one there
    // is nothing to key on, and silently keying on a class would mix two
    // panels' positions together.
    return panel && panel.id ? panel.id : null;
}

/**
 * Clamp a proposed box to the viewport, then snap it to any edge it is nearly
 * touching. Pure arithmetic, so it can be reasoned about and tested directly.
 */
export function clampBox(box, view) {
    const w = Math.min(box.width, view.width);
    const h = Math.min(box.height, view.height);

    let left = Math.min(Math.max(box.left, KEEP_VISIBLE - w), view.width - KEEP_VISIBLE);
    // The top edge is different: a header dragged above the viewport can never
    // be grabbed again, so the panel may not start above zero.
    let top = Math.min(Math.max(box.top, 0), view.height - KEEP_VISIBLE);

    if (Math.abs(left) <= SNAP_PX) left = 0;
    if (Math.abs(view.width - (left + w)) <= SNAP_PX) left = view.width - w;
    if (Math.abs(top) <= SNAP_PX) top = 0;
    if (Math.abs(view.height - (top + h)) <= SNAP_PX) top = view.height - h;

    return { left: Math.round(left), top: Math.round(top), width: Math.round(w), height: Math.round(h) };
}

function viewport() {
    return { width: window.innerWidth, height: window.innerHeight };
}

function applyBox(panel, box, { withSize }) {
    panel.style.left = box.left + 'px';
    panel.style.top = box.top + 'px';
    // A stored left/top only means anything if the CSS right/bottom anchors
    // that positioned the panel by default are released.
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    if (withSize) {
        panel.style.width = box.width + 'px';
        panel.style.height = box.height + 'px';
    }
}

/**
 * Record where a panel currently is. Call this when a drag or resize ENDS.
 *
 * @param {Element} panel
 * @param {boolean} [withSize] also record width/height (resizes only)
 */
export function savePanelGeometry(panel, withSize = false) {
    const key = keyFor(panel);
    if (!key) return;
    const r = panel.getBoundingClientRect();
    if (!r.width || !r.height) return;              // hidden panel: nothing real to save
    const box = clampBox({ left: r.left, top: r.top, width: r.width, height: r.height }, viewport());
    applyBox(panel, box, { withSize: false });      // let the snap be visible immediately
    const store = loadPanelGeometry();
    const prev = store[key] || {};
    store[key] = {
        left: box.left,
        top: box.top,
        width: withSize ? box.width : prev.width,
        height: withSize ? box.height : prev.height,
    };
    writeStore(store);
}

/**
 * Put a panel back where it was left, clamped to the viewport it is opening
 * into. Safe to call on a panel that has never been moved.
 */
export function restorePanelGeometry(panel) {
    const key = keyFor(panel);
    if (!key) return false;
    const saved = loadPanelGeometry()[key];
    if (!saved || typeof saved.left !== 'number' || typeof saved.top !== 'number') return false;
    const r = panel.getBoundingClientRect();
    const box = clampBox({
        left: saved.left,
        top: saved.top,
        width: typeof saved.width === 'number' ? saved.width : r.width,
        height: typeof saved.height === 'number' ? saved.height : r.height,
    }, viewport());
    applyBox(panel, box, { withSize: typeof saved.width === 'number' });
    return true;
}

/** Pull every visible float panel back inside the current viewport. */
export function reclampAllPanels() {
    document.querySelectorAll(FLOAT_PANEL_SELECTOR).forEach((panel) => {
        const r = panel.getBoundingClientRect();
        if (!r.width || !r.height) return;          // closed panels have no geometry
        if (!panel.style.left && !panel.style.top) return;   // never moved: CSS owns it
        applyBox(panel, clampBox(
            { left: r.left, top: r.top, width: r.width, height: r.height }, viewport(),
        ), { withSize: false });
    });
}

let resizeTimer = null;

/**
 * Watch for viewport changes.
 *
 * Debounced: a window drag-resize fires continuously, and re-laying out every
 * panel on each event is both wasteful and visibly jumpy.
 */
export function initPanelGeometry() {
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(reclampAllPanels, 150);
    });
}
