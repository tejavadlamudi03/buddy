import threading
import time
from datetime import datetime

import pytest

from buddy import actions, engine as engine_module, reminders, whatsapp
from buddy.engine import Engine
from buddy.parser import parse

NOW = datetime(2026, 11, 2, 15, 30)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(reminders, "FILE", tmp_path / "reminders.json")
    monkeypatch.setattr(whatsapp, "_bridge", None)


class Bridge:
    phone = "1"

    def __init__(self, ready=True):
        self.ready = threading.Event()
        if ready:
            self.ready.set()


@pytest.mark.parametrize("said,expected", [
    ("remind me in 10 minutes to call home", {"seconds": 600, "text": "call home"}),
    ("Remind me to Call Home in 10 minutes", {"seconds": 600, "text": "Call Home"}),
    ("remind me after an hour to stretch", {"seconds": 3600, "text": "stretch"}),
    ("remind me in half an hour about the oven", {"seconds": 1800, "text": "the oven"}),
    ("remind me in 1 hour and 30 minutes to leave", {"seconds": 5400, "text": "leave"}),
    ("remind me at 5 pm to call home", {"hour": 5, "minute": 0, "meridiem": "p", "text": "call home"}),
    ("remind me to pay rent tomorrow at 9", {"hour": 9, "minute": 0, "day": "tomorrow", "text": "pay rent"}),
    ("remind me tomorrow at 9:30 am to pay rent and call home", {"hour": 9, "minute": 30, "meridiem": "a", "day": "tomorrow", "text": "pay rent and call home"}),
    ("set a reminder at 17:45 for the meeting", {"hour": 17, "minute": 45, "text": "the meeting"}),
])
def test_reminder_phrases(said, expected):
    assert parse(said) == [{"action": "remind", **expected}]


@pytest.mark.parametrize("said,seconds", [
    ("set a timer for 5 minutes", 300), ("start a 10 minute timer", 600), ("timer for 30 seconds", 30), ("set a timer for an hour", 3600),
])
def test_timer_phrases(said, seconds):
    steps = parse(said)
    assert steps[0]["action"] == "remind" and steps[0]["seconds"] == seconds and "timer is up" in steps[0]["text"]


def test_no_time_asks_rather_than_guessing():
    assert parse("remind me to call home")[0]["action"] == "reply"
    assert parse("remind me at the station to call home")[0]["action"] == "reply"


def test_list_and_cancel_phrases():
    assert parse("what are my reminders") == parse("list my reminders") == [{"action": "list_reminders"}]
    assert parse("cancel my reminders") == parse("clear all reminders") == parse("cancel the timer") == [{"action": "cancel_reminders"}]
    assert parse("cancel the reminder about rent") == [{"action": "cancel_reminders", "words": "rent"}]


def test_plain_stop_words_are_untouched():
    engine = Engine()
    assert engine_module.STOP.fullmatch("cancel")


@pytest.mark.parametrize("args,expected", [
    ((5, 0, "p", ""), datetime(2026, 11, 2, 17, 0)),   # later today
    ((3, 0, "p", ""), datetime(2026, 11, 3, 15, 0)),   # already gone, so tomorrow
    ((9, 0, "a", ""), datetime(2026, 11, 3, 9, 0)),
    ((4, 0, "", ""), datetime(2026, 11, 2, 16, 0)),    # the next 4 coming up
    ((3, 0, "", ""), datetime(2026, 11, 3, 3, 0)),     # 3:00 and 15:00 have both gone
    ((18, 15, "", ""), datetime(2026, 11, 2, 18, 15)),
    ((12, 0, "p", ""), datetime(2026, 11, 3, 12, 0)),  # noon
    ((12, 0, "a", ""), datetime(2026, 11, 3, 0, 0)),   # midnight
    ((9, 0, "", "tomorrow"), datetime(2026, 11, 3, 9, 0)),   # a waking hour
    ((5, 0, "", "tomorrow"), datetime(2026, 11, 3, 17, 0)),
])
def test_clock_times(args, expected):
    assert reminders.clock_time(*args, now=NOW) == expected


def test_impossible_times():
    with pytest.raises(ValueError):
        reminders.clock_time(25)
    with pytest.raises(ValueError):
        reminders.clock_time(14, 0, "p")


