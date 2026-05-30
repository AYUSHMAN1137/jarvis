# Desktop Automation — JARVIS Implementation Plan

## Overview

JARVIS mein ek naya **Desktop Automation Layer** add karna hai jo natural language commands se local Windows machine control kare. Architecture existing pattern ke saath perfectly fit hogi — ek naya `desktop_service.py` skill, naye intent types, brain prompts mein extension, aur frontend actions.

---

## Architecture — How It Will Work (End-to-End)

```
User: "Volume 50 pe set karo" / "Temp files clean karo" / "Screenshot lo"
         │
         ▼
   BrainService (primary classifier)
         │ category = "task"
         ▼
   BrainService (task classifier)
         │ task_type = "desktop_volume" / "desktop_cleanup" / "desktop_screenshot"
         ▼
   TaskExecutor._do_desktop_*()
         │
         ▼
   DesktopService (NEW skill file)
         │  — uses: psutil, pyautogui, pycaw, shutil, winreg, etc.
         ▼
   Response back to user (text + optional frontend action)
```

> [!IMPORTANT]
> **Future-proof design:** Saare desktop operations ek dedicated `DesktopService` class mein rahenge. Naya feature add karna ho to sirf ek method + ek intent type + ek brain prompt line add karo — baki system automatically handle ho jaayega.

---

## Files Jo Change/Create Hongi

---

### Layer 1 — New Skill: Desktop Service

#### [NEW] `app/services/skills/desktop_service.py`

Ek single class `DesktopService` jisme saare desktop automation methods honge. Har method ek clean string return karega (JARVIS ka response).

**Methods breakdown:**

| Method | Kya Karega | Library |
|---|---|---|
| `copy_file(src, dst)` | File/folder copy karna | `shutil` |
| `move_file(src, dst)` | File/folder move karna | `shutil` |
| `create_folder(path)` | Naya folder banana | `pathlib` |
| `create_file(path)` | Naya file banana (extension ke saath) | `pathlib` |
| `open_path(path)` | File/folder open karna Explorer mein | `os.startfile` |
| `empty_recycle_bin()` | Recycle bin khali karna | `winshell` |
| `take_screenshot(save_path)` | Screenshot lena | `pyautogui` / `PIL` |
| `clear_temp_files()` | %TEMP% aur Windows\Temp clean karna | `shutil`, `os` |
| `kill_process(name)` | Process naam se kill karna | `psutil` |
| `list_running_apps()` | Running apps list karna | `psutil` |
| `hibernate_system()` | System hibernate karna | `subprocess` |
| `sleep_system()` | System sleep karna | `subprocess` |
| `lock_system()` | PC lock karna | `ctypes` |
| `check_updates()` | Windows update check karna | `subprocess` (wuauclt) |
| `set_volume(level)` | Volume set karna (0-100) | `pycaw` |
| `get_volume()` | Current volume get karna | `pycaw` |
| `mute_volume()` | Volume mute/unmute | `pycaw` |
| `set_brightness(level)` | Screen brightness set karna | `screen-brightness-control` |
| `get_brightness()` | Current brightness get karna | `screen-brightness-control` |
| `open_app(name)` | Local app launch karna | `subprocess` |
| `close_app(name)` | App band karna | `psutil` |
| `get_system_stats()` | CPU/RAM/Battery/Disk stats | `psutil` |
| `get_battery_status()` | Battery % aur charging status | `psutil` |
| `get_network_info()` | IP, WiFi name | `socket`, `subprocess` |
| `clipboard_copy(text)` | Text clipboard mein copy karna | `pyperclip` |
| `clipboard_get()` | Clipboard content padhna | `pyperclip` |

---

### Layer 2 — Intent Types (decision_types.py)

#### [MODIFY] `app/services/decision_types.py`

Naye intent constants add honge:

```python
# Desktop Automation Intents
INTENT_DESKTOP_FILE_COPY     = "desktop file copy"
INTENT_DESKTOP_FILE_MOVE     = "desktop file move"
INTENT_DESKTOP_FILE_CREATE   = "desktop file create"
INTENT_DESKTOP_FOLDER_CREATE = "desktop folder create"
INTENT_DESKTOP_OPEN_PATH     = "desktop open path"
INTENT_DESKTOP_RECYCLE_BIN   = "desktop recycle bin"
INTENT_DESKTOP_SCREENSHOT    = "desktop screenshot"
INTENT_DESKTOP_CLEANUP       = "desktop cleanup"
INTENT_DESKTOP_KILL_PROCESS  = "desktop kill process"
INTENT_DESKTOP_LIST_APPS     = "desktop list apps"
INTENT_DESKTOP_HIBERNATE     = "desktop hibernate"
INTENT_DESKTOP_SLEEP         = "desktop sleep"
INTENT_DESKTOP_LOCK          = "desktop lock"
INTENT_DESKTOP_UPDATES       = "desktop updates"
INTENT_DESKTOP_VOLUME        = "desktop volume"
INTENT_DESKTOP_BRIGHTNESS    = "desktop brightness"
INTENT_DESKTOP_OPEN_APP      = "desktop open app"
INTENT_DESKTOP_CLOSE_APP     = "desktop close app"
INTENT_DESKTOP_SYSTEM_STATS  = "desktop system stats"
INTENT_DESKTOP_BATTERY       = "desktop battery"
INTENT_DESKTOP_NETWORK       = "desktop network"
INTENT_DESKTOP_CLIPBOARD     = "desktop clipboard"
```

