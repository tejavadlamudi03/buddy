import importlib
import json

import pytest

from buddy import actions, ai, desktop, ocr


@pytest.fixture
def agent(monkeypatch):
    import buddy.agent as module
    module = importlib.reload(module)  # the real loop; conftest only replaces the name it had before
    monkeypatch.setattr(ocr, "read_screen", lambda rect=None: [])
    monkeypatch.setattr(desktop, "screenshot_for_model", lambda: (b"", 1.0, 0, 0))
    monkeypatch.setattr(desktop, "list_windows", lambda: [])
    return module


def scripted(monkeypatch, *replies):
    remaining = list(replies)
    prompts = []

    def chat(model, system, prompt, image=None):
        prompts.append(prompt)
        return json.dumps(remaining.pop(0) if len(remaining) > 1 else remaining[0])

    monkeypatch.setattr(ai, "chat", chat)
    return prompts


def run(agent, task):
    return agent.run(task, "m", 6, lambda line: None, lambda: False)[0]


CLAIM = {"actions": [], "done": True, "say": "The volume has been set to 100%."}


def test_a_claim_with_no_action_is_never_passed_on_as_success(agent, monkeypatch):
    prompts = scripted(monkeypatch, CLAIM)
    assert run(agent, "increase the volume somehow") == "I couldn't work out how to do that, so nothing has changed"
    assert "without taking any action" in prompts[1]


def test_being_challenged_can_make_it_really_act(agent, monkeypatch):
    done = []
    monkeypatch.setitem(actions.REGISTRY, "volume", lambda change, level=None: done.append(level) or "Volume 100%")
    scripted(monkeypatch, CLAIM,
             {"actions": [{"action": "volume", "change": "set", "level": 100}], "done": True, "say": "Set to 100."})
    assert run(agent, "make the sound as big as it goes") == "Set to 100."
    assert done == [100]


def test_questions_can_be_answered_without_actions(agent, monkeypatch):
    scripted(monkeypatch, {"actions": [], "done": True, "say": "Paris."})
    assert run(agent, "what is the capital of France") == "Paris."


def test_a_failed_action_is_reported_not_covered_up(agent, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("no such window")

    monkeypatch.setitem(actions.REGISTRY, "focus_window", broken)
    scripted(monkeypatch, {"actions": [{"action": "focus_window", "name": "x"}], "done": True, "say": "All done!"})
    answer = run(agent, "switch over to x")
    assert "didn't work" in answer and "no such window" in answer


def test_a_stuck_model_cannot_repeat_an_action_endlessly(agent, monkeypatch):
    done = []
    monkeypatch.setitem(actions.REGISTRY, "volume", lambda change, level=None: done.append(change) or "ok")
    scripted(monkeypatch, {"actions": [{"action": "volume", "change": "down"}] * 60, "done": True, "say": "ok"})
    run(agent, "lower the sound a lot")
    assert len(done) == agent.MAX_ACTIONS_PER_TURN


def test_malformed_replies_are_retried_not_fatal(agent, monkeypatch):
    replies = iter(["{not json", json.dumps({"actions": [], "done": True, "say": "Paris."})])
    monkeypatch.setattr(ai, "chat", lambda *a, **k: next(replies))
    assert run(agent, "what is the capital of France") == "Paris."


def _window(cls):
    from buddy.desktop import Window
    return Window(1, "x", cls)


def test_the_ai_cannot_type_unless_the_request_is_about_typing(agent, monkeypatch):
    typed = []
    monkeypatch.setitem(actions.REGISTRY, "type_text", lambda text: typed.append(text) or "ok")
    monkeypatch.setattr(desktop, "target_window", lambda: _window("firefox"))
    for task in ("80%", "80 percent", "turn it up to the max"):
        assert agent._execute({"action": "type_text", "text": task}, task, 1.0, 0, 0).startswith("refused"), task
    assert typed == []
    agent._execute({"action": "type_text", "text": "cats"}, "search for cats on this page", 1.0, 0, 0)
    assert typed == ["cats"]


def test_the_ai_leaves_a_code_editor_alone_unless_it_is_named(agent, monkeypatch):
    pressed = []
    monkeypatch.setitem(actions.REGISTRY, "press_keys", lambda combo: pressed.append(combo) or "ok")
    monkeypatch.setitem(actions.REGISTRY, "type_text", lambda text: pressed.append(text) or "ok")
    monkeypatch.setattr(desktop, "target_window", lambda: _window("com.microsoft.vscode com.microsoft.VSCode"))
    assert agent._execute({"action": "press_keys", "combo": "ctrl+a"}, "tidy this up", 1.0, 0, 0).startswith("refused")
    assert agent._execute({"action": "type_text", "text": "x"}, "write a note", 1.0, 0, 0).startswith("refused")
    assert pressed == []
    agent._execute({"action": "press_keys", "combo": "ctrl+s"}, "save the file in vs code", 1.0, 0, 0)
    assert pressed == ["ctrl+s"]
