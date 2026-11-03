# Installs Buddy's local AI into AppData\Local\Buddy\ollama. No administrator needed, nothing system-wide.
# Downloads Ollama (about 1.6 GB, checksum verified) and the open source model (about 6 GB).
param([string]$Model = 'gemma4:e4b-it-q4_K_M')

$Version = 'v0.40.13'
$Dir = Join-Path $env:LOCALAPPDATA 'Buddy\ollama'
$Archive = 'ollama-windows-amd64.zip'
$Base = "https://github.com/ollama/ollama/releases/download/$Version"
$AiHost = '127.0.0.1:11435'

New-Item -ItemType Directory -Force -Path "$Dir\download", "$Dir\models" | Out-Null
$Ollama = Join-Path $Dir 'ollama.exe'

function Test-Up {
    try { Invoke-RestMethod "http://$AiHost/api/version" -TimeoutSec 1 | Out-Null; return $true } catch { return $false }
}

if (-not (Test-Path $Ollama)) {
    Write-Host "Downloading Ollama $Version..."
    $zip = Join-Path $Dir "download\$Archive"
    $sums = Join-Path $Dir 'download\sha256sum.txt'
    & curl.exe -fSL -o $sums "$Base/sha256sum.txt"
    if ($LASTEXITCODE -ne 0) { Write-Host 'Could not download the checksums.'; exit 1 }
    & curl.exe -fSL -C - -o $zip "$Base/$Archive"
    if ($LASTEXITCODE -ne 0) { Write-Host 'Could not download Ollama.'; exit 1 }
    $line = Get-Content $sums | Where-Object { $_ -match "[ /*]$([regex]::Escape($Archive))$" } | Select-Object -First 1
    $want = if ($line) { ($line -split '\s+')[0] } else { '' }
    $got = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if (-not $want -or $want -ne $got) {
        Write-Host "Checksum mismatch, so nothing was installed. Expected '$want', got '$got'."
        exit 1
    }
    & tar.exe -xf $zip -C $Dir  # much faster than Expand-Archive on a file this size
    if ($LASTEXITCODE -ne 0) { Write-Host 'Could not unpack Ollama.'; exit 1 }
    Remove-Item $zip
}

$env:OLLAMA_HOST = $AiHost
$env:OLLAMA_MODELS = "$Dir\models"
$started = $null
if (-not (Test-Up)) {
    $started = Start-Process -FilePath $Ollama -ArgumentList 'serve' -WindowStyle Hidden -PassThru
    foreach ($try in 1..40) {
        if (Test-Up) { break }
        Start-Sleep -Milliseconds 500
    }
}

Write-Host "Downloading the model $Model..."
& $Ollama pull $Model
$pulled = $LASTEXITCODE
& $Ollama list
if ($started) { Stop-Process -Id $started.Id -ErrorAction SilentlyContinue }
if ($pulled -ne 0) { Write-Host 'The model did not download.'; exit 1 }
Write-Host 'Done. Buddy will start the local AI by itself when it needs it.'
exit 0
