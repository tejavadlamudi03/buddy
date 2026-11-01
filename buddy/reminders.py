"""Reminders and timers. They survive a restart, and arrive late rather than never if the laptop slept."""
import json
import os
import threading
import time
from datetime import datetime, timedelta

from . import config, whatsapp

FILE = config.DATA_DIR / "reminders.json"
CHECK_SECONDS = 15
LATE_SECONDS = 120  # later than this, and the message says when it was due
GIVE_UP_SECONDS = 24 * 3600  # waiting this long for WhatsApp, it's said aloud instead
MAX_SECONDS = 366 * 24 * 3600
_lock = threading.Lock()
_started = False


def _load() -> list:
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return []


def _save(items: list) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # what you plan to do is private
    with os.fdopen(fd, "w") as stream:
        json.dump(items, stream)


def clock_time(hour: int, minute: int = 0, meridiem: str = "", day: str = "", now: datetime | None = None) -> datetime:
    """The next time that clock reading comes round. Without am or pm: the next one today, or a waking hour tomorrow."""
    now = now or datetime.now()
    meridiem = meridiem.lower()[:1]
    if hour > 23 or minute > 59 or (meridiem and not 1 <= hour <= 12):
        raise ValueError("That isn't a time I understand")
    if meridiem:
        options = [hour % 12 + (12 if meridiem == "p" else 0)]
    elif hour == 0 or hour > 12:
        options = [hour]
    elif day == "tomorrow":
        options = [hour if 7 <= hour <= 12 else hour + 12]
    else:
        options = [hour % 12, hour % 12 + 12]
    base = now + timedelta(days=1) if day == "tomorrow" else now
    for option in options:
        moment = base.replace(hour=option, minute=minute, second=0, microsecond=0)
        if moment > now:
            return moment
    return now.replace(hour=options[0], minute=minute, second=0, microsecond=0) + timedelta(days=1)


def spoken(moment: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now()
    clock = f"{moment:%-I:%M %p}"
    if moment.date() == now.date():
        return f"at {clock}"
    if moment.date() == (now + timedelta(days=1)).date():
        return f"tomorrow at {clock}"
    return f"on {moment:%A %-d %B} at {clock}"


def add(when: float, text: str, whatsapp_only: bool = False) -> None:
    with _lock:
        _save([*_load(), {"when": when, "text": text, "whatsapp_only": whatsapp_only}])


def upcoming() -> list:
    with _lock:
        return sorted(_load(), key=lambda item: item["when"])


def cancel(words: str = "") -> int:
    """How many were removed: those mentioning the words, or all of them with no words."""
    with _lock:
        items = _load()
        kept = [item for item in items if words and words.lower() not in item["text"].lower()]
        _save(kept)
    return len(items) - len(kept)


def _message(item: dict, now: float) -> str:
    message = f"Reminder: {item['text']}"
    if now - item["when"] > LATE_SECONDS:
        message += f" (this was due {spoken(datetime.fromtimestamp(item['when']))}, while I was asleep or busy)"
    return message


def _deliver(item: dict, engine, now: float) -> bool:
    """Whether it's been delivered and can be forgotten."""
    bridge = whatsapp.current_bridge()
    ready = bridge is not None and bridge.ready.is_set()
    message = _message(item, now)
    if ready:
        try:
            whatsapp.send_text(bridge.phone, message)
        except Exception as error:  # the browser may be restarting, so try again shortly
            print(f"Reminder not sent: {type(error).__name__}", flush=True)
            ready = False
    if item["whatsapp_only"] and not ready and now - item["when"] < GIVE_UP_SECONDS:
        return False
    if not item["whatsapp_only"] or not ready:
        engine.emit("say", message)
    return True


def check(engine, now: float | None = None) -> None:
    now = time.time() if now is None else now
    for item in upcoming():
        if item["when"] > now:
            break
        if _deliver(item, engine, now):
            with _lock:
                items = _load()
                if item in items:
                    items.remove(item)
                    _save(items)


def start(engine) -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        while True:
            try:
                check(engine)
            except Exception as error:
                print(f"Reminder check failed: {type(error).__name__}: {str(error)[:200]}", flush=True)
            time.sleep(CHECK_SECONDS)

    threading.Thread(target=loop, daemon=True).start()
