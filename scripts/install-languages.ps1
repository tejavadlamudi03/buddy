# Lets Buddy hear and speak Hindi and German. Downloads the open Whisper model that knows them (about 1.5 GB, or
# 500 MB for the small one) and the two voices (about 130 MB). No administrator needed. Typing in them works without this.
#   powershell -ExecutionPolicy Bypass -File scripts\install-languages.ps1 [medium|small]
param([ValidateSet('medium', 'small')][string]$Model = 'medium')

$Root = Split-Path -Parent $PSScriptRoot
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$script = Join-Path ([IO.Path]::GetTempPath()) "buddy-languages-$PID.py"
@'
import sys
from faster_whisper.utils import download_model
from buddy import config, tts
print(f"Downloading the {sys.argv[1]} speech model...", flush=True)
download_model(sys.argv[1])
settings = config.load()
settings["languages_whisper_model"] = sys.argv[1]
config.save(settings)
for name in ("hindi", "german"):
    print(f"Downloading the {name} voice...", flush=True)
    tts._voice_path(*tts.VOICES[name])
'@ | Set-Content -Path $script -Encoding ASCII
& "$Root\.venv\Scripts\python.exe" $script $Model
$result = $LASTEXITCODE
Remove-Item $script
if ($result -ne 0) { exit $result }
Write-Host 'Done. Log out and back in, or restart Buddy, to use it.'
exit 0
