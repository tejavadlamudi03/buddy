#!/bin/bash
# Lets Buddy hear and speak Hindi and German. Downloads the open Whisper model that knows them (about 1.5 GB, or
# 500 MB for the small one) and the two voices (about 130 MB). No sudo needed. Typing in them works without this.
#   scripts/install-languages.sh [medium|small]
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODEL="${1:-medium}"
unset PYTHONPATH  # the ROS environment breaks Python packages here

"$ROOT/.venv/bin/python" - "$MODEL" <<'PY'
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
PY
echo "Done. Restart Buddy to use it: systemctl --user restart buddy"
