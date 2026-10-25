@echo off
rem One-command wrapper around push_to_github.ps1 (works from cmd.exe too).
rem
rem   scripts\push_to_github.cmd https://github.com/USER/REPO.git
rem   scripts\push_to_github.cmd https://github.com/USER/REPO.git -DryRun
rem
rem Double-clicking it without arguments prints the usage.
setlocal
if "%~1"=="" (
    echo Usage: scripts\push_to_github.cmd https://github.com/USER/REPO.git
    pause
    exit /b 2
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0push_to_github.ps1" -RepoUrl "%~1" %2 %3 %4
if errorlevel 1 (
    echo.
    echo Failed - read the messages above.
    pause
    exit /b 1
)
endlocal
exit /b 0
