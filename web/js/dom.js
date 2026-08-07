/* =====================================================================
   M14 P4: DOM builder.
   Render code used to concatenate HTML strings and assign innerHTML, which
   made every API-supplied field a potential injection point. These helpers
   build real nodes, so text is always text.

   script.js is still a classic script and duplicates el()/icon() as a shim.
   P9 removes that shim and imports from here instead.
   ===================================================================== */

/**
 * Create an element.
 *
 *   el('div', { class: 'card', dataset: { id: '7' }, text: userValue })
 *
 * Recognised props:
 *   class   -> className
 *   text    -> textContent (always escaped, by definition)
 *   dataset -> data-* attributes
 *   style   -> individual style properties
 *   on*     -> addEventListener (onClick -> 'click')
 *   true    -> boolean attribute
 *   anything else -> setAttribute
 * null / undefined / false props are skipped.
 */
/* getElementById, spelled the way the old single-file script spelled it.
 * Lives here now so every module reaches for the same helper.  [M14 P9.3] */
export function $(id) {
    return document.getElementById(id);
}

export function el(tag, props, children) {
	const node = document.createElement(tag)
	if (props) {
		for (const key in props) {
			const val = props[key]
			if (val === null || val === undefined || val === false) continue
			if (key === "class") node.className = val
			else if (key === "text") node.textContent = val
			else if (key === "dataset") {
				for (const d in val) node.dataset[d] = val[d]
			} else if (key === "style") {
				for (const p in val) node.style[p] = val[p]
			} else if (key.startsWith("on") && typeof val === "function") {
				node.addEventListener(key.slice(2).toLowerCase(), val)
			} else if (val === true) node.setAttribute(key, "")
			else node.setAttribute(key, val)
		}
	}
	if (children) {
		for (const child of [].concat(children)) {
			if (child === null || child === undefined || child === false) continue
			node.appendChild(
				typeof child === "string" ? document.createTextNode(child) : child,
			)
		}
	}
	return node
}

/**
 * A decorative glyph. Glyphs are literals owned by the UI, never user data,
 * and are hidden from assistive tech because the visible label carries meaning.
 */
export function icon(glyph) {
	return el("span", { class: "ui-glyph", "aria-hidden": "true", text: glyph })
}

/**
 * Replace a container's children in one shot.
 * Prefer this over `innerHTML = ''` followed by appends: it is a single
 * mutation, so it cannot leave a half-rendered list on screen.
 */
export function replaceChildren(parent, ...children) {
	if (!parent) return parent
	parent.replaceChildren(
		...children
			.flat()
			.filter((c) => c !== null && c !== undefined && c !== false)
			.map((c) => (typeof c === "string" ? document.createTextNode(c) : c)),
	)
	return parent
}
