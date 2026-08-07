// ---------------------------------------------------------------------------
// Unit tests for web/js/markdown.js.                                 [M14 P6]
//
// Run with:  node web/js/markdown.test.mjs
//
// There is no browser here, so the file installs a deliberately small DOM shim
// first and imports the renderer afterwards. The shim covers exactly what the
// renderer touches; it is not a DOM implementation, and layout-dependent
// behaviour (scrolling, widths) is covered by the headless browser probe
// instead.
// ---------------------------------------------------------------------------

class TextNode {
	constructor(text) {
		this.nodeType = 3
		this.data = String(text)
		this.childNodes = []
	}
	get textContent() {
		return this.data
	}
}

class Element {
	constructor(tag) {
		this.nodeType = 1
		this.tagName = String(tag).toUpperCase()
		this.childNodes = []
		this.attributes = {}
		this.style = {}
		this._className = ""
		this._listeners = {}
		this.classList = {
			add: c => {
				const set = new Set(this._className.split(/\s+/).filter(Boolean))
				set.add(c)
				this._className = Array.from(set).join(" ")
			},
			remove: c => {
				this._className = this._className
					.split(/\s+/)
					.filter(x => x && x !== c)
					.join(" ")
			},
			contains: c => this._className.split(/\s+/).indexOf(c) !== -1,
		}
	}
	get className() {
		return this._className
	}
	set className(v) {
		this._className = String(v)
	}
	appendChild(node) {
		if (node && node.nodeType === 11) {
			node.childNodes.slice().forEach(c => this.appendChild(c))
			node.childNodes.length = 0
			return node
		}
		this.childNodes.push(node)
		return node
	}
	removeChild(node) {
		const i = this.childNodes.indexOf(node)
		if (i !== -1) this.childNodes.splice(i, 1)
		return node
	}
	setAttribute(k, v) {
		this.attributes[k] = String(v)
	}
	getAttribute(k) {
		return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null
	}
	addEventListener(type, fn) {
		;(this._listeners[type] = this._listeners[type] || []).push(fn)
	}
	dispatch(type) {
		;(this._listeners[type] || []).forEach(fn => fn({ type }))
	}
	get lastChild() {
		return this.childNodes[this.childNodes.length - 1] || null
	}
	get children() {
		return this.childNodes.filter(n => n.nodeType === 1)
	}
	get textContent() {
		return this.childNodes.map(n => n.textContent).join("")
	}
	set textContent(v) {
		this.childNodes = []
		if (v !== "") this.childNodes.push(new TextNode(v))
	}
	querySelectorAll(sel) {
		const out = []
		const wantClass = sel.startsWith(".")
		const needle = wantClass ? sel.slice(1) : sel.toUpperCase()
		const walk = node => {
			node.childNodes.forEach(c => {
				if (c.nodeType !== 1) return
				if (wantClass ? c.classList.contains(needle) : c.tagName === needle) out.push(c)
				walk(c)
			})
		}
		walk(this)
		return out
	}
	querySelector(sel) {
		return this.querySelectorAll(sel)[0] || null
	}
}

class Fragment extends Element {
	constructor() {
		super("#fragment")
		this.nodeType = 11
	}
}

globalThis.document = {
	createElement: tag => new Element(tag),
	createTextNode: t => new TextNode(t),
	createDocumentFragment: () => new Fragment(),
}

// Node 24 exposes globalThis.navigator as a getter-only accessor, so a plain
// assignment throws. defineProperty is the only way to swap it.
function setNavigator(value) {
	Object.defineProperty(globalThis, "navigator", {
		value: value,
		configurable: true,
		writable: true,
	})
}
setNavigator(undefined)

const md = await import("./markdown.js")
const { render, renderInline, splitBlocks, repairForStream } = md

// --- tiny harness ----------------------------------------------------------
let passed = 0
const failures = []
function test(name, fn) {
	try {
		fn()
		passed++
	} catch (err) {
		failures.push(name + ": " + (err && err.message ? err.message : String(err)))
	}
}
function eq(actual, expected, what) {
	const a = JSON.stringify(actual)
	const b = JSON.stringify(expected)
	if (a !== b) throw new Error((what || "value") + " expected " + b + " got " + a)
}
function ok(cond, what) {
	if (!cond) throw new Error(what || "expected truthy")
}