Ye sab `INSTANT_INTENTS` set mein add honge (fast execution, no background).

`ROUTE_TO_INTENT` dict mein mapping:
```python
"desktop_file_copy":     INTENT_DESKTOP_FILE_COPY,
"desktop_file_move":     INTENT_DESKTOP_FILE_MOVE,
# ... etc
```

---

### Layer 3 — Brain (AI Classifier) Updates

#### [MODIFY] `app/services/brain_service.py`

**3a. `TaskType` enum + `ALL_TASK_TYPES` list** mein naye task types add honge:
```python
"desktop_file_copy", "desktop_file_move", "desktop_file_create",
"desktop_folder_create", "desktop_open_path", "desktop_recycle_bin",
"desktop_screenshot", "desktop_cleanup", "desktop_kill_process",
"desktop_list_apps", "desktop_hibernate", "desktop_sleep", "desktop_lock",
"desktop_updates", "desktop_volume", "desktop_brightness",
"desktop_open_app", "desktop_close_app", "desktop_system_stats",
"desktop_battery", "desktop_network", "desktop_clipboard"
```

**3b. `_TASK_BRAIN_PROMPT`** mein ek naya section add hoga:

```
-> 'desktop_volume (level/action)' — Volume set/mute/unmute/get.
   "Volume 50 pe set karo" → desktop_volume set 50
   "Volume mute karo" → desktop_volume mute
   "Volume kitna hai?" → desktop_volume get
   "Awaaz kam karo" → desktop_volume decrease
   "Volume band karo" → desktop_volume mute

-> 'desktop_screenshot' — Screenshot lena.
   "Screenshot lo" → desktop_screenshot
   "Screen capture karo" → desktop_screenshot
   "Abhi ka screenshot Desktop pe save karo" → desktop_screenshot

-> 'desktop_brightness (level)' — Brightness control.
   "Brightness 70 karo" → desktop_brightness set 70
   "Screen thodi dark karo" → desktop_brightness decrease
   "Brightness kitni hai?" → desktop_brightness get

-> 'desktop_cleanup' — Temp files delete karo.
   "Temp files clean karo" → desktop_cleanup
   "System cleanup karo" → desktop_cleanup
   "Junk files hatao" → desktop_cleanup

-> 'desktop_kill_process (process name)' — Process/app kill karo.
   "Chrome band karo" → desktop_kill_process chrome
   "Spotify kill karo" → desktop_kill_process spotify
   "Koi atak hua app band karo" → desktop_kill_process

-> 'desktop_hibernate / desktop_sleep / desktop_lock' — Power actions.
   "PC hibernate karo" → desktop_hibernate
   "System so jao" → desktop_sleep
   "PC lock karo" → desktop_lock

-> 'desktop_file_copy (src > dst)' — File copy karna.
   "C:\file.txt ko D:\Backup mein copy karo" → desktop_file_copy C:\file.txt > D:\Backup
   
-> 'desktop_file_move (src > dst)' — File move karna.
   "report.pdf ko Desktop se Documents mein move karo" → desktop_file_move ...

-> 'desktop_folder_create (path)' — Naya folder banana.
   "Desktop pe MyProject naam ka folder banao" → desktop_folder_create C:\Users\...\Desktop\MyProject
   
-> 'desktop_file_create (path)' — Naya file banana.
   "notes.txt file banao Desktop pe" → desktop_file_create C:\Users\...\Desktop\notes.txt

-> 'desktop_open_path (path)' — File/folder open karna.
   "D:\Projects folder open karo" → desktop_open_path D:\Projects
   
-> 'desktop_open_app (app name)' — Local app open karna.
   "Notepad open karo" → desktop_open_app notepad
   "Calculator chalao" → desktop_open_app calculator

-> 'desktop_close_app (app name)' — App close karna.
   "Notepad band karo" → desktop_close_app notepad

-> 'desktop_system_stats' — CPU/RAM/Disk stats.
   "System ka haal batao" → desktop_system_stats
   "CPU/RAM kitna use ho raha hai?" → desktop_system_stats

-> 'desktop_battery' — Battery status.
   "Battery kitni hai?" → desktop_battery
   "Charging ho rahi hai kya?" → desktop_battery

-> 'desktop_recycle_bin' — Recycle bin empty karna.
   "Recycle bin khali karo" → desktop_recycle_bin

-> 'desktop_updates' — Windows update check.
   "Updates check karo" → desktop_updates

-> 'desktop_network' — Network info.
   "Mera IP address kya hai?" → desktop_network
   "WiFi connected hai?" → desktop_network
   
-> 'desktop_clipboard (action/text)' — Clipboard operations.
   "Clipboard mein kya hai?" → desktop_clipboard get
```

