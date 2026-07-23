# 🧪 JARVIS — Deep Testing Checklist (Groq + Gemini)

Ye checklist apni **Windows machine** par chalao (yahan sandbox me internet/GUI/LLM nahi the, isliye live test wahin hoga).

---

## 0. Setup (ek baar)

- [ ] `cd C:\Users\ayush_lr8ru2y\Documents\jarvis`
- [ ] `pip install -r requirements.txt`  → ye `openai` + `langchain-openai` install karega (Gemini ke liye zaroori)
- [ ] `.env` me confirm karo:
  - [ ] 10 `GEMINI_API_KEY` lines hain
  - [ ] `GEMINI_MODEL=gemini-2.5-flash`
  - [ ] `GEMINI_BRAIN_MODEL=gemini-flash-latest`
  - [ ] `ENABLE_GEMINI_FALLBACK=True`
  - [ ] `ENABLE_RACE_MODE=True`
- [ ] JARVIS start karo, koi import/startup error nahi aana chahiye
- [ ] Startup logs me ye dikhe:
  - `[GEMINI] GroqService secondary provider ready with 10 key(s)`
  - `[GEMINI] Realtime fast-extract ready: 10 key(s) (RACE mode ON)`
  - `[AGENT] Gemini fallback clients ready: 10`

---

## 1. Normal chat (Groq primary) ✅ basic

- [ ] Ek simple message bhejo: `"hello, how are you"`
- [ ] Reply aata hai
- [ ] **Activity panel** me dikhe: **"Answered by Groq"** (route-groq, key label)
- [ ] Voice se bhi ek message bolo → reply + voice output sahi (daily use safe)

---

## 2. Failover (Groq fail → Gemini sambhal le) ⭐ important

Kaise force karein (koi ek):
- **Tareeka A:** `.env` me saari `GROQ_API_KEY` lines temporarily galat kar do (ek random char add) → restart
- **Tareeka B:** Internet pe Groq block / ya keys hata do

- [ ] Ab ek message bhejo
- [ ] Reply phir bhi aata hai (Gemini se)
- [ ] Activity me dikhe: **"Failover"** / **"Answered by Gemini"** (route-gemini)
- [ ] Test ke baad `.env` wapas sahi kar dena!

---

## 3. Race mode (Groq + Gemini saath, jo pehle aaye) ⭐ tumne ye maanga tha

Race 2 jagah ON hai: **brain (route decide)** aur **realtime search query extraction**.

- [ ] Ek realtime/web wala sawal pucho: `"aaj ka gold rate kya hai"` ya `"latest news about ..."`
- [ ] Reply aata hai
- [ ] Activity me dikhe: **"Race winner"** → `Search query race -> Groq won` ya `-> Gemini won`
- [ ] Alag-alag sawal pe kabhi Groq kabhi Gemini jeet sakta hai (normal hai)

---

## 4. Agent / Tool-calling (task commands) ⭐ important

> Note: Agent **race nahi karta** (tool-calling safe rakhne ke liye) — sirf failover.

- [ ] `"open notepad"` → notepad khule, activity me tool call dikhe
- [ ] `"create a file test.txt on desktop"` → file bane
- [ ] `"what's the time"` / system info wala command
- [ ] Multi-step task: `"open calculator and tell me 25 x 4"`
- [ ] **Check:** koi tool **double-call** na ho, koi step repeat na ho
- [ ] Activity me har step ka tool call + result dikhe
- [ ] Activity me dikhe kisne answer diya (Groq/Gemini)

---

## 5. 400 commands — category spot-check

`automation_test_commands.md` ki har category se 3-4 command try karo:

- [ ] General chat
- [ ] Realtime / web search
- [ ] Camera / vision (agar webcam hai)
- [ ] Desktop control (open/close apps)
- [ ] File operations (create/read/delete)
- [ ] Google tools (calendar / gmail / drive) — agar OAuth setup hai
- [ ] System commands (volume, time, etc.)
- [ ] Mixed / multi-intent

> Jo command na chale, uska naam + error note kar lena — fir batana, theek kar denge.

---

## 6. Stability (background me dekhte rehna)

- [ ] Server crash to nahi ho raha
- [ ] Koi key rate-limit (429) pe ho to dusri key/Gemini pakad le (cooldown 30s)
- [ ] `/api/key-monitor` endpoint khol ke dekho → har key ka attempt/success/fail count
- [ ] Lambe use me memory/latency theek

---

## ✅ Sab pass = JARVIS ready

Kuch fail ho to: command + exact error mujhe bata dena. Main fix kar dunga.

---

### 📌 Quick reference — kya kahan use hota hai

| Kaam | Provider order | Model |
|---|---|---|
| Normal chat / realtime answer / agent | Groq → Gemini (failover) | `gemini-2.5-flash` |
| Brain (route decide) | Race: Groq vs Gemini | `gemini-flash-latest` |
| Realtime search-query extract | Race: Groq vs Gemini | `gemini-flash-latest` |
| Vision (camera) | **Groq only** (Gemini nahi) | - |

- Cooldown: koi key 429 de to 30 sec ke liye skip
- `gemini-2.0-flash` use NAHI hota (uspe free quota 0 tha)
