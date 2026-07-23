# JARVIS — Real-Machine Testing Guide (for AI IDE Agent)

<aside>
🤖

**Ye guide kiske liye:** ek AI code-editor agent (Cursor / Codex / Windsurf jaisa) jo user ki **real Windows machine** pe JARVIS ko chalayega aur ek-ek karke test karega. Har test ka **ID, steps, expected, kaise verify, PASS/FAIL rule** diya hai. Aakhir me ek **final report** banani hai (Section 15 ka format).

</aside>

## 0. Agent ke liye meta-instructions (pehle ye padho)

<aside>
⚠️

**Golden rules:** (1) Har test **actual evidence** pe judge karo — JARVIS ki chat reply pe *bharosa mat karo* ("opened" bol dena kaafi nahi; sach me khula ya nahi wo check karo). (2) Verdict = watcher-state / process-list / screenshot / tool-result / log — narration nahi. (3) Fail ho to **ruko mat**, note karke aage badho. (4) Dangerous test (delete/shutdown/restart) **mat chalao** — sirf confirm-gate aata hai ya nahi wo dekho, phir cancel.

</aside>

**Har test ke liye ye record karo:**

| Field | Matlab |
| --- | --- |
| Test ID | e.g. T-4.2 |
| Steps kiye | exact command / action |
| Expected | kya hona chahiye tha |
| Actual | sach me kya hua |
| Evidence | log line / screenshot / watcher-state / process name |
| Verdict | ✅ PASS / ❌ FAIL / ⚠️ UNVERIFIED / ⏭️ SKIP (dangerous) / 🚫 BLOCKED (setup) |
| Notes | agar fail: root-cause guess + kaunsi file dekhni hai |

**Project facts (agent ke liye):** Python app, entry `run.py` (`reload=False`), `start.bat` = one-click launch. Routes: `/jarvis` (main UI), `/watcher`, `/dashboard`. Config flags `config.py` me: `PHASE5_ENABLED`, `PHASE6_ENABLED`, `PHASE7_ENABLED`, `PHASE8_ENABLED` (sab default True), `PROACTIVE_AUTO_ACT=False`, `CACHE_SEMANTIC_THRESHOLD=0.93`, `CACHE_MAX_ENTRIES=500`. Logs terminal pe aate hain, tags: `[AGENT] [BRAIN] [FAST-PATH] [CACHE-HIT] [TIMING] [MEMORY] [REALTIME] [STARTUP-STREAM]`.

---

## 1. Pre-flight — Environment setup (Section: SETUP)

**T-1.1 — Install & venv**

- Action: repo folder me `python -m venv .venv` → activate → `pip install -r requirements.txt`.
- PASS if: install error-free; `pywin32`, `pycaw`, `pywinauto`, `comtypes`, `sentence-transformers` install ho gaye.

**T-1.2 — .env keys**

- Action: `.env` check karo — `GROQ_API_KEY*`, `GEMINI_API_KEY*`, `SERPER_API_KEY*` present hain?
- PASS if: kam se kam 1 Groq + 1 Gemini + 1 Serper key set. Warna baaki tests me note "key missing".

**T-1.3 — Static compile check**

- Action: `python -m py_compile $(git ls-files "*.py")` (ya har `app/**.py`).
- PASS if: zero syntax errors.

**T-1.4 — Baseline unit tests**

- Action: har `tests/test_*.py` chalao: `python tests/test_command_tester.py` etc. (pytest install nahi — direct run, har file me `__main__` runner hai).
- Expected counts: command_tester **11/11**, context_engine **15/15**, phase4 **13/13**, phase5 **19/19**, phase6 **21/21**, phase7 **15/15**, phase8 **11/11**.
- PASS if: sab green. Kisi bhi count mismatch = FAIL, us suite ka output report me daalo.

---

## 2. Startup & crash-fix (Section: STARTUP) — 🔴 high priority

**T-2.1 — Clean launch**

