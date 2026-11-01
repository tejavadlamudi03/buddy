"""WhatsApp Web, in a Chrome profile kept apart from the main one.

Sends images, and with whatsapp_control on, takes commands from exactly one number.
"""
import base64
import io
import mimetypes
import queue
import re
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path

import numpy as np
from faster_whisper.audio import decode_audio
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from . import apps, attachments, config, fetch, language, share

COMPOSER = 'footer div[contenteditable="true"]'
QR_CODE = 'canvas[aria-label*="scan" i], div[data-ref]'
# By label's start: the preview's button reads "Send 1 selected".
SEND_BUTTON = '[data-icon*="send"], [aria-label^="Send" i], [aria-label^="Senden" i]'
MESSAGES = '#main [data-testid="msg-container"]'
PREVIEW_SECONDS = 20
# True while something, like the file preview, sits on top of the message box.
COMPOSER_COVERED = """
() => {
  const box = document.querySelector('footer div[contenteditable="true"]');
  if (!box) return true;
  const r = box.getBoundingClientRect();
  if (!r.width) return true;
  const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return !(top && (top === box || box.contains(top) || top.contains(box)));
}
"""
ICONS_ON_PAGE = "() => [...new Set([...document.querySelectorAll('[data-icon]')].map(e => e.dataset.icon))].slice(-25)"
PROFILE = "whatsapp-profile"
CHAT_URL = "https://web.whatsapp.com/send?phone={phone}"
WINDOW_CLASS = "BuddyWhatsApp"
# WhatsApp Web turns away browsers that identify themselves as headless.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
POLL_MS = 1500
MEDIA_SECONDS = 40
RECLICK_SECONDS = 6
MAX_COMMAND_CHARS = 12000  # a long text to review; about what fits in the model's context
MAX_AGE_MINUTES = 3
MAX_MESSAGE_CHARS = 3500
VOICE_NOTE_SECONDS = 30
PLAY_ATTEMPTS = 3
MAX_NO_SPEECH = 0.6
MIN_CONFIDENCE = -1.2
# Below these a voice note isn't acted on until the owner has seen what was heard and said yes.
CLEAR_NO_SPEECH = 0.3
CLEAR_CONFIDENCE = -0.5
CONFIRM_VOICE_SECONDS = 120
_YES = re.compile(rf"(?:yes|yeah|yep|y|ok|okay|correct|do it|{language.TELUGU_YES})[.!\s]*", re.I)
SETTLE_SECONDS = 5
_STAMP = re.compile(r"\[\s*(\d{1,2})[:.](\d{2})(?:[:.]\d{2})?\s*([ap])?\.?\s*m?\.?", re.I)
SCREENSHOT_REQUEST = re.compile(r"(?:(?:send|show)(?: me)? )?(?:a |the |my )?(?:screen ?shot|screen)(?: please)?[.!?]*", re.I)

