@echo off
REM ===========================================================================
REM  J.A.R.V.I.S launcher (smart / self-cleaning)
REM  1) Kills any stale server still holding port 8000 (fixes "refused to
REM     connect" caused by a leftover/zombie process from a previous run)
REM  2) Starts the backend (run.py) in its own window
REM  3) Waits until it is online, then opens JARVIS + the dashboard
REM ===========================================================================

title J.A.R.V.I.S Launcher
cd /d "%~dp0"

REM --- Pick a Python: prefer a local virtual environment if one exists ---
set "PY=python"
if exist "venv\Scripts\python.exe"  set "PY=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo.
echo  ============================================
echo   J.A.R.V.I.S - Smart Launcher
echo  ============================================
echo   Python : %PY%
echo   Folder : %CD%
echo.

REM --- Step 1: free port 8000 (kill any stale/zombie server holding it) ---
echo  [1/3] Checking for an existing server on port 8000...
set "_killed="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo         - Stopping stale process PID %%P ...
    taskkill /F /PID %%P >nul 2>&1
    set "_killed=1"
)
REM also close any leftover server window from a previous run
taskkill /F /FI "WINDOWTITLE eq J.A.R.V.I.S Server*" >nul 2>&1
if defined _killed (
    echo         - Old server stopped. Releasing port...
    timeout /t 2 /nobreak >nul
) else (
    echo         - Port 8000 is free.
)

REM --- Step 2: start the server in its own window so logs stay visible ---
echo  [2/3] Starting the server...
start "J.A.R.V.I.S Server" cmd /k ""%PY%" run.py"

REM --- Step 3: wait until the server answers on port 8000 (max ~60s) ---
echo  [3/3] Waiting for the server to come online (first start can take ~20s)...
set /a _tries=0
:waitloop
set /a _tries+=1
powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8000/health' -TimeoutSec 2) ^| Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 goto ready
if %_tries% geq 60 goto giveup
timeout /t 1 /nobreak >nul
goto waitloop

:ready
echo  Server is up. Opening JARVIS and the dashboard...
start "" "http://localhost:8000/jarvis/"
timeout /t 1 /nobreak >nul
start "" "http://localhost:8000/dashboard"
goto done

:giveup
echo  Server did not respond in time. Check the "J.A.R.V.I.S Server" window for errors.

:done
echo.
echo  All set. JARVIS is running in the "J.A.R.V.I.S Server" window.
echo  (Close THAT window to stop JARVIS. You can close this one.)
timeout /t 5 /nobreak >nul
