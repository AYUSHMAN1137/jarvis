"""Standalone test harness to exercise JARVIS agent tools without a GUI/LLM.
Run from the jarvis project root: python3 _agent_test_harness.py
"""
import os, sys, json, traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

results = {"pass": [], "fail": [], "info": []}

def ok(name, detail=""):
    results["pass"].append((name, detail))
    print(f"PASS | {name} | {detail}")

def bad(name, detail=""):
    results["fail"].append((name, detail))
    print(f"FAIL | {name} | {detail}")

def info(name, detail=""):
    results["info"].append((name, detail))
    print(f"INFO | {name} | {detail}")

# --- 1. Import registry + all tools ---
try:
    from app.services.agent.tool_registry import registry
    from app.services.agent.tools import load_all_tools
    load_all_tools()
    names = registry.names()
    ok("import+register tools", f"{len(names)} tools: {sorted(names)}")
except Exception as e:
    bad("import+register tools", repr(e)); traceback.print_exc(); print(json.dumps({"fatal":True})); sys.exit(1)

# --- 2. OpenAI schema generation for every tool ---
try:
    schemas = registry.openai_schemas()
    assert isinstance(schemas, list) and len(schemas) == len(names)
    for s in schemas:
        assert s["type"] == "function"
        assert "name" in s["function"] and "parameters" in s["function"]
    ok("openai_schemas", f"{len(schemas)} valid schemas")
except Exception as e:
    bad("openai_schemas", repr(e))

# --- 3. Dangerous flags ---
try:
    dangerous = sorted([n for n in names if registry.is_dangerous(n)])
    info("dangerous tools", str(dangerous))
except Exception as e:
    bad("dangerous flags", repr(e))

# --- helper to run a tool through the registry (same path the agent uses) ---
def run(name, args):
    return registry.execute(name, args)

# --- 4. WEB TOOLS (pure python, fully testable) ---
from app.services.agent import action_sink

def test_web():
    action_sink.reset()
    r = run("open_website", {"target": "github"})
    b = action_sink.collect()
    info("open_website github", f"{r} | wopens={b['wopens']}")
    if b["wopens"] and b["wopens"][0] == "https://github.com": ok("open_website known-site", b["wopens"][0])
    else: bad("open_website known-site", str(b["wopens"]))

    action_sink.reset()
    run("open_website", {"target": "amazon.in"})
    b = action_sink.collect()
    if b["wopens"] and b["wopens"][0] == "https://amazon.in": ok("open_website domain", b["wopens"][0])
    else: bad("open_website domain", str(b["wopens"]))

    action_sink.reset()
    run("open_website", {"target": "the news website"})
    b = action_sink.collect()
    info("open_website fuzzy 'the news website'", str(b["wopens"]))

    action_sink.reset()
    run("open_website", {"target": "192.168.1.1"})
    b = action_sink.collect()
    info("open_website ip 192.168.1.1", str(b["wopens"]))

    action_sink.reset()
    run("open_website", {"target": "localhost:8000"})
    b = action_sink.collect()
    info("open_website localhost:8000", str(b["wopens"]))

    action_sink.reset()
    r = run("play_on_youtube", {"query": "despacito"})
    b = action_sink.collect()
    url = b["plays"][0] if b["plays"] else ""
    if url == "https://www.youtube.com/results?search_query=despacito": ok("play_on_youtube", url)
    else: bad("play_on_youtube", f"{r} | {url}")

    action_sink.reset()
    r = run("search_google", {"query": "how to tie a tie"})
    b = action_sink.collect()
    url = b["googlesearches"][0] if b["googlesearches"] else ""
    if url == "https://www.google.com/search?q=how%20to%20tie%20a%20tie": ok("search_google", url)
    else: bad("search_google", f"{r} | {url}")

    action_sink.reset()
    r = run("search_youtube", {"query": "python web scraping"})
    b = action_sink.collect()
    url = b["youtubesearches"][0] if b["youtubesearches"] else ""
    if url == "https://www.youtube.com/results?search_query=python%20web%20scraping": ok("search_youtube", url)
    else: bad("search_youtube", f"{r} | {url}")

test_web()

# --- 5. CONTENT: generate_image URL (pure python) ---
def test_image():
    action_sink.reset()
    r = run("generate_image", {"prompt": "a futuristic city at night"})
    b = action_sink.collect()
    url = b["images"][0] if b["images"] else ""
    if url.startswith("https://image.pollinations.ai/prompt/") and "model=flux" in url:
        ok("generate_image url", url[:90])
    else:
        bad("generate_image url", f"{r} | {url[:120]}")
    # write_content needs groq deps -> expect graceful error
    r2 = run("write_content", {"prompt": "a poem about the stars"})
    info("write_content (no groq dep)", r2)