# Each message in the chat on show. WhatsApp no longer marks sender or direction in message ids,
# so direction comes from layout: incoming bubbles sit left, outgoing ones right and carry tail-out.
# A row can hold several consecutive messages, so messages are told apart by stamp and text.
# Only the start of the text, so a long message keeps its key if WhatsApp folds it up again.
READ_ROWS = """
() => {
  const main = document.querySelector('#main');
  if (!main) return [];
  const box = main.getBoundingClientRect();
  const counts = {}, out = [];
  const onLeft = e => { const r = e.getBoundingClientRect(); return r.width > 0 && (r.left - box.left) < (box.right - r.right); };
  for (const row of main.querySelectorAll('[data-id]')) {
    const tailOut = !!row.querySelector('[data-icon="tail-out"]');
    const stamps = [...row.querySelectorAll('[data-pre-plain-text]')];
    const image = !!row.querySelector('[data-testid="image-thumb"]');
    const documentThumb = row.querySelector('[data-testid="document-thumb"]');
    const voice = !!row.querySelector('[role="slider"], input[type="range"]');
    const bubble = row.querySelector('[data-testid="msg-container"]') || row;
    if (image || documentThumb || voice || !stamps.length) {
      // Only the first message in a run has a tail, so direction comes from which side it sits on.
      if (!tailOut && onLeft(bubble)) {
        const view = [...row.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label')).find(l => /^(view|anzeigen)\b/i.test(l)) || '';
        out.push({
          key: 'media:' + row.dataset.id, kind: 'media', id: row.dataset.id, voice, image, document: !!documentThumb,
          name: (view.match(/"(.+)"/) || [])[1] || '',
          caption: stamps.map(e => (e.querySelector('.selectable-text') || {}).innerText || '').join(' ').trim(),
          meta: (row.querySelector('[data-testid="msg-meta"]') || {}).innerText || '',
        });
      }
      continue;
    }
    for (const element of stamps) {
      const r = element.getBoundingClientRect();
      const onLeft = r.width > 0 && (r.left - box.left) < (box.right - r.right);
      const stamp = element.dataset.prePlainText || '';
      const text = (element.querySelector('.selectable-text') || {}).innerText || '';
      const base = stamp + '|' + text.slice(0, 80);
      counts[base] = (counts[base] || 0) + 1;
      out.push({key: base + '|' + counts[base], kind: onLeft && !tailOut ? 'in' : 'out', text, stamp});
    }
  }
  return out;
}
"""
# WhatsApp folds long messages behind "Read more", and folded text would be all that gets read.
UNFOLD_LONG = """
() => {
  const folded = [...document.querySelectorAll('#main [data-id] [role="button"], #main [data-id] button')]
    .filter(e => /^(read more|mehr lesen|mehr anzeigen)\\W*$/i.test((e.innerText || '').trim()));
  folded.forEach(e => e.click());
  return folded.length;
}
"""
# The open chat's name. Message ids no longer name the sender, so staying in this chat is what
# guarantees a message came from the owner: it was opened by number, and it's the only one read.
CHAT_TITLE = """
() => (document.querySelector('#main header span[dir="auto"]') || {}).innerText || ''
"""

# Pasting skips the attach menu, whose markup changes far more often than the composer's.
PASTE_FILE = """
([selector, base64, name, type]) => {
  const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
  const data = new DataTransfer();
  data.items.add(new File([bytes], name, {type}));
  const target = document.querySelector(selector);
  target.focus();
  target.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
}
"""


def _open_context(playwright, *args: str, visible: bool = False):
    """No window unless one is needed for scanning the QR code, so it's never in the way."""
    profile = config.DATA_DIR / PROFILE
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    size = {"no_viewport": True} if visible else {"viewport": {"width": 1280, "height": 900}, "user_agent": USER_AGENT}
    return playwright.chromium.launch_persistent_context(
        profile, channel="chrome", headless=not visible, env=apps.session_env(),
        # desktop.py hides this class from window commands. The basic password store means a
        # locked keyring can't stop this with a prompt nobody is there to answer.
        args=[f"--class={WINDOW_CLASS}", "--password-store=basic", *args], **size,
    )


