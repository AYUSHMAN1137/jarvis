# JARVIS — Watcher / System State Daemon (FUTURE PLAN)

> Status: NOTED, not yet built. Discuss & refine before implementing.
> Added: 2026-06-17 (Ayush's idea)

## Core idea
Ek background service jo system start pe chalu ho aur continuously ek live "world model" / state maintain kare. JARVIS guess na kare — is state ko padhe. Result: open/close/file/automation sab 100% accurate, unknown apps pe bhi (NO hardcode).

## Why
- Abhi JARVIS "blind" hai — har command pe fresh guess (e.g. settings ka process) -> galti.
- Watcher ke saath JARVIS "aankhwala" -> pehle se pata, action sahi.
- "isko / ye / wo" jaise references resolve ho jaate hai (last opened app/window/file).

## What it monitors
- Apps/Processes: kaunsa khula, real PID, kab khula
- Windows: title, position, focus
- Files: desktop/folders me kya, kahan (move/delete ke liye)
- Settings/toggles: wifi, bluetooth, volume on/off
- Clipboard / active app: user abhi kahan hai (context-aware)

## Cautions
1. Resource use -> event-driven preferred (change pe update), polling minimal.
2. Privacy/Security -> max OS access powerful; sab LOCAL rakhna, kabhi upload nahi.

## Phased roadmap (chhote se bada)
- Phase 1 (abhi): App/window registry. Open pe process-diff se real PID pakdo -> registry. close us PID se. (Turant close-bug fix.)
- Phase 2: File watcher (desktop/downloads) -> move/delete commands.
- Phase 3: Settings/toggle state (wifi/bt/volume).
- Phase 4: Full world model -> complete OS awareness; aage smartphone pe extend.

## Immediate bug this fixes
close_application abhi launch-alias se kill karta hai (ms-settings: -> galat). UWP apps (Settings=SystemSettings.exe, Calculator=CalculatorApp.exe) fail. Watcher registry real PID rakhega -> taskkill /PID <pid> /F.
