"""One-off helper: gather the real numbers CLAUDE.md claims.

Run:  .venv\\Scripts\\python.exe scripts\\_m13_doc_audit.py

CLAUDE.md's own preamble promises that every path, line count, singleton and
constant in it was verified against the working tree. This prints the facts so
that promise can actually be kept after a large change.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def lines(relative):
    path = REPO / relative
    if not path.exists():
        return f"MISSING ({relative})"
    return sum(1 for _ in path.open(encoding="utf-8", errors="replace"))


def main():
    print("=" * 64)
    print("FILE LINE COUNTS")
    print("=" * 64)
    for relative in [
        "config.py", "run.py", "app/main.py",
        "app/services/chat_service.py",
        "app/services/resolver.py",
        "app/services/agent/agent_loop.py",
        "app/services/agent/action_sink.py",
        "app/services/agent/execution/coordinator.py",
        "app/services/agent/checker/coordinator.py",
        "app/services/agent/checker/checker.py",
        "app/services/agent/checker/learner.py",
        "app/services/agent/cache/coordinator.py",
        "app/services/agent/tools/web_tools.py",
        "app/services/agent/tools/desktop_tools.py",
        "app/services/groq_service.py",
        "app/services/realtime_service.py",
        "app/services/vision_service.py",
        # [M14] web/script.js, web/orb.js and web/style.css are gone:
        # the first two were split into web/js/*.js in P9, the third was an
        # @import shim deleted in P12 once its last consumer was repointed.
        "web/index.html", "web/viewer.html", "web/api-monitor.html",
        "web/js/main.js", "web/js/chat.js", "web/js/orb.js",
        "web/css/tokens.css", "web/css/layout.css",
    ]:
        print(f"  {lines(relative):>6}  {relative}")

    print("\n" + "=" * 64)
    print("TOOLS")
    print("=" * 64)
    from app.services.agent.tools import load_all_tools
    from app.services.agent.tool_registry import registry
    load_all_tools()
    names = registry.names()
    print(f"  registered tools: {len(names)}")
    dangerous = sorted(n for n in names if registry.is_dangerous(n))
    print(f"  dangerous: {len(dangerous)} -> {dangerous}")
    families = {}
    missing = []
    for name in names:
        spec = registry.get(name)
        meta = getattr(spec, "verification", {}) or {}
        family = meta.get("family")
        if not family:
            missing.append(name)
        families.setdefault(family, []).append(name)
    print(f"  tools with no verification family: {len(missing)} {missing}")
    for family in sorted(families, key=lambda f: str(f)):
        print(f"    {str(family):<10} {len(families[family])}")

    print("\n" + "=" * 64)
    print("ROUTES")
    print("=" * 64)
    import app.main as main_module
    routes = []
    for route in main_module.app.routes:
        methods = sorted(getattr(route, "methods", []) or [])
        path = getattr(route, "path", "")
        for method in methods or ["MOUNT"]:
            if method in ("HEAD", "OPTIONS"):
                continue
            routes.append(f"{method} {path}")
    print(f"  route entries (excl. HEAD/OPTIONS): {len(routes)}")
    print(f"  distinct paths: {len(set(r.split(' ', 1)[1] for r in routes))}")

    print("\n" + "=" * 64)
    print("TESTS")
    print("=" * 64)
    test_files = sorted((REPO / "tests").glob("test_*.py"))
    print(f"  test files: {len(test_files)}")
    for path in test_files:
        print(f"    {lines('tests/' + path.name):>5}  {path.name}")

    print("\n" + "=" * 64)
    print("DATABASES declared in config")
    print("=" * 64)
    import config as cfg
    for attr in sorted(a for a in dir(cfg) if a.endswith("_DB_PATH")):
        value = getattr(cfg, attr)
        print(f"  {attr:<26} {Path(value).name}")

    print("\n" + "=" * 64)
    print("SERVICE FILE COUNT")
    print("=" * 64)
    service_files = list((REPO / "app" / "services").rglob("*.py"))
    service_files = [p for p in service_files if "__pycache__" not in p.parts]
    total = sum(lines(p.relative_to(REPO).as_posix()) for p in service_files)
    print(f"  {len(service_files)} python files, {total:,} lines under app/services/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
