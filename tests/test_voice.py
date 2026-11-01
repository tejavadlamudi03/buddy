import math
import shutil
import struct
import subprocess
import wave

import pytest

from buddy import actions, agent, config, tts
from buddy.parser import VOICE_STYLES, parse

RATE = 22050
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


@pytest.fixture
def speech(tmp_path):
    """Two seconds of a 200 Hz tone stands in for speech."""
    path = tmp_path / "speech.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 200 * n / RATE))) for n in range(2 * RATE)))
    return path


def read(path):
    with wave.open(str(path), "rb") as wav:
        return wav.getframerate(), struct.unpack(f"<{wav.getnframes()}h", wav.readframes(wav.getnframes()))


def pitch(samples):
    rises = sum(1 for before, after in zip(samples, samples[1:]) if before < 0 <= after)
    return rises / (len(samples) / RATE)


@pytest.mark.parametrize("said, style", [
    ("use the funny voice", "funny"), ("Buddy, use a funny voice", "funny"), ("talk funny", "funny"), ("try the chipmunk voice", "funny"),
    ("change your voice to funny", "funny"), ("make your voice silly", "funny"),
    ("talk like a robot", "robot"), ("switch to the robot voice", "robot"), ("set the voice to robotic", "robot"),
    ("use the giant voice", "giant"), ("speak in a deep voice", "giant"), ("talk like a monster", "giant"),
    ("go back to your normal voice", "normal"), ("use the default voice", "normal"), ("talk normal", "normal"),
])
def test_phrases(said, style):
    assert parse(said) == [{"action": "set_voice", "style": style}]


def test_every_word_leads_to_a_real_style():
    assert {*tts.STYLES} == {*VOICE_STYLES.values()}


@needs_ffmpeg
def test_funny_is_higher_and_a_little_shorter(speech):
    tts.restyle(str(speech), "funny")
    rate, samples = read(speech)
    assert rate == RATE
    assert pitch(samples) == pytest.approx(290, abs=5)
    assert len(samples) / RATE == pytest.approx(2 / 1.45 / 0.85, abs=0.05)


@needs_ffmpeg
def test_giant_is_lower(speech):
    tts.restyle(str(speech), "giant")
    rate, samples = read(speech)
    assert rate == RATE
    assert pitch(samples) == pytest.approx(140, abs=5)


@needs_ffmpeg
def test_robot_changes_the_sound_and_keeps_its_length(speech):
    before = read(speech)[1]
    tts.restyle(str(speech), "robot")
    samples = read(speech)[1]
    assert samples != before
    assert any(samples)
    assert len(samples) / RATE == pytest.approx(2, abs=0.1)


def test_normal_leaves_the_speech_alone(speech, monkeypatch):
    before = speech.read_bytes()
    monkeypatch.setattr(tts.subprocess, "run", lambda *args, **kwargs: pytest.fail("ran ffmpeg"))
    tts.restyle(str(speech), "normal")
    assert speech.read_bytes() == before


@pytest.mark.parametrize("failure", [FileNotFoundError("ffmpeg"), subprocess.CalledProcessError(1, "ffmpeg")])
def test_a_failed_effect_keeps_the_answer(speech, monkeypatch, failure):
    before = speech.read_bytes()

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(tts.subprocess, "run", fail)
    tts.restyle(str(speech), "funny")
    assert speech.read_bytes() == before


def test_a_style_from_an_old_config_is_treated_as_normal(speech):
    before = speech.read_bytes()
    tts.restyle(str(speech), "something-removed")
    assert speech.read_bytes() == before


def test_the_choice_is_remembered_and_the_rest_of_the_config_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config.save({"token": "test", "whatsapp_phone": "4900000000"})
    assert config.load()["voice_style"] == "normal"
    assert "funny voice" in actions.run("set_voice", style="funny")
    assert config.load()["voice_style"] == "funny"
    assert config.load()["whatsapp_phone"] == "4900000000"
    assert "normal voice" in actions.run("set_voice", style="normal")


def test_the_agent_cant_change_the_voice():
    assert "set_voice" not in agent.AGENT_ACTIONS
