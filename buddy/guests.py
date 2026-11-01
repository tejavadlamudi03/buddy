"""Other people on WhatsApp: a password first, and then a conversation with the local AI and nothing more.

A guest's words only ever reach the model, in a conversation of their own. They never reach the engine,
so nothing a guest says can touch the laptop or see what the owner has said. Off until a password is set.
"""
import base64
import collections
import hashlib
import json
import os
import secrets
import threading
import time

from . import ai, config, language

STATE = config.DATA_DIR / "whatsapp-guests.json"
MIN_PASSWORD = 8
ACCESS_SECONDS = 7 * 24 * 3600
MAX_TRIES = 3
LOCK_SECONDS = 3600
MAX_FAILS_PER_HOUR = 20  # across everyone, so many numbers together can't keep guessing
MAX_WAITING = 10
MAX_PER_HOUR = 30
MAX_GUEST_CHARS = 2000
MAX_GUEST_TOKENS = 800  # about 12 seconds, so the owner never waits long for a guest's answer to finish
HISTORY_TURNS = 6
GUEST_NOTE = """
You are talking with a guest, not with your owner. You know nothing about your owner, and you have no access to their laptop, files, messages or anything else of theirs. Never claim otherwise, and never make up details about them. If asked to do something on a computer, say you can only chat here."""
ASK = "Hi! This is Buddy, an assistant on this number. Please send the password to continue."
WELCOME = "Thanks, that's correct. Ask me anything. I can only chat here, and I can't do anything on the laptop."
LOCKED = "That's too many wrong tries. Please try again in an hour."
STILL_WORKING = "I'm still working on your last message. Please wait for that answer first."
TOO_BUSY = "Too many people are waiting right now. Please try again in a few minutes."
TOO_MANY = "You've reached the limit of messages for this hour. Please try again later."
TEXT_ONLY = "I can only read text messages here."
CANT_ANSWER = "Sorry, I couldn't answer that."


def _read() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _write(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)


def _hash(password: str, salt: bytes) -> str:
    return base64.b64encode(hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)).decode()


def enabled() -> bool:
    return "hash" in _read()


def set_password(password: str) -> None:
    """Everyone who had access has to send the new one."""
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"The password needs at least {MIN_PASSWORD} characters")
    salt = secrets.token_bytes(16)
    _write({"salt": base64.b64encode(salt).decode(), "hash": _hash(password, salt), "guests": {}})


def turn_off() -> None:
    STATE.unlink(missing_ok=True)


def check_password(text: str) -> bool:
    state = _read()
    if "hash" not in state:
        return False
    return secrets.compare_digest(_hash(text.strip(), base64.b64decode(state["salt"])), state["hash"])


def has_access(name: str) -> bool:
    return _read().get("guests", {}).get(name, 0) > time.time()


def with_access() -> int:
    return sum(until > time.time() for until in _read().get("guests", {}).values())


def _grant(name: str) -> None:
    state = _read()
    state.setdefault("guests", {})[name] = time.time() + ACCESS_SECONDS
    _write(state)


class Desk:
    """Who may talk, and the one line everyone waits in, since the model answers one question at a time."""

    def __init__(self, engine):
        self.engine = engine
        self._lock = threading.Lock()
        self._line = collections.deque()
        self._working_for = None
        self._wake = threading.Event()
        self._worker = None
        self._replies = {}
        self._asked = set()
        self._tries = {}
        self._locked_until = {}
        self._fails = collections.deque()
        self._answered = {}
        self._history = {}

    def receive(self, name: str, text: str) -> str | None:
        """What to send right away, if anything. A real answer arrives later, through take_replies."""
        if not enabled():
            return None
        if has_access(name):
            return self._queue(name, text)
        now = time.monotonic()
        if self._locked_until.get(name, 0) > now:
            return None
        self._locked_until.pop(name, None)
        while self._fails and now - self._fails[0] > LOCK_SECONDS:
            self._fails.popleft()
        if len(self._fails) >= MAX_FAILS_PER_HOUR:  # someone's guessing: stop answering, and stop checking
            return None
        if check_password(text):
            self._tries.pop(name, None)
            _grant(name)
            self.engine.emit("log", f"A guest got access on WhatsApp: {name}")
            return WELCOME
        if name not in self._asked:  # a first hello isn't a wrong password
            self._asked.add(name)
            return ASK
        self._fails.append(now)
        self._tries[name] = self._tries.get(name, 0) + 1
        left = MAX_TRIES - self._tries[name]
        if left <= 0:
            self._locked_until[name] = now + LOCK_SECONDS
            self._tries.pop(name)
            return LOCKED
        return f"That's not the right password. {left} {'try' if left == 1 else 'tries'} left."

    def about_file(self, name: str) -> str | None:
        return TEXT_ONLY if enabled() and has_access(name) else None

    def _queue(self, name: str, text: str) -> str | None:
        with self._lock:
            if self._working_for == name or any(waiting == name for waiting, _ in self._line):
                return STILL_WORKING
            now = time.monotonic()
            recent = self._answered.setdefault(name, collections.deque())
            while recent and now - recent[0] > 3600:
                recent.popleft()
            if len(recent) >= MAX_PER_HOUR:
                return TOO_MANY
            ahead = len(self._line) + (self._working_for is not None)
            if ahead >= MAX_WAITING:
                return TOO_BUSY
            recent.append(now)
            self._line.append((name, text[:MAX_GUEST_CHARS]))
            if self._worker is None:
                self._worker = threading.Thread(target=self._work, daemon=True)
                self._worker.start()
        self._wake.set()
        return f"Got it. You're number {ahead + 1} in line." if ahead else None

    def _answer(self, name: str, text: str) -> str:
        history = self._history.setdefault(name, collections.deque(maxlen=HISTORY_TURNS))
        messages = []
        for said, answer in history:
            messages += [{"role": "user", "content": said}, {"role": "assistant", "content": answer}]
        messages.append({"role": "user", "content": text})
        try:
            answer = language.tidy(ai.talk(config.load()["ai_model"], language.chat_prompt(text) + GUEST_NOTE, messages,
                                           max_tokens=MAX_GUEST_TOKENS)).strip()
        except (RuntimeError, OSError, ValueError, KeyError) as error:
            print(f"A guest's answer failed: {type(error).__name__}", flush=True)
            return CANT_ANSWER
        answer = answer or CANT_ANSWER
        history.append((text, answer))
        return answer

    def work_once(self) -> bool:
        with self._lock:
            if not self._line:
                return False
            name, text = self._line.popleft()
            self._working_for = name
        while self.engine.busy:  # the owner goes first
            time.sleep(0.5)
        answer = self._answer(name, text)
        with self._lock:
            self._working_for = None
            self._replies.setdefault(name, []).append(answer)
        return True

    def _work(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            while self.work_once():
                pass

    def take_replies(self) -> dict:
        with self._lock:
            replies, self._replies = self._replies, {}
        return replies

    def waiting(self) -> int:
        with self._lock:
            return len(self._line) + (self._working_for is not None)


current: Desk | None = None