- Action: `start.bat` chalao (ya `python run.py`). ~30s wait.
- Verify: `/health` 200 deta hai; `/jarvis`, `/watcher`, `/dashboard` load hote hain.
- PASS if: server up, teeno routes khulte hain.

**T-2.2 — 🔴 COM crash-fix (sabse important)**

- Background: pehle startup pe har ~2s ye crash aata tha: `_compointer_base.__del__ → Release() → access violation reading 0xFFFFFFFFFFFFFFFF`. Fix = saare pycaw/COM calls ek hi dedicated STA thread `jarvis-audio-com` pe.
- Action: app ko **60 second** idle chalne do, logs dekho.
- PASS if: **koi** `access violation` / `_compointer_base` / `Release()` traceback **nahi** aata. Watcher har 2s volume cached se padhe (COM churn nahi).
- FAIL if: wo traceback dikhe → note karo, `app/services/agent/tools/_audio.py` + `app/services/watcher/state_service.py` dekhne ko bolo.

**T-2.3 — Startup brief + greeting (naya fix)**

- Action: fresh session me startup brief chalne do (voice/text).
- Verify: greeting me **owner ka naam** aaye (memory→`JARVIS_OWNER_NAME` config se). 6-line brief: greeting, date, weather, advice, email-count, calendar-count.
- PASS if: greeting me sahi naam; brief 6 lines; **logs me poora prompt web-search me leak nahi** hota (`[REALTIME] Query extraction` line short/clean honi chahiye, multi-line prompt nahi).

**T-2.4 — Voice cache (ek hi rule)**

- Action: koi line 2 baar bulwao (ya startup brief 2 baar).
- Verify: `VOICE_CACHE_DIR` me `.mp3` bante hain; doosri baar same text = cache-hit (naya file nahi banta, fast).
- PASS if: same text pe dobara TTS generate nahi hota.

---

## 3. Phase 1 — Watcher / real-PID (Section: WATCHER)

**T-3.1 — Open app**

- Command: "open notepad" → phir "open calculator".
- Verify: `/watcher` pe dono processes dikhein; real PID track ho.
- PASS if: dono actually khule + watcher me list hue.

**T-3.2 — Close by real PID (UWP included)**

- Command: "close notepad", "close calculator", "close settings" (Settings=`SystemSettings.exe`, Calc=`CalculatorApp.exe`).
- PASS if: sahi process band hua (galat/dusra nahi). UWP apps bhi band hue.
- FAIL if: launch-naam se galat PID killed → `close_by_name` logic dekhne ko bolo.

**T-3.3 — Live toggles read**

- Command: "wifi on hai kya?", "volume kitna hai?"
- Verify: watcher state me wifi/bt/volume/brightness reflect ho.
- PASS if: report sach ke kareeb (jaisa system me set hai).

---

## 4. Agent tool-calling + volume/mute crash (Section: TOOLS-CORE) — 🔴

**T-4.1 — 🔴 Volume/mute (COM crash regression)**

- Commands (ek-ek, 5-6 baar): "volume 50", "mute", "unmute", "volume 20", "volume 80".
- PASS if: har baar actually set/mute hua **aur koi crash nahi** (T-2.2 wala). Ye wahi command tha jispe pehle crash hota tha.

**T-4.2 — Dangerous confirm-gate**

- Command: "delete <koi test file>" ya "shutdown".
- Expected: JARVIS **confirm maange** ("sensitive action... say yes to confirm") — turant execute NAHI.
- Action: **"no" bol ke cancel karo** (kabhi confirm mat karo).
- PASS if: confirm-gate aaya + cancel pe kuch nahi hua. (Ye ⏭️ SKIP-safe test hai.)

**T-4.3 — Brightness / display**

- Command: "brightness 40".
- PASS if: screen brightness sach me badla.

---

## 5. Phase 2 — Memory (Section: MEMORY)

**T-5.1 — Name capture**

- Command: "my name is Ayushman" → phir "mera naam kya hai?"
- PASS if: JARVIS "Ayushman" bataye.

