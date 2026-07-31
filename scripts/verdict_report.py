"""Baseline health report: verifier coverage, verdicts, cache, storage, latency.

This is the measurement step that every later change is judged against. It is
strictly READ-ONLY -- it opens the databases, reads the debug logs, and prints.
Nothing is written unless you pass --write-baseline.

    python scripts/verdict_report.py                  # print the report
    python scripts/verdict_report.py --markdown       # print as markdown
    python scripts/verdict_report.py --write-baseline # also save docs/BASELINE.md

Why each section matters
  1. Verifier coverage -- a tool with no verifier can only ever produce UNKNOWN,
     and UNKNOWN never promotes to the command cache. This is the single number
     that explains an empty cache.
  2. Verdicts        -- what actually happened on real turns, per tool.
  3. Cache           -- how much of the 500-entry budget is really being used.
  4. Storage         -- WAL bloat means the last shutdown never checkpointed.
  5. Latency         -- per-turn wall clock, parsed from the session debug logs.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
DEBUG_LOGS = DATA / "debug_logs"

# (filename, [tables to count])
DATABASES = [
    ("memory.db", ["facts", "actions", "corrections"]),
    ("skills.db", ["skills", "skill_observations"]),
    ("command_cache.db", ["command_cache"]),
    ("user_model.db", ["um_facts", "um_aliases", "um_habits"]),
    ("proactive.db", ["proactive_suggestions", "proactive_consent"]),
]

_TURN_END_RE = re.compile(r"\[\+\s*([\d.]+)s\]\s*\[T\d+\]\s*TURN\s+\d+\s+END")


def _snapshot_wal_state() -> dict:
    """Record -wal/-shm state before this process opens anything.

    Even a read-only connection creates a -shm file, and section 1 loads the
    tool registry (which constructs the memory and cache singletons). Measuring
    later would report this script's own footprint as leftover state.
    """
    snapshot = {}
    for filename, _ in DATABASES:
        wal = DATA / (filename + "-wal")
        shm = DATA / (filename + "-shm")
        snapshot[filename] = (wal.stat().st_size if wal.exists() else 0, shm.exists())
    return snapshot


_WAL_SNAPSHOT = _snapshot_wal_state()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    except sqlite3.Error:
        return -1


def _percentile(values, pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


class Report:
    """Collects lines in both plain and markdown form."""

    def __init__(self, markdown: bool) -> None:
        self.markdown = markdown
        self.lines: list[str] = []

    def head(self, text: str) -> None:
        self.lines.append(f"\n## {text}\n" if self.markdown else f"\n{'=' * 68}\n  {text}\n{'=' * 68}")

    def line(self, text: str = "") -> None:
        # Console output is indented for readability; markdown treats a leading
        # indent as significant, so drop it there.
        self.lines.append(text.lstrip() if self.markdown else text)

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        if not rows:
            self.line("_(no rows)_" if self.markdown else "  (no rows)")
            return
        if self.markdown:
            self.line("| " + " | ".join(headers) + " |")
            self.line("|" + "|".join("---" for _ in headers) + "|")
            for row in rows:
                self.line("| " + " | ".join(str(c) for c in row) + " |")
            return
        widths = [max(len(str(headers[i])), *(len(str(r[i])) for r in rows))
                  for i in range(len(headers))]
        self.line("  " + "  ".join(str(headers[i]).ljust(widths[i]) for i in range(len(headers))))
        self.line("  " + "  ".join("-" * widths[i] for i in range(len(headers))))
        for row in rows:
            self.line("  " + "  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))

    def render(self) -> str:
        return "\n".join(self.lines)


# --------------------------------------------------------------------------- #
# 1. verifier coverage
# --------------------------------------------------------------------------- #
def section_verifier_coverage(rep: Report) -> dict:
    rep.head("1. Verifier coverage  (tools that can ever be verified)")
    try:
        from app.services.agent.tools import load_all_tools
        from app.services.agent.tool_registry import registry
        from app.services.agent.checker.checker import classify_family
        load_all_tools()
    except Exception as exc:  # noqa: BLE001 - report must run even if imports fail
        rep.line(f"  could not load the tool registry: {exc}")
        return {}

    by_family: dict[str, list[str]] = defaultdict(list)
    for name in sorted(registry.names()):
        by_family[classify_family(name) or "NONE (always UNKNOWN)"].append(name)

    total = sum(len(v) for v in by_family.values())
    # Three distinct states, and conflating them would flatter the number:
    #   verified     -- a verifier exists and can return PASS/FAIL
    #   by design    -- honestly unverifiable (shutdown, sleep, ...) -> UNKNOWN
    #   unclassified -- nobody tagged it; the bug this milestone fixed
    unclassified = len(by_family.get("NONE (always UNKNOWN)", []))
    by_design = len(by_family.get("none", []))
    verified = total - unclassified - by_design

    rep.table(
        ["family", "tools", "names"],
        [[fam, len(names), ", ".join(names)] for fam, names in sorted(by_family.items())],
    )
    rep.line()
    pct = (verified / total * 100.0) if total else 0.0
    rep.line(f"  VERIFIABLE:   {verified}/{total} tools have a real verifier ({pct:.0f}%)")
    rep.line(f"  BY DESIGN:    {by_design} tool(s) declared unverifiable -> always UNKNOWN, never cached")
    rep.line(f"  UNCLASSIFIED: {unclassified} tool(s) with no verifier at all"
             + ("  <-- these are the bug" if unclassified else "  (good)"))

    declared = sum(1 for n in registry.names() if (registry.get(n).verification or {}))
    rep.line(f"  {declared}/{total} tools declare verification= metadata.")
    return {"total": total, "verified": verified, "by_design": by_design,
            "unclassified": unclassified, "declared": declared}


# --------------------------------------------------------------------------- #
# 2. verdict distribution
# --------------------------------------------------------------------------- #
def section_verdicts(rep: Report) -> dict:
    rep.head("2. Verification verdicts on real actions  (memory.db)")
    path = DATA / "memory.db"
    if not path.exists():
        rep.line("  memory.db not found")
        return {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(actions)")}
        if "verification_verdict" not in cols:
            rep.line("  actions table has no verification_verdict column yet")
            return {}
        rows = conn.execute(
            "SELECT tool, COALESCE(NULLIF(verification_verdict,''),'(none recorded)'), COUNT(*) "
            "FROM actions GROUP BY 1, 2"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        rep.line("  no actions recorded yet")
        return {}

    per_tool: dict[str, Counter] = defaultdict(Counter)
    totals: Counter = Counter()
    for tool, verdict, n in rows:
        per_tool[tool][verdict] += n
        totals[verdict] += n

    grand = sum(totals.values())
    rep.table(
        ["verdict", "count", "share"],
        [[v, n, f"{n / grand * 100:.0f}%"] for v, n in totals.most_common()],
    )
    rep.line()
    rep.line("  Per tool:")
    rep.table(
        ["tool", "PASS", "FAIL", "UNKNOWN", "none recorded"],
        [[tool, c.get("PASS", 0), c.get("FAIL", 0), c.get("UNKNOWN", 0),
          c.get("(none recorded)", 0)]
         for tool, c in sorted(per_tool.items(), key=lambda kv: -sum(kv[1].values()))],
    )
    never_pass = sorted(t for t, c in per_tool.items() if not c.get("PASS"))
    if never_pass:
        rep.line()
        rep.line(f"  {len(never_pass)} tool(s) have NEVER produced a PASS:")
        rep.line("    " + ", ".join(never_pass))
    return {"totals": dict(totals), "grand": grand}


# --------------------------------------------------------------------------- #
# 3. cache
# --------------------------------------------------------------------------- #
def section_cache(rep: Report) -> dict:
    rep.head("3. Verified command cache  (command_cache.db)")
    path = DATA / "command_cache.db"
    if not path.exists():
        rep.line("  command_cache.db not found")
        return {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT trigger, kind, status, hits, fail_count FROM command_cache "
            "ORDER BY hits DESC"
        ).fetchall()
    except sqlite3.Error as exc:
        rep.line(f"  unreadable: {exc}")
        return {}
    finally:
        conn.close()

    try:
        from config import CACHE_MAX_ENTRIES
    except Exception:  # noqa: BLE001
        CACHE_MAX_ENTRIES = 500

    rep.line(f"  {len(rows)} / {CACHE_MAX_ENTRIES} entries used "
             f"({len(rows) / CACHE_MAX_ENTRIES * 100:.0f}% of budget)")
    rep.line(f"  total hits: {sum(r[3] or 0 for r in rows)}")
    rep.line()
    rep.table(["trigger", "kind", "status", "hits", "fails"],
              [[r[0][:48], r[1], r[2], r[3], r[4]] for r in rows[:25]])
    return {"entries": len(rows), "hits": sum(r[3] or 0 for r in rows)}


# --------------------------------------------------------------------------- #
# 4. storage health
# --------------------------------------------------------------------------- #
def section_storage(rep: Report) -> dict:
    rep.head("4. Storage health  (WAL bloat = last shutdown never checkpointed)")
    pre = _WAL_SNAPSHOT
    dirty = [name for name, (wal_size, shm) in pre.items() if wal_size or shm]

    rows = []
    for filename, tables in DATABASES:
        db = DATA / filename
        if not db.exists():
            rows.append([filename, "missing", "-", "-", "-"])
            continue
        wal_size, had_shm = pre[filename]
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            counts = ", ".join(f"{t}={_count(conn, t)}" for t in tables)
            conn.close()
        except sqlite3.Error as exc:
            counts = f"unreadable: {exc}"
        rows.append([filename, _human_bytes(db.stat().st_size),
                     _human_bytes(wal_size) if wal_size else "-",
                     "yes" if had_shm else "-", counts])
    rep.table(["database", "size", "WAL", "SHM", "rows"], rows)

    for label, folder, pattern in (
        ("debug_logs", DEBUG_LOGS, "*"),
        ("voice_cache", DATA / "voice_cache", "*"),
        ("chats_data", DATA / "chats_data", "*.json"),
        ("backups", DATA / "backups", "*"),
    ):
        if not folder.exists():
            continue
        files = [f for f in folder.glob(pattern) if f.is_file()]
        total = sum(f.stat().st_size for f in files)
        rep.line(f"  {label:<12} {len(files):>4} file(s)  {_human_bytes(total)}")

    rep.line()
    if dirty:
        rep.line(f"  [!] {len(dirty)} database(s) have leftover WAL/SHM: {', '.join(dirty)}")
        rep.line("      If the server is stopped, this means shutdown never checkpointed.")
    else:
        rep.line("  [ok] No leftover WAL/SHM files.")
    return {"dirty": dirty}


# --------------------------------------------------------------------------- #
# 5. latency
# --------------------------------------------------------------------------- #
def section_latency(rep: Report) -> dict:
    rep.head("5. Turn latency  (parsed from data/debug_logs/session_*.log)")
    if not DEBUG_LOGS.exists():
        rep.line("  no debug_logs directory")
        return {}
    durations: list[float] = []
    for log in DEBUG_LOGS.glob("session_*.log"):
        try:
            text = log.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        durations.extend(float(m) for m in _TURN_END_RE.findall(text))
    if not durations:
        rep.line("  no completed turns found")
        return {}

    stats = {
        "turns": len(durations),
        "min": min(durations),
        "p50": _percentile(durations, 50),
        "p90": _percentile(durations, 90),
        "p99": _percentile(durations, 99),
        "max": max(durations),
        "mean": sum(durations) / len(durations),
    }
    rep.table(
        ["turns", "min", "p50", "p90", "p99", "max", "mean"],
        [[stats["turns"]] + [f"{stats[k]:.2f}s" for k in
                             ("min", "p50", "p90", "p99", "max", "mean")]],
    )
    slow = sorted((d for d in durations if d >= 10.0), reverse=True)
    if slow:
        rep.line()
        rep.line(f"  {len(slow)} turn(s) took 10s or longer "
                 f"({len(slow) / len(durations) * 100:.0f}% of all turns): "
                 + ", ".join(f"{d:.0f}s" for d in slow[:12]))
    return stats


# --------------------------------------------------------------------------- #
def main() -> None:
    # The Windows console defaults to cp1252 and blows up on any non-latin1
    # character that came out of a tool name or a file path.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - older/odd stdout
        pass

    markdown = "--markdown" in sys.argv or "--write-baseline" in sys.argv
    rep = Report(markdown)

    stamp = datetime.now().isoformat(timespec="seconds")
    if markdown:
        rep.line("# J.A.R.V.I.S — Baseline Health Report")
        rep.line()
        rep.line(f"Generated: `{stamp}`  ")
        rep.line("Produced by `scripts/verdict_report.py`. Re-run after each milestone "
                 "and compare.")
    else:
        rep.line(f"J.A.R.V.I.S baseline health report  --  {stamp}")

    section_verifier_coverage(rep)
    section_verdicts(rep)
    section_cache(rep)
    section_storage(rep)
    section_latency(rep)

    out = rep.render()
    print(out)

    if "--write-baseline" in sys.argv:
        target = ROOT / "docs" / "BASELINE.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(out + "\n", encoding="utf-8")
        print(f"\n[written] {target}")


if __name__ == "__main__":
    main()
