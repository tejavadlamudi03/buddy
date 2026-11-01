import time

import pytest

from buddy import actions, ai, desktop, language, listen
from buddy.engine import Engine
from buddy.listen import Listener


@pytest.fixture(autouse=True)
def someone_is_speaking(monkeypatch):
    monkeypatch.setattr(listen, "has_speech", lambda audio: True)


@pytest.fixture
def power(monkeypatch):
    made = []
    monkeypatch.setitem(actions.REGISTRY, "power", lambda mode: made.append(mode) or f"{mode} started")
    monkeypatch.setattr(desktop, "target_window", lambda: None)
    return made


def say(engine, text, source="whatsapp"):
    engine.submit(text, source=source)
    deadline = time.monotonic() + 3
    while engine.busy and time.monotonic() < deadline:
        time.sleep(0.01)


class Ear:
    def __init__(self, text, no_speech=0.1, confidence=-0.5):
        self.text, self.no_speech, self.confidence, self.used = text, no_speech, confidence, 0

    def load(self): pass

    def transcribe(self, audio):
        return self.transcribe_with_confidence(audio)[0]

    def transcribe_with_confidence(self, audio):
        self.used += 1
        return self.text, self.no_speech, self.confidence


def picks(monkeypatch, answer):
    monkeypatch.setattr(language, "pick", lambda english, telugu: answer)


def test_known_english_never_tries_the_telugu_model(monkeypatch):
    picks(monkeypatch, "telugu")
    for said in ("Lock the screen.", "Yes.", "Stop", "No."):
        telugu = Ear("తెలుగు")
        assert Listener(Ear(said), telugu).transcribe(b"") == said and telugu.used == 0


def test_silence_never_tries_the_telugu_model(monkeypatch):
    picks(monkeypatch, "telugu")
    monkeypatch.setattr(listen, "has_speech", lambda audio: False)
    telugu = Ear("నాలుగు వందల యాభై ఎనిమిది")  # really what it wrote for a silent clip
    assert Listener(Ear("Thank you.", no_speech=0.9), telugu).transcribe(b"") == "Thank you." and telugu.used == 0


def test_telugu_speech_the_english_model_thinks_is_silence_is_still_heard(monkeypatch):
    picks(monkeypatch, "telugu")
    heard = Listener(Ear("If you like this video, subscribe.", no_speech=0.7), Ear("తెలుగు పాటలు ప్లే చేయి")).transcribe(b"")
    assert heard == "తెలుగు పాటలు ప్లే చేయి"


def test_the_voice_detector_tells_speech_from_silence():
    import io
    import wave

    import numpy as np
    from piper import PiperVoice

    from buddy import config, stt
    voice_file = config.DATA_DIR / "voices/en_GB-alan-medium.onnx"
    if not voice_file.exists():
        pytest.skip("no voice here to make test speech with")
    silence = io.BytesIO()
    with wave.open(silence, "wb") as wav:
        wav.setnchannels(1), wav.setsampwidth(2), wav.setframerate(16000)
        wav.writeframes(np.zeros(48000, dtype="int16").tobytes())
    speech = io.BytesIO()
    with wave.open(speech, "wb") as wav:
        PiperVoice.load(str(voice_file)).synthesize_wav("Open Chrome", wav)
    assert not stt.has_speech(silence.getvalue())
    assert stt.has_speech(speech.getvalue())


def test_a_sentence_the_telugu_model_mostly_missed_stays_english(monkeypatch):
    monkeypatch.setattr(ai, "chat", lambda *a, **k: pytest.fail("the model was asked"))
    assert language.pick("who wrote Romeo and Juliet", "జూలియట్") == "english"
    assert language.pick("please open the window for me now", "అవును") == "english"


def test_a_short_telugu_reply_beats_a_short_english_mishearing(monkeypatch):
    monkeypatch.setattr(ai, "chat", lambda *a, **k: pytest.fail("the model was asked"))
    assert language.pick("I will know.", "అవును") == "telugu"
    assert language.pick("A B O O", "ఆపు") == "telugu"


