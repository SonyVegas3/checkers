#Requires -Version 5.1
<#
.SYNOPSIS
    Puts this folder into a GitHub repository and tells you how to get the .ipa.

.DESCRIPTION
    One command, no GitHub CLI needed:

        .\scripts\push_to_github.ps1 -RepoUrl https://github.com/<you>/<repo>.git

    It initialises the repository if needed, commits everything (the .gitignore
    keeps .venv, dist/, logs/, .browser-profile/ and the iOS build folders out),
    adds "origin" and pushes.  The iOS build then runs on a GitHub macOS runner
    (.github/workflows/ios.yml) - iOS binaries cannot be produced on Windows.

.EXAMPLE
    .\scripts\push_to_github.ps1 -RepoUrl https://github.com/ika/checkersbot.git
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$RepoUrl,

    [string]$Branch = "main",

    [string]$Message = "CheckersBot: checkers engine + jokergame.cc bot + iOS app",

    # stage and commit, print the plan, but do not add a remote or push
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git is not installed - install Git for Windows first."
    }
    if (-not (Test-Path (Join-Path $root ".gitignore"))) {
        throw "no .gitignore in $root - is this the CheckersBot folder?"
    }

    if (-not (Test-Path (Join-Path $root ".git"))) {
        Write-Host "==> git init ($Branch)"
        git init -b $Branch | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "git init failed" }
    }

    $name = git config --get user.name
    $email = git config --get user.email
    if (-not $name -or -not $email) {
        git config user.name "CheckersBot"
        git config user.email "checkersbot@localhost"
        Write-Host "    (local git identity set to CheckersBot <checkersbot@localhost>)"
    }

    Write-Host "==> staging files"
    git add -A
    if ($LASTEXITCODE -ne 0) { throw "git add failed" }

    $staged = @(git diff --cached --name-only)
    $forbidden = $staged | Where-Object {
        $_ -match '^(\.venv|\.venv-ios|\.browser-profile[^/]*|\.backup|dist|logs|build|build-ios|build-ipa|iOS|__pycache__)/'
    }
    if ($forbidden) {
        throw "refusing to commit local state ($($forbidden -join ', ')) - fix .gitignore first"
    }

    if ($staged.Count -eq 0) {
        Write-Host "    nothing new to commit"
    } else {
        Write-Host "==> committing $($staged.Count) file(s)"
        git commit -m $Message | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "git commit failed" }
    }

    $slug = $RepoUrl.Trim()
    $slug = $slug -replace '^git@github\.com:', ''
    $slug = $slug -replace '^https?://github\.com/', ''
    $slug = $slug -replace '\.git$', ''
    $actions = "https://github.com/$slug/actions/workflows/ios.yml"

    if ($DryRun) {
        Write-Host ""
        Write-Host "Dry run - nothing was pushed. Would run:" -ForegroundColor Yellow
        Write-Host "    git remote add origin $RepoUrl      (set-url if origin exists)"
        Write-Host "    git push -u origin $Branch"
        Write-Host "    then start the build: $actions"
        return
    }

    $remotes = @(git remote)
    if ($remotes -contains "origin") {
        git remote set-url origin $RepoUrl
    } else {
        git remote add origin $RepoUrl
    }

    Write-Host "==> pushing to $RepoUrl"
    git push -u origin $Branch
    if ($LASTEXITCODE -ne 0) {
        throw "git push failed - log in when Git asks (browser window / personal access token)"
    }

    Write-Host ""
    Write-Host "Pushed. Now build the iPhone app:" -ForegroundColor Green
    Write-Host "  1. open   $actions"
    Write-Host "  2. press  'Run workflow' (branch $Branch) - takes ~10-20 min"
    Write-Host "  3. download the artifact  CheckersBot-unsigned-ipa  (a zip with dist/*.ipa)"
    Write-Host "  4. sign the .ipa with iLoader and install it on the phone"
    Write-Host ""
    Write-Host "Tip: a public repository gets free macOS minutes; private ones are billed"
    Write-Host "     at 10x (one build is roughly 15-25 of those minutes)."

    if (Get-Command gh -ErrorAction SilentlyContinue) {
        Write-Host ""
        Write-Host "GitHub CLI found - you can also start the build from here:"
        Write-Host "    gh workflow run ios.yml --repo $slug"
    }
}
finally {
    Pop-Location
}