// Readable serialiser, used only for assertions.
function html(node) {
	if (node.nodeType === 3) return node.data
	const tag = node.tagName.toLowerCase()
	const cls = node.className ? ' class="' + node.className + '"' : ""
	const attrs = Object.keys(node.attributes)
		.filter(k => k !== "class")
		.map(k => " " + k + '="' + node.attributes[k] + '"')
		.join("")
	const inner = node.childNodes.map(html).join("")
	return "<" + tag + cls + attrs + ">" + inner + "</" + tag + ">"
}
function inline(src) {
	const host = new Element("span")
	host.appendChild(renderInline(src))
	return host.childNodes.map(html).join("")
}
function renderToHost(src, streaming) {
	const host = new Element("span")
	render(host, src, !!streaming)
	return host
}
function shape(host) {
	return host.children
		.map(n => n.tagName.toLowerCase() + (n.className ? "." + n.className : ""))
		.join(" ")
}

// === inline ================================================================

test("01 bold", () => eq(inline("a **b** c"), "a <strong>b</strong> c"))
test("02 italic", () => eq(inline("a *b* c"), "a <em>b</em> c"))
test("03 underscore emphasis at a word boundary", () =>
	eq(inline("say _hello_ now"), "say <em>hello</em> now"))
test("04 intraword underscores stay literal", () => {
	eq(inline("snake_case_name"), "snake_case_name")
	eq(inline("data.query_type and session_id"), "data.query_type and session_id")
})
test("05 strikethrough", () => eq(inline("~~gone~~"), "<del>gone</del>"))
test("06 code span beats emphasis", () =>
	eq(inline("`**not bold**`"), "<code>**not bold**</code>"))
test("07 code span keeps a windows path", () =>
	eq(inline("`C:\\Users\\me\\notes.txt`"), "<code>C:\\Users\\me\\notes.txt</code>"))
test("08 windows path outside code keeps its backslashes", () =>
	eq(inline("C:\\Users\\ayush_lr8ru2y\\Desktop"), "C:\\Users\\ayush_lr8ru2y\\Desktop"))
test("09 backslash escapes punctuation only", () => {
	eq(inline("\\*not em\\*"), "*not em*")
	eq(inline("a \\| b"), "a | b")
})
test("10 safe link", () =>
	eq(
		inline("[Google](https://google.com)"),
		'<a href="https://google.com" target="_blank" rel="noopener noreferrer">Google</a>',
	))
test("11 javascript: url renders as literal text", () => {
	const out = inline("[x](javascript:alert(1))")
	ok(out.indexOf("<a") === -1, "no anchor for an unsafe scheme")
	ok(out.indexOf("javascript:alert(1)") !== -1, "shows what was suggested")
})
test("12 mailto is allowed", () =>
	ok(inline("[mail](mailto:a@b.com)").indexOf('href="mailto:a@b.com"') !== -1))
test("13 autolink drops trailing sentence punctuation", () => {
	const out = inline("see https://x.dev/a. ok")
	ok(out.indexOf('href="https://x.dev/a"') !== -1, "url without the full stop")
	ok(out.indexOf("</a>. ok") !== -1, "full stop stays outside the link")
})
test("14 raw html is literal text, never markup", () => {
	const host = renderToHost('<img src=x onerror="boom"> and <script>bad()</script>', false)
	eq(host.querySelectorAll("img").length, 0, "no img element")
	eq(host.querySelectorAll("script").length, 0, "no script element")
	ok(host.textContent.indexOf("<img src=x") !== -1, "shown as text")
})
test("15 an image renders as a link, never an img", () => {
	const out = inline("![alt](https://x.dev/a.png)")
	ok(out.indexOf("<img") === -1, "no img element")
	ok(out.indexOf('<a href="https://x.dev/a.png"') !== -1, "rendered as a link")
})

