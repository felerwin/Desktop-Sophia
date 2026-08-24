param(
    [string]$Branch = "main",
    [switch]$SkipDependencyRefresh
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

if (-not (Test-Path -LiteralPath ".git")) {
    throw "This Ember installation is not connected to GitHub. Install the current GitHub release once before using the updater."
}

if ($null -eq (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required to update Ember. Install Git for Windows and try again."
}

$TrackedChanges = & git status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0) { throw "Could not inspect the Ember installation." }
if ($TrackedChanges) {
    throw "Ember's program files have local changes, so the updater stopped instead of overwriting them. Personal configuration and memory files do not trigger this check."
}

Write-Step "Checking GitHub for Ember updates"
& git fetch origin $Branch
if ($LASTEXITCODE -ne 0) { throw "Could not fetch the latest Ember update from GitHub." }

$Current = (& git rev-parse HEAD).Trim()
$Target = (& git rev-parse "origin/$Branch").Trim()
if ($LASTEXITCODE -ne 0) { throw "The GitHub branch '$Branch' was not found." }

if ($Current -ne $Target) {
    Write-Step "Updating Ember's program files"
    & git merge --ff-only "origin/$Branch"
    if ($LASTEXITCODE -ne 0) {
        throw "This installation cannot fast-forward safely. No files were overwritten; reinstall or ask for help reconciling the local branch."
    }
} else {
    Write-Host "Ember is already current."
}

if (-not $SkipDependencyRefresh) {
    Write-Step "Refreshing application dependencies and validating Ember"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ProjectRoot\install_windows.ps1"
    if ($LASTEXITCODE -ne 0) { throw "The code update succeeded, but dependency validation failed." }
}

Write-Host ""
Write-Host "Ember is up to date. Her configuration, memory, logs, voice cache, and environments were preserved." -ForegroundColor Green
