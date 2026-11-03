# Sets up Buddy on a Windows laptop in one go, choosing what suits its hardware. Safe to run again.
# Run it from a normal PowerShell window, not as administrator:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1                 checks the laptop, then installs everything
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Check          only checks and says what it would choose
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Setup light    full, light or basic, instead of the one chosen
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -NoAi           the same as -Setup basic: built-in commands only
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Yes            doesn't stop to ask before installing programs
param(
    [switch]$Check,
    [ValidateSet('full', 'light', 'basic')][string]$Setup = '',
    [switch]$NoAi,
    [switch]$Yes
)

$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root '.venv\Scripts'
$MinVideoMb = 7500  # an "8 GB" card reports a little under 8192
$AiMemoryGb = 15    # "16 GB" reports a little under
$MinWindowsBuild = 17763
$Programs = [ordered]@{
    'Gyan.FFmpeg'                        = 'FFmpeg, for voice effects and converting audio and video'
    'oschwartz10612.Poppler'             = 'Poppler, for reading PDFs'
    'TheDocumentFoundation.LibreOffice'  = 'LibreOffice, for converting and reading documents'
}
$script:Problems = 0

function Ok($text) { Write-Host "  ok       $text" }
function Warn($text) { Write-Host "  warning  $text" }
function Problem($text) { Write-Host "  PROBLEM  $text"; $script:Problems++ }
function Step($text) { Write-Host ''; Write-Host "== $text" }
function Stop-OnFailure($text) {
    Write-Host ''
    Write-Host "Setup stopped at: $text. Fix that and run this script again, and it picks up from there."
    exit 1
}
function Update-Path {
    # What winget just added only reaches new windows unless it's read again.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Test-System {
    $build = [Environment]::OSVersion.Version.Build
    if ($build -ge $MinWindowsBuild) { Ok "Windows build $build" } else { Problem "Windows build $build is too old. Buddy needs Windows 10 version 1809 or newer." }
    if ([Environment]::Is64BitOperatingSystem -and $env:PROCESSOR_ARCHITECTURE -eq 'AMD64') {
        Ok '64-bit Intel or AMD processor'
    } else {
        Problem "Needs a 64-bit Intel or AMD processor, not $env:PROCESSOR_ARCHITECTURE."
    }
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($admin) { Warn 'This window is running as administrator. Buddy runs fine without it, and a normal window is safer.' }
}

function Get-VideoMb {
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) { return 0 }
    $cards = & nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $cards) { return 0 }
    return ($cards | ForEach-Object { [int]$_ } | Measure-Object -Maximum).Maximum
}

# Chooses rather than refuses: a weaker laptop gets a smaller model, or the built-in commands.
function Select-Setup {
    $memory = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1000000000)
    $threads = [Environment]::ProcessorCount
    $disk = Get-PSDrive -Name ([IO.Path]::GetPathRoot($env:USERPROFILE).Substring(0, 1))
    $freeGb = [math]::Floor($disk.Free / 1000000000)
    $video = Get-VideoMb
    if ($memory -lt 7) { Problem "Only $memory GB of memory. Buddy needs at least 8 GB." }
    if ($freeGb -lt 5) { Problem "Only $freeGb GB of free disk. Buddy needs at least 5 GB." }
    Ok "$memory GB of memory, $threads processor threads, $freeGb GB of free disk"
    if ($video) { Ok "NVIDIA graphics card with $video MB of video memory" } else { Warn 'No working NVIDIA card found. If you have one, install its driver from nvidia.com, then restart.' }

    if ($memory -lt $AiMemoryGb) { $chosen = 'basic'; $why = 'the local AI needs 16 GB of memory' }
    elseif ($freeGb -lt 12) { $chosen = 'basic'; $why = 'the local AI needs about 12 GB of free disk' }
    elseif ($video -ge $MinVideoMb -and $freeGb -ge 15) { $chosen = 'full'; $why = 'this graphics card runs the larger model well' }
    elseif ($video -ge $MinVideoMb) { $chosen = 'light'; $why = 'the larger model needs 15 GB of free disk' }
    elseif ($video) { $chosen = 'light'; $why = 'the larger model needs 8 GB of video memory' }
    else { $chosen = 'light'; $why = 'with no suitable graphics card, the smaller model runs on the processor, more slowly' }

    if ($NoAi) { $script:Setup = 'basic' }
    if ($script:Setup) {
        if ($script:Setup -ne $chosen) { $why = "you asked for it. For this laptop I'd have chosen $chosen, because $why" }
    } else {
        $script:Setup = $chosen
    }
    Write-Host ''
    switch ($script:Setup) {
        'full'  { Write-Host "Setup: full. Gemma 4 (about 6.6 GB) with Telugu, because $why." }
        'light' { Write-Host "Setup: light. A smaller Gemma 4 (about 4.6 GB) with Telugu, because $why." }
        'basic' { Write-Host "Setup: basic. Built-in commands only, with no AI chat, file reading or Telugu, because $why." }
    }
}