**T-5.2 — 🆕 Name-correction persist (naya fix)**

- Command: "actually my name is Rohan" → phir "mera naam kya hai?"
- PASS if: ab "Rohan" bole (purana overwrite hua, duplicate nahi). Optional: memory DB me `facts` table, `key='name'` ki sirf **1 row** honi chahiye.

**T-5.3 — Name-question galti se save na ho**

- Command: "what was my name?" (bina naam bataye).
- PASS if: "Kya Tha"/"was" naam ke roop me save **nahi** hua (T-5.1 ka naam intact rahe).

**T-5.4 — Secret redaction**

- Command: "remember my password is hunter2".
- PASS if: JARVIS refuse kare ("looks like a password") — store nahi kare.

**T-5.5 — Correction memory**

- Command: koi galat action → "nahi aise nahi, X karo".
- PASS if: correction yaad rahe (agli baar wahi galti nahi).

---

## 6. Phase 3 — Context Engine (Section: CONTEXT)

**T-6.1 — Reference resolve**

- Command: "open notepad" → phir "isko band karo".
- PASS if: notepad band hua ("isko" = last opened resolve).

**T-6.2 — Ordinal**

- Setup: 2-3 cheezein khol/nikaal ke → "pehla wala band karo" / "last wala".
- PASS if: sahi target chuna.

**T-6.3 — Tool-result content search**

- Command: web search karke → "wo wala kholo jisme <keyword> tha".
- PASS if: content ke andar se sahi result resolve hua.

---

## 7. Phase 5 — Multi-step + UIA (Section: UIA) — 🔴 sabse zyada unverified

<aside>
🖱️

**Ye sabse important real-machine test hai** — Linux sandbox pe UIA click test ho hi nahi sakta tha. Yahan actual pywinauto clicks honge. Har step: UIA-first → vision-fallback → checker verify.

</aside>

**T-7.1 — Multi-step plan (`do_multistep`)**

- Command: "wifi off karke phir on karo" (ya koi 2-step).
- Verify: logs me plan → steps → precondition-skip → per-step verify. `plan.done` event.
- PASS if: dono step actually hue + honest verdict (fake PASS nahi).

**T-7.2 — UIA toggle: Wi-Fi**

- Command: "wifi off karo" → "wifi on karo".
- PASS if: Settings/quick-panel me toggle sach me click hua (screenshot se confirm).
- Note: "Let apps control radios" Windows permission ON honi chahiye, warna radio API block karega — us case me guide me BLOCKED mark karo.

**T-7.3 — UIA toggle: Bluetooth** — same as 7.2.

**T-7.4 — UIA: Privacy/settings page**

- Command: "camera privacy settings kholo".
- PASS if: sahi specific page khuli (privacy→camera), generic Settings nahi.

**T-7.5 — YouTube play (UIA/browser)**

- Command: "play <song> on youtube".
- PASS if: browser me actual video chala (ye pehle crash/fail karta tha — `_open_in_browser` robust fix: webbrowser → os.startfile → `cmd /c start`).

**T-7.6 — Browser / chrome / google search launch**

- Commands: "open [google.com](http://google.com)", "chrome kholo", "google karo <query>".
- PASS if: browser actually khula (in-code fast-path `[FAST-PATH]` log dikhega). Verdict frontend-only → ⚠️ UNVERIFIED acceptable, par browser to khulna chahiye.

---

## 8. Phase 4 — Checker / Learner / Skills (Section: SELF-LEARN)

**T-8.1 — Verification verdict**

- Action: koi strong-tool command (open app) → `/dashboard` Checker panel dekho.
- PASS if: PASS/FAIL/UNKNOWN + reason + evidence dikhe; weak tools (open website, search) = ⚠️ UNVERIFIED (fake PASS nahi).

**T-8.2 — Skill crystallize (N=3)**

- Action: same verifiable command **3 baar** successfully chalao.
- PASS if: dashboard Skills panel me skill crystallize ho (success count↑).

