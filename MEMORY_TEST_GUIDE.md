# JARVIS Phase 2 (Memory) — Real-World Test Guide

> Yeh woh tests hain jo **tumhe apne Windows machine pe** karne hain.
> Main offline jo test kar sakta tha woh kar chuka (37 checks pass + persistence verified).
> Yeh wale Groq/voice ke saath actual chalne pe hi confirm honge.

---

## 0. Setup (ek baar)

```powershell
cd C:\Users\ayush_lr8ru2y\Downloads\jarvis-2-main\jarvis-2-main
pip install -r requirements.txt
# phir server restart karo (jaise hamesha chalate ho)
```

**Startup pe check:** log/console me yeh line aani chahiye:
```
[STARTUP] Persistent memory ready: facts=... , fts=on
```
- `fts=on` → full-text search active (best).
- `fts=off` → chalega phir bhi (LIKE fallback), bas thoda kam smart. Theek hai.

**File check:** server start hone ke baad yeh ban jaane chahiye:
```
database\memory.db
database\memory\user_profile.md
database\memory\jarvis_persona.md
```

---

## 1. Basic remember + recall  (sabse zaroori)

| # | Bolo / Type karo | Expected |
|---|------------------|----------|
| 1 | "my name is Ayush" ya "mera naam Ayush hai" | JARVIS acknowledge kare |
| 2 | (naya turn) "mera naam kya hai?" | **"Ayush"** bole — yaad rakhe |
| 3 | "remember that I prefer Brave browser" | acknowledge |
| 4 | "konsa browser pasand hai mujhe?" | **Brave** bole |

> Asli test #2 aur #4 hain — agar yeh yaad rakhta hai, memory kaam kar rahi hai.

---

## 2. Memory ZINDA rehti hai restart ke baad

1. "remember that my favourite city is Bangalore"
2. **Server band karke dobara start karo**
3. "meri favourite city kya hai?"

**Expected:** restart ke baad bhi **Bangalore** yaad rahe. (Offline yeh maine 2-process test se verify kar liya hai — tum bas confirm kar lena.)

---

## 3. Correction memory (galti dobara na ho)

1. JARVIS se kuch karwao jisme woh galti kare (ya jaan-bujhke): e.g. "open browser" → maan lo galat khola
2. Bolo: "nahi, Chrome nahi Brave kholna tha" (ya `note_correction` jaisa phrasing)
3. Thodi der baad phir same kaam bolo

**Expected:** ab woh Brave khole / pehle wali galti repeat na kare.

---

## 4. Auto-capture (bina 'remember' bole bhi seekhe)

Normal baat me bolo:
- "by the way I work night shifts"
- "i prefer dark mode"

Phir poocho: "mere baare me kya jaante ho?" → yeh baatein aani chahiye.

---

## 5. Secret SAFETY (yeh fail NAHI hona chahiye)

Bolo: "remember my password is hunter2secret"  (ya koi fake API key)

**Expected:** JARVIS **save karne se mana kare** — "I won't save that, it looks like a secret" type. Recall me kabhi password na aaye.

> ⚠️ Yeh privacy ke liye critical hai. Agar password save ho jaaye to batana.

---

## 6. Profile injection (personality continuity)

`database\memory\user_profile.md` file me apne haath se kuch likho, e.g.:
```
- Mujhe Hindi-English mix me short reply pasand hai.
```
Server restart karo → ab har reply me JARVIS isko dhyan me rakhe.

---

## 7. Voice flow (daily use)

- Normal voice se 1–2 cheez batao ("mera naam ...", "mujhe ... pasand hai")
- Naye voice session me poocho → yaad rahe.
- **Reliability check:** agar memory me kabhi koi dikkat ho, **chat/voice crash NAHI hona chahiye** — normal chalta rahe (fail-soft).

---

## ✅ Quick checklist (tick karke batana)

- [ ] Startup log: `Persistent memory ready ... fts=on`
- [ ] Naam yaad rakha (test 1)
- [ ] Restart ke baad bhi yaad (test 2)
- [ ] Correction follow kiya (test 3)
- [ ] Auto-capture chala (test 4)
- [ ] Password save NAHI hua (test 5)
- [ ] Profile file inject hui (test 6)
- [ ] Voice me yaad rakha + koi crash nahi (test 7)

---

## Agar kuch toote to mujhe yeh bhejna

1. Console/terminal ka error (jo `[STARTUP]` ya memory se related ho)
2. Exact command jo bola
3. JARVIS ne kya reply diya

Isse main turant fix kar dunga. Phase 3 (context resolver: "isko/ye/wo") tabhi shuru karunge jab yeh sab green ho jaaye.