# WhatsApp decrypts a voice note when it's played. These keep a reference to whatever it plays.
CATCH_AUDIO = """
window.__buddyAudio = []; window.__buddyPlays = []; window.__buddyBlobTypes = [];
const createObjectURL = URL.createObjectURL;
URL.createObjectURL = function (blob) {
  if (blob && blob.type !== undefined) {
    window.__buddyBlobTypes.push(blob.type + ':' + blob.size);
    if (blob.type.startsWith('audio')) window.__buddyAudio.push(blob);
  }
  return createObjectURL.call(URL, blob);
};
const play = HTMLMediaElement.prototype.play;
HTMLMediaElement.prototype.play = function () {
  window.__buddyPlays.push(this);
  return play.apply(this, arguments);
};
"""
AUDIO_SEEN = "[window.__buddyAudio.length, window.__buddyPlays.length]"
# The play button is the last button before the voice note's slider.
PLAY_BUTTON = """
id => {
  const row = [...document.querySelectorAll('#main [data-id]')].find(r => r.dataset.id === id);
  const slider = row && row.querySelector('[role="slider"], input[type="range"]');
  if (!slider) return null;
  return [...row.querySelectorAll('button, [role="button"]')]
    .filter(b => b.compareDocumentPosition(slider) & Node.DOCUMENT_POSITION_FOLLOWING).pop() || null;
}
"""
# Only audio that appeared after the press counts, or an older note could be transcribed instead.
# Only decrypted blobs count too: WhatsApp's own notification sound also plays, from an ordinary URL.
READ_NEW_AUDIO = """
async ([blobsBefore, playsBefore]) => {
  let blob = null, source = '';
  const plays = window.__buddyPlays.slice(playsBefore).reverse();
  const skipped = plays.filter(p => !(p.currentSrc || p.src || '').startsWith('blob:')).length;
  for (const player of plays) {
    const url = player.currentSrc || player.src || '';
    if (!url.startsWith('blob:')) continue;
    const found = await (await fetch(url)).blob();
    if (found.type.startsWith('audio') || found.type === '') { blob = found; source = 'player'; break; }
  }
  if (!blob && window.__buddyAudio.length > blobsBefore) {
    blob = window.__buddyAudio[window.__buddyAudio.length - 1];
    source = 'created';
  }
  if (!blob) return null;
  const data = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
  return {data, type: blob.type, size: blob.size, source, skipped};
}
"""
NOTE_TIMES = """
id => {
  const row = [...document.querySelectorAll('#main [data-id]')].find(r => r.dataset.id === id);
  return ((row && row.innerText) || '').match(/\\b\\d{1,2}:\\d{2}\\b/g) || [];
}
"""
AUDIO_DIAGNOSIS = """
() => ({blobs: window.__buddyBlobTypes.slice(-6), plays: window.__buddyPlays.length,
        audio_tags: document.querySelectorAll('audio').length})
"""

# The largest decrypted picture, in a message or, once one is opened, in the viewer on top of the chat.
READ_PICTURE = """
async id => {
  const row = id && [...document.querySelectorAll('#main [data-id]')].find(r => r.dataset.id === id);
  const pictures = [...(row || document).querySelectorAll('img[src^="blob:"]')].filter(i => i.naturalWidth);
  const largest = pictures.sort((a, b) => b.naturalWidth - a.naturalWidth)[0];
  if (!largest) return null;
  const blob = await (await fetch(largest.src)).blob();
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}
"""
# Each row's way in: the picture or the document itself.
OPEN_MEDIA = """
id => {
  const row = [...document.querySelectorAll('#main [data-id]')].find(r => r.dataset.id === id);
  if (!row) return false;
  const inside = row.querySelector('[aria-label^="View"], [aria-label^="Open"], [aria-label^="Anzeigen"], [aria-label^="Öffnen"]')
    || row.querySelector('[data-testid="document-thumb"], [data-testid="image-thumb"]');
  if (!inside) return false;
  (inside.closest('[role="button"], button') || inside).click();
  return true;
}
"""
VIEWER_DOWNLOAD = '[aria-label="Download"], [title="Download"], [aria-label="Herunterladen"], [title="Herunterladen"]'

# Page structure only, for diagnosing WhatsApp layout changes. Never message text, names or numbers.
PAGE_SHAPE = """
() => {
  const mask = v => (v || '').replace(/[0-9]/g, '9').replace(/[A-Za-z]{5,}/g, 'x');
  const main = document.querySelector('#main');
  const count = (root, selector) => root ? root.querySelectorAll(selector).length : 0;
  const ids = [...document.querySelectorAll('#main [data-id]')].slice(-4).map(e => mask(e.dataset.id).slice(0, 60));
  const stamps = [...document.querySelectorAll('#main [data-pre-plain-text]')].slice(-2)
    .map(e => mask((e.dataset.prePlainText || '').split(']')[0]));
  return {
    main: !!main, footer: !!document.querySelector('footer'),
    composer: count(document, 'footer div[contenteditable="true"]'),
    rows_data_id: count(main, '[data-id]'), rows_role_row: count(main, '[role="row"]'),
    message_in: count(main, '.message-in'), message_out: count(main, '.message-out'),
    stamps: count(main, '[data-pre-plain-text]'), selectable: count(main, '.selectable-text'),
    copyable: count(main, '.copyable-text'), ids, stamp_shapes: stamps,
    width: main ? Math.round(main.getBoundingClientRect().width) : 0,
    media_rows: main ? [...main.querySelectorAll('[data-id]')]
      .filter(row => row.querySelector('[data-icon="tail-in"]') && !row.querySelector('[data-pre-plain-text]'))
      .slice(-3).map(row => ({
        tags: [...new Set([...row.querySelectorAll('*')].map(e => e.tagName.toLowerCase()))].slice(0, 20),
        buttons: row.querySelectorAll('button, [role="button"]').length,
        sliders: row.querySelectorAll('[role="slider"], input[type="range"]').length,
        testids: [...new Set([...row.querySelectorAll('[data-testid]')].map(e => e.dataset.testid))].slice(0, 15),
      })) : [],
  };
}
"""
ANNOUNCE_EVERY_SECONDS = 6 * 3600


