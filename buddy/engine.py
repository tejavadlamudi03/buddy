"""Takes transcribed speech and runs it: wake word, stop, confirmation, rules, then agent."""
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import actions, agent, ai, config, desktop, fetch, language
from .language import TELUGU_NO, TELUGU_STOP, TELUGU_YES
from .parser import Context, clean, parse

WAKE = re.compile(r"^\s*(?:(?:hey|hi|ok|okay|yo)[,\s]+)?(?:buddy|body|birdie)\b", re.I)
# Not "body" here: mid-sentence it's a common word, and sound can be running when this is checked.
WAKE_ANYWHERE = re.compile(r"\b(?:(?:hey|hi|ok|okay|yo)[,\s]+)?(?:buddy|birdie)\b", re.I)
STOP = re.compile(rf"(?:buddy[,\s]*)?(?:(?:stop|cancel|abort)(?: it| that| everything)?|{TELUGU_STOP})[.!\s]*", re.I)
YES = re.compile(rf"(?:buddy[,\s]*)?(?:(?:yes|yeah|yep|confirm|do it|go ahead)(?: please)?|{TELUGU_YES})[.!\s]*", re.I)
NO = re.compile(rf"(?:buddy[,\s]*)?(?:(?:no|nope|don'?t|do not|never ?mind|not now)(?: thanks| please| do that| it)?|{TELUGU_NO})[.!\s]*", re.I)
AWAKE_SECONDS = 10
FOLLOW_UP_SECONDS = 12
ANSWER_SECONDS = 60
RECENT_TURNS = 6
SPOKEN_LIMIT = 200  # longer answers are shown and sent, not read aloud
CONFIRM_SECONDS = 30
PASTED_CHARS = 300  # this long, or several lines: material to discuss, which must never run as commands
LINK = re.compile(r"https?://\S+")  # download links can be very long, and aren't what makes text pasted
HISTORY_CHARS = 1500
AGENT_HISTORY_CHARS = 200  # the agent's context also has to hold the screen