function Install-Programs {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Warn 'winget is missing, so FFmpeg, Poppler and LibreOffice were not installed. Get "App Installer" from the Microsoft Store, then run this again.'
        return
    }
    $missing = @()
    foreach ($id in $Programs.Keys) {
        & winget list --id $id --exact --accept-source-agreements *> $null
        if ($LASTEXITCODE -ne 0) { $missing += $id }
    }
    if (-not $missing) { Ok 'Programs are already installed'; return }
    Write-Host '  To install with winget:'
    foreach ($id in $missing) { Write-Host "    $($Programs[$id])" }
    if (-not $Yes) {
        $answer = Read-Host '  Install these now? [y/N]'
        if ($answer -notmatch '^[yY]') { Write-Host 'Stopped, with nothing installed.'; exit 1 }
    }
    foreach ($id in $missing) {
        & winget install --id $id --exact --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { Warn "$id didn't install, so what it's for won't work. The rest of Buddy will." }
    }
    Update-Path
}

Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue

Write-Host 'Checking this laptop'
Test-System
Select-Setup
if ($script:Problems -gt 0) {
    Write-Host ''
    Write-Host "$script:Problems problem(s) to fix first. Nothing has been installed."
    exit 1
}
if ($Check) {
    Write-Host ''
    Write-Host 'This laptop is ready. Run this script without -Check to install Buddy.'
    exit 0
}

Step 'Programs'
Install-Programs

Step 'Python and Buddy'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        & winget install --id astral-sh.uv --exact --silent --accept-package-agreements --accept-source-agreements
    }
    Update-Path
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Stop-OnFailure 'installing uv. Get it from https://docs.astral.sh/uv' }
}
Set-Location $Root
& uv sync --quiet
if ($LASTEXITCODE -ne 0) { Stop-OnFailure "installing Buddy's Python packages" }
Ok 'Buddy is installed'

$Model = & "$Venv\buddy.exe" use-profile $script:Setup
if ($LASTEXITCODE -ne 0) { Stop-OnFailure 'choosing the settings' }
if ($script:Setup -ne 'basic') {
    Step "Local AI: $Model"
    & "$PSScriptRoot\install-local-ai.ps1" $Model
    if ($LASTEXITCODE -ne 0) { Stop-OnFailure 'the local AI' }
    Step 'Telugu speech model (about 750 MB when done)'
    & "$PSScriptRoot\install-telugu.ps1"
    if ($LASTEXITCODE -ne 0) { Stop-OnFailure 'the Telugu speech model' }
    Step 'Hindi and German: speech model and voices'
    & "$PSScriptRoot\install-languages.ps1" $(if ($script:Setup -eq 'full') { 'medium' } else { 'small' })
    if ($LASTEXITCODE -ne 0) { Stop-OnFailure 'the Hindi and German speech model' }
}

Step 'Start menu and auto-start'
& "$Venv\buddy.exe" install-launcher
if ($LASTEXITCODE -ne 0) { Stop-OnFailure 'the Start menu entry' }
& "$Venv\buddy.exe" install-autostart
if ($LASTEXITCODE -ne 0) { Stop-OnFailure 'auto-start' }

Step 'Final check'
& "$Venv\buddy.exe" check
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'Buddy is installed, but the check above found a problem to fix. .venv\Scripts\buddy check runs it again.'
}

Write-Host ''
Write-Host 'Open Buddy from the Start menu. It also starts by itself whenever you log in.'
Write-Host 'For WhatsApp, enter your number under Settings, then run: .venv\Scripts\buddy whatsapp-login'
Write-Host "It needs a separate WhatsApp account for this laptop. See 'WhatsApp' in the README."