def _linked_marker() -> Path:
    return config.DATA_DIR / "whatsapp-linked"


def age_in_minutes(stamp: str, now: datetime) -> int | None:
    """Age from a stamp like '[16:32, 31/10/2026] Name: '. None when it can't be read."""
    match = _STAMP.match(stamp or "")
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    meridiem = (match.group(3) or "").lower()
    if meridiem:
        hour = hour % 12 + (12 if meridiem == "p" else 0)
    if hour > 23 or minute > 59:
        return None
    return (now.hour * 60 + now.minute - hour * 60 - minute) % (24 * 60)  # modulo handles midnight


def commands_from(rows: list, seen: set, now: datetime | None = None,
                  unreadable: list | None = None, media: list | None = None) -> list[str]:
    """New, recent incoming messages. Anything uncertain is dropped, never run.

    The age check matters because WhatsApp syncs late: without it, a message sent while the
    laptop was asleep would run hours later, when nobody is expecting it.
    """
    commands = []
    now = now or datetime.now()
    for row in rows:
        key = row.get("key") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        if row.get("kind") == "media":
            if media is not None:
                media.append(row)
            continue
        text = (row.get("text") or "").strip()
        if row.get("kind") != "in" or not text:
            continue
        age = age_in_minutes(row.get("stamp") or "", now)
        if age is None and unreadable is not None:
            unreadable.append(text)
        elif age is not None and age <= MAX_AGE_MINUTES:
            commands.append(text[:MAX_COMMAND_CHARS])
    return commands


