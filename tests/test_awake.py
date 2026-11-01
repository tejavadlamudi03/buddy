import os
import signal
import time

import pytest

from buddy import actions, awake
from buddy.parser import duration_seconds, parse


@pytest.fixture
def block(tmp_path, monkeypatch):
    """A plain sleep stands in for systemd-inhibit, recognised the same way."""
    monkeypatch.setattr(awake, "STATE", tmp_path / "stay-awake.json")
    script = tmp_path / "systemd-inhibit"
    script.write_text('#!/bin/sh\nexec -a systemd-inhibit "$@"\n')
    script.chmod(0o755)
    monkeypatch.setattr(awake, "BLOCK", [str(script)])
    yield
    awake.stop()


def test_phrases():
    assert parse("stay awake") == [{"action": "stay_awake"}]
    assert parse("Buddy, keep the laptop awake") == [{"action": "stay_awake"}]
    assert parse("don't sleep") == [{"action": "stay_awake"}]
    assert parse("stay awake for 2 hours") == [{"action": "stay_awake", "seconds": 7200}]
    assert parse("stay awake for half an hour") == [{"action": "stay_awake", "seconds": 1800}]
    assert parse("allow sleep") == parse("let the laptop sleep") == parse("you can sleep") == [{"action": "allow_sleep"}]


def test_sleeping_on_command_is_still_sleep():
    assert parse("go to sleep") == [{"action": "power", "mode": "suspend"}]


def test_an_unclear_length_goes_to_the_model_rather_than_a_guess():
    assert parse("stay awake for my meeting") is None


def test_durations():
    assert duration_seconds("two hours") == 7200
    assert duration_seconds("an hour") == 3600
    assert duration_seconds("1 hour and 30 minutes") == 5400
    assert duration_seconds("90 seconds") == 90
    assert duration_seconds("a while") is None
    assert duration_seconds("5 minutes or so") is None


def test_start_and_stop(block):
    assert awake.until() is None
    until = awake.start(60)
    assert awake.until() == until
    assert awake.stop() is True
    assert awake.until() is None
    assert awake.stop() is False


def test_starting_again_replaces_the_old_block(block):
    awake.start(60)
    first = awake._read()["pid"]
    awake.start(60)
    assert awake._read()["pid"] != first
    for _ in range(100):  # killed, but it can take a moment to go
        if not awake._running(first):
            break
        time.sleep(0.02)
    assert not awake._running(first)


def test_never_more_than_a_day(block):
    assert awake.start(10 * awake.MAX_SECONDS) <= time.time() + awake.MAX_SECONDS


def test_restore_after_the_block_died(block):
    awake.start(60)
    state = awake._read()
    os.killpg(state["pid"], signal.SIGKILL)
    for _ in range(100):
        if not awake._running(state["pid"]):
            break
        time.sleep(0.02)
    awake.STATE.write_text(awake.json.dumps(state))
    awake.restore()
    assert awake.until() is not None and awake._read()["pid"] != state["pid"]


def test_restore_does_nothing_once_the_time_has_passed(block):
    awake.STATE.write_text(awake.json.dumps({"until": time.time() - 5, "pid": 1}))
    awake.restore()
    assert awake.until() is None


def test_a_reused_process_number_is_never_killed(block):
    awake.STATE.write_text(awake.json.dumps({"until": time.time() + 60, "pid": os.getpid()}))
    assert awake.until() is None and awake.stop() is False


def test_actions_report_what_happened(block):
    assert "stay awake until" in actions.run("stay_awake", seconds=60)
    assert "sleep normally" in actions.run("allow_sleep")
    assert "wasn't" in actions.run("allow_sleep")
