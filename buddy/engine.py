"""Takes transcribed speech and runs it: wake word, stop, confirmation, rules, then agent."""
import base64
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import actions, agent, ai, config, desktop, fetch, games, language
from .language import SAID_NO, SAID_STOP, SAID_YES
from .parser import Context, clean, parse

WAKE = re.compile(r"^\s*(?:(?:hey|hi|ok|okay|yo)[,\s]+)?(?:buddy|body|birdie)\b", re.I)
# Not "body" here: mid-sentence it's a common word, and sound can be running when this is checked.
WAKE_ANYWHERE = re.compile(r"\b(?:(?:hey|hi|ok|okay|yo)[,\s]+)?(?:buddy|birdie)\b", re.I)
STOP = re.compile(rf"(?:buddy[,\s]*)?(?:(?:stop|cancel|abort)(?: it| that| everything)?|{SAID_STOP})[.!।\s]*", re.I)
YES = re.compile(rf"(?:buddy[,\s]*)?(?:(?:yes|yeah|yep|confirm|do it|go ahead)(?: please)?|{SAID_YES})[.!।\s]*", re.I)
NO = re.compile(rf"(?:buddy[,\s]*)?(?:(?:no|nope|don'?t|do not|never ?mind|not now)(?: thanks| please| do that| it)?|{SAID_NO})[.!।\s]*", re.I)
AWAKE_SECONDS = 15
FOLLOW_UP_SECONDS = 12
ANSWER_SECONDS = 60
RECENT_TURNS = 6
SPOKEN_LIMIT = 200  # longer answers are shown and sent, not read aloud
CONFIRM_SECONDS = 30
PASTED_CHARS = 300  # this long, or several lines: material to discuss, which must never run as commands
LINK = re.compile(r"https?://\S+")  # download links can be very long, and aren't what makes text pasted
HISTORY_CHARS = 1500
AGENT_HISTORY_CHARS = 200  # the agent's context also has to hold the screen
ATTACHMENT_SECONDS = 30 * 60  # how long follow-up questions can still be about a file
GAME_IDLE_SECONDS = 15 * 60  # left this long, a game is over, so the next message is a command again


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
    attachment: Any = None  # the file last sent on WhatsApp
    attachment_at: float = 0.0
    game: Any = None
    last_game: str = ""  # so "one more" can start it again
    last_game_at: float = 0.0
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
        self.pending = self.resume = self.game = None
        self.awake_until = 0.0
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

    def submit_file(self, question: str, attachment) -> None:
        """A file and its caption. Only ever discussed: the caption can't run as a command, nor can what's in the file."""
        if self.busy:
            self.emit("say", "Still working on the last one. Send the file again when I'm done, or say stop.", True)
            return
        game = self._current_game()
        if isinstance(game, games.DrawAndGuess) and game.source == "whatsapp" and attachment.pictures:
            self._guess_drawing(attachment.pictures[0], "whatsapp")
            return
        self.emit("heard", f"[{attachment.name}] {question}".strip())
        self.attachment, self.attachment_at = attachment, time.monotonic()
        self.busy = True
        self._source = "whatsapp"
        self._stop.clear()
        threading.Thread(target=self._process, args=(question or language.about_file(attachment),), kwargs={"talk_only": True}, daemon=True).start()

    def submit_drawing(self, picture: bytes) -> None:
        """A drawing from the page, for the game that's waiting for one."""
        game = self._current_game()
        if self.busy or not isinstance(game, games.DrawAndGuess):
            self.emit("say", "Still working on the last one." if self.busy else "Start Draw and Guess first, then draw for me.")
            return
        self._guess_drawing(picture, "local")

    def _guess_drawing(self, picture: bytes, source: str) -> None:
        self.emit("heard", "[your drawing]")
        self.busy = True
        self._source = source
        self._stop.clear()
        threading.Thread(target=self._process, args=("",), kwargs={"picture": picture}, daemon=True).start()

    def _fresh_attachment(self):
        return self.attachment if time.monotonic() - self.attachment_at < ATTACHMENT_SECONDS else None

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
        game = self._current_game()
        if game and game.source == "local" and time.monotonic() - game.last_turn < ANSWER_SECONDS:
            return True
        return self.pending is not None and time.monotonic() - self.pending_since < CONFIRM_SECONDS

    def _process(self, text: str, talk_only: bool = False, picture: bytes | None = None) -> None:
        quiet = self._source == "whatsapp"
        try:
            if picture is not None:
                answer = self._play(lambda game: game.drawing(picture))
            else:
                answer = self._talk(text) if talk_only else self._handle(text)
            if talk_only and self.attachment.note:
                answer = f"{self.attachment.note}\n\n{answer}"
            if self.game is None:  # the turns of a game would only confuse a later conversation
                self.recent = (self.recent + [(clean(text), answer)])[-RECENT_TURNS:]
            self.emit("say" if len(answer) <= SPOKEN_LIMIT or self.game else "report", answer, quiet)
        except Exception as error:
            self.emit("error", str(error), quiet)
        finally:
            self.follow_up_until = time.monotonic() + FOLLOW_UP_SECONDS
            self.busy = False

    def _current_game(self):
        if self.game and time.monotonic() - self.game.last_turn > GAME_IDLE_SECONDS:
            self.game = None
        return self.game

    def game_status(self) -> dict | None:
        game = self._current_game()
        return game.status() if game else None

    def _start_game(self, key: str, speaking: str) -> str:
        if games.GAMES[key].needs_ai and not config.load()["agent_enabled"]:
            return "That game needs the local AI, and it's turned off in the settings. Hangman works without it."
        model = config.load()["ai_model"]
        if key == "draw" and not ai.can_see(model):
            return f"Draw and Guess needs a model that can see pictures, and {model} can't. You can change the model in Settings."
        game = games.GAMES[key](self._source, speaking)
        self.game = game
        answer = self._play(lambda game: game.begin())
        game.started = True
        if self.game is game and self._source == "whatsapp":  # there are no buttons here to show the way out
            return f"{answer}\n\nSend quit to stop playing."
        return answer

    def _play(self, turn) -> str:
        """One turn. The model failing costs that turn and not the game, unless it isn't there at all."""
        game = self.game
        try:
            answer = turn(game)
        except ai.NotReady as error:
            self.game = None
            return str(error)
        except (RuntimeError, ValueError, OSError) as error:
            print(f"A game's turn failed: {type(error).__name__}: {str(error)[:200]}", flush=True)
            if not game.started:
                self.game = None
                return "Sorry, my brain slipped on a banana skin. Please ask me to play again."
            return games.SLIPPED
        if self._stop.is_set():
            return "Stopped"
        game.last_turn = time.monotonic()
        if game.mood:
            self.emit("mood", game.mood)
            game.mood = ""
        if game.over:
            self.game = None
            self.last_game, self.last_game_at = game.key, time.monotonic()
        return answer

    def _game_turn(self, text: str) -> str:
        """Everything said in a game belongs to it, so a story about opening a door can't open anything."""
        game = self.game
        if games.wants_quit(text):
            self.game = None
            self.last_game, self.last_game_at = game.key, time.monotonic()
            self.emit("mood", "sulk")
            return "Okay, game over. That was fun!"
        if (written := language.written_in(text)) != "english":  # the buttons send English, which shouldn't undo this
            game.language = written
        return self._play(lambda game: game.turn(text))

    def _command_in_game(self, text: str) -> bool:
        """Addressing Buddy by name steps outside the game: "Buddy, next song". The game carries on afterwards."""
        try:
            return bool(WAKE.match(text)) and bool(games.gag_for(text) or parse(text, Context()))
        except actions.UnknownTarget:
            return False

    def _about_games(self, text: str, speaking: str) -> str | None:
        """The answer, if this is a request for a game or for something silly."""
        if gag := games.gag_for(text):
            self.emit("mood", gag[0])
            return "Watch the laptop's screen!" if self._source == "whatsapp" else gag[1]
        if key := games.wanted(text):
            return games.menu(self._source) if key == "menu" else self._start_game(key, speaking)
        if games.is_ending(text):
            return "We aren't playing anything right now"
        if self.last_game and time.monotonic() - self.last_game_at < games.AGAIN_SECONDS and games.wants_again(text):
            return self._start_game(self.last_game, speaking)
        return None

    def _handle(self, text: str) -> str:
        game = self._current_game()
        # A yes for something else that's waiting is handled below, and not taken as an answer in the game.
        if game and game.source == self._source and self.pending is None and self.resume is None and not self._command_in_game(text):
            return self._game_turn(text)

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

        if (answer := self._about_games(text, language.written_in(text))) is not None:
            return answer

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
        file = self._fresh_attachment()
        english, kind = language.understand(text, file.name if file else "")
        if (links := LINK.findall(text)) and not LINK.search(english):  # the model sometimes writes "this link" instead
            english = f"{english} {links[0]}"
        if english != text and (answer := self._about_games(english, language.written_in(text))) is not None:
            return answer
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
        latest = {"role": "user", "content": text}
        system = language.chat_prompt(text)
        if file := self._fresh_attachment():
            system += language.file_context(file)
            if file.pictures:
                latest["images"] = [base64.b64encode(picture).decode() for picture in file.pictures]
        messages.append(latest)
        try:
            answer = language.tidy(ai.talk(config.load()["ai_model"], system, messages)).strip()
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
        if self._source == "whatsapp":
            steps = [{**step, "send": True} if step["action"] in actions.ANSWER_ON_WHATSAPP else step for step in steps]
            steps = [{**step, "send": False} if step["action"] == "share_location" else step for step in steps]
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
