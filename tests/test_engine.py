import sys
import time

import pytest

from buddy import actions, desktop
from buddy.engine import Engine


@pytest.fixture
def calls(monkeypatch):
    """Replaces real power actions, so these tests can never shut anything down."""
    made = []
    monkeypatch.setitem(actions.REGISTRY, "power", lambda mode: made.append(mode) or f"{mode} started")
    monkeypatch.setattr(desktop, "target_window", lambda: None)
    return made


def settle(engine):
    deadline = time.monotonic() + 3
    while engine.busy and time.monotonic() < deadline:
        time.sleep(0.01)


def say(engine, text, **kwargs):
    accepted = engine.submit(text, **kwargs)
    settle(engine)
    return accepted


def test_shutdown_waits_for_yes(calls):
    engine = Engine()
    say(engine, "shut down the laptop")
    assert calls == []
    assert "Are you sure" in engine.events[-1]["text"]
    say(engine, "yes")
    assert calls == ["shutdown"]


def test_anything_but_yes_cancels_shutdown(calls, monkeypatch):
    monkeypatch.setitem(actions.REGISTRY, "screenshot", lambda: "shot")
    engine = Engine()
    say(engine, "shut down the laptop")
    say(engine, "take a screenshot")
    say(engine, "yes")
    assert calls == []


def test_expired_confirmation_does_nothing(calls, monkeypatch):
    engine = Engine()
    say(engine, "restart the laptop")
    engine.pending_since -= 60
    say(engine, "yes")
    assert calls == []


def test_lock_needs_no_confirmation(calls):
    say(Engine(), "lock the screen")
    assert calls == ["lock"]


def test_hands_free_ignores_speech_without_wake_word(calls):
    engine = Engine()
    assert say(engine, "lock the screen", require_wake=True) is False
    assert calls == []
    assert say(engine, "Buddy, lock the screen.", require_wake=True) is True
    assert calls == ["lock"]


def test_wake_word_alone_opens_a_listening_window(calls):
    engine = Engine()
    say(engine, "Buddy.", require_wake=True)
    assert say(engine, "lock the screen", require_wake=True) is True
    assert calls == ["lock"]


def test_stop_cancels_a_pending_confirmation(calls):
    engine = Engine()
    say(engine, "shut down")
    say(engine, "stop")
    say(engine, "yes")
    assert calls == []


def test_every_parsed_step_reaches_its_action_with_its_arguments(calls, monkeypatch):
    seen = []
    monkeypatch.setitem(actions.REGISTRY, "open", lambda name, search_fallback=False: seen.append(name) or "ok")
    say(Engine(), "open vs code")
    assert seen == ["vs code"]


def test_follow_up_works_without_the_wake_word(calls):
    engine = Engine()
    say(engine, "Buddy, lock the screen", require_wake=True)
    assert say(engine, "lock the screen", require_wake=True) is True
    assert calls == ["lock", "lock"]


def test_follow_up_never_passes_unknown_speech_to_the_agent(calls):
    engine = Engine()
    say(engine, "Buddy, lock the screen", require_wake=True)
    assert say(engine, "and I will always love you", require_wake=True) is False


def test_follow_up_window_expires(calls):
    engine = Engine()
    say(engine, "Buddy, lock the screen", require_wake=True)
    engine.follow_up_until -= 60
    assert say(engine, "lock the screen", require_wake=True) is False


def test_page_from_before_a_restart_still_gets_events():
    engine = Engine()
    engine.emit("say", "hello")
    assert engine.events_after(60)[0]["text"] == "hello"
    assert engine.events_after(1) == []


def test_agent_question_gets_the_next_answer(calls, monkeypatch):
    runs = []

    def fake_agent(task, model, steps, emit, stopped, recent=(), history=None):
        runs.append((task, history))
        return ("Send it to Sam?", ["typed the email"]) if history is None else ("Sent", None)

    monkeypatch.setattr("buddy.agent.run", fake_agent)
    engine = Engine()
    say(engine, "email Sam that I'm late")
    assert say(engine, "yes do it", require_wake=True) is True  # no wake word needed to answer
    assert runs[1][0] == "email Sam that I'm late"
    assert "yes do it" in runs[1][1][-1]
    assert engine.events[-1]["text"] == "Sent"