def _audio_levels(audio: bytes) -> str:
    """Loudness only, for telling silence or a cut-off clip from a recognition problem."""
    try:
        samples = decode_audio(io.BytesIO(audio))
        frames = samples[: len(samples) // 320 * 320].reshape(-1, 320)
        loudness = np.sqrt((frames ** 2).mean(axis=1))
        return (f"decoded {len(samples) / 16000:.1f}s, peak {abs(samples).max():.3f}, rms {np.sqrt((samples ** 2).mean()):.4f}, "
                f"{100 * (loudness > 0.01).mean():.0f}% with sound")
    except Exception as error:
        return f"couldn't decode: {type(error).__name__}"


class Bridge:
    """Keeps the owner's chat open. All browser work stays on this one thread, as Playwright requires."""

    def __init__(self, engine, transcriber=None):
        self.engine = engine
        self.transcriber = transcriber
        self.ready = threading.Event()
        self.phone = ""
        self._tasks = queue.Queue()
        self._reply_from = None
        self._last_link = None
        self._unsure = None  # (text, heard_at) for a voice note waiting for a yes
        self.heard_clearly = True
        threading.Thread(target=self._run, daemon=True).start()

    def on_page(self, work) -> None:
        """Runs work(page) on the browser thread and waits for it."""
        done = Future()
        self._tasks.put((work, done))
        done.result(timeout=180)

    def _run(self) -> None:
        while True:
            settings = config.load()
            if settings["whatsapp_control"] and settings["whatsapp_phone"] and _linked_marker().exists():
                try:
                    self._session(settings["whatsapp_phone"])
                except Exception as error:
                    print(f"WhatsApp bridge stopped: {error}", flush=True)
                    self.engine.emit("log", "WhatsApp commands paused, retrying shortly")
                    time.sleep(20)
                finally:
                    self.ready.clear()
            time.sleep(5)

    def _still_wanted(self) -> bool:
        settings = config.load()
        return settings["whatsapp_control"] and settings["whatsapp_phone"] == self.phone

    def _session(self, phone: str) -> None:
        self.phone = phone
        with sync_playwright() as playwright:
            # Muted, because hearing a voice note means playing it.
            context = _open_context(playwright, "--mute-audio")
            context.add_init_script(CATCH_AUDIO)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                if not self._open_chat(page):
                    return
                seen = self._settled_history(page)
                title = page.evaluate(CHAT_TITLE)
                self._last_link = _share_link()
                self.ready.set()
                self.engine.emit("log", "WhatsApp commands are on")
                print(f"WhatsApp page shape: {page.evaluate(PAGE_SHAPE)}", flush=True)
                kinds = [row["kind"] for row in page.evaluate(READ_ROWS)]
                print(f"WhatsApp messages read: {kinds.count('in')} in, {kinds.count('out')} out, "
                      f"{kinds.count('media')} media", flush=True)
                if _should_announce():
                    _send_text(page, "Buddy is online. Send me a command.")
                while self._still_wanted():
                    self._do_tasks(page)
                    if page.evaluate(CHAT_TITLE) != title:
                        raise RuntimeError("the chat on show changed, so I stopped reading it")
                    unreadable, media = [], []
                    for text in commands_from(_read_rows(page), seen, unreadable=unreadable, media=media):
                        if len(text) >= MAX_COMMAND_CHARS:
                            _send_text(page, f"That's very long, so I only read the first {MAX_COMMAND_CHARS} characters.")
                        self._handle(self._confirmed(text))
                    if unreadable:
                        _send_text(page, "I couldn't read the time on your last message, so I didn't run it.")
                    for row in media:
                        self._handle_media(page, row)
                    self._send_replies(page)
                    page.wait_for_timeout(POLL_MS)
            finally:
                context.close()

    def _settled_history(self, page) -> set:
        """Every message already in the chat. History loads gradually, so wait until it stops growing."""
        seen, quiet_since = set(), time.monotonic()
        while time.monotonic() - quiet_since < SETTLE_SECONDS:
            current = {row["key"] for row in _read_rows(page)}
            if current - seen:
                seen |= current
                quiet_since = time.monotonic()
            page.wait_for_timeout(500)
        return seen

    def _open_chat(self, page) -> bool:
        page.goto(CHAT_URL.format(phone=self.phone), wait_until="domcontentloaded")
        page.locator(f"{COMPOSER}, {QR_CODE}").first.wait_for(state="visible", timeout=90_000)
        if page.locator(COMPOSER).count():
            return True
        _linked_marker().unlink(missing_ok=True)
        self.engine.emit("say", "WhatsApp was unlinked. Run buddy whatsapp-login to link it again")
        return False

    def _press_play(self, page, row_id: str) -> None:
        """Finds the button afresh each try, because WhatsApp redraws it whenever its state changes."""
        for attempt in range(PLAY_ATTEMPTS):
            button = page.evaluate_handle(PLAY_BUTTON, row_id).as_element()
            if button is None:
                raise RuntimeError("couldn't find the voice note's play button")
            try:
                button.click(timeout=5_000)
                return
            except PlaywrightError:
                if attempt == PLAY_ATTEMPTS - 1:
                    raise
                page.wait_for_timeout(400)

    def _transcribe(self, page, row_id: str) -> str:
        before = page.evaluate(AUDIO_SEEN)
        labels = page.evaluate(NOTE_TIMES, row_id)  # read before playing, while it still shows the length
        self._press_play(page, row_id)
        # Polled from here: wait_for_function evaluates a string in the page, which WhatsApp's CSP blocks.
        deadline = time.monotonic() + VOICE_NOTE_SECONDS
        found = None
        while found is None:
            if page.evaluate(AUDIO_SEEN) != before:
                found = page.evaluate(READ_NEW_AUDIO, before)
            if found is None:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"the voice note didn't load in time: {page.evaluate(AUDIO_DIAGNOSIS)}")
                page.wait_for_timeout(200)
        audio = base64.b64decode(found["data"])
        (config.DATA_DIR / "last-voice-note.ogg").write_bytes(audio)  # for checking by ear what Whisper received
        print(f"Voice note audio: {found['type']}, {found['size']} bytes, from {found['source']}, "
              f"times on the note {labels}, {_audio_levels(audio)}", flush=True)
        try:
            self._press_play(page, row_id)  # stops it; the window is muted, so this can't be allowed to fail the job
        except PlaywrightError:
            pass
        text, no_speech, confidence = self.transcriber.transcribe_with_confidence(audio)
        print(f"Voice note result: {len(text)} characters, no-speech {no_speech:.2f}, confidence {confidence:.2f}", flush=True)
        if no_speech > MAX_NO_SPEECH or confidence < MIN_CONFIDENCE:  # Whisper invents polite phrases for silence
            return ""
        self.heard_clearly = no_speech <= CLEAR_NO_SPEECH and confidence >= CLEAR_CONFIDENCE
        return text

    def _read_file(self, page, row: dict) -> None:
        age = age_in_minutes("[" + (row.get("meta") or "").strip(), datetime.now())
        if age is None or age > MAX_AGE_MINUTES:  # same rule as text: late arrivals never run
            return
        try:
            path = fetch_picture(page, row["id"]) if row.get("image") else fetch_document(page, row["id"], row.get("name") or "")
            attachment = attachments.read(path)
        except ValueError as error:
            _send_text(page, str(error))
            return
        except Exception as error:
            print(f"Reading a file failed: {type(error).__name__}: {str(error)[:200]}", flush=True)
            _send_text(page, "I couldn't open that file. Please send it again, or paste the text.")
            return
        if self._reply_from is None:
            self._reply_from = len(self.engine.events)
        self.engine.submit_file(row.get("caption") or "", attachment)

    def _handle_media(self, page, row: dict) -> None:
        if row.get("image") or row.get("document"):
            self._read_file(page, row)
            return
        if not row.get("voice") or self.transcriber is None:
            _send_text(page, "I can read text, voice notes, pictures and documents, but not that. Please send it as one of those.")
            return
        age = age_in_minutes("[" + (row.get("meta") or "").strip(), datetime.now())
        if age is None or age > MAX_AGE_MINUTES:  # same rule as text: late arrivals never run
            return
        try:
            text = self._transcribe(page, row["id"])
        except Exception as error:
            print(f"Voice note failed: {type(error).__name__}: {str(error)[:200]}", flush=True)
            _send_text(page, "I couldn't play that voice note. Please type it instead.")
            return
        if not text:
            _send_text(page, "I couldn't hear anything in that voice note.")
            return
        if not self.heard_clearly:
            self._unsure = (text, time.monotonic())
            _send_text(page, f'I\'m not sure I heard that right. I heard: "{text}". Reply yes to do it, or send it again.')
            return
        _send_text(page, f'I heard: "{text}"')  # so a misheard word is visible right away
        self._handle(text)

    def _do_tasks(self, page) -> None:
        while not self._tasks.empty():
            work, done = self._tasks.get_nowait()
            try:
                done.set_result(work(page))
            except Exception as error:
                done.set_exception(error)

    def _confirmed(self, text: str) -> str:
        """A yes to an unsure voice note runs what was heard. Anything else drops that note."""
        unsure, self._unsure = self._unsure, None
        if unsure and _YES.fullmatch(text) and time.monotonic() - unsure[1] < CONFIRM_VOICE_SECONDS:
            return unsure[0]
        return text

    def _handle(self, text: str) -> None:
        if self._reply_from is None:
            self._reply_from = len(self.engine.events)
        self.engine.emit("log", "Command from WhatsApp")
        command = "send me a screenshot on whatsapp" if SCREENSHOT_REQUEST.fullmatch(text) else text
        self.engine.submit(command, source="whatsapp")

    def _send_replies(self, page) -> None:
        if self._reply_from is None or self.engine.busy:
            return
        events = self.engine.events_after(self._reply_from)
        self._reply_from = None
        for event in events:
            if event["kind"] in ("say", "error", "report"):
                _send_text(page, event["text"])
        link = _share_link()
        if link and link != self._last_link:
            _send_text(page, f"Open your live screen here: {link}")
        self._last_link = link


