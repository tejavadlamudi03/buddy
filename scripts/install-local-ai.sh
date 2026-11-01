#!/bin/bash
# Installs Buddy's local AI into ~/.local/share/buddy/ollama. No sudo needed, nothing system-wide.
# Downloads Ollama (about 1.5 GB, checksum verified) and the open source model (about 6 GB).
set -euo pipefail

VERSION=v0.40.13
MODEL="${1:-gemma4:e4b-it-q4_K_M}"
DIR="$HOME/.local/share/buddy/ollama"
ARCHIVE=ollama-linux-amd64.tar.zst
BASE="https://github.com/ollama/ollama/releases/download/$VERSION"
HOST=127.0.0.1:11435

mkdir -p "$DIR/download" "$DIR/models"
cd "$DIR/download"

if [ ! -x "$DIR/bin/ollama" ]; then
    echo "Downloading Ollama $VERSION..."
    curl -fSL -o sha256sum.txt "$BASE/sha256sum.txt"
    curl -fSL -C - -o "$ARCHIVE" "$BASE/$ARCHIVE"
    want=$(grep -E "[ /*]$ARCHIVE\$" sha256sum.txt | awk '{print $1}')
    got=$(sha256sum "$ARCHIVE" | awk '{print $1}')
    if [ -z "$want" ] || [ "$want" != "$got" ]; then
        echo "Checksum mismatch, so nothing was installed. Expected '$want', got '$got'." >&2
        exit 1
    fi
    if command -v zstd >/dev/null; then
        tar --zstd -xf "$ARCHIVE" -C "$DIR"
    else
        uv run --no-project --with zstandard python - "$ARCHIVE" "$DIR" <<'PY'
import sys, tarfile, zstandard
with open(sys.argv[1], "rb") as f, zstandard.ZstdDecompressor().stream_reader(f) as r, tarfile.open(fileobj=r, mode="r|") as t:
    t.extractall(sys.argv[2], filter="data")
PY
    fi
    rm -f "$ARCHIVE"
fi

export OLLAMA_HOST=$HOST OLLAMA_MODELS="$DIR/models"
started=""
if ! curl -fs "http://$HOST/api/version" >/dev/null; then
    "$DIR/bin/ollama" serve >"$DIR/serve.log" 2>&1 &
    started=$!
    for _ in $(seq 1 40); do curl -fs "http://$HOST/api/version" >/dev/null && break; sleep 0.5; done
fi

echo "Downloading the model $MODEL..."
"$DIR/bin/ollama" pull "$MODEL"
"$DIR/bin/ollama" list
[ -n "$started" ] && kill "$started"
echo "Done. Buddy will start the local AI by itself when it needs it."
