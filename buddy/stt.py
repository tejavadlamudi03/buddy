"""Local speech-to-text. Audio stays on this machine."""
import io
import threading

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio
from faster_whisper.vad import get_speech_timestamps


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

    def transcribe_with_confidence(self, audio: bytes, expect: str | None = None) -> tuple[str, float, float]:
        """The text, how likely the audio held no speech, and the model's average log probability.

        expect: words the speech may well contain, which helps with accents the model finds harder.
        """
        self.load()
        with self._lock:
            segments, _ = self._model.transcribe(
                io.BytesIO(audio), language=self.language, beam_size=self.beam_size, vad_filter=self.skip_silence, hotwords=expect,
                condition_on_previous_text=False,
            )
            parts = list(segments)
        if not parts:
            return "", 1.0, -10.0
        count = len(parts)
        return (" ".join(part.text.strip() for part in parts).strip(),
                sum(part.no_speech_prob for part in parts) / count,
                sum(part.avg_logprob for part in parts) / count)