**3c. Few-shot examples** add honge `_TASK_FEW_SHOTS` mein:
```python
("volume 50 pe set karo", "desktop_volume set 50"),
("screenshot lo", "desktop_screenshot"),
("brightness 60 karo", "desktop_brightness set 60"),
("chrome kill karo", "desktop_kill_process chrome"),
("PC lock karo", "desktop_lock"),
("system hibernate", "desktop_hibernate"),
("temp files clean karo", "desktop_cleanup"),
("battery status batao", "desktop_battery"),
("notepad open karo", "desktop_open_app notepad"),
# ... and more
```

**3d. `_parse_task_decisions`** mein `TASK_PREFIXES` list mein naye `desktop_*` prefixes add honge.

**3e. `NORMALIZE` dict** mein space-variant mappings add honge (e.g., `"desktop file copy"` → `"desktop_file_copy"`).

---

### Layer 4 — Task Executor Updates

#### [MODIFY] `app/services/task_executor.py`

- `DesktopService` import hoga
- `__init__` mein `self.desktop_service = DesktopService()` initialize hoga
- `execute()` mein naye `elif` blocks add honge har desktop intent ke liye:

```python
elif intent_type == INTENT_DESKTOP_VOLUME:
    tasks.append(("desktop", self._do_desktop_volume, payload))

elif intent_type == INTENT_DESKTOP_SCREENSHOT:
    tasks.append(("desktop", self._do_desktop_screenshot, payload))

# ... etc
```

- Private methods add honge jo `DesktopService` ko call karenge:
```python
def _do_desktop_volume(self, payload: dict) -> str:
    query = payload.get("query", "")
    return self.desktop_service.set_volume_from_text(query)

def _do_desktop_screenshot(self, payload: dict) -> str:
    return self.desktop_service.take_screenshot()

# ... etc
```

---

### Layer 5 — Skills Init

#### [MODIFY] `app/services/skills/__init__.py`

```python
from app.services.skills.desktop_service import DesktopService
# __all__ mein add karo
```

---

### Layer 6 — Requirements

#### [MODIFY] `requirements.txt`

Naye dependencies add honge:

```
psutil>=5.9.0           # Process management, system stats, battery
pyautogui>=0.9.54       # Screenshot, mouse/keyboard
Pillow>=10.0.0          # Image handling (pyautogui ke saath)
pycaw>=20240210         # Windows volume control
screen-brightness-control>=0.16.0  # Brightness control
winshell>=0.6           # Recycle bin operations
pywin32>=306            # Windows API (winshell needs this)
pyperclip>=1.8.2        # Clipboard operations
```

> [!NOTE]
> `pyautogui` ko `Pillow` chahiye screenshot ke liye. `winshell` ko `pywin32` chahiye. Ye sab Windows-only packages hain jo sirf local machine pe chalenge — bilkul theek hai kyunki JARVIS personal use ke liye hai.

---

## Natural Language Parsing Strategy (Key Design Decision)

> [!IMPORTANT]
> **Ye sabse important part hai.** User kuch bhi bol sakta hai — Hindi, English, Hinglish. Brain (LLM) already bahut intelligent hai, lekin structured query extract karna zaroori hai.

**Approach:**

```
User input → Brain LLM → "desktop_volume set 50"
                              │
                              ▼
              DesktopService.set_volume_from_text("set 50")
                              │
                   parse "set" + "50" → volume = 50
                              │
                   pycaw → set Windows volume to 50%
```

Har `DesktopService` method ke andar **smart text parsing** hoga:

```python
def set_volume_from_text(self, query: str) -> str:
    q = query.lower().strip()
    
    if "mute" in q or "band" in q:
        return self.mute_volume()
    
    if "unmute" in q or "chalu" in q:
        return self.unmute_volume()
    
    if "get" in q or "kitna" in q or "batao" in q:
        return self.get_volume()
    
    # Extract number: "set 50", "50 karo", "50%", etc.
    numbers = re.findall(r'\d+', q)
    if numbers:
        level = max(0, min(100, int(numbers[0])))
        return self.set_volume(level)
    
    if any(x in q for x in ["kam", "decrease", "down", "lower"]):
        return self.adjust_volume(-10)  # current - 10
    
    if any(x in q for x in ["zyada", "increase", "up", "higher"]):
        return self.adjust_volume(+10)
    
    return "Volume ke liye level bolo, jaise '50' ya 'mute'."
```

