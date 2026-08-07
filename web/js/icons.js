/* ---------------------------------------------------------------------------
 * icons.js - the interface icon set.
 *
 * Emoji are not an icon system. They render differently on every platform,
 * they ignore currentColor so they cannot follow a theme or a disabled state,
 * they sit on a different baseline than the text beside them, and a screen
 * reader announces "wastebasket" where the button means "delete note". Two of
 * the ones this replaces carried an invisible variation selector, which is a
 * silent trap in any edit.
 *
 * Every icon is one 24x24 stroke path in the same style as the header icons
 * already in index.html. Sizing is in em, so an icon scales with the text it
 * labels, and the stroke is currentColor, so it follows the theme for free.
 *
 * Accessibility contract:
 *   - decorative next to a visible label -> aria-hidden, the default here
 *   - the only content of a control      -> pass { label }, which sets
 *                                           role="img" and an accessible name
 *   [M14 P10.4]
 * ------------------------------------------------------------------------- */

const NS = 'http://www.w3.org/2000/svg';

/* Path data only. Anything shared - viewBox, stroke width, linecaps - is
 * applied once in svgIcon(), so the set cannot drift into fifteen slightly
 * different stroke weights. */
export const ICON_PATHS = Object.freeze({
    check:    ['M20 6 9 17l-5-5'],
    close:    ['M18 6 6 18', 'M6 6l12 12'],
    clock:    ['M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z', 'M12 6v6l4 2'],
    trash:    ['M3 6h18', 'M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2',
               'M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6', 'M10 11v6', 'M14 11v6'],
    pin:      ['M12 17v5', 'M9 3h6l-1 6 3 3v2H7v-2l3-3-1-6z'],
    bell:     ['M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9', 'M13.7 21a2 2 0 0 1-3.4 0'],
    plus:     ['M12 5v14', 'M5 12h14'],
    repeat:   ['M17 2l4 4-4 4', 'M3 11V9a4 4 0 0 1 4-4h14', 'M7 22l-4-4 4-4',
               'M21 13v2a4 4 0 0 1-4 4H3'],
    copy:     ['M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1z',
               'M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1'],
    warn:     ['M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
               'M12 9v4', 'M12 17h.01'],
    calendar: ['M6 4h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
               'M16 2v4', 'M8 2v4', 'M4 10h16'],
    horizon:  ['M12 3v2', 'M5.6 5.6l1.4 1.4', 'M3 12h2', 'M19 12h2', 'M18.4 5.6L17 7',
               'M7 17a5 5 0 0 1 10 0', 'M3 21h18'],
    search:   ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z', 'M21 21l-4.35-4.35'],
    command:  ['M6 3a3 3 0 0 1 3 3v12a3 3 0 1 1-3-3h12a3 3 0 1 1-3 3V6a3 3 0 1 1 3 3H6a3 3 0 0 1 0-6z'],
});

/**
 * Build an icon element.
 *
 * @param {string} name  key of ICON_PATHS
 * @param {object} [opts]
 * @param {string} [opts.label]  accessible name; omit for decorative icons
 * @param {string} [opts.class]  extra class names
 * @param {string} [opts.size]   any CSS length, default 1em
 */
export function svgIcon(name, opts = {}) {
    const paths = ICON_PATHS[name];
    if (!paths) {
        // Loud but harmless: a typo should be visible in the console rather
        // than silently rendering an empty box in the UI.
        console.warn('[icons] unknown icon "' + name + '" - see ICON_PATHS in js/icons.js');
    }
    const size = opts.size || '1em';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', size);
    svg.setAttribute('height', size);
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('focusable', 'false');
    svg.setAttribute('class', 'ui-icon' + (opts.class ? ' ' + opts.class : ''));
    if (opts.label) {
        svg.setAttribute('role', 'img');
        svg.setAttribute('aria-label', opts.label);
    } else {
        svg.setAttribute('aria-hidden', 'true');
    }
    for (const d of (paths || [])) {
        const p = document.createElementNS(NS, 'path');
        p.setAttribute('d', d);
        svg.appendChild(p);
    }
    return svg;
}

/** Names, for tests and for the icon audit. */
export function iconNames() {
    return Object.keys(ICON_PATHS);
}
