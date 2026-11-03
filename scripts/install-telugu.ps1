# Lets Buddy hear Telugu. Downloads an open Whisper model tuned for Telugu (vasista22/whisper-telugu-medium,
# Apache 2.0) and converts it for Buddy, in a temporary environment. No administrator needed, about 750 MB when done.
$Source = 'vasista22/whisper-telugu-medium'
$Target = Join-Path $env:LOCALAPPDATA 'Buddy\whisper-telugu-medium'

if ((Test-Path "$Target\model.bin") -and (Test-Path "$Target\tokenizer.json")) {
    Write-Host 'The Telugu model is already installed.'
    exit 0
}

Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$Work = Join-Path ([IO.Path]::GetTempPath()) "buddy-telugu-$PID"
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$result = 1
try {
    & uv venv --quiet --python 3.12 "$Work\env"
    if ($LASTEXITCODE -ne 0) { return }
    $env:VIRTUAL_ENV = "$Work\env"
    & uv pip install --quiet ctranslate2 'transformers<5' torch --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple
    if ($LASTEXITCODE -ne 0) { return }

    Write-Host "Downloading and converting $Source..."
    & "$Work\env\Scripts\ct2-transformers-converter.exe" --model $Source --output_dir "$Work\model" --quantization int8 --copy_files preprocessor_config.json
    if ($LASTEXITCODE -ne 0) { return }
    # This model ships no tokenizer.json, which Buddy's Whisper needs.
    & "$Work\env\Scripts\python.exe" -c 'import sys; from transformers import WhisperTokenizerFast; WhisperTokenizerFast.from_pretrained(sys.argv[1]).save_pretrained(sys.argv[2])' $Source "$Work\tokenizer"
    if ($LASTEXITCODE -ne 0) { return }
    Copy-Item "$Work\tokenizer\tokenizer.json" "$Work\model\"

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target) | Out-Null
    Move-Item "$Work\model" $Target
    $result = 0
    Write-Host 'Done. Log out and back in, or restart Buddy, to use it.'
} finally {
    Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
    Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
}
exit $result