def test_stop_abandons_the_agents_question(calls, monkeypatch):
    runs = []
    monkeypatch.setattr("buddy.agent.run", lambda *a, **k: runs.append(a) or ("Sure?", []))
    engine = Engine()
    say(engine, "email Sam that I'm late")
    say(engine, "stop")
    assert engine.resume is None


def test_agent_can_never_start_sharing_or_power_off():
    from buddy.agent import AGENT_ACTIONS
    assert not AGENT_ACTIONS & {"share_screen", "stop_sharing", "power"}


def test_cutting_the_connection_needs_a_yes(calls, monkeypatch):
    changed = []
    monkeypatch.setitem(actions.REGISTRY, "set_option", lambda option, on: changed.append((option, on)) or "ok")
    engine = Engine()
    say(engine, "turn off wifi")
    assert changed == []
    assert "turn off Wi-Fi" in engine.events[-1]["text"]
    say(engine, "yes")
    assert changed == [("wifi", False)]
    say(engine, "turn on wifi")
    assert changed[-1] == ("wifi", True)  # turning it on is never risky


def test_emptying_the_trash_needs_a_yes(calls, monkeypatch):
    emptied = []
    monkeypatch.setitem(actions.REGISTRY, "empty_trash", lambda: emptied.append(1) or "ok")
    engine = Engine()
    say(engine, "empty the trash")
    assert emptied == []
    say(engine, "no")
    assert engine.events[-1]["text"] == "Okay, I won't"
    say(engine, "yes")
    assert emptied == []


def test_agent_is_refused_risky_switches():
    from buddy.agent import _execute
    outcome = _execute({"action": "set_option", "option": "wifi", "on": False}, "turn off wifi", 1.0, 0, 0)
    assert outcome.startswith("refused")


def test_nothing_in_buddy_can_reach_claude():
    from pathlib import Path

    import buddy
    for source in Path(buddy.__file__).parent.glob("*.py"):
        code = source.read_text()
        assert 'which("claude")' not in code and '"--tools"' not in code, source
        assert "anthropic" not in code.lower(), source


def test_missing_local_ai_is_explained_not_crashed(monkeypatch):
    import importlib

    import buddy.agent as agent
    real_run = importlib.reload(agent).run  # the real loop, with only the AI call replaced
    from buddy import ai, ocr

    def not_ready(*args, **kwargs):
        raise ai.NotReady(ai.INSTALL_HINT)

    monkeypatch.setattr(ai, "chat", not_ready)
    monkeypatch.setattr(ocr, "read_screen", lambda: [])
    monkeypatch.setattr(desktop, "screenshot_for_model", lambda: (b"", 1.0, 0, 0))
    monkeypatch.setattr(desktop, "list_windows", lambda: [])
    answer, resume = real_run("do something", "m", 3, lambda line: None, lambda: False)
    assert answer == ai.INSTALL_HINT and resume is None


def test_a_missing_button_is_reported_as_that_when_the_ai_is_absent(calls, monkeypatch):
    def not_found(text, **kwargs):
        raise actions.UnknownTarget(f"I can't see {text} on the screen")

    monkeypatch.setitem(actions.REGISTRY, "click_text", not_found)
    monkeypatch.setattr("buddy.ai.installed", lambda: False)
    engine = Engine()
    say(engine, "click save")
    assert engine.events[-1]["text"] == "I can't see save on the screen"


