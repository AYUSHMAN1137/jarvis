// ---------------------------------------------------------------------------
// Streaming-safe markdown renderer for assistant replies.            [M14 P6]
//
// Why hand-written instead of a library: the reply arrives one chunk at a time,
// so the renderer is handed a *partial* document sixty times a second. Every
// off-the-shelf parser happily renders "**Sir" as a literal asterisk pair and
// then, one chunk later, as bold - which reads as flickering. This renderer
// repairs the partial document before parsing it, so a construct appears once,
// in its final form, and never switches.
//
// Hard rule: no innerHTML anywhere in this file. Every piece of model output
// reaches the DOM through textContent, which makes injection structurally
// impossible rather than merely filtered.
//
// Supported: ATX headings, fenced + indented code, bullet and ordered lists
// (two levels), blockquotes, thematic breaks, tables with alignment, strong,
// em, del, inline code, links, bare autolinks.
// Deliberately not supported: reference links, footnotes, raw HTML, real
// images (rendered as links), task checkboxes, math, setext headings.
// ---------------------------------------------------------------------------

// Anything else - javascript:, data:, vbscript: - renders as literal text.
const SAFE_SCHEME = /^(https?:|mailto:)/i

// Backslash escapes apply to punctuation only. This is what keeps
// C:\Users\name\Desktop intact: \U is not an escape, so it stays literal.
const ESCAPABLE = "\\`*_{}[]()#+-.!~>|"