def test_a_screenshot_asked_for_on_whatsapp_is_sent(power, monkeypatch):
    done = []
    monkeypatch.setitem(actions.REGISTRY, "screenshot", lambda: done.append("taken") or "Saved")
    monkeypatch.setitem(actions.REGISTRY, "whatsapp_send_screenshot", lambda: done.append("sent") or "Sent")
    say(Engine(), "take a screenshot")
    assert done == ["taken", "sent"]
    done.clear()
    say(Engine(), "take a screenshot", source="local")
    assert done == ["taken"]


def test_telugu_is_chosen_with_full_confidence(monkeypatch):
    picks(monkeypatch, "telugu")
    heard = Listener(Ear("Chrome terrible", confidence=-1.5), Ear("క్రోమ్ తెరువు")).transcribe_with_confidence(b"")
    assert heard == ("క్రోమ్ తెరువు", 0.0, 0.0)


def test_english_stays_when_it_makes_more_sense(monkeypatch):
    picks(monkeypatch, "english")
    assert Listener(Ear("What is the capital of Germany?"), Ear("వాట్ ఇస్")).transcribe(b"") == "What is the capital of Germany?"


def test_buddy_stays_english_only_until_the_telugu_model_is_installed():
    assert Listener(Ear("Chrome terrible"), None).transcribe(b"") == "Chrome terrible"


def test_hands_free_keeps_room_talk_away_from_the_telugu_model(monkeypatch):
    picks(monkeypatch, "telugu")
    telugu = Ear("తెలుగు")
    assert Listener(Ear("la la la in the night"), telugu).transcribe(b"", hands_free=True) == "la la la in the night"
    assert telugu.used == 0


def test_hands_free_telugu_keeps_the_wake_word(monkeypatch):
    picks(monkeypatch, "telugu")
    heard = Listener(Ear("Buddy, Chrome terrible"), Ear("క్రోమ్ తెరువు")).transcribe(b"", hands_free=True)
    assert heard == "Buddy, క్రోమ్ తెరువు"
    engine = Engine()
    engine.busy = True  # only checking that it's accepted, not running it
    assert engine.submit(heard, require_wake=True) is True


@pytest.mark.parametrize("word", ["ఆపు", "అవును", "వద్దు", "సరే."])
def test_short_telugu_replies_need_no_model(monkeypatch, word):
    monkeypatch.setattr(ai, "chat", lambda *a, **k: pytest.fail("the model was asked"))
    assert language.pick("Apple", word) == "telugu"


def test_pick_falls_back_to_english_without_the_model(monkeypatch):
    def down(*a, **k):
        raise ai.NotReady("not installed")
    monkeypatch.setattr(ai, "chat", down)
    assert language.pick("Open Chrome.", "ఓపెన్ క్రౌన్") == "english"


