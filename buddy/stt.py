"""Local speech-to-text. Audio stays on this machine."""
import io
import threading

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio
from faster_whisper.vad import get_speech_timestamps

from . import config


def seconds_of(audio: bytes) -> float:
    return len(decode_audio(io.BytesIO(audio))) / 16000  # what decode_audio always gives


def has_speech(audio: bytes) -> bool:
    """A separate voice detector, because Whisper itself can invent whole sentences from silence."""
    return bool(get_speech_timestamps(decode_audio(io.BytesIO(audio))))


class Transcriber:
    def __init__(self, model_name: str, beam_size: int = 1, skip_silence: bool = True, language: str = "en"):
        self.model_name = model_name
        self.beam_size = beam_size
        self.skip_silence = skip_silence  # for continuous listening; it can clip words in a short clip
        self.language = language
        self._model = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._model is None:
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")

    def transcribe(self, audio: bytes) -> str:
        return self.transcribe_with_confidence(audio)[0]

    def language_scores(self, audio: bytes, among: tuple) -> dict:
        """For a model that knows many languages: how likely each of these is, out of 1. Whisper splits Hindi between its Hindi and Urdu, which sound the same."""
        self.load()
        with self._lock:
            _, _, scores = self._model.detect_language(audio=decode_audio(io.BytesIO(audio)))
        likely = dict(scores)
        likely["hi"] = likely.get("hi", 0) + likely.get("ur", 0)
        total = sum(likely.get(name, 0) for name in among) or 1
        return {name: likely.get(name, 0) / total for name in among}

    def transcribe_with_confidence(self, audio: bytes, expect: str | None = None, language: str | None = None) -> tuple[str, float, float]:
        """The text, how likely the audio held no speech, and the model's average log probability.

        expect: words the speech may well contain, which helps with accents the model finds harder.
        language: for a model that knows many, what this audio is in.
        """
        self.load()
        with self._lock:
            segments, _ = self._model.transcribe(
                io.BytesIO(audio), language=language or self.language, beam_size=self.beam_size, vad_filter=self.skip_silence,
                hotwords=" ".join(filter(None, [expect, *config.load()["dictionary"]])) or None,
                condition_on_previous_text=False,
            )
            parts = list(segments)
        if not parts:
            return "", 1.0, -10.0
        count = len(parts)
        return (" ".join(part.text.strip() for part in parts).strip(),
                sum(part.no_speech_prob for part in parts) / count,
                sum(part.avg_logprob for part in parts) / count)
