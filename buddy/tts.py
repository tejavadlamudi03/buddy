"""Buddy's voice: neural speech generated and played on this machine."""
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path

from . import config, language

VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/"
VOICE = "en_GB-alan-medium.onnx"
VOICES = {
    "english": (VOICE_URL, VOICE),
    "telugu": ("https://huggingface.co/rhasspy/piper-voices/resolve/main/te/te_IN/venkatesh/medium/", "te_IN-venkatesh-medium.onnx"),
    "hindi": ("https://huggingface.co/rhasspy/piper-voices/resolve/main/hi/hi_IN/pratham/medium/", "hi_IN-pratham-medium.onnx"),
    "german": ("https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/", "de_DE-thorsten-medium.onnx"),
}
MARKUP = re.compile(r"[*_`~]")  # WhatsApp formatting in a reply, which shouldn't be read out
# ffmpeg filters, applied after the speech is made, so they work for every language. {rate} is its sample rate.
STYLES = {
    "normal": "",
    "funny": "asetrate={rate}*1.45,aresample={rate},atempo=0.85",
    # The effect leaves it much quieter, so it's turned back up, with a limiter to stop it distorting.
    "robot": "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75,volume=13dB,alimiter=limit=0.95:level=0",
    "giant": "asetrate={rate}*0.7,aresample={rate},atempo=1.2",
}
STYLE_SECONDS = 30


def restyle(path: str, style: str) -> None:
    """Rewrites the speech in this style. A voice effect isn't worth losing the answer, so failing leaves it as it was."""
    if not STYLES.get(style):
        return
    with wave.open(path, "rb") as wav:
        rate = wav.getframerate()
    styled = f"{path}.styled.wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-af", STYLES[style].format(rate=rate), styled],
                       check=True, capture_output=True, timeout=STYLE_SECONDS, **config.NO_WINDOW)
        os.replace(styled, path)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Couldn't apply the {style} voice: {error}", file=sys.stderr)
        Path(styled).unlink(missing_ok=True)


def _voice_path(url: str, voice: str):
    directory = config.DATA_DIR / "voices"
    directory.mkdir(parents=True, exist_ok=True)
    for name in (voice, f"{voice}.json"):
        if not (directory / name).exists():
            urllib.request.urlretrieve(url + name, directory / f"{name}.download")
            (directory / f"{name}.download").rename(directory / name)
    return directory / voice


class _WindowsPlayer:
    """Plays a sound through Windows itself, with the same wait and terminate as the Linux player's process."""

    def __init__(self, path: str):
        import winsound
        self._winsound = winsound
        with wave.open(path, "rb") as wav:
            self._ends_at = time.monotonic() + wav.getnframes() / wav.getframerate()
        self._stopped = threading.Event()
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def wait(self) -> None:
        self._stopped.wait(max(0, self._ends_at - time.monotonic()) + 0.1)
        self.terminate()  # lets go of the file, so it can be deleted

    def terminate(self) -> None:
        self._stopped.set()
        self._winsound.PlaySound(None, self._winsound.SND_NODEFAULT)


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
        url, name = VOICES[language.written_in(text)]
        if name not in self._voices:
            from piper import PiperVoice
            self._voices[name] = PiperVoice.load(str(_voice_path(url, name)))
        return self._voices[name]

    def _speak(self, text: str) -> None:
        voice = self._voice_for(text)
        with tempfile.TemporaryDirectory(prefix="buddy-voice-", ignore_cleanup_errors=True) as folder:  # Windows can't reopen an open temporary file
            audio = str(Path(folder) / "speech.wav")
            with wave.open(audio, "wb") as wav:
                voice.synthesize_wav(text, wav)
            restyle(audio, config.load()["voice_style"])
            self._player = _WindowsPlayer(audio) if config.WINDOWS else subprocess.Popen(["pw-play", audio], stderr=subprocess.DEVNULL)
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
