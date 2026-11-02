"""Hears English, Telugu, Hindi and German, trying English first because known commands then cost nothing extra."""
from pathlib import Path

from faster_whisper.utils import download_model

from . import config, language
from .engine import NO, STOP, WAKE, WAKE_ANYWHERE, YES
from .parser import Context, clean, parse
from .stt import Transcriber, has_speech, seconds_of

WIDER_LANGUAGES = {"hi": "hindi", "de": "german"}
SURE_OF_LANGUAGE = 0.7  # this sure it's Hindi or German, and the Telugu model isn't worth the wait
UNSURE_ENGLISH = -0.6  # the English model can turn German into a known command, but it isn't confident about it
SHORT_CLIP_SECONDS = 1.0  # too little to tell a language by, so a word like "nein" is tried in each
WAKE_PHRASE = "Hey Buddy"  # without this hint, an Indian-accented test voice was often heard as "K buddy"


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


def languages_ear() -> Transcriber | None:
    """None until scripts/install-languages.sh has been run, so a first run never stalls on a large download."""
    model = config.load()["languages_whisper_model"]
    try:
        download_model(model, local_files_only=True)
    except (OSError, ValueError, RuntimeError):  # what Hugging Face's own errors are built on
        return None
    return Transcriber(model, beam_size=5, skip_silence=False, language=None)


class Listener:
    def __init__(self, english: Transcriber, telugu: Transcriber | None, wider: Transcriber | None = None):
        self.english = english
        self.telugu = telugu
        self.wider = wider

    def load(self) -> None:
        self.english.load()
        for ear in (self.telugu, self.wider):
            if ear:
                ear.load()

    def _might_be_foreign(self, audio: bytes, confidence: float) -> bool:
        if self.wider is None or confidence >= UNSURE_ENGLISH:
            return False
        scores = self.wider.language_scores(audio, ("en", "hi", "de"))
        return max(scores, key=scores.get) in WIDER_LANGUAGES and max(scores.values()) >= SURE_OF_LANGUAGE

    def _other_languages(self, audio: bytes) -> dict:
        """What each other language's model heard. Detecting the language first saves running all of them."""
        heard, to_try, sure = {}, [], False
        if self.wider:
            scores = self.wider.language_scores(audio, ("en", "te", "hi", "de") if self.telugu else ("en", "hi", "de"))
            likeliest = max(scores, key=scores.get)
            sure = likeliest in WIDER_LANGUAGES and scores[likeliest] >= SURE_OF_LANGUAGE
            to_try = list(WIDER_LANGUAGES) if seconds_of(audio) < SHORT_CLIP_SECONDS else [likeliest] if likeliest in WIDER_LANGUAGES else []
        if self.telugu and not sure and (telugu := self.telugu.transcribe(audio)):
            heard["telugu"] = (telugu, 0.0, 0.0)
        for code in to_try:
            found = self.wider.transcribe_with_confidence(audio, language=code)
            if found[0]:
                heard[WIDER_LANGUAGES[code]] = found
        return heard

    def transcribe(self, audio: bytes, hands_free: bool = False) -> str:
        return self.transcribe_with_confidence(audio, hands_free)[0]

    def transcribe_with_confidence(self, audio: bytes, hands_free: bool = False) -> tuple[str, float, float]:
        """Same as Transcriber's. The Telugu model is sure of everything, so its text reports full confidence.

        hands_free: speech not yet addressed to Buddy. Without the wake word, other languages aren't tried, so
        talk and music in the room never reach the slower models. A sentence in one keeps that wake word.
        """
        heard = self.english.transcribe_with_confidence(audio, expect=WAKE_PHRASE) if hands_free else self.english.transcribe_with_confidence(audio)
        english, no_speech, confidence = heard
        unchanged = english, no_speech, confidence
        if (self.telugu is None and self.wider is None) or not english:
            return unchanged
        if _is_known(english) and not self._might_be_foreign(audio, confidence):
            return unchanged
        woken = WAKE.match(english) or WAKE_ANYWHERE.search(english)
        if hands_free and not woken:
            return unchanged
        # The English no-speech score runs high on Telugu speech, so it can't decide this. The Telugu
        # model's can't either: it writes confident sentences from pure silence.
        if not has_speech(audio):
            return unchanged
        heard = self._other_languages(audio)
        chosen = language.pick(english, {name: result[0] for name, result in heard.items()}) if heard else "english"
        if chosen not in heard:
            return unchanged
        text, no_speech, confidence = heard[chosen]
        return f"Buddy, {text}" if hands_free else text, no_speech, confidence