test_image()

# --- 6. FILE TOOLS (run on server fs; fully testable on Linux) ---
def test_files():
    base = "/tmp/jarvis_test"
    os.makedirs(base, exist_ok=True)
    fpath = os.path.join(base, "todo.txt")
    r = run("write_file", {"path": fpath, "content": "buy milk"})
    if os.path.isfile(fpath) and open(fpath).read() == "buy milk": ok("write_file", r)
    else: bad("write_file", r)
    r = run("read_file", {"path": fpath})
    if "buy milk" in r: ok("read_file", r[:40])
    else: bad("read_file", r)
    r = run("list_directory", {"path": base})
    if "todo.txt" in r: ok("list_directory", "listed ok")
    else: bad("list_directory", r)
    # open_file uses os.startfile (Windows only) -> expect graceful error on Linux
    r = run("open_file", {"path": fpath})
    info("open_file (windows-only api)", r)
    # delete_file is dangerous; call directly through registry (gating is in loop)
    r = run("delete_file", {"path": fpath})
    if not os.path.exists(fpath) and r.startswith("Deleted"): ok("delete_file", r)
    else: bad("delete_file", r)
    # error path
    r = run("read_file", {"path": "/tmp/does_not_exist_xyz.txt"})
    if r.startswith("ERROR"): ok("read_file missing -> error", r[:40])
    else: bad("read_file missing", r)
test_files()

# --- 7. DESKTOP/SYSTEM TOOLS: must degrade gracefully (no crash) on headless ---
def test_graceful():
    for name, args in [
        ("open_application", {"app_name": "notepad"}),
        ("close_application", {"app_name": "notepad"}),
        ("type_text", {"text": "hello", "press_enter": True}),
        ("press_hotkey", {"keys": "ctrl+s"}),
        ("mouse_click", {"x": 100, "y": 100, "button": "left"}),
        ("scroll", {"amount": -5}),
        ("take_screenshot", {}),
        ("set_clipboard", {"text": "jarvis"}),
        ("focus_window", {"title": "Notepad"}),
        ("window_action", {"title": "Notepad", "action": "minimize"}),
        ("set_volume", {"level": 50}),
        ("mute_volume", {"mute": True}),
        ("set_brightness", {"level": 30}),
        ("media_control", {"action": "playpause"}),
        ("lock_screen", {}),
        ("cancel_power_action", {}),
    ]:
        try:
            r = run(name, args)
            if isinstance(r, str): ok(f"graceful:{name}", r[:70])
            else: bad(f"graceful:{name}", f"non-str: {type(r)}")
        except Exception as e:
            bad(f"graceful:{name}", f"RAISED {e!r}")
test_graceful()

# --- 8. GOOGLE TOOLS: not configured -> must return ERROR string, not crash ---
def test_google():
    for name, args in [
        ("gmail_inbox", {}), ("gmail_unread", {}),
        ("calendar_list", {"scope": "today"}), ("calendar_search", {"query": "Dentist"}),
        ("calendar_create", {"details": "Team Sync tomorrow 10am"}),
        ("calendar_delete", {"query": "Team Sync"}),
        ("drive_search", {"query": "Invoice"}), ("drive_list", {}),
        ("drive_upload", {"request": "/tmp/x.pdf"}),
    ]:
        try:
            r = run(name, args)
            if isinstance(r, str): ok(f"graceful:{name}", r[:60])
            else: bad(f"graceful:{name}", "non-str")
        except Exception as e:
            bad(f"graceful:{name}", f"RAISED {e!r}")
test_google()

# --- 9. Registry robustness: unknown tool + bad args ---
def test_robust():
    r = run("does_not_exist", {})
    if r.startswith("ERROR"): ok("unknown tool -> error", r)
    else: bad("unknown tool", r)
    r = run("set_volume", {"level": "abc", "bogus": 1})  # bad type + extra arg
    if isinstance(r, str): ok("bad args handled", r[:50])
    else: bad("bad args", r)
test_robust()

print("\n==== SUMMARY ====")
print(f"PASS={len(results['pass'])} FAIL={len(results['fail'])} INFO={len(results['info'])}")
if results["fail"]:
    print("FAILURES:")
    for n,d in results["fail"]:
        print(f"  - {n}: {d}")