def _close_viewer(page) -> None:
    """Escape only with something open: with nothing open, it closes the chat itself."""
    for _ in range(2):
        if not page.evaluate(COMPOSER_COVERED):
            return
        page.keyboard.press("Escape")
        _wait_for(page, COMPOSER_COVERED, False, 4)  # the document viewer takes a moment to go


def fetch_picture(page: Page, row_id: str) -> Path:
    """Saved in Downloads, so "convert it" and "send me the latest download" work on it afterwards."""
    def read(scope):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if found := page.evaluate(READ_PICTURE, scope):
                return found
            page.wait_for_timeout(300)
        return None

    data = read(row_id)
    if data is None:  # not fetched yet, as in a chat that hasn't been opened since it arrived
        page.evaluate(OPEN_MEDIA, row_id)
        data = read(None)
        _close_viewer(page)
    if data is None:
        raise RuntimeError("the picture didn't load")
    return fetch.save_received(f"whatsapp-photo-{datetime.now():%Y%m%d-%H%M%S}.jpg", base64.b64decode(data))


def fetch_document(page: Page, row_id: str, name: str) -> Path:
    arrived = []

    def on_download(download):
        arrived.append(download)

    page.on("download", on_download)
    try:
        if not page.evaluate(OPEN_MEDIA, row_id):
            raise RuntimeError("couldn't find the document in the chat")
        deadline = time.monotonic() + MEDIA_SECONDS
        pressed = False
        click_again = time.monotonic() + RECLICK_SECONDS
        while not arrived and time.monotonic() < deadline:
            page.wait_for_timeout(300)
            button = page.locator(VIEWER_DOWNLOAD).first  # some types open in a viewer instead of downloading
            if not pressed and button.count() and button.is_visible():
                button.click(timeout=3_000)
                pressed = True
            elif not pressed and time.monotonic() > click_again:  # WhatsApp sometimes ignores the first click
                page.evaluate(OPEN_MEDIA, row_id)
                click_again = time.monotonic() + RECLICK_SECONDS
        if not arrived:
            raise RuntimeError("the document didn't download in time")
        download = arrived[0]
        return fetch.save_received(name or download.suggested_filename, Path(download.path()).read_bytes())
    finally:
        page.remove_listener("download", on_download)
        _close_viewer(page)