**T-8.3 — Learner honest-stop**

- Action: koi risky/na-hone-wala command.
- PASS if: safe-idempotent pe max 2 auto-retry; risky pe auto-retry **nahi** → honest stop note.

---

## 9. Phase 6 — Cache (Section: CACHE)

**T-9.1 — Promote + replay**

- Action: ek verified command dobara chalao.
- PASS if: doosri baar `[CACHE-HIT]` log + instant (`[TIMING] route=task(cache)`).

**T-9.2 — Evict on fail**

- Action: cached command ko fail karwa do (jaise target hata ke).
- PASS if: cache se evict ho (agli baar full agent path).

---

## 10. Phase 7 / 8 — Proactive + Personalization (Section: PROACTIVE)

**T-10.1 — Suggest-only**

- PASS if: proactive suggestions aayein par **khud act na kare** (`PROACTIVE_AUTO_ACT=False`).

**T-10.2 — Habit model**

- Action: kuch apps baar-baar khol ke pattern banao.
- PASS if: Phase 8 user-model frequent apps/timing capture kare (dashboard/logs).

---

## 11. Command Tester (dashboard) (Section: BULK)

**T-11.1 — Bulk run**

- Action: `/dashboard` → Command Tester → 8-10 commands paste → Run.
- PASS if: ek-ek chale, live table PASS/FAIL/SKIP/UNVERIFIED, risky auto-SKIP, fail pe aage badhe.

**T-11.2 — Logs download**

- PASS if: per-session terminal-identical `logs.txt` download ho.
- **Bonus:** ye downloaded logs.txt final report ke saath attach karna (crash-fix confirm ke liye सबसे useful).

---

## 12. Providers / failover (Section: PROVIDERS)

**T-12.1 — Failover badge**

- Action: (agar possible) ek Groq key invalid karke dekho.
- PASS if: dashboard Activity panel me provider switch (Gemini/Groq/rule-based) dikhe; koi request drop na ho.

---

## 13. Google integration (Section: GOOGLE) — ⚠️ known pending

**T-13.1 — OAuth**

- Known: "missing required scopes" — re-auth chahiye (user action).
- Action: Gmail/Calendar command ("unread emails?", "today's events?").
- PASS if: auth ok → count aaye. FAIL/BLOCKED if: scope error → note "re-authorize Google".

---

## 14. Regression sweep (Section: REGRESSION)

Purane fix dobara toote to nahi — quick re-check:

- Telegram/AppData app open (Start-Menu shortcut resolver) + honest reporting.
- Noisy-log spam silence (EDID/watcher-poll lines nahi).
- start.bat ek-click (server + /health wait + dono tabs).

---

## 15. 📊 Final Report format (ye banana hai)

<aside>
📋

Saare tests ke baad AI agent ye report file banaye: **`TEST_REPORT.md`** (repo root me).

</aside>

**15.1 — Summary banner:** total tests, ✅ PASS / ❌ FAIL / ⚠️ UNVERIFIED / ⏭️ SKIP / 🚫 BLOCKED counts.

**15.2 — Full results table** (har test):

| ID | Area | Command/Action | Expected | Actual | Verdict | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| T-2.2 | Startup | 60s idle | no COM crash | ... | ✅/❌ | log line |

**15.3 — Failures deep-dive:** har FAIL ke liye — kya hua, log/screenshot evidence, **root-cause hypothesis**, aur **kaunsi file/function** dekhni/fix karni hai.

**15.4 — Priority fix list:** 🔴 critical (crash/data), 🟡 medium, 🟢 minor — sorted.

**15.5 — Verdict:** overall "ship-ready?" haan/nahi + 3-line summary.

<aside>
✅

**Honesty rule (sabse upar):** koi cheez verify na ho paaye → ⚠️ UNVERIFIED likho, jhootha PASS **kabhi nahi**. Yehi poore test ka point hai.

</aside>