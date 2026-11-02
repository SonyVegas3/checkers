@echo off
rem ---------------------------------------------------------------------------
rem  Opens the interactive BACKGAMMON test board (backgammon\board_lab.py).
rem  Double-click this file. No console stays open (pythonw is used).
rem  Needs the .venv-bg environment (bgsage + tkinter are installed there).
rem  ASCII only on purpose: a .bat is read in the console code page.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "PY="
if exist ".venv-bg\Scripts\pythonw.exe" set "PY=.venv-bg\Scripts\pythonw.exe"
if not defined PY if exist ".venv-bg\Scripts\python.exe" set "PY=.venv-bg\Scripts\python.exe"
if not defined PY for %%P in (pythonw.exe) do if not "%%~$PATH:P"=="" set "PY=%%~$PATH:P"
if not defined PY for %%P in (python.exe) do if not "%%~$PATH:P"=="" set "PY=%%~$PATH:P"
if not defined PY (
  echo Python was not found. Install Python 3 or create .venv-bg in this folder.
  pause
  exit /b 1
)

start "" "%PY%" "backgammon\board_lab.py"
endlocal