_bridge: Bridge | None = None


def start_bridge(engine, transcriber=None) -> None:
    global _bridge
    _bridge = Bridge(engine, transcriber)


def _should_announce() -> bool:
    """Only after a real absence, so restarts don't fill the chat."""
    marker = config.DATA_DIR / "whatsapp-announced"
    recent = marker.exists() and time.time() - marker.stat().st_mtime < ANNOUNCE_EVERY_SECONDS
    marker.touch()
    return not recent


def _share_link() -> str | None:
    return share.current.status().get("url") if share.current else None


def _read_rows(page) -> list:
    if page.evaluate(UNFOLD_LONG):
        page.wait_for_timeout(300)
    return page.evaluate(READ_ROWS)


def _message_parts(text: str) -> list[str]:
    """Pieces WhatsApp will accept, broken between lines where possible so code stays readable."""
    parts = []
    while len(text) > MAX_MESSAGE_CHARS:
        cut = text.rfind("\n", 0, MAX_MESSAGE_CHARS)
        cut = cut if cut > 0 else MAX_MESSAGE_CHARS
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts + [text] if text else parts


def _send_text(page: Page, text: str) -> None:
    for part in _message_parts(text):
        page.locator(COMPOSER).first.click()
        for index, line in enumerate(part.split("\n")):
            if index:
                page.keyboard.press("Shift+Enter")  # plain Enter would send the message here
            if line:
                page.keyboard.insert_text(line)
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)