def test_launched_apps_do_not_inherit_ros_or_buddys_env(monkeypatch):
    from buddy import apps
    for name, value in [("LD_LIBRARY_PATH", "/opt/ros/jazzy/lib"), ("ROS_DISTRO", "jazzy"),
                        ("QT_QPA_PLATFORM_PLUGIN_PATH", "/cv2/qt"), ("ELECTRON_RUN_AS_NODE", "1"), ("SECRET_TOKEN", "x")]:
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: (_ for _ in ()).throw(OSError()))  # no session available
    env = apps.session_env()
    if sys.platform == "win32":  # there's no session to take a clean environment from, so only what breaks programs goes
        assert not {"QT_QPA_PLATFORM_PLUGIN_PATH", "ELECTRON_RUN_AS_NODE"} & set(env)
        return
    assert env["DISPLAY"] == ":1"
    assert not {"LD_LIBRARY_PATH", "ROS_DISTRO", "QT_QPA_PLATFORM_PLUGIN_PATH", "ELECTRON_RUN_AS_NODE", "SECRET_TOKEN"} & set(env)


def test_click_text_only_reads_the_front_window(monkeypatch):
    from buddy import ocr
    from buddy.desktop import Window
    seen, clicks = [], []
    monkeypatch.setattr(desktop, "target_window", lambda: Window(5, "App", "app"))
    monkeypatch.setattr(desktop, "activate", lambda window_id: None)
    monkeypatch.setattr(desktop, "window_rect", lambda window_id: (100, 100, 300, 300))
    monkeypatch.setattr(desktop, "click", lambda *a: clicks.append(a))
    monkeypatch.setattr(desktop, "window_pid", lambda window_id: 1)
    monkeypatch.setattr("buddy.a11y.press", lambda pid, text: False)
    monkeypatch.setattr(ocr, "read_screen", lambda rect=None: seen.append(rect) or [ocr.Item("Save", 150, 150)])
    actions.run("click_text", text="save")
    assert seen == [(100, 100, 300, 300)] and clicks == [(150, 150, 1, False)]
    with pytest.raises(actions.UnknownTarget):
        actions.run("click_text", text="delete")


def test_keys_are_withheld_if_the_right_window_cannot_be_focused(monkeypatch):
    from buddy.desktop import BUDDY_TITLE, Window, focus_target
    buddy_page = Window(1, BUDDY_TITLE, "localhost")
    monkeypatch.setattr(desktop, "active_window", lambda: buddy_page)
    monkeypatch.setattr(desktop, "target_window", lambda: Window(2, "Editor", "editor"))
    monkeypatch.setattr(desktop, "activate", lambda window_id: None)
    with pytest.raises(RuntimeError, match="didn't press"):
        focus_target()


def test_visible_text_gets_a_real_click_and_accessibility_is_the_fallback(monkeypatch):
    from buddy import a11y, ocr
    from buddy.desktop import Window
    clicks, presses = [], []
    monkeypatch.setattr(desktop, "target_window", lambda: Window(5, "App", "app"))
    monkeypatch.setattr(desktop, "activate", lambda window_id: None)
    monkeypatch.setattr(desktop, "window_rect", lambda window_id: (0, 0, 100, 100))
    monkeypatch.setattr(desktop, "window_pid", lambda window_id: 1)
    monkeypatch.setattr(desktop, "click", lambda *a: clicks.append(a))
    monkeypatch.setattr(a11y, "press", lambda pid, text: presses.append(text) or True)
    monkeypatch.setattr(ocr, "read_screen", lambda rect=None: [ocr.Item("Sound", 10, 20)])
    assert actions.run("click_text", text="sound") == "Clicked sound"
    assert clicks == [(10, 20, 1, False)] and presses == []
    assert actions.run("click_text", text="7") == "Pressed 7"
    assert presses == ["7"]


@pytest.fixture
def unlocks(calls, monkeypatch):
    made = []
    monkeypatch.setitem(actions.REGISTRY, "unlock_screen", lambda: made.append(1) or "Unlocked")
    return made


def say_from(engine, text, source):
    engine.submit(text, source=source)
    settle(engine)


def test_unlock_works_from_whatsapp_after_a_yes(unlocks):
    engine = Engine()
    say_from(engine, "unlock my laptop", "whatsapp")
    assert unlocks == [] and "unlock the laptop" in engine.events[-1]["text"]
    say_from(engine, "yes", "whatsapp")
    assert unlocks == [1]


