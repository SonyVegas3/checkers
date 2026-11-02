@echo off
rem ---------------------------------------------------------------------------
rem  Opens the interactive checkers test board (board_lab.py).
rem  Default position: the one from the analysis report - red man b2 takes c3,
rem  then either b6 (2 pieces) or h4 (3 pieces).
rem  Double-click this file. No console stays open (pythonw is used).
rem  Note: the preset is passed as the ASCII fragment "b2" on purpose - a .bat is
rem  read in the console code page, so non-ASCII text here would arrive mangled.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "PY="
if exist ".venv\Scripts\pythonw.exe" set "PY=.venv\Scripts\pythonw.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY for %%P in (pythonw.exe) do if not "%%~$PATH:P"=="" set "PY=%%~$PATH:P"
if not defined PY for %%P in (python.exe) do if not "%%~$PATH:P"=="" set "PY=%%~$PATH:P"
if not defined PY (
  echo Python was not found. Install Python 3 or create .venv in this folder.
  pause
  exit /b 1
)

start "" "%PY%" "board_lab.py" --preset b2
endlocal