**Isi pattern se sab methods kaam karenge** — robust, Hindi+English aware.

---

## App Name Resolution (Local Apps)

`desktop_open_app` ke liye ek smart app name → executable mapping:

```python
APP_MAP = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "firefox": r"C:\Program Files\Mozilla Firefox\firefox.exe",
    "vlc": r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    "vs code": r"C:\Users\...\AppData\Local\Programs\Microsoft VS Code\Code.exe",
    "spotify": r"C:\Users\...\AppData\Roaming\Spotify\Spotify.exe",
    "word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
    "paint": "mspaint.exe",
    "task manager": "taskmgr.exe",
    "file explorer": "explorer.exe",
    # ... etc
}
```

Agar app map mein nahi milta, to `subprocess.Popen(app_name, shell=True)` try karega.

---

## Potential Problems & Solutions

| Problem | Solution |
|---|---|
| `pycaw` COM initialization error | Har call mein `CoInitialize()` + `CoUninitialize()` wrap karein |
| Brightness control laptop/desktop difference | Try `screen-brightness-control`, fallback WMI PowerShell command |
| File paths Hindi/natural language mein hain | Brain LLM ko path extract karne ki responsibility, desktop service ko clean path milega |
| `pyautogui` screenshot DISPLAY error (headless) | Try/except se handle, meaningful error message |
| App name case mismatch (Chrome vs chrome.exe) | `psutil` mein case-insensitive search |
| Recycle bin `winshell` pywin32 dependency | Version pinning, installation guide |
| Windows Update command (wuauclt deprecated) | PowerShell `Get-WindowsUpdate` ya USOClient use karein |
| Temp file deletion — kuch files lock hain | `ignore_errors=True` with `shutil.rmtree`, count jo delete huye |
| Screenshot save path | Default: `C:\Users\{username}\Desktop\JARVIS_Screenshot_{timestamp}.png` |

---

## Implementation Order (Step-by-Step)

> [!NOTE]
> Ye order is liye important hai — pehle infrastructure, phir features. Har step ke baad JARVIS restart karke test kar sakte hain.

**Step 1:** `requirements.txt` update + packages install karo
**Step 2:** `desktop_service.py` create karo (skeleton + Volume + Screenshot + Brightness)
**Step 3:** `decision_types.py` mein naye intents add karo
**Step 4:** `brain_service.py` update karo (TaskType list + prompts + few-shots + parsing)
**Step 5:** `task_executor.py` update karo (import + init + execute cases + private methods)
**Step 6:** `skills/__init__.py` update karo
**Step 7:** Test: Volume, Screenshot, Brightness
**Step 8:** `desktop_service.py` mein baki features add karo (File ops, Process, Power, System stats)
**Step 9:** Full testing sab commands ke saath

---

## Extra Improvements (Meri Suggestions)

Ye tumhara original scope se bahar hain, but bahut value add karenge:

| Feature | Benefit |
|---|---|
| **`desktop_list_apps`** | "Kaun kaun se apps chal rahe hain?" — psutil se running processes list |
| **`desktop_battery`** | "Battery 20% hai, charging lagao" — startup brief mein bhi use ho sakta hai |
| **`desktop_system_stats`** | CPU/RAM real-time — health monitoring |
| **`desktop_network`** | IP address, WiFi status — IT help ke liye useful |
| **`desktop_clipboard`** | Copy/paste automation — workflow mein helpful |
| **Screenshot auto-open** | Screenshot lene ke baad automatically open karo — better UX |
| **Volume feedback** | "Volume 60 pe set kar diya" + current level confirm karo |

---

## Verification Plan

**Manual test cases (natural language):**
```
"Volume 40 pe set karo"              → Volume set ✓
"Awaaz mute karo"                    → Mute ✓  
"Screenshot lo"                      → PNG file Desktop pe save ✓
"Brightness 70 karo"                 → Brightness set ✓
"Temp files clean karo"              → Cleanup + count report ✓
"Notepad open karo"                  → notepad.exe launch ✓
"Chrome band karo"                   → chrome process kill ✓
"PC lock karo"                       → Windows lock screen ✓
"Battery kitni hai?"                 → Battery % + charging status ✓
"CPU RAM kitna use ho raha hai?"     → System stats ✓
"Desktop pe notes naam ka folder banao" → Folder create ✓
"Recycle bin khali karo"             → Bin empty ✓
"Mera IP address kya hai?"           → Network info ✓
```

**Hindi + Hinglish variations test:** Har feature ke liye 2-3 alag tarike se bolke test karna hai.
