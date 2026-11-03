import time
import types

import pytest

from buddy import actions, config, dictation, server
from buddy.parser import Context, parse

CTRL_R, OTHER = "ctrl_r", "c"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(dictation.ai, "installed", lambda: True)
    return tmp_path


class Steps:
    def __init__(self):
        self.done = []

    def __getattr__(self, name):
        return lambda: self.done.append(name)


def hotkey():
    steps = Steps()
    return dictation.Hotkey(CTRL_R, steps.begin, steps.finish, steps.cancel), steps.done


def held(key, seconds, monkeypatch, other=None):
    key_handler, done = hotkey()
    start = time.monotonic()
    monkeypatch.setattr(dictation.time, "monotonic", lambda: start)
    key_handler.press(CTRL_R)
    key_handler.press(CTRL_R)  # a held key repeats
    if other:
        key_handler.press(other)
    monkeypatch.setattr(dictation.time, "monotonic", lambda: start + seconds)
    key_handler.release(CTRL_R)
    return done


def test_holding_the_key_dictates(monkeypatch):
    assert held(CTRL_R, 2, monkeypatch) == ["begin", "finish"]


def test_a_tap_of_the_key_is_not_dictation(monkeypatch):
    assert held(CTRL_R, 0.1, monkeypatch) == ["begin", "cancel"]


def test_a_shortcut_with_the_key_is_not_dictation(monkeypatch):
    assert held(CTRL_R, 2, monkeypatch, other=OTHER) == ["begin", "cancel"]


def test_other_keys_alone_do_nothing():
    key_handler, done = hotkey()
    key_handler.press(OTHER)
    key_handler.release(OTHER)
    key_handler.release(CTRL_R)
    assert done == [] and not key_handler.other


@pytest.mark.parametrize("name, label", [("ctrl_r", "Right Ctrl"), ("alt_l", "Left Alt"), ("f8", "F8"), ("caps_lock", "Caps Lock")])
def test_key_names_read_well(name, label):
    assert dictation.key_label(name) == label


def test_the_configured_key_exists():
    assert dictation.key_named(config.DEFAULTS["dictation_key"]) is not None


@pytest.mark.parametrize("said, typed", [
    ("um so I'll be there at three", "So I'll be there at three."),
    ("uh, thanks, uhh, see you", "Thanks, see you."),
    ("Is it ready?", "Is it ready?"),
    ("um is it ready", "Is it ready?"),
    ("hello, um, world", "Hello, world."),
    ("um", ""),
    ("hmm, the umbrella and the erm hummus", "The umbrella and the hummus."),
])
def test_tidying_without_the_ai(said, typed):
    assert dictation.tidy_without_ai(said) == typed


def test_tidying_without_the_ai_keeps_other_scripts():
    assert dictation.tidy_without_ai("నేను వస్తాను") == "నేను వస్తాను."


@pytest.mark.parametrize("spoken, tidied, kept", [
    ("um the meeting is at two no wait at three", "The meeting is at three.", True),
    ("what is the capital of france", "What is the capital of France?", True),
    ("what is the capital of france", "The capital of France is Paris.", False),  # it answered instead of writing
    ("write me a poem about cats", "Cats are soft and sleep all day, they chase the mice and run away.", False),
    ("milk eggs and bread", "- Milk\n- Eggs\n- Bread", True),
    ("hello", "", False),
    ("translate hello into german", "Hallo", False),
    ("hi bhanu teja its at two no three", "Hi Bhanuteja, it's at three.", True),  # Bhanuteja is in the dictionary
    ("the meeting is at three", "The meeting is at 3.", True),
])
def test_tidying_must_keep_what_was_said(spoken, tidied, kept):
    assert dictation.kept_what_was_said(spoken, tidied, ("Bhanuteja",)) is kept


def test_tidying_falls_back_when_the_model_answers(settings, monkeypatch):
    monkeypatch.setattr(dictation.ai, "talk", lambda *a, **k: "The capital of France is Paris.")
    assert dictation.tidy("um what is the capital of france") == "What is the capital of france?"


def test_tidying_uses_the_model_and_the_dictionary(settings, monkeypatch):
    config.save({**config.load(), "dictionary": ["Bhanuteja", "Buddy"]})
    asked = {}
    monkeypatch.setattr(dictation.ai, "talk", lambda model, system, messages, **k: asked.update(system=system, messages=messages) or "<speech>Hi Bhanuteja, it's at three.</speech>")
    assert dictation.tidy("hi bhanu teja its at two no three") == "Hi Bhanuteja, it's at three."
    assert "Bhanuteja, Buddy" in asked["system"] and asked["messages"][0]["content"] == "<speech>hi bhanu teja its at two no three</speech>"


def test_telugu_is_never_reworded_by_the_model(settings, monkeypatch):
    monkeypatch.setattr(dictation.ai, "talk", lambda *a, **k: pytest.fail("it changed the meaning of Telugu when tested"))
    assert dictation.tidy("నేను రేపు వస్తాను") == "నేను రేపు వస్తాను."


