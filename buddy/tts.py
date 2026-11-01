"""Buddy's voice: neural speech generated and played on this machine."""
import queue
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
import wave

from . import config

VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/"
VOICE = "en_GB-alan-medium.onnx"
TELUGU_VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/te/te_IN/venkatesh/medium/"
TELUGU_VOICE = "te_IN-venkatesh-medium.onnx"
TELUGU_SCRIPT = re.compile(r"[ఀ-౿]")
MARKUP = re.compile(r"[*_`~]")  # WhatsApp formatting in a reply, which shouldn't be read out


def _voice_path(url: str, voice: str):
    directory = config.DATA_DIR / "voices"
    directory.mkdir(parents=True, exist_ok=True)
    for name in (voice, f"{voice}.json"):
        if not (directory / name).exists():
            urllib.request.urlretrieve(url + name, directory / f"{name}.download")
            (directory / f"{name}.download").rename(directory / name)
    return directory / voice


class Speaker:
    def __init__(self):
        self._queue = queue.Queue()
        self._player = None
        self._voices = {}
        self._active = False
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def speaking(self) -> bool:
        """The page stops listening meanwhile, so Buddy doesn't hear itself."""
        return self._active or not self._queue.empty()

    def say(self, text: str) -> None:
        self._queue.put(MARKUP.sub("", text))

    def stop(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
        if self._player:
            self._player.terminate()

    def _voice_for(self, text: str):
        url, name = (TELUGU_VOICE_URL, TELUGU_VOICE) if TELUGU_SCRIPT.search(text) else (VOICE_URL, VOICE)
        if name not in self._voices:
            from piper import PiperVoice
            self._voices[name] = PiperVoice.load(str(_voice_path(url, name)))
        return self._voices[name]

    def _speak(self, text: str) -> None:
        voice = self._voice_for(text)
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio:
            with wave.open(audio.name, "wb") as wav:
                voice.synthesize_wav(text, wav)
            self._player = subprocess.Popen(["pw-play", audio.name], stderr=subprocess.DEVNULL)
            self._player.wait()

    def _loop(self) -> None:
        while True:
            text = self._queue.get()
            self._active = True
            try:
                self._speak(text)
            except Exception as error:  # a voice failure must never stop the command itself
                print(f"Buddy couldn't speak: {error}", file=sys.stderr)
            finally:
                self._active = False
