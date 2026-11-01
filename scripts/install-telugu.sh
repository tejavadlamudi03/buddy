#!/bin/bash
# Lets Buddy hear Telugu. Downloads an open Whisper model tuned for Telugu (vasista22/whisper-telugu-medium,
# Apache 2.0) and converts it for Buddy, in a temporary environment. No sudo needed, about 750 MB when done.
set -euo pipefail

SOURCE=vasista22/whisper-telugu-medium
TARGET="$HOME/.local/share/buddy/whisper-telugu-medium"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if [ -f "$TARGET/model.bin" ] && [ -f "$TARGET/tokenizer.json" ]; then
    echo "The Telugu model is already installed."
    exit 0
fi

unset PYTHONPATH  # the ROS environment breaks Python packages here
uv venv --quiet --python 3.12 "$WORK/env"
VIRTUAL_ENV="$WORK/env" uv pip install --quiet ctranslate2 'transformers<5' torch \
    --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple

echo "Downloading and converting $SOURCE..."
"$WORK/env/bin/ct2-transformers-converter" --model "$SOURCE" --output_dir "$WORK/model" \
    --quantization int8 --copy_files preprocessor_config.json
# This model ships no tokenizer.json, which Buddy's Whisper needs.
"$WORK/env/bin/python" -c "
import sys
from transformers import WhisperTokenizerFast
WhisperTokenizerFast.from_pretrained(sys.argv[1]).save_pretrained(sys.argv[2])" "$SOURCE" "$WORK/tokenizer"
cp "$WORK/tokenizer/tokenizer.json" "$WORK/model/"

mkdir -p "$(dirname "$TARGET")"
mv "$WORK/model" "$TARGET"
echo "Done. Restart Buddy to use it: systemctl --user restart buddy"
