import time

import pytest

from buddy import actions, config, listen, stt
from buddy.engine import AWAKE_SECONDS, Engine
from buddy.server import hear


class Speaker:
    speaking = False

    def say(self, text): pass

    def stop(self): pass


class Ear:
    """Answers with fixed text, and keeps whether it was told to expect the wake phrase."""

    def __init__(self, text=""):
        self.text = text
        self.hands_free = []
        self.calls = 0

    def transcribe(self, audio, hands_free=False):
        self.calls += 1
        self.hands_free.append(hands_free)
        return self.text


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(actions, "is_locked", lambda: False)
    engine = Engine(speaker=Speaker())
    engine.emit = lambda *args: None
    monkeypatch.setattr(engine, "_process", lambda *args, **kwargs: setattr(engine, "busy", False))
    return engine


@pytest.mark.parametrize("said", ["Hey Buddy", "Hey, Buddy!", "hey buddy.", "Buddy", "Okay Buddy", "K buddy", "Yay buddy!"])
def test_the_wake_phrase_alone_starts_listening(engine, said):
    result = hear(engine, Ear(said), b"", True)
    assert result["accepted"] is True
    assert engine.listening()
    assert engine.awake_until - time.monotonic() > AWAKE_SECONDS - 1


def test_the_next_sentence_needs_no_wake_word_and_listening_then_ends(engine):
    hear(engine, Ear("Hey Buddy"), b"", True)
    ear = Ear("what time is it")
    assert hear(engine, ear, b"", True)["accepted"] is True
    assert ear.hands_free == [False]  # already addressed, so nothing biases it towards the wake phrase
    assert not engine.listening()


def test_room_talk_is_ignored(engine):
    assert hear(engine, Ear("pass me the salt"), b"", True)["accepted"] is False
    assert not engine.listening()


def test_an_answer_counts_if_it_arrived_in_time_even_if_transcribing_ran_late(engine):
    hear(engine, Ear("Hey Buddy"), b"", True)

    class Slow(Ear):
        def transcribe(self, audio, hands_free=False):
            engine.awake_until = 0.0  # the window closes while this is being worked out
            return "what time is it"

    assert hear(engine, Slow(), b"", True)["accepted"] is True


def test_nothing_is_heard_at_a_lock_screen(engine, monkeypatch):
    monkeypatch.setattr(actions, "is_locked", lambda: True)
    ear = Ear("Hey Buddy, turn off wifi")
    assert hear(engine, ear, b"", True) == {"text": "", "accepted": False}
    assert ear.calls == 0


def test_holding_to_talk_isnt_limited_by_either_check(engine, monkeypatch):
    monkeypatch.setattr(actions, "is_locked", lambda: True)
    engine.speaker.speaking = True
    assert hear(engine, Ear("what time is it"), b"", False)["accepted"] is True


def test_buddy_doesnt_hear_itself(engine):
    engine.speaker.speaking = True
    ear = Ear("Yes?")
    assert hear(engine, ear, b"", True)["accepted"] is False
    assert ear.calls == 0


def test_stop_ends_listening(engine):
    hear(engine, Ear("Hey Buddy"), b"", True)
    engine.stop()
    assert not engine.listening()


def test_listening_ends_by_itself(engine):
    hear(engine, Ear("Hey Buddy"), b"", True)
    engine.awake_until = time.monotonic() - 1
    assert hear(engine, Ear("pass me the salt"), b"", True)["accepted"] is False


class EnglishEar:
    def __init__(self): self.expected = []

    def transcribe_with_confidence(self, audio, expect=None):
        self.expected.append(expect)
        return "Hey Buddy", 0.0, -0.1


def test_the_wake_phrase_is_only_expected_when_not_yet_addressed():
    ear = EnglishEar()
    listener = listen.Listener(ear, None)
    listener.transcribe(b"", hands_free=True)
    listener.transcribe(b"", hands_free=False)
    assert ear.expected == [listen.WAKE_PHRASE, None]


def test_the_hint_reaches_the_speech_model():
    seen = {}

    class Model:
        def transcribe(self, audio, **options):
            seen.update(options)
            return [], None

    transcriber = stt.Transcriber("unused")
    transcriber._model = Model()
    transcriber.transcribe_with_confidence(b"", expect="Hey Buddy")
    assert seen["hotwords"] == "Hey Buddy"
    transcriber.transcribe_with_confidence(b"")
    assert seen["hotwords"] is None


def test_off_until_asked_for(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.load()["hey_buddy"] is False