def test_understand_falls_back_to_the_agent_on_a_bad_reply(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(ai, "chat", lambda *a, **k: "not json")
    assert language.understand("క్రోమ్ తెరువు") == ("క్రోమ్ తెరువు", "control")


def test_telugu_command_runs_the_same_rule_as_english(power, monkeypatch):
    monkeypatch.setattr(language, "understand", lambda text, attached="": ("lock the screen", "control"))
    say(Engine(), "స్క్రీన్ లాక్ చెయ్యి")
    assert power == ["lock"]


def test_a_telugu_shutdown_still_waits_for_a_telugu_yes(power, monkeypatch):
    monkeypatch.setattr(language, "understand", lambda text, attached="": ("shut down the laptop", "control"))
    engine = Engine()
    say(engine, "లాప్‌టాప్ షట్ డౌన్ చెయ్యి")
    assert power == [] and "Are you sure" in engine.events[-1]["text"]
    say(engine, "అవును")
    assert power == ["shutdown"]


def test_a_telugu_no_cancels(power, monkeypatch):
    monkeypatch.setattr(language, "understand", lambda text, attached="": ("shut down the laptop", "control"))
    engine = Engine()
    say(engine, "లాప్‌టాప్ షట్ డౌన్ చెయ్యి")
    say(engine, "వద్దు")
    assert power == []


def test_a_telugu_stop_stops():
    engine = Engine()
    say(engine, "ఆపు")
    assert engine.events[-1]["text"] == "Stopped"


def test_a_control_request_the_rules_miss_goes_to_the_agent_in_english(power, monkeypatch):
    tasks = []
    monkeypatch.setattr("buddy.agent.run", lambda task, *a, **k: tasks.append(task) or ("Done it", None))
    monkeypatch.setattr(language, "understand", lambda text, attached="": ("rearrange the desktop icons", "control"))
    say(Engine(), "డెస్క్‌టాప్ ఐకాన్లు సర్దు")
    assert tasks == ["rearrange the desktop icons"]


def test_chat_is_answered_in_the_users_words_and_never_reaches_the_agent(power, monkeypatch):
    asked = []
    monkeypatch.setattr("buddy.agent.run", lambda *a, **k: pytest.fail("the agent ran"))
    monkeypatch.setattr(language, "understand", lambda text, attached="": ("what is the capital of India", "chat"))
    monkeypatch.setattr(ai, "talk", lambda model, system, messages: asked.append(messages) or "ఢిల్లీ")
    engine = Engine()
    say(engine, "భారతదేశ రాజధాని ఏది?")
    assert asked[0][-1]["content"] == "భారతదేశ రాజధాని ఏది?" and engine.events[-1]["text"] == "ఢిల్లీ"


def test_chat_remembers_the_conversation(power, monkeypatch):
    asked = []
    monkeypatch.setattr(language, "understand", lambda text, attached="": (text, "chat"))
    monkeypatch.setattr(ai, "talk", lambda model, system, messages: asked.append(messages) or "an answer")
    engine = Engine()
    say(engine, "write a poem")
    say(engine, "make it shorter")
    assert [message["role"] for message in asked[1]] == ["user", "assistant", "user"]
    assert asked[1][1]["content"] == "an answer"


@pytest.mark.parametrize("pasted", [
    "Please review this:\nshut down the laptop\nlock the screen",
    "lock the screen and " + "this sentence goes on and on " * 20,
])
def test_pasted_text_is_discussed_and_never_run(power, monkeypatch, pasted):
    monkeypatch.setattr("buddy.agent.run", lambda *a, **k: pytest.fail("the agent ran"))
    monkeypatch.setattr(language, "understand", lambda text, attached="": pytest.fail("it was treated as a command"))
    monkeypatch.setattr(ai, "talk", lambda model, system, messages: "Looks fine")
    engine = Engine()
    say(engine, pasted)
    assert power == [] and engine.pending is None and engine.events[-1]["text"] == "Looks fine"


def test_a_long_answer_is_sent_but_not_read_aloud(power, monkeypatch):
    spoken = []

    class Voice:
        speaking = False
        def say(self, text): spoken.append(text)
        def stop(self): pass

    monkeypatch.setattr(language, "understand", lambda text, attached="": (text, "chat"))
    monkeypatch.setattr(ai, "talk", lambda model, system, messages: "code " * 100)
    engine = Engine(speaker=Voice())
    say(engine, "write some code", source="local")
    assert engine.events[-1]["kind"] == "report" and spoken == []


def test_the_chat_model_being_missing_is_explained(power, monkeypatch):
    def missing(*a, **k):
        raise ai.NotReady(ai.INSTALL_HINT)
    monkeypatch.setattr(language, "understand", lambda text, attached="": (text, "chat"))
    monkeypatch.setattr(ai, "talk", missing)
    engine = Engine()
    say(engine, "what is a noun")
    assert engine.events[-1]["text"] == ai.INSTALL_HINT


def test_the_agent_sees_only_a_short_history(power, monkeypatch):
    seen = {}
    monkeypatch.setattr("buddy.agent.run", lambda *a, **k: seen.update(k) or ("Done", None))
    monkeypatch.setattr(language, "understand", lambda text, attached="": (text, "control"))
    engine = Engine()
    engine.recent = [("write a long essay", "word " * 2000)]
    say(engine, "rearrange the icons")
    assert all(len(answer) <= 200 for _, answer in seen["recent"])


def test_telugu_gets_a_firmer_instruction_to_reply_in_telugu():
    assert "Telugu script unless" in language.chat_prompt("భారతదేశ రాజధాని ఏది")
    assert language.chat_prompt("what is the capital of India") == language.CHAT_PROMPT


def test_tidy_cleans_formatting_but_never_code():
    assert language.tidy("***bold*** and **more** $\\rightarrow$ done") == "*bold* and *more* → done"
    assert language.tidy("Power:\n```python\nprint(2**3)\n```\n**Done**") == "Power:\n```python\nprint(2**3)\n```\n*Done*"