def test_spoken_times():
    assert reminders.spoken(datetime(2026, 11, 2, 17, 0), NOW) == "at 5:00 PM"
    assert reminders.spoken(datetime(2026, 11, 3, 9, 5), NOW) == "tomorrow at 9:05 AM"
    assert reminders.spoken(datetime(2026, 11, 20, 9, 0), NOW) == "on Friday 20 November at 9:00 AM"


def test_the_reply_repeats_when_so_a_mistake_is_visible():
    assert "remind you at" in actions.run("remind", text="call home", seconds=60)
    assert reminders.upcoming()[0]["text"] == "call home"


def test_listing_and_cancelling():
    assert actions.run("list_reminders") == "You have no reminders"
    actions.run("remind", text="pay rent", seconds=120)
    actions.run("remind", text="call home", seconds=60)
    listed = actions.run("list_reminders")
    assert listed.index("call home") < listed.index("pay rent")
    assert "couldn't find" in actions.run("cancel_reminders", words="dentist")
    assert actions.run("cancel_reminders", words="RENT") == "Cancelled that reminder"
    assert actions.run("cancel_reminders") == "Cancelled that reminder"
    assert reminders.upcoming() == []


def test_private_file_permissions():
    reminders.add(1.0, "x")
    assert reminders.FILE.stat().st_mode & 0o077 == 0


def test_not_due_yet_stays():
    engine = Engine()
    reminders.add(time.time() + 100, "later")
    reminders.check(engine)
    assert not engine.events and reminders.upcoming()


def test_a_reminder_set_aloud_is_spoken_and_messaged(monkeypatch):
    sent = []
    monkeypatch.setattr(whatsapp, "_bridge", Bridge())
    monkeypatch.setattr(whatsapp, "send_text", lambda phone, text: sent.append(text))
    engine = Engine()
    reminders.add(time.time() - 1, "call home")
    reminders.check(engine)
    assert sent == ["Reminder: call home"] and engine.events[-1]["text"] == "Reminder: call home"
    assert not reminders.upcoming()


def test_a_reminder_set_on_whatsapp_stays_quiet_on_the_laptop(monkeypatch):
    sent = []
    monkeypatch.setattr(whatsapp, "_bridge", Bridge())
    monkeypatch.setattr(whatsapp, "send_text", lambda phone, text: sent.append(text))
    engine = Engine()
    reminders.add(time.time() - 1, "call home", True)
    reminders.check(engine)
    assert sent and not engine.events


def test_waits_for_whatsapp_then_says_it_aloud_rather_than_losing_it(monkeypatch):
    monkeypatch.setattr(whatsapp, "_bridge", Bridge(ready=False))
    engine = Engine()
    when = time.time() - 60
    reminders.add(when, "call home", True)
    reminders.check(engine)
    assert not engine.events and reminders.upcoming()
    reminders.check(engine, now=when + reminders.GIVE_UP_SECONDS + 1)
    assert "call home" in engine.events[-1]["text"] and not reminders.upcoming()


def test_a_failed_send_is_tried_again(monkeypatch):
    monkeypatch.setattr(whatsapp, "_bridge", Bridge())
    def fail(phone, text):
        raise RuntimeError("browser restarting")
    monkeypatch.setattr(whatsapp, "send_text", fail)
    engine = Engine()
    reminders.add(time.time() - 1, "call home", True)
    reminders.check(engine)
    assert reminders.upcoming() and not engine.events


def test_late_reminders_say_when_they_were_due(monkeypatch):
    monkeypatch.setattr(whatsapp, "_bridge", Bridge(ready=False))
    engine = Engine()
    reminders.add(time.time() - 3600, "call home")
    reminders.check(engine)
    assert "was due" in engine.events[-1]["text"]


def test_a_damaged_file_loses_nothing_else(monkeypatch):
    reminders.FILE.write_text("not json")
    assert reminders.upcoming() == []
    reminders.add(1.0, "x")
    assert len(reminders.upcoming()) == 1


def test_whatsapp_requests_are_answered_on_whatsapp(monkeypatch):
    monkeypatch.setattr(engine_module.desktop, "target_window", lambda: None)
    engine = Engine()
    engine.submit("remind me in 10 minutes to call home", source="whatsapp")
    for _ in range(300):
        if not engine.busy:
            break
        time.sleep(0.01)
    assert reminders.upcoming()[0]["whatsapp_only"] is True