// === blocks =================================================================

test("16 headings h1..h6, seven hashes is not a heading", () => {
	for (let n = 1; n <= 6; n++) {
		const host = renderToHost("#".repeat(n) + " Title", false)
		eq(shape(host), "h" + n, "level " + n)
	}
	eq(shape(renderToHost("####### Title", false)), "p")
})
test("17 bullet list", () => {
	const host = renderToHost("- one\n- two", false)
	eq(shape(host), "ul")
	eq(host.querySelectorAll("li").length, 2)
})
test("18 nested list", () => {
	const host = renderToHost("- one\n  - deeper\n- two", false)
	const top = host.children[0]
	eq(top.children.length, 2, "two top-level items")
	eq(top.children[0].querySelectorAll("li").length, 1, "one nested item")
})
test("19 ordered list keeps its start number", () => {
	const host = renderToHost("3. three\n4. four", false)
	eq(host.children[0].tagName, "OL")
	eq(host.children[0].getAttribute("start"), "3")
})
test("20 blockquote", () => {
	const host = renderToHost("> quoted **text**", false)
	eq(shape(host), "blockquote")
	eq(host.querySelectorAll("strong").length, 1)
})
test("21 thematic break", () => eq(shape(renderToHost("---", false)), "hr"))
test("22 fenced code keeps its language and exact body", () => {
	const host = renderToHost('```python\nprint("hi")\n```', false)
	eq(shape(host), "div.md-code")
	eq(host.querySelector(".md-code-lang").textContent, "python")
	eq(host.querySelectorAll("CODE")[0].textContent, 'print("hi")')
})
test("23 tilde fences work too", () =>
	eq(shape(renderToHost("~~~\nx\n~~~", false)), "div.md-code"))
test("24 a longer fence is not closed by a shorter one", () => {
	const host = renderToHost("````\n```\ninner\n```\n````", false)
	eq(shape(host), "div.md-code")
	eq(host.querySelectorAll("CODE")[0].textContent, "```\ninner\n```")
})
test("25 table with alignment", () => {
	const host = renderToHost("| a | b |\n|:--|--:|\n| 1 | 2 |", false)
	eq(shape(host), "div.md-table-wrap")
	const ths = host.querySelectorAll("TH")
	eq(ths.length, 2)
	eq(ths[0].style.textAlign, "left")
	eq(ths[1].style.textAlign, "right")
	eq(host.querySelectorAll("TD").length, 2)
})
test("26 block order is preserved", () => {
	const host = renderToHost("# H\n\ntext\n\n- a\n\n```js\nx\n```\n\n---", false)
	eq(shape(host), "h1 p ul div.md-code hr")
})
test("27 a paragraph does not swallow the list that follows it", () => {
	eq(shape(renderToHost("intro:\n- a\n- b", false)), "p ul")
})

// === streaming repair =======================================================

test("28 dangling bold is dropped, not rendered", () => {
	const host = renderToHost("Yes Sir, **Sir", true)
	ok(host.textContent.indexOf("*") === -1, "no stray asterisk on screen")
	eq(host.querySelectorAll("strong").length, 0, "not bold yet")
	ok(host.textContent.indexOf("Sir") !== -1, "text is kept")
})
test("29 dangling italic and code markers are dropped", () => {
	eq(repairForStream("a *hello").indexOf("*"), -1)
	eq(repairForStream("path `C:\\Use").indexOf("`"), -1)
	ok(repairForStream("path `C:\\Use").indexOf("C:\\Use") !== -1, "text kept")
})
test("30 half-written link shows its label only", () => {
	const out = repairForStream("see [Google](ht")
	ok(out.indexOf("](") === -1, "no half link syntax")
	ok(out.indexOf("Google") !== -1, "label kept")
})
test("31 an unclosed fence is closed with its own marker", () => {
	const host = renderToHost("```python\nprint(1)", true)
	eq(shape(host), "div.md-code")
	ok(host.textContent.indexOf("```") === -1, "no backtick line on screen")
	eq(host.querySelectorAll("CODE")[0].textContent, "print(1)")
})
test("32 inline backticks do not fake a fence", () => {
	const host = renderToHost("use `npm i` now", true)
	eq(shape(host), "p")
	eq(host.querySelectorAll("CODE").length, 1)
})
test("33 intraword underscore is never treated as dangling", () =>
	eq(repairForStream("call session_id now"), "call session_id now"))
