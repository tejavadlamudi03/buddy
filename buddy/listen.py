"""Hears English and Telugu, trying English first because known commands then cost nothing extra."""
from pathlib import Path

from . import config, language
from .engine import NO, STOP, WAKE, WAKE_ANYWHERE, YES
from .parser import Context, clean, parse
from .stt import Transcriber, has_speech


def _is_known(english: str) -> bool:
    if not clean(english) or any(words.fullmatch(english) for words in (STOP, YES, NO)):
        return True
    return parse(english, Context()) is not None


def telugu_ear() -> Transcriber | None:
    """None until scripts/install-telugu.sh has been run, and Buddy then simply stays English only."""
    model = config.load()["telugu_whisper_model"]
    if not Path(model).exists():
        return None
    return Transcriber(model, beam_size=5, skip_silence=False, language="te")


class Listener:
    def __init__(self, english: Transcriber, telugu: Transcriber | None):
        self.english = english
        self.telugu = telugu

    def load(self) -> None:
        self.english.load()
        if self.telugu:
            self.telugu.load()

    def transcribe(self, audio: bytes, hands_free: bool = False) -> str:
        return self.transcribe_with_confidence(audio, hands_free)[0]

    def transcribe_with_confidence(self, audio: bytes, hands_free: bool = False) -> tuple[str, float, float]:
        """Same as Transcriber's. The Telugu model is sure of everything, so its text reports full confidence.

        hands_free: speech not yet addressed to Buddy. Without the wake word, Telugu isn't tried, so
        talk and music in the room never reach the slower model. A Telugu sentence keeps that wake word.
        """
        english, no_speech, confidence = self.english.transcribe_with_confidence(audio)
        unchanged = english, no_speech, confidence
        if self.telugu is None or not english or _is_known(english):
            return unchanged
        woken = WAKE.match(english) or WAKE_ANYWHERE.search(english)
        if hands_free and not woken:
            return unchanged
        # The English no-speech score runs high on Telugu speech, so it can't decide this. The Telugu
        # model's can't either: it writes confident sentences from pure silence.
        if not has_speech(audio):
            return unchanged
        telugu = self.telugu.transcribe(audio)
        if not telugu or language.pick(english, telugu) != "telugu":
            return unchanged
        return f"Buddy, {telugu}" if hands_free else telugu, 0.0, 0.0