def send_text(phone: str, text: str) -> None:
    if _bridge and _bridge.ready.is_set() and _bridge.phone == phone:
        _bridge.on_page(lambda page: _send_text(page, text))
        return
    with sync_playwright() as playwright:
        context = _open_context(playwright)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(CHAT_URL.format(phone=phone), wait_until="domcontentloaded")
            page.locator(COMPOSER).first.wait_for(state="visible", timeout=60_000)
            _send_text(page, text)
            page.wait_for_timeout(2_000)
        finally:
            context.close()


def attach_and_send(page: Page, image: Path) -> None:
    encoded = base64.b64encode(image.read_bytes()).decode()
    kind = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
    page.evaluate(PASTE_FILE, [COMPOSER, encoded, image.name, kind])
    before = page.locator(MESSAGES).count()
    if not _wait_for(page, COMPOSER_COVERED, True, PREVIEW_SECONDS):
        raise RuntimeError("WhatsApp didn't open that file for sending")
    page.wait_for_timeout(800)  # the preview's own controls settle just after it appears
    print(f"WhatsApp preview icons: {page.evaluate(ICONS_ON_PAGE)}", flush=True)
    send = page.locator(SEND_BUTTON).last
    if send.count() and send.is_visible():
        send.click(timeout=5_000)
    else:
        page.keyboard.press("Enter")  # sends from the preview, whatever its button is called
    if not _wait_for(page, COMPOSER_COVERED, False, PREVIEW_SECONDS):
        page.keyboard.press("Escape")  # a stuck preview would otherwise block every later message
        page.keyboard.press("Escape")
        raise RuntimeError("WhatsApp didn't accept that file")
    deadline = time.monotonic() + PREVIEW_SECONDS
    while page.locator(MESSAGES).count() <= before and not _newest_is_mine(page):
        if time.monotonic() > deadline:
            raise RuntimeError("I couldn't confirm that the file was sent")
        page.wait_for_timeout(300)
    page.wait_for_timeout(3_000)  # time for the upload to leave


def _wait_for(page: Page, script: str, wanted, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if page.evaluate(script) == wanted:
            return True
        page.wait_for_timeout(200)
    return False


def _newest_is_mine(page: Page) -> bool:
    return page.evaluate("""() => {
      const rows = [...document.querySelectorAll('#main [data-id]')];
      return !!(rows.length && rows[rows.length - 1].querySelector('[data-icon="tail-out"]'));
    }""")


def send_image(phone: str, image: Path) -> None:
    if _bridge and _bridge.ready.is_set() and _bridge.phone == phone:  # it already holds the profile
        _bridge.on_page(lambda page: attach_and_send(page, image))
        return
    with sync_playwright() as playwright:
        context = _open_context(playwright)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(f"https://web.whatsapp.com/send?phone={phone}", wait_until="domcontentloaded")
            ready = page.locator(f"{COMPOSER}, {QR_CODE}").first
            try:
                ready.wait_for(state="visible", timeout=60_000)
            except PlaywrightTimeout:
                raise RuntimeError("WhatsApp Web didn't load in time") from None
            if page.locator(QR_CODE).count() and not page.locator(COMPOSER).count():
                raise RuntimeError("WhatsApp isn't linked yet. Run: buddy whatsapp-login")
            attach_and_send(page, image)
        finally:
            context.close()


def login() -> None:
    """Shows WhatsApp Web until the phone is linked; the session stays in Buddy's profile."""
    if _linked_marker().exists():
        print("WhatsApp is already linked.")
        return
    with sync_playwright() as playwright:
        context = _open_context(playwright, visible=True)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
        print("Scan the QR code: WhatsApp on your phone > Linked devices > Link a device.")
        print("Waiting for up to 3 minutes...")
        page.locator("#side, #pane-side").first.wait_for(state="visible", timeout=180_000)
        page.wait_for_timeout(5_000)
        context.close()
        _linked_marker().touch()
        print("WhatsApp is linked.")