test("34 unchanged earlier blocks keep their DOM node", () => {
	const host = new Element("span")
	render(host, "# Title\n\nfirst para\n\nsecond", true)
	const firstNode = host.children[0]
	const secondNode = host.children[1]
	render(host, "# Title\n\nfirst para\n\nsecond para grew", true)
	ok(host.children[0] === firstNode, "heading node reused")
	ok(host.children[1] === secondNode, "untouched paragraph reused")
	eq(host.children[2].textContent, "second para grew")
})
test("35 copy button appears only once the fence can no longer change", () => {
	const streaming = renderToHost("```js\nlet a = 1;", true)
	eq(streaming.querySelectorAll(".md-code-copy").length, 0, "none while open")
	const mid = renderToHost("```js\nlet a = 1;\n```\n\nmore text", true)
	eq(mid.querySelectorAll(".md-code-copy").length, 1, "earlier block is settled")
	const done = renderToHost("```js\nlet a = 1;\n```", false)
	eq(done.querySelectorAll(".md-code-copy").length, 1, "present when finished")
})
test("36 copy copies the exact code, not the fence", () => {
	let copied = null
	setNavigator({ clipboard: { writeText: t => { copied = t; return Promise.resolve() } } })
	const host = renderToHost('```js\nlet a = 1;\nconsole.log(a);\n```', false)
	host.querySelector(".md-code-copy").dispatch("click")
	eq(copied, "let a = 1;\nconsole.log(a);")
	setNavigator(undefined)
})
test("37 render never throws and never leaves the answer blank", () => {
	const nasty = ["", "   ", "```", "|", "|---|", "[", "](", "***", "~~", "#", "- ", "> "]
	nasty.forEach(src => {
		const host = new Element("span")
		render(host, src, true)
		render(host, src, false)
	})
})
test("38 empty input renders nothing at all", () => {
	const host = renderToHost("", false)
	eq(host.childNodes.length, 0)
})
test("39 a torture reply produces the expected block sequence", () => {
	const src = [
		"# Demo",
		"",
		"Some **bold** and `code`.",
		"",
		"```python",
		'print("hi")',
		"```",
		"",
		"- one",
		"  - nested",
		"",
		"1. first",
		"2. second",
		"",
		"| a | b |",
		"|---|---|",
		"| 1 | 2 |",
		"",
		"> quoted",
		"",
		"tail",
		"",
		"---",
	].join("\n")
	eq(shape(renderToHost(src, false)), "h1 p div.md-code ul ol div.md-table-wrap blockquote p hr")
})
test("40 streaming chunk by chunk never shows raw syntax", () => {
	const full = "## Plan\n\nStep **one** and `code`.\n\n```js\nlet a = 1;\n```\n\n- done"
	const host = new Element("span")
	for (let i = 1; i <= full.length; i++) {
		render(host, full.slice(0, i), true)
		const text = host.textContent
		ok(text.indexOf("```") === -1, "fence marker leaked at " + i)
		ok(text.indexOf("**") === -1, "dangling bold leaked at " + i)
		ok(text.indexOf("## ") === -1, "heading marker leaked at " + i)
	}
	render(host, full, false)
	eq(shape(host), "h2 p div.md-code ul")
})
test("41 splitBlocks keeps raw text for the diff", () => {
	const blocks = splitBlocks("# A\n\nbody")
	eq(blocks.length, 2)
	eq(blocks[0].raw, "# A")
	eq(blocks[1].raw, "body")
})

// --- report -----------------------------------------------------------------
failures.forEach(f => console.log("FAIL " + f))
console.log("RESULT " + passed + "/" + (passed + failures.length) + " passed")
if (failures.length) process.exitCode = 1
