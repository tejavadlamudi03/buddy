import pytest


@pytest.fixture(autouse=True)
def no_real_agent(monkeypatch):
    """A test must never send the real screen to Claude. Tests that need the agent fake agent.run."""
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the real agent")

    monkeypatch.setattr("buddy.agent.run", refuse)
    monkeypatch.setattr("buddy.language.understand", lambda text, attached="": (text, "control"))


@pytest.fixture(autouse=True)
def no_real_keyboard(monkeypatch):
    """A test must never listen to the real keyboard. Dictation's tests drive its key handling directly."""
    monkeypatch.setattr("buddy.dictation.Dictation.start", lambda self: None)
