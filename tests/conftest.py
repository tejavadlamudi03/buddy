import pytest


@pytest.fixture(autouse=True)
def no_real_agent(monkeypatch):
    """A test must never send the real screen to Claude. Tests that need the agent fake agent.run."""
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the real agent")

    monkeypatch.setattr("buddy.agent.run", refuse)
    monkeypatch.setattr("buddy.language.understand", lambda text, attached="": (text, "control"))
