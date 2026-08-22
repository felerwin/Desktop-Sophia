param(
    [switch]$SkipKokoro,
    [switch]$Portable
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-Python312 {
    $launchers = @(
        @{ File = "py"; Args = @("-3.12") },
        @{ File = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"; Args = @() },
        @{ File = "C:\Program Files\Python312\python.exe"; Args = @() }
    )
    foreach ($entry in $launchers) {
        try {
            $version = & $entry.File @($entry.Args) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $version -eq "3.12") { return $entry }
        } catch { }
    }
    return $null
}

Write-Host "Desktop Sophia Windows Installer" -ForegroundColor Magenta
if ($Portable) {
    Write-Host "Portable mode: runtime, memory, configuration, logs, and model cache stay with this folder."
    Write-Host "Note: the host PC still needs Windows-compatible drivers/audio support; Python is only used to build the portable environments."
    $env:HF_HOME = Join-Path $ProjectRoot ".cache\huggingface"
    $env:XDG_CACHE_HOME = Join-Path $ProjectRoot ".cache"
    New-Item -ItemType Directory -Force -Path $env:HF_HOME | Out-Null
}

$Python = Find-Python312
if ($null -eq $Python) {
    Write-Step "Installing Python 3.12"
    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($null -eq $Winget) { throw "Python 3.12 is required for setup and Winget is unavailable. Install Python 3.12, then run this installer again." }
    & winget install --id Python.Python.3.12 --exact --scope user --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) { throw "Winget could not install Python 3.12 (exit code $LASTEXITCODE)." }
    $Python = Find-Python312
    if ($null -eq $Python) { throw "Python installed, but its executable could not be found. Restart Windows and run setup_windows.bat again." }
}

function Invoke-Python312([string[]]$Arguments) {
    & $Python.File @($Python.Args) @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python command failed with exit code $LASTEXITCODE." }
}

Write-Step "Creating Sophia's application environment"
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) { Invoke-Python312 @("-m", "venv", ".venv") }
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not update pip in .venv." }
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Could not install Sophia's application dependencies." }

if (-not $SkipKokoro) {
    Write-Step "Creating the local Kokoro voice environment"
    if (-not (Test-Path -LiteralPath ".kokoro_venv\Scripts\python.exe")) { Invoke-Python312 @("-m", "venv", ".kokoro_venv") }
    & ".kokoro_venv\Scripts\python.exe" -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Could not update pip in .kokoro_venv." }

    $HasNvidia = $null -ne (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
    if ($HasNvidia) {
        Write-Step "Installing CUDA-enabled PyTorch"
        & ".kokoro_venv\Scripts\python.exe" -m pip install torch --index-url https://download.pytorch.org/whl/cu126
    } else {
        Write-Step "Installing CPU PyTorch"
        & ".kokoro_venv\Scripts\python.exe" -m pip install torch
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not install PyTorch for Kokoro." }

    Write-Step "Installing Kokoro and audio playback"
    & ".kokoro_venv\Scripts\python.exe" -m pip install "kokoro>=0.9.4,<1" "sounddevice>=0.4.6,<1" "soundfile>=0.12,<1"
    if ($LASTEXITCODE -ne 0) { throw "Could not install Kokoro's remaining dependencies." }

    Write-Step "Downloading Sophia's Kokoro model and Lily voice"
    & ".kokoro_venv\Scripts\python.exe" -c "from kokoro import KPipeline; p=KPipeline(lang_code='b', repo_id='hexgrad/Kokoro-82M', device='cpu'); next(iter(p('Sophia is ready.', voice='bf_lily'))); print('Kokoro model and voice cached')"
    if ($LASTEXITCODE -ne 0) { throw "Could not download or validate the Kokoro model and Lily voice." }
}

Write-Step "Creating private local configuration"
if (-not (Test-Path -LiteralPath ".env")) { Copy-Item -LiteralPath ".env.example" -Destination ".env" }
if (-not (Test-Path -LiteralPath "config.json")) { Copy-Item -LiteralPath "config.example.json" -Destination "config.json" }
if (-not (Test-Path -LiteralPath "youtube_library.json")) { Set-Content -LiteralPath "youtube_library.json" -Value "[]" -Encoding UTF8 }
New-Item -ItemType Directory -Force -Path "soundboard\archive" | Out-Null
if (-not (Test-Path -LiteralPath "soundboard\library.json")) { Set-Content -LiteralPath "soundboard\library.json" -Value "{}" -Encoding UTF8 }

if ($Portable) {
    $config = Get-Content -LiteralPath "config.json" -Raw | ConvertFrom-Json
    $config | Add-Member -NotePropertyName portable_mode -NotePropertyValue $true -Force
    $config.kokoro_python = ".kokoro_venv\Scripts\python.exe"
    $config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath "config.json" -Encoding UTF8
    Set-Content -LiteralPath ".portable" -Value "Desktop Sophia portable installation" -Encoding UTF8
}

Write-Step "Checking the installation"
& ".venv\Scripts\python.exe" -m py_compile sophia.py dashboard_server.py game_events.py memory_store.py spotify_control.py wow_pixel_bridge.py
if ($LASTEXITCODE -ne 0) { throw "Sophia's Python validation failed." }
if (-not $SkipKokoro) {
    & ".kokoro_venv\Scripts\python.exe" -c "import kokoro, sounddevice, torch; print('Kokoro ready; device=' + ('cuda' if torch.cuda.is_available() else 'cpu'))"
    if ($LASTEXITCODE -ne 0) { throw "Kokoro validation failed." }
}

Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
if ($Portable) { Write-Host "Portable Sophia is ready. You can move this entire folder to a sufficiently large USB drive." -ForegroundColor Green }
Write-Host "Open .env, add your OpenAI API key, then run run_sophia.bat."