@dataclass
class Engine:
    events: list = field(default_factory=list)
    ctx: Context = field(default_factory=Context)
    busy: bool = False
    pending: list | None = None
    pending_since: float = 0.0
    pending_source: str = ""
    _source: str = "local"  # where the command being handled came from
    awake_until: float = 0.0
    follow_up_until: float = 0.0
    speaker: Any = None
    recent: list = field(default_factory=list)
    resume: tuple | None = None  # (task, history, asked_at) while the agent awaits an answer
    _stop: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, kind: str, text: str, quiet: bool = False) -> None:
        with self._lock:
            self.events.append({"id": len(self.events) + 1, "kind": kind, "text": text, "quiet": quiet})
        if kind in ("say", "error") and self.speaker and not quiet:
            self.speaker.say(text)

    def events_after(self, last_id: int) -> list:
        with self._lock:
            # A page that outlived a server restart holds an id from the old list.
            return self.events[last_id if last_id <= len(self.events) else 0:]

    def stop(self, quiet: bool = False) -> None:
        self._stop.set()
        fetch.cancel.set()
        self.pending = self.resume = None
        if self.speaker:
            self.speaker.stop()
        self.emit("say", "Stopped", quiet)

    def submit(self, text: str, require_wake: bool = False, source: str = "local") -> bool:
        """Returns whether the speech was meant for Buddy. Execution happens in the background."""
        text = text.strip()
        if not text:
            return False
        if require_wake and time.monotonic() > self.awake_until and not self._awaiting_answer():
            mentions = list(WAKE_ANYWHERE.finditer(text))
            if WAKE.match(text):
                pass
            elif mentions:  # with music on, a clip can start well before the wake word
                text = text[mentions[-1].start():]
            elif not self._is_follow_up(text):
                return False
        self.emit("heard", text)
        self.awake_until = 0.0
        quiet = source == "whatsapp"  # the answer goes back in the chat, so it isn't also read aloud

        if STOP.fullmatch(text):
            self.stop(quiet)
            return True
        if self.busy:
            self.emit("say", "Still working on the last one. Say stop to cancel it.", quiet)
            return True
        if not clean(text):  # just the wake word: listen to the next sentence without it
            self.awake_until = time.monotonic() + AWAKE_SECONDS
            self.emit("say", "Yes?", quiet)
            return True

        self.busy = True
        self._source = source
        self._stop.clear()
        fetch.cancel.clear()
        threading.Thread(target=self._process, args=(text,), daemon=True).start()
        return True

    def _is_follow_up(self, text: str) -> bool:
        """Right after a command, carry on without the wake word, as in a conversation.

        Only for things the rules recognise, so song lyrics or a passing remark
        can never reach the agent and send it a screenshot.
        """
        if time.monotonic() > self.follow_up_until:
            return False
        return bool(STOP.fullmatch(text) or parse(text, Context()))

    def listening(self) -> bool:
        """Whether the next thing said is meant for Buddy even with no wake word in it."""
        return time.monotonic() <= self.awake_until or self._awaiting_answer()

    def _awaiting_answer(self) -> bool:
        if self.resume is not None and time.monotonic() - self.resume[2] < ANSWER_SECONDS:
            return True
        return self.pending is not None and time.monotonic() - self.pending_since < CONFIRM_SECONDS

    def _process(self, text: str) -> None:
        quiet = self._source == "whatsapp"
        try:
            answer = self._handle(text)
            self.recent = (self.recent + [(clean(text), answer)])[-RECENT_TURNS:]
            self.emit("say" if len(answer) <= SPOKEN_LIMIT else "report", answer, quiet)
        except Exception as error:
            self.emit("error", str(error), quiet)
        finally:
            self.follow_up_until = time.monotonic() + FOLLOW_UP_SECONDS
            self.busy = False

    def _handle(self, text: str) -> str:
        if self.resume is not None:
            task, history, asked_at = self.resume
            self.resume = None
            if time.monotonic() - asked_at < ANSWER_SECONDS:
                if NO.fullmatch(text):
                    return "Okay, I won't"
                history.append(f"You asked the user, and they answered: {clean(text)}")
                return self._run_agent(task, history)

        if self.pending is not None:
            steps, fresh = self.pending, self._awaiting_answer()
            same_source = self._source == self.pending_source
            self.pending = None
            if fresh and not same_source and YES.fullmatch(text):
                return "That can only be confirmed from where it was asked, so I cancelled it"
            if fresh and same_source and YES.fullmatch(text):
                return self._run_steps(steps, confirmed=True)
            if fresh:
                self.emit("log", "Cancelled the pending action")
                if NO.fullmatch(text):
                    return "Okay, I won't"
        if YES.fullmatch(text):
            return "There's nothing waiting for confirmation"
        if NO.fullmatch(text):  # never worth a trip to the agent, or a screenshot of the screen
            return "Okay"

        agent_enabled = config.load()["agent_enabled"]
        if agent_enabled and (len(LINK.sub("", text)) > PASTED_CHARS or "\n" in text):
            return self._talk(text)

        target = desktop.target_window()
        self.ctx.on_youtube = bool(target and "youtube" in target.title.lower())
        unknown = None
        try:
            steps = parse(text, self.ctx)
            if steps is not None:
                return self._run_steps(steps)
        except actions.UnknownTarget as error:
            unknown = str(error)

        if not agent_enabled or (unknown and not ai.installed()):
            return unknown or "I didn't understand that"
        self.emit("log", "Not a built-in command, asking the local AI")
        english, kind = language.understand(text)
        if (links := LINK.findall(text)) and not LINK.search(english):  # the model sometimes writes "this link" instead
            english = f"{english} {links[0]}"
        if kind == "chat":
            return self._talk(text)
        if english != text:
            self.emit("log", f"Understood as: {english}")
            try:
                steps = parse(english, self.ctx)
                if steps is not None:
                    return self._run_steps(steps)
            except actions.UnknownTarget:
                pass
        return self._run_agent(clean(english))

    def _talk(self, text: str) -> str:
        """A conversation with the model. It's given no actions, so nothing said here can touch the laptop."""
        messages = []
        for said, answer in self.recent:
            messages += [{"role": "user", "content": said[:HISTORY_CHARS]},
                         {"role": "assistant", "content": answer[:HISTORY_CHARS]}]
        messages.append({"role": "user", "content": text})
        try:
            answer = language.tidy(ai.talk(config.load()["ai_model"], language.chat_prompt(text), messages)).strip()
        except ai.NotReady as error:
            return str(error)
        if self._stop.is_set():
            return "Stopped"
        return answer or "I couldn't come up with an answer to that"

    def _run_agent(self, task: str, history: list | None = None) -> str:
        settings = config.load()
        recent = tuple((said[:AGENT_HISTORY_CHARS], answer[:AGENT_HISTORY_CHARS]) for said, answer in self.recent)
        answer, to_resume = agent.run(
            task, settings["ai_model"], settings["agent_max_steps"],
            lambda line: self.emit("log", line), self._stop.is_set,
            recent=recent, history=history,
        )
        if to_resume is not None:
            self.resume = (task, to_resume, time.monotonic())
        return answer

    def _run_steps(self, steps: list, confirmed: bool = False) -> str:
        if self._source != "whatsapp" and any(step["action"] in actions.WHATSAPP_ONLY for step in steps):
            return "For safety, I only do that from your WhatsApp number"
        actions_asked = {step["action"] for step in steps}
        if self._source == "whatsapp" and "screenshot" in actions_asked and "whatsapp_send_screenshot" not in actions_asked:
            steps = [*steps, {"action": "whatsapp_send_screenshot"}]  # from a phone, a saved picture is no use
        if self._source == "whatsapp":  # likewise a file only saved on the laptop
            steps = [{**step, "send": True} if step["action"] in actions.SENDS_FILE else step for step in steps]
        if not confirmed and any(actions.is_risky(step) for step in steps):
            self.pending, self.pending_since, self.pending_source = steps, time.monotonic(), self._source
            risk = next(actions.risk_of(step) for step in steps if actions.is_risky(step))
            return f"Are you sure you want to {risk}? Say yes to confirm."
        spoken = []
        for step in steps:
            if self._stop.is_set():
                return "Stopped"
            args = {key: value for key, value in step.items() if key != "action"}
            outcome = actions.run(step["action"], **args)
            if outcome:
                self.emit("log", outcome)
                spoken.append(outcome)
        return spoken[-1] if spoken else "Done"