def test_unlock_is_refused_by_voice_and_from_the_phone_link(unlocks):
    for source in ("local", "phone"):
        engine = Engine()
        say_from(engine, "unlock the laptop", source)
        say_from(engine, "yes", source)
        assert unlocks == []
        assert "only" in engine.events_after(0)[1]["text"]


def test_someone_nearby_cannot_confirm_a_whatsapp_unlock_by_voice(unlocks):
    engine = Engine()
    say_from(engine, "unlock my laptop", "whatsapp")
    say_from(engine, "yes", "local")
    assert unlocks == []
    say_from(engine, "yes", "whatsapp")  # the voice answer cancelled it, so this confirms nothing
    assert unlocks == []


def test_agent_can_never_unlock():
    from buddy.agent import AGENT_ACTIONS, _execute
    assert "unlock_screen" not in AGENT_ACTIONS
    assert _execute({"action": "unlock_screen"}, "unlock", 1.0, 0, 0).startswith("refused")


@pytest.mark.skipif(sys.platform == "win32", reason="Windows can't be unlocked by an app")
def test_a_display_wake_failure_never_turns_an_unlock_into_an_error(monkeypatch):
    states = iter([True, False])
    monkeypatch.setattr(actions, "is_locked", lambda: next(states))
    monkeypatch.setattr(actions, "_graphical_session", lambda: "2")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)

    def broken():
        raise OSError("no display")

    monkeypatch.setattr(desktop, "nudge_pointer", broken)
    assert actions.run("unlock_screen").startswith("Unlocked")


def test_run_commands_are_read_back_and_need_a_yes(calls, monkeypatch):
    ran = []
    monkeypatch.setitem(actions.REGISTRY, "run_command", lambda command: ran.append(command) or "done")
    engine = Engine()
    say(engine, "run ls -la")
    assert ran == [] and "ls -la" in engine.events[-1]["text"]
    say(engine, "yes")
    assert ran == ["ls -la"]


def test_long_answers_are_sent_not_read_aloud(calls, monkeypatch):
    monkeypatch.setitem(actions.REGISTRY, "system_report", lambda: "x" * 500)
    spoken = []

    class Voice:
        speaking = False
        def say(self, text): spoken.append(text)
        def stop(self): pass

    engine = Engine(speaker=Voice())
    say(engine, "report")
    assert engine.events[-1]["kind"] == "report" and spoken == []


class Voice:
    speaking = False
    def __init__(self): self.spoken = []
    def say(self, text): self.spoken.append(text)
    def stop(self): pass


def test_whatsapp_replies_are_not_read_aloud_but_voice_replies_are(calls):
    voice = Voice()
    engine = Engine(speaker=voice)
    say_from(engine, "lock the screen", "whatsapp")
    say_from(engine, "stop", "whatsapp")
    says = [event for event in engine.events if event["kind"] == "say"]
    assert voice.spoken == [] and len(says) == 2 and all(event["quiet"] for event in says)
    say_from(engine, "lock the screen", "local")
    assert len(voice.spoken) == 1 and not engine.events[-1]["quiet"]


def test_a_failed_whatsapp_command_is_not_read_aloud(calls, monkeypatch):
    def fail(mode):
        raise RuntimeError("no luck")
    monkeypatch.setitem(actions.REGISTRY, "power", fail)
    voice = Voice()
    engine = Engine(speaker=voice)
    say_from(engine, "lock the screen", "whatsapp")
    assert engine.events[-1]["kind"] == "error" and voice.spoken == []


def test_a_warning_after_a_whatsapp_command_is_still_read_aloud(calls):
    voice = Voice()
    engine = Engine(speaker=voice)
    say_from(engine, "lock the screen", "whatsapp")
    engine.emit("say", "A device just connected to your screen")
    assert voice.spoken == ["A device just connected to your screen"]


def test_wake_word_is_heard_anywhere_in_a_clip_with_music(calls):
    engine = Engine()
    assert say(engine, "la la la somewhere in the night Buddy, lock the screen", require_wake=True) is True
    assert calls == ["lock"]
    assert say(Engine(), "la la la body and soul lock the screen", require_wake=True) is False