const RE_FENCE = /^ {0,3}(`{3,}|~{3,})[ \t]*([A-Za-z0-9_+#.-]*)[ \t]*$/
const RE_HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$/
const RE_HR = /^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$/
const RE_QUOTE = /^ {0,3}>[ \t]?(.*)$/
const RE_BULLET = /^([ \t]*)([-*+])[ \t]+(.*)$/
const RE_ORDERED = /^([ \t]*)(\d{1,9})[.)][ \t]+(.*)$/
const RE_TABLE_DELIM = /^[ \t]*\|?[ \t]*:?-{1,}:?[ \t]*(\|[ \t]*:?-{1,}:?[ \t]*)*\|?[ \t]*$/
const RE_BLANK = /^[ \t]*$/
const RE_INDENT_CODE = /^(?: {4}|\t)(.*)$/
const RE_AUTOLINK = /(https?:\/\/[^\s<>"')\]]+)/g

// ===========================================================================
// Small helpers
// ===========================================================================

function isWordChar(ch) {
	return !!ch && /[0-9A-Za-z_]/.test(ch)
}

function countRun(text, i, ch) {
	let n = 0
	while (text[i + n] === ch) n++
	return n
}

function expandTabs(line) {
	return line.replace(/\t/g, "    ")
}

// ===========================================================================
// Inline rendering
// ===========================================================================

// A bare URL in prose should be clickable, but the sentence's punctuation is
// not part of it: "see https://x.dev." must not link the full stop. Unbalanced
// closing brackets are trimmed for the same reason.
function appendTextWithAutolinks(parent, text) {
	if (!text) return
	let last = 0
	RE_AUTOLINK.lastIndex = 0
	let m
	while ((m = RE_AUTOLINK.exec(text)) !== null) {
		let url = m[0]
		let trimmed = 0
		while (url.length > 0 && ".,;:!?".indexOf(url[url.length - 1]) !== -1) {
			url = url.slice(0, -1)
			trimmed++
		}
		if (url.length === 0) continue
		if (m.index > last) {
			parent.appendChild(document.createTextNode(text.slice(last, m.index)))
		}
		parent.appendChild(buildLink(url, url))
		last = m.index + m[0].length - trimmed
	}
	if (last < text.length) {
		parent.appendChild(document.createTextNode(text.slice(last)))
	}
}

function buildLink(href, label) {
	const a = document.createElement("a")
	a.setAttribute("href", href)
	a.setAttribute("target", "_blank")
	// noopener is the security half; noreferrer keeps the assistant's page out
	// of whatever the model just linked to.
	a.setAttribute("rel", "noopener noreferrer")
	a.textContent = label
	return a
}

// Finds the closing run for a code span: same character, same run length.
// Counting backticks would break on ``a ` b`` and on longer fences.
function findCodeClose(text, from, run) {
	let i = from
	while (i < text.length) {
		if (text[i] === "`") {
			const n = countRun(text, i, "`")
			if (n === run) return i
			i += n
			continue
		}
		i++
	}
	return -1
}

// Finds a closing emphasis marker, skipping escaped characters and code spans
// so that `a * b` cannot close an emphasis opened outside it.
function findCloseMarker(text, from, marker) {
	const ch = marker[0]
	let i = from
	while (i < text.length) {
		const c = text[i]
		if (c === "\\" && ESCAPABLE.indexOf(text[i + 1]) !== -1) {
			i += 2
			continue
		}
		if (c === "`") {
			const run = countRun(text, i, "`")
			const close = findCodeClose(text, i + run, run)
			i = close === -1 ? i + run : close + run
			continue
		}
		if (c === ch) {
			const run = countRun(text, i, ch)
			if (run >= marker.length) {
				const after = text[i + marker.length]
				// An underscore only closes at a word boundary, so session_id and
				// snake_case_name never turn italic.
				if (ch === "_" && isWordChar(after)) {
					i += run
					continue
				}
				if (text[i - 1] === " " && marker.length === 1) {
					i += run
					continue
				}
				return i
			}
			i += run
			continue
		}
		i++
	}
	return -1
}

// Parses [label](url) / ![alt](url) starting at the bracket.
function parseLink(text, start) {
	let i = start + 1
	let depth = 1
	let label = ""
	while (i < text.length && depth > 0) {
		const c = text[i]
		if (c === "\\" && ESCAPABLE.indexOf(text[i + 1]) !== -1) {
			label += text[i + 1]
			i += 2
			continue
		}
		if (c === "[") depth++
		if (c === "]") {
			depth--
			if (depth === 0) break
		}
		label += c
		i++
	}
	if (depth !== 0 || text[i + 1] !== "(") return null
	let j = i + 2
	let url = ""
	let paren = 1
	while (j < text.length) {
		const c = text[j]
		if (c === "\\" && ESCAPABLE.indexOf(text[j + 1]) !== -1) {
			url += text[j + 1]
			j += 2
			continue
		}
		if (c === "(") paren++
		if (c === ")") {
			paren--
			if (paren === 0) break
		}
		url += c
		j++
	}
	if (paren !== 0) return null
	// Drop any "title" and the <> form.
	url = url.trim().replace(/^<|>$/g, "").split(/\s+/)[0] || ""
	return { label: label, url: url, end: j + 1 }
}

/**
 * Renders inline markdown into a DocumentFragment. Text always lands in
 * textContent, so any HTML in the source shows up as visible literal text.
 */
export function renderInline(text) {
	const frag = document.createDocumentFragment()
	if (!text) return frag
	let buf = ""
	const flush = () => {
		if (buf) {
			appendTextWithAutolinks(frag, buf)
			buf = ""
		}
	}
	let i = 0
	while (i < text.length) {
		const ch = text[i]

		// Escapes win over everything.
		if (ch === "\\" && ESCAPABLE.indexOf(text[i + 1]) !== -1) {
			buf += text[i + 1]
			i += 2
			continue
		}

		// Code spans win over emphasis and links: `**not bold**` is literal.
		if (ch === "`") {
			const run = countRun(text, i, "`")
			const close = findCodeClose(text, i + run, run)
			if (close === -1) {
				buf += text.slice(i, i + run)
				i += run
				continue
			}
			flush()
			const code = document.createElement("code")
			let inner = text.slice(i + run, close)
			if (inner.length > 2 && inner[0] === " " && inner[inner.length - 1] === " ") {
				inner = inner.slice(1, -1)
			}
			code.textContent = inner
			frag.appendChild(code)
			i = close + run
			continue
		}

		// An image is rendered as a link. The model cannot be allowed to make the
		// browser fetch an arbitrary URL just by emitting markdown.
		if (ch === "!" && text[i + 1] === "[") {
			const link = parseLink(text, i + 1)
			if (link && SAFE_SCHEME.test(link.url)) {
				flush()
				frag.appendChild(buildLink(link.url, link.label || link.url))
				i = link.end
				continue
			}
		}

		if (ch === "[") {
			const link = parseLink(text, i)
			if (link) {
				if (SAFE_SCHEME.test(link.url)) {
					flush()
					const a = buildLink(link.url, "")
					a.appendChild(renderInline(link.label))
					frag.appendChild(a)
				} else {
					// Unsafe scheme: show the whole thing as text so the user can see
					// exactly what was suggested, and cannot click it.
					buf += text.slice(i, link.end)
				}
				i = link.end
				continue
			}
		}

		if (ch === "~" && text[i + 1] === "~") {
			const close = findCloseMarker(text, i + 2, "~~")
			if (close !== -1) {
				flush()
				const del = document.createElement("del")
				del.appendChild(renderInline(text.slice(i + 2, close)))
				frag.appendChild(del)
				i = close + 2
				continue
			}
		}

		if (ch === "*" || ch === "_") {
			// Intraword underscores are identifiers, not emphasis.
			const okOpen = ch === "*" || !isWordChar(text[i - 1])
			if (okOpen) {
				const run = countRun(text, i, ch)
				const marker = run >= 2 ? ch + ch : ch
				const tag = run >= 2 ? "strong" : "em"
				const inner0 = i + marker.length
				if (text[inner0] !== " " && text[inner0] !== undefined) {
					const close = findCloseMarker(text, inner0, marker)
					if (close !== -1 && close > inner0) {
						flush()
						const node = document.createElement(tag)
						node.appendChild(renderInline(text.slice(inner0, close)))
						frag.appendChild(node)
						i = close + marker.length
						continue
					}
				}
			}
		}

		buf += ch
		i++
	}
	flush()
	return frag
}

// ===========================================================================
// Block splitting
// ===========================================================================

function isTableStart(lines, i) {
	const line = lines[i]
	const next = lines[i + 1]
	return (
		!!line &&
		!!next &&
		line.indexOf("|") !== -1 &&
		RE_TABLE_DELIM.test(next) &&
		next.indexOf("|") !== -1
	)
}

// True when a line inside a paragraph actually starts a new block, which is
// what stops a paragraph from swallowing the list or fence that follows it.
function startsNewBlock(lines, i) {
	const line = lines[i]
	return (
		RE_BLANK.test(line) ||
		RE_FENCE.test(line) ||
		RE_HEADING.test(line) ||
		RE_HR.test(line) ||
		RE_QUOTE.test(line) ||
		RE_BULLET.test(line) ||
		RE_ORDERED.test(line) ||
		isTableStart(lines, i)
	)
}

function splitRow(row) {
	const cells = []
	let cur = ""
	let i = 0
	const trimmed = row.trim().replace(/^\|/, "").replace(/\|$/, "")
	while (i < trimmed.length) {
		const c = trimmed[i]
		if (c === "\\" && trimmed[i + 1] === "|") {
			cur += "|"
			i += 2
			continue
		}
		if (c === "|") {
			cells.push(cur.trim())
			cur = ""
			i++
			continue
		}
		cur += c
		i++
	}
	cells.push(cur.trim())
	return cells
}

/**
 * Splits a markdown source into blocks. Each block keeps its own `raw` text,
 * which is what the render diff compares - so an unchanged earlier block keeps
 * its existing DOM node instead of being rebuilt on every chunk.
 */
export function splitBlocks(src) {
	const lines = String(src == null ? "" : src).split("\n")
	const blocks = []
	let i = 0

	while (i < lines.length) {
		const line = lines[i]

		if (RE_BLANK.test(line)) {
			i++
			continue
		}

		const fence = line.match(RE_FENCE)
		if (fence) {
			const marker = fence[1]
			const lang = fence[2] || ""
			const body = []
			const startLine = i
			i++
			let closed = false
			while (i < lines.length) {
				const m = lines[i].match(RE_FENCE)
				if (m && m[1][0] === marker[0] && m[1].length >= marker.length && !m[2]) {
					closed = true
					i++
					break
				}
				body.push(lines[i])
				i++
			}
			blocks.push({
				type: "fence",
				lang: lang,
				code: body.join("\n"),
				closed: closed,
				raw: lines.slice(startLine, i).join("\n"),
			})
			continue
		}

		const heading = line.match(RE_HEADING)
		if (heading) {
			blocks.push({
				type: "heading",
				level: heading[1].length,
				text: heading[2],
				raw: line,
			})
			i++
			continue
		}

		if (RE_HR.test(line)) {
			blocks.push({ type: "hr", raw: line })
			i++
			continue
		}

		if (isTableStart(lines, i)) {
			const startLine = i
			const header = splitRow(lines[i])
			const align = splitRow(lines[i + 1]).map(spec => {
				const left = spec.charAt(0) === ":"
				const right = spec.charAt(spec.length - 1) === ":"
				if (left && right) return "center"
				if (right) return "right"
				if (left) return "left"
				return ""
			})
			i += 2
			const rows = []
			while (i < lines.length && !RE_BLANK.test(lines[i]) && lines[i].indexOf("|") !== -1) {
				rows.push(splitRow(lines[i]))
				i++
			}
			blocks.push({
				type: "table",
				header: header,
				align: align,
				rows: rows,
				raw: lines.slice(startLine, i).join("\n"),
			})
			continue
		}

		if (RE_QUOTE.test(line)) {
			const startLine = i
			const body = []
			while (i < lines.length && RE_QUOTE.test(lines[i])) {
				body.push(lines[i].match(RE_QUOTE)[1])
				i++
			}
			blocks.push({
				type: "quote",
				text: body.join("\n"),
				raw: lines.slice(startLine, i).join("\n"),
			})
			continue
		}

		if (RE_BULLET.test(line) || RE_ORDERED.test(line)) {
			const startLine = i
			const ordered = !RE_BULLET.test(line) && RE_ORDERED.test(line)
			const first = line.match(ordered ? RE_ORDERED : RE_BULLET)
			const start = ordered ? parseInt(first[2], 10) : 1
			const items = []
			while (i < lines.length) {
				const raw = expandTabs(lines[i])
				const bullet = raw.match(RE_BULLET)
				const num = raw.match(RE_ORDERED)
				const match = ordered ? num || bullet : bullet || num
				if (!match) {
					// A plain indented line continues the item above it.
					if (!RE_BLANK.test(raw) && /^\s+\S/.test(raw) && items.length) {
						items[items.length - 1].text += "\n" + raw.trim()
						i++
						continue
					}
					break
				}
				const indent = match[1].length
				const text = match[match.length - 1]
				const isSub = indent >= 2
				const subOrdered = match === num
				if (isSub && items.length) {
					const parent = items[items.length - 1]
					if (!parent.children) parent.children = { ordered: subOrdered, items: [] }
					parent.children.items.push({ text: text })
				} else {
					items.push({ text: text })
				}
				i++
			}
			blocks.push({
				type: "list",
				ordered: ordered,
				start: start,
				items: items,
				raw: lines.slice(startLine, i).join("\n"),
			})
			continue
		}

		if (RE_INDENT_CODE.test(line)) {
			const startLine = i
			const body = []
			while (i < lines.length && (RE_INDENT_CODE.test(lines[i]) || RE_BLANK.test(lines[i]))) {
				if (RE_BLANK.test(lines[i]) && !RE_INDENT_CODE.test(lines[i + 1] || "")) break
				body.push(expandTabs(lines[i]).slice(4))
				i++
			}
			blocks.push({
				type: "fence",
				lang: "",
				code: body.join("\n"),
				closed: true,
				raw: lines.slice(startLine, i).join("\n"),
			})
			continue
		}

		// Paragraph: runs until a blank line or the start of another block.
		const startLine = i
		const body = [line]
		i++
		while (i < lines.length && !startsNewBlock(lines, i)) {
			body.push(lines[i])
			i++
		}
		blocks.push({
			type: "para",
			text: body.join("\n"),
			raw: lines.slice(startLine, i).join("\n"),
		})
	}

	return blocks
}

// ===========================================================================
// Streaming repair
// ===========================================================================

// Returns the marker of a fence that is still open at the end of the source.
function unclosedFence(src) {
	const lines = src.split("\n")
	let open = null
	for (let i = 0; i < lines.length; i++) {
		const m = lines[i].match(RE_FENCE)
		if (!m) continue
		if (open === null) {
			open = m[1]
		} else if (m[1][0] === open[0] && m[1].length >= open.length && !m[2]) {
			open = null
		}
	}
	return open
}

// Counts marker occurrences that are neither escaped nor inside a code span,
// and returns the index of the last one.
function lastUnmatched(text, marker) {
	const ch = marker[0]
	const hits = []
	let i = 0
	while (i < text.length) {
		const c = text[i]
		if (c === "\\" && ESCAPABLE.indexOf(text[i + 1]) !== -1) {
			i += 2
			continue
		}
		if (c === "`" && ch !== "`") {
			const run = countRun(text, i, "`")
			const close = findCodeClose(text, i + run, run)
			if (close === -1) {
				i += run
				continue
			}
			i = close + run
			continue
		}
		if (c === ch) {
			const run = countRun(text, i, ch)
			if (run === marker.length) {
				// An underscore only counts at a word boundary, so snake_case_name
				// is never seen as a dangling marker.
				const boundaryOk =
					ch !== "_" || !isWordChar(text[i - 1]) || !isWordChar(text[i + run])
				if (boundaryOk) hits.push(i)
			}
			i += run
			continue
		}
		i++
	}
	return hits.length % 2 === 1 ? hits[hits.length - 1] : -1
}

// Removes an odd trailing marker so a half-typed construct renders as plain
// text now and as the real thing once its closer arrives - one transition,
// not two.
export function dropDangling(text, marker) {
	const at = lastUnmatched(text, marker)
	if (at === -1) return text
	return text.slice(0, at) + text.slice(at + marker.length)
}

export function repairTrailingInline(text) {
	let out = text

	// A link whose URL is still being typed shows its label only. "[Google" on
	// its own is left alone: it is already literal text.
	const openParen = out.lastIndexOf("](")
	if (openParen !== -1 && out.indexOf(")", openParen) === -1) {
		const openBracket = out.lastIndexOf("[", openParen)
		if (openBracket !== -1) {
			out = out.slice(0, openBracket) + out.slice(openBracket + 1, openParen)
		}
	}

	// Order matters: code first, then the longer markers before the shorter
	// ones, or "**" would be seen as two separate "*".
	out = dropDangling(out, "`")
	out = dropDangling(out, "**")
	out = dropDangling(out, "~~")
	out = dropDangling(out, "*")
	out = dropDangling(out, "_")
	return out
}

/**
 * Repairs a partial markdown document so it can be rendered without anything
 * flickering on and off as later chunks arrive.
 */
export function repairForStream(src) {
	let out = String(src == null ? "" : src)
	const open = unclosedFence(out)
	if (open) {
		// Close it with its own marker and run length. Counting backticks would
		// break on ````longer```` fences and on inline code spans.
		if (!out.endsWith("\n")) out += "\n"
		return out + open
	}
	return repairTrailingInline(out)
}

// ===========================================================================
// Block rendering
// ===========================================================================

function buildCopyButton(text) {
	const btn = document.createElement("button")
	btn.type = "button"
	btn.className = "md-code-copy"
	btn.textContent = "Copy"
	btn.setAttribute("aria-label", "Copy code")
	btn.addEventListener("click", () => {
		const done = () => {
			btn.textContent = "Copied"
			btn.classList.add("copied")
			setTimeout(() => {
				btn.textContent = "Copy"
				btn.classList.remove("copied")
			}, 1400)
		}
		const nav = typeof navigator !== "undefined" ? navigator : null
		if (nav && nav.clipboard && nav.clipboard.writeText) {
			nav.clipboard.writeText(text).then(done, () => {
				btn.textContent = "Failed"
				setTimeout(() => { btn.textContent = "Copy" }, 1400)
			})
			return
		}
		done()
	})
	return btn
}

function buildCodeBlock(block, withCopy) {
	const wrap = document.createElement("div")
	wrap.className = "md-code"

	const head = document.createElement("div")
	head.className = "md-code-head"
	const lang = document.createElement("span")
	lang.className = "md-code-lang"
	lang.textContent = block.lang || "code"
	head.appendChild(lang)
	// The button appears only once the block can no longer change, so it can
	// never copy half a snippet.
	if (withCopy) head.appendChild(buildCopyButton(block.code))
	wrap.appendChild(head)

	const pre = document.createElement("pre")
	const code = document.createElement("code")
	if (block.lang) code.className = "language-" + block.lang
	code.textContent = block.code
	pre.appendChild(code)
	wrap.appendChild(pre)
	return wrap
}

/** Renders one block object into a single DOM element. */
export function renderBlock(block) {
	switch (block.type) {
		case "heading": {
			const h = document.createElement("h" + block.level)
			h.appendChild(renderInline(block.text))
			return h
		}
		case "hr":
			return document.createElement("hr")
		case "fence":
			return buildCodeBlock(block, !!block.canCopy)
		case "quote": {
			const q = document.createElement("blockquote")
			const inner = splitBlocks(block.text)
			if (inner.length === 0) {
				q.appendChild(document.createElement("p"))
			} else {
				for (let i = 0; i < inner.length; i++) q.appendChild(renderBlock(inner[i]))
			}
			return q
		}
		case "list": {
			const list = document.createElement(block.ordered ? "ol" : "ul")
			if (block.ordered && block.start && block.start !== 1) {
				list.setAttribute("start", String(block.start))
			}
			for (let i = 0; i < block.items.length; i++) {
				const item = block.items[i]
				const li = document.createElement("li")
				li.appendChild(renderInline(item.text))
				if (item.children && item.children.items.length) {
					const sub = document.createElement(item.children.ordered ? "ol" : "ul")
					for (let j = 0; j < item.children.items.length; j++) {
						const subLi = document.createElement("li")
						subLi.appendChild(renderInline(item.children.items[j].text))
						sub.appendChild(subLi)
					}
					li.appendChild(sub)
				}
				list.appendChild(li)
			}
			return list
		}
		case "table": {
			// The wrapper is what lets a wide table scroll instead of widening the
			// bubble; it only works because every flex ancestor has min-width: 0.
			const wrap = document.createElement("div")
			wrap.className = "md-table-wrap"
			const table = document.createElement("table")
			const thead = document.createElement("thead")
			const hrow = document.createElement("tr")
			for (let i = 0; i < block.header.length; i++) {
				const th = document.createElement("th")
				if (block.align[i]) th.style.textAlign = block.align[i]
				th.appendChild(renderInline(block.header[i]))
				hrow.appendChild(th)
			}
			thead.appendChild(hrow)
			table.appendChild(thead)
			const tbody = document.createElement("tbody")
			for (let r = 0; r < block.rows.length; r++) {
				const tr = document.createElement("tr")
				for (let c = 0; c < block.rows[r].length; c++) {
					const td = document.createElement("td")
					if (block.align[c]) td.style.textAlign = block.align[c]
					td.appendChild(renderInline(block.rows[r][c]))
					tr.appendChild(td)
				}
				tbody.appendChild(tr)
			}
			table.appendChild(tbody)
			wrap.appendChild(table)
			return wrap
		}
		default: {
			const p = document.createElement("p")
			p.appendChild(renderInline(block.text))
			return p
		}
	}
}

function blockKey(block) {
	return block.type + "\u0000" + block.raw + "\u0000" + (block.canCopy ? "1" : "0")
}

/**
 * Renders `src` into `host`, reusing the DOM of blocks that have not changed.
 *
 * The host's children belong entirely to this function: it clears anything it
 * does not recognise. Callers attach the stream cursor and action chips to the
 * PARENT element, never here.
 *
 * @param host      element to render into (a .msg-stream-text span)
 * @param src       markdown source, possibly partial
 * @param streaming true while chunks are still arriving: repair is applied and
 *                  the last block is treated as unfinished
 */
export function render(host, src, streaming) {
	try {
		const source = streaming ? repairForStream(src) : String(src == null ? "" : src)
		const blocks = splitBlocks(source)
		for (let i = 0; i < blocks.length; i++) {
			blocks[i].canCopy = blocks[i].type === "fence" && (!streaming || i < blocks.length - 1)
		}

		const prev = host._mdBlocks || []
		let firstDirty = 0
		while (
			firstDirty < prev.length &&
			firstDirty < blocks.length &&
			prev[firstDirty] === blockKey(blocks[firstDirty])
		) {
			firstDirty++
		}
		// Only blocks from the first change onwards are rebuilt. Everything above
		// keeps its node, which is what keeps a long reply cheap to re-render and
		// stops text selection from being destroyed on every chunk.
		while (host.childNodes.length > firstDirty) {
			host.removeChild(host.childNodes[host.childNodes.length - 1])
		}
		for (let i = firstDirty; i < blocks.length; i++) {
			host.appendChild(renderBlock(blocks[i]))
		}
		host._mdBlocks = blocks.map(blockKey)
	} catch (err) {
		// A renderer bug must never cost the user their answer.
		console.warn("[markdown] render failed, falling back to plain text", err)
		host._mdBlocks = []
		host.textContent = String(src == null ? "" : src)
	}
}

// script.js is still a classic script until P9, so the renderer is published
// on window for it. P9: delete this and import { render } directly.
if (typeof window !== "undefined") {
	window.JarvisMarkdown = {
		render: render,
		renderInline: renderInline,
		renderBlock: renderBlock,
		splitBlocks: splitBlocks,
		repairForStream: repairForStream,
	}
}