def test_tidying_without_the_ai_switched_on(settings, monkeypatch):
    config.save({**config.load(), "agent_enabled": False})
    monkeypatch.setattr(dictation.ai, "talk", lambda *a, **k: pytest.fail("the AI is switched off"))
    assert dictation.tidy("uh hello there") == "Hello there."


def test_tidying_still_types_when_the_model_fails(settings, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("connection refused")
    monkeypatch.setattr(dictation.ai, "talk", broken)
    assert dictation.tidy("uh hello there") == "Hello there."


class FakeEngine:
    def __init__(self):
        self.submitted, self.events = [], []

    def submit(self, text, *args):
        self.submitted.append(text)
        return True

    def emit(self, kind, text, quiet=False):
        self.events.append((kind, text))


class FakeRecorder:
    def __init__(self):
        self.started = self.stopped = self.cancelled = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        return b"audio"

    def cancel(self):
        self.cancelled += 1


@pytest.fixture
def dictating(settings, monkeypatch):
    typed = []
    monkeypatch.setattr(dictation.desktop, "type_text", typed.append)
    monkeypatch.setattr(dictation, "tidy", lambda text: text.replace("um ", "").capitalize() + ".")
    monkeypatch.setattr(dictation.threading, "Timer", lambda *a: types.SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(dictation.threading, "Thread", lambda target, args, daemon: types.SimpleNamespace(start=lambda: target(*args)))
    engine, recorder = FakeEngine(), FakeRecorder()
    heard = {"text": "um see you at three"}
    listener = types.SimpleNamespace(english=types.SimpleNamespace(transcribe_with_confidence=lambda audio: (heard["text"], 0.0, -0.3)),
                                     transcribe=lambda audio: pytest.fail("the command listener is only for commands"),
                                     telugu=None, wider=None)
    return dictation.Dictation(engine, listener, recorder), engine, recorder, typed, heard


def hold_and_speak(flow, monkeypatch, seconds=2):
    start = time.monotonic()
    monkeypatch.setattr(dictation.time, "monotonic", lambda: start)
    flow.hotkey.press(flow.hotkey.key)
    monkeypatch.setattr(dictation.time, "monotonic", lambda: start + seconds)
    flow.hotkey.release(flow.hotkey.key)


def test_speech_is_typed_where_the_cursor_is(dictating, monkeypatch):
    flow, engine, recorder, typed, _ = dictating
    hold_and_speak(flow, monkeypatch)
    assert typed == ["See you at three."] and recorder.stopped == 1
    assert ("log", "Dictated: See you at three.") in engine.events and engine.submitted == []


def test_starting_with_buddy_makes_it_a_command(dictating, monkeypatch):
    flow, engine, _, typed, heard = dictating
    heard["text"] = "Hey Buddy, open YouTube."
    hold_and_speak(flow, monkeypatch)
    assert engine.submitted == ["Hey Buddy, open YouTube."] and typed == []


def test_nothing_heard_types_nothing(dictating, monkeypatch):
    flow, engine, _, typed, heard = dictating
    heard["text"] = ""
    hold_and_speak(flow, monkeypatch)
    assert typed == [] and engine.submitted == []


def test_a_tap_records_nothing_that_is_kept(dictating, monkeypatch):
    flow, _, recorder, typed, _ = dictating
    hold_and_speak(flow, monkeypatch, seconds=0.1)
    assert recorder.cancelled == 1 and recorder.stopped == 0 and typed == []


def test_switched_off_it_does_not_record(dictating, monkeypatch):
    flow, _, recorder, typed, _ = dictating
    config.save({**config.load(), "dictation": False})
    hold_and_speak(flow, monkeypatch)
    assert recorder.started == 0 and typed == []


def test_a_broken_microphone_is_said(dictating, monkeypatch):
    flow, engine, recorder, typed, _ = dictating
    def broken():
        raise OSError("no microphone")
    recorder.start = broken
    hold_and_speak(flow, monkeypatch)
    assert engine.events == [("error", "Dictation couldn't use the microphone: no microphone")] and typed == []


def test_the_page_ignores_what_dictation_already_heard(dictating, monkeypatch):
    flow, engine, *_ = dictating
    monkeypatch.setattr(dictation, "current", flow)
    monkeypatch.setattr(server.actions, "is_locked", lambda: False)
    flow.recording = True
    ear = types.SimpleNamespace(transcribe=lambda *a, **k: pytest.fail("already heard by dictation"))
    engine.speaker = None
    assert server.hear(engine, ear, b"audio", require_wake=True) == {"text": "", "accepted": False}


def test_dictation_busy_window_closes(dictating, monkeypatch):
    flow, *_ = dictating
    now = time.monotonic()
    flow.last_heard = now
    monkeypatch.setattr(dictation.time, "monotonic", lambda: now + dictation.QUIET_SECONDS + 0.1)
    assert not flow.busy()


class Ear:
    def __init__(self, text, confidence=-0.3, language=None):
        self.text, self.confidence, self.language, self.asked = text, confidence, language, []

    def transcribe_with_confidence(self, audio, language=None):
        self.asked.append(language)
        return self.text, 0.0, self.confidence

    def transcribe(self, audio):
        self.asked.append("transcribe")
        return self.text

    def language_scores(self, audio, among):
        return {code: 1.0 if code == self.language else 0.0 for code in among}


def ears(english, confidence, language=None, telugu="", other=""):
    return types.SimpleNamespace(english=Ear(english, confidence), telugu=Ear(telugu), wider=Ear(other, language=language),
                                 transcribe=lambda audio: f"command: {english}")


def test_sure_english_is_taken_straight_away():
    listener = ears("See you at three.", -0.25, language="te")
    assert dictation.hear(listener, b"") == ("See you at three.", False)
    assert listener.telugu.asked == [] and listener.wider.asked == []


@pytest.mark.parametrize("language, expected, asked", [("te", "నేను వస్తాను", "transcribe"), ("hi", "मैं आऊंगा", "hi"), ("de", "Ich komme", "de")])
def test_other_languages_use_their_own_model(language, expected, asked):
    listener = ears("Nenu vastanu", -0.9, language=language, telugu="నేను వస్తాను", other="मैं आऊंगा" if language == "hi" else "Ich komme")
    assert dictation.hear(listener, b"") == (expected, False)
    assert asked in listener.telugu.asked + listener.wider.asked


def test_unsure_english_that_is_english_stays_english():
    listener = ears("see you at three", -0.7, language="en", telugu="సీ యు")
    assert dictation.hear(listener, b"") == ("see you at three", False)
    assert listener.telugu.asked == []


def test_only_english_installed():
    listener = types.SimpleNamespace(english=Ear("see you", -0.9), telugu=None, wider=None)
    assert dictation.hear(listener, b"") == ("see you", False)


def test_commands_go_through_the_command_listener_when_unsure():
    assert dictation.hear(ears("Hey Buddy, open YouTube.", -0.3), b"") == ("Hey Buddy, open YouTube.", True)
    assert dictation.hear(ears("Buddy, chrome open cheyyi", -0.9), b"") == ("command: Buddy, chrome open cheyyi", True)


@pytest.mark.parametrize("said, step", [
    ("add Bhanuteja to my dictionary", {"action": "add_to_dictionary", "word": "Bhanuteja"}),
    ("Add the word ROS2 to the dictionary.", {"action": "add_to_dictionary", "word": "ROS2"}),
    ("remove Bhanuteja from my dictionary", {"action": "remove_from_dictionary", "word": "Bhanuteja"}),
    ("what's in my dictionary", {"action": "list_dictionary"}),
    ("turn off dictation", {"action": "set_dictation", "on": False}),
    ("enable dictation", {"action": "set_dictation", "on": True}),
])
def test_dictionary_and_dictation_commands(said, step):
    assert parse(said, Context()) == [step]


def test_the_dictionary_is_kept(settings):
    assert actions.list_dictionary().startswith("Your dictionary is empty")
    assert actions.add_to_dictionary("Bhanuteja") == "Added Bhanuteja to your dictionary. I'll spell it like that from now on"
    assert actions.add_to_dictionary("bhanuteja") == "bhanuteja is already in your dictionary"
    assert actions.list_dictionary() == "Your dictionary: Bhanuteja"
    assert actions.remove_from_dictionary("BHANUTEJA") == "Removed BHANUTEJA from your dictionary"
    assert actions.remove_from_dictionary("nobody") == "nobody isn't in your dictionary"


def test_the_dictionary_helps_the_speech_model(settings, monkeypatch):
    from buddy import stt
    config.save({**config.load(), "dictionary": ["Bhanuteja"]})
    seen = {}

    class Model:
        def transcribe(self, audio, **options):
            seen.update(options)
            return iter([]), None

    ear = stt.Transcriber("small.en")
    ear._model = Model()
    ear.transcribe_with_confidence(b"", expect="Hey Buddy")
    assert seen["hotwords"] == "Hey Buddy Bhanuteja"
    config.save({**config.load(), "dictionary": []})
    ear.transcribe_with_confidence(b"")
    assert seen["hotwords"] is None


def test_switching_dictation(settings):
    assert actions.set_dictation(False) == "Dictation is off" and config.load()["dictation"] is False
    assert actions.set_dictation(True) == "Dictation is on. Hold Right Ctrl in any app and speak"


def test_recorder_on_windows_makes_a_wav(monkeypatch):
    import io
    import sys
    import wave
    chunks = []

    class Stream:
        def __init__(self, callback, **options):
            self.callback = callback

        def start(self):
            self.callback(b"\x01\x00" * 1600, 1600, None, None)

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawInputStream=Stream))
    monkeypatch.setattr(config, "WINDOWS", True)
    recorder = dictation.Recorder()
    recorder.start()
    with wave.open(io.BytesIO(recorder.stop())) as wav:
        chunks.append(wav.getnframes())
    assert chunks == [1600]
