from datetime import datetime

from buddy.whatsapp import SCREENSHOT_REQUEST, age_in_minutes, commands_from

NOW = datetime(2026, 10, 31, 16, 32)
STAMP = "[16:31, 31/10/2026] Me: "


def row(key="a", text="open chrome", kind="in", stamp=STAMP):
    return {"key": key, "text": text, "kind": kind, "stamp": stamp}


def test_incoming_message_becomes_a_command_once():
    seen = set()
    assert commands_from([row()], seen, NOW) == ["open chrome"]
    assert commands_from([row()], seen, NOW) == []


def test_a_new_message_in_the_same_group_is_still_seen():
    seen = set()
    commands_from([row("1", "first")], seen, NOW)
    assert commands_from([row("1", "first"), row("2", "second")], seen, NOW) == ["second"]


def test_buddys_own_replies_are_never_read_as_commands():
    assert commands_from([row(kind="out"), row("b", kind=None)], set(), NOW) == []


def test_messages_from_before_startup_stay_ignored():
    assert commands_from([row("old")], {"old"}, NOW) == []


def test_empty_text_and_missing_keys_are_dropped():
    assert commands_from([row(key=""), row("b", text="   ")], set(), NOW) == []


def test_long_messages_are_cut():
    from buddy.whatsapp import MAX_COMMAND_CHARS
    assert len(commands_from([row(text="x" * 50000)], set(), NOW)[0]) == MAX_COMMAND_CHARS


def test_long_replies_break_between_lines():
    from buddy.whatsapp import MAX_MESSAGE_CHARS, _message_parts
    code = "\n".join(f"line {number}" for number in range(2000))
    parts = _message_parts(code)
    assert len(parts) > 1 and all(len(part) <= MAX_MESSAGE_CHARS for part in parts)
    assert "\n".join(parts) == code
    assert _message_parts("x" * (MAX_MESSAGE_CHARS + 1)) == ["x" * MAX_MESSAGE_CHARS, "x"]
    assert _message_parts("") == []


def test_lines_in_a_reply_never_send_early(monkeypatch):
    from buddy import whatsapp
    pressed = []

    class Keyboard:
        def press(self, key): pressed.append(key)
        def insert_text(self, text): pressed.append(text)

    class Box:
        first = property(lambda self: self)
        def click(self): pass

    class Page:
        keyboard = Keyboard()
        def locator(self, selector): return Box()
        def wait_for_timeout(self, ms): pass

    whatsapp._send_text(Page(), "def f():\n\n    pass")
    assert pressed == ["def f():", "Shift+Enter", "Shift+Enter", "    pass", "Enter"]


def test_a_long_message_is_read_whole_and_only_once():
    """Runs the page scripts in a real browser, as unfolding changes the text the message is read as."""
    import pytest
    pytest.importorskip("playwright")
    from playwright.sync_api import Error, sync_playwright

    from buddy import whatsapp
    html = """<div id="main"><div data-id="1"><span data-icon="tail-in"></span>
      <div data-pre-plain-text="[16:31, 31/10/2026] Me: "><span class="selectable-text" id="text">START</span>
      <div role="button" id="more" onclick="document.getElementById('text').innerText = 'START' + ' whole'.repeat(50);
        this.remove()">Read more</div></div></div></div>"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            page.set_content(html)
            folded_key = page.evaluate(whatsapp.READ_ROWS)[0]["key"]
            rows = whatsapp._read_rows(page)
            assert rows[0]["text"].endswith("whole") and len(rows[0]["text"]) > 100
            assert page.evaluate(whatsapp.UNFOLD_LONG) == 0
            page.evaluate("() => { document.getElementById('text').innerText = 'x'.repeat(200); }")
            first, again = page.evaluate(whatsapp.READ_ROWS)[0]["key"], None
            page.evaluate("() => { document.getElementById('text').innerText = 'x'.repeat(100); }")
            again = page.evaluate(whatsapp.READ_ROWS)[0]["key"]
            assert first == again and folded_key
            browser.close()
    except Error as error:
        pytest.skip(f"no browser here: {error}")


def test_voice_notes_and_media_are_reported_not_run():
    media = []
    note = {"key": "media:1", "kind": "media", "voice": True, "id": "1", "meta": "16:31"}
    assert commands_from([note], set(), NOW, media=media) == []
    assert media == [note]


def test_screenshot_shorthand():
    for text in ["screenshot", "Screenshot.", "send me a screenshot", "show me the screen", "screen"]:
        assert SCREENSHOT_REQUEST.fullmatch(text), text
    assert not SCREENSHOT_REQUEST.fullmatch("take a screenshot of chrome and close it")


def test_old_messages_never_run_even_if_they_sync_late():
    assert commands_from([row(text="shut down", stamp="[09:00, 31/10/2026] Me: ")], set(), NOW) == []
    assert commands_from([row(stamp="[16:33, 30/10/2026] Me: ")], set(), NOW) == []


def test_a_message_with_an_unreadable_time_is_dropped_and_reported():
    unreadable = []
    assert commands_from([row(stamp="")], set(), NOW, unreadable=unreadable) == []
    assert unreadable == ["open chrome"]
    stale = []
    commands_from([row(stamp="[09:00, 31/10/2026] Me: ")], set(), NOW, unreadable=stale)
    assert stale == []  # an old message is simply old, and shouldn't produce a reply


def test_stamp_formats():
    assert age_in_minutes("[16:30, 31/10/2026] Me: ", NOW) == 2
    assert age_in_minutes("[4:30 pm, 31/10/2026] Me: ", NOW) == 2
    assert age_in_minutes("[4:30 PM, 10/31/2026] Me: ", NOW) == 2
    assert age_in_minutes("[16.30, 31.10.2026] Me: ", NOW) == 2
    assert age_in_minutes("[12:05 am, 1/11/2026] Me: ", datetime(2026, 11, 1, 0, 6)) == 1
    assert age_in_minutes("[23:59, 31/10/2026] Me: ", datetime(2026, 11, 1, 0, 1)) == 2
    assert age_in_minutes("nonsense", NOW) is None


class FakePage:
    def __init__(self): self.sent = []


def _voice_bridge(monkeypatch, heard="open chrome"):
    from buddy import whatsapp
    bridge = whatsapp.Bridge.__new__(whatsapp.Bridge)
    bridge.transcriber = object()
    bridge._transcribe = lambda page, row_id: heard
    bridge.heard_clearly = True
    bridge._unsure = None
    handled = []
    bridge._handle = handled.append
    monkeypatch.setattr(whatsapp, "_send_text", lambda page, text: page.sent.append(text))
    return bridge, handled


def _fresh_meta():
    return datetime.now().strftime("%H:%M")


def test_a_voice_note_is_read_back_then_acted_on(monkeypatch):
    bridge, handled = _voice_bridge(monkeypatch)
    page = FakePage()
    bridge._handle_media(page, {"voice": True, "id": "1", "meta": _fresh_meta()})
    assert handled == ["open chrome"] and 'I heard: "open chrome"' in page.sent


def test_an_old_voice_note_never_runs(monkeypatch):
    bridge, handled = _voice_bridge(monkeypatch)
    page = FakePage()
    hours_ago = (datetime.now().hour + 6) % 24
    bridge._handle_media(page, {"voice": True, "id": "1", "meta": f"{hours_ago:02d}:00"})
    assert handled == [] and page.sent == []


def test_silence_and_other_media_get_a_clear_reply(monkeypatch):
    bridge, handled = _voice_bridge(monkeypatch, heard="")
    page = FakePage()
    bridge._handle_media(page, {"voice": True, "id": "1", "meta": _fresh_meta()})
    bridge._handle_media(page, {"voice": False, "id": "2", "meta": _fresh_meta()})
    assert handled == [] and len(page.sent) == 2


def test_a_voice_note_is_collected_without_evaluating_strings_in_the_page():
    """WhatsApp's security policy blocks wait_for_function, so using it must fail this test."""
    from buddy import whatsapp

    class Button:
        def click(self, timeout=None): pass

    class Handle:
        def as_element(self): return Button()

    class Page:
        def __init__(self): self.loaded = 0
        def wait_for_timeout(self, ms): self.loaded = 1
        def evaluate_handle(self, script, *args): return Handle()
        def evaluate(self, script, *args):
            if script == whatsapp.READ_NEW_AUDIO:
                return {"data": "aGVsbG8=", "type": "audio/ogg", "size": 5, "source": "player", "skipped": 0}
            return [self.loaded, 0]

    class Ear:
        def transcribe(self, audio):
            assert audio == b"hello"
            return "what time is it"

        def transcribe_with_confidence(self, audio):
            return self.transcribe(audio), 0.1, -0.3

    bridge = whatsapp.Bridge.__new__(whatsapp.Bridge)
    bridge.transcriber = Ear()
    assert bridge._transcribe(Page(), "row") == "what time is it"


def _voice_page(button_clicks):
    from buddy import whatsapp

    class Button:
        def click(self, timeout=None):
            button_clicks["count"] += 1
            if button_clicks["count"] in button_clicks["fail_on"]:
                raise whatsapp.PlaywrightError("Element is not attached to the DOM")

    class Handle:
        def as_element(self): return Button()

    class Page:
        def __init__(self): self.loaded = 0
        def wait_for_timeout(self, ms): self.loaded = 1
        def evaluate_handle(self, script, *args): return Handle()
        def evaluate(self, script, *args):
            if script == whatsapp.READ_NEW_AUDIO:
                return {"data": "aGVsbG8=", "type": "audio/ogg", "size": 5, "source": "player", "skipped": 0}
            return [self.loaded, 0]

    return Page()


def _bridge_with_ear():
    from buddy import whatsapp

    class Ear:
        def transcribe_with_confidence(self, audio): return "heard it", 0.1, -0.3

    bridge = whatsapp.Bridge.__new__(whatsapp.Bridge)
    bridge.transcriber = Ear()
    return bridge


def test_the_button_being_redrawn_after_play_cannot_lose_the_voice_note():
    clicks = {"count": 0, "fail_on": {2, 3, 4}}  # play works, then every stop click hits a redrawn button
    assert _bridge_with_ear()._transcribe(_voice_page(clicks), "row") == "heard it"


def test_pressing_play_retries_when_the_button_is_redrawn():
    clicks = {"count": 0, "fail_on": {1}}
    assert _bridge_with_ear()._transcribe(_voice_page(clicks), "row") == "heard it"


def test_older_audio_is_never_returned_for_a_new_note():
    """Runs the page script in a real browser: replaying must not hand back an earlier note's audio."""
    import pytest
    pytest.importorskip("playwright")
    from playwright.sync_api import Error, sync_playwright

    from buddy import whatsapp
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            page.evaluate("""() => {
              window.__buddyPlays = [];
              window.__buddyAudio = [new Blob(['older note'], {type: 'audio/ogg'})];
            }""")
            assert page.evaluate(whatsapp.READ_NEW_AUDIO, [1, 0]) is None
            page.evaluate("() => { window.__buddyAudio.push(new Blob(['new note'], {type: 'audio/ogg'})); }")
            import base64
            assert base64.b64decode(page.evaluate(whatsapp.READ_NEW_AUDIO, [1, 0])["data"]) == b"new note"
            # WhatsApp's notification sound plays from an ordinary URL, and must never be taken for a note
            page.evaluate("""() => {
              const ding = new Audio(); ding.src = 'https://example.com/notification.mp3';
              window.__buddyPlays.push(ding);
            }""")
            found = page.evaluate(whatsapp.READ_NEW_AUDIO, [1, 0])
            assert base64.b64decode(found["data"]) == b"new note" and found["skipped"] == 1
            browser.close()
    except Error as error:
        pytest.skip(f"no browser here: {error}")


def test_whisper_inventing_words_from_silence_is_not_acted_on(tmp_path, monkeypatch):
    from buddy import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    clicks = {"count": 0, "fail_on": set()}
    bridge = _bridge_with_ear()

    class SilenceEar:
        def transcribe_with_confidence(self, audio): return "Thank you very much.", 0.9, -0.4

    bridge.transcriber = SilenceEar()
    assert bridge._transcribe(_voice_page(clicks), "row") == ""


def _unsure_bridge(monkeypatch):
    bridge, handled = _voice_bridge(monkeypatch, heard="volume 100%")
    bridge.heard_clearly = False
    bridge._unsure = None
    return bridge, handled


def test_an_unsure_voice_note_waits_for_yes(monkeypatch):
    bridge, handled = _unsure_bridge(monkeypatch)
    page = FakePage()
    bridge._handle_media(page, {"voice": True, "id": "1", "meta": _fresh_meta()})
    assert handled == [] and "volume 100%" in page.sent[0] and "Reply yes" in page.sent[0]
    assert bridge._confirmed("yes") == "volume 100%"


def test_anything_but_yes_drops_the_unsure_note(monkeypatch):
    bridge, _ = _unsure_bridge(monkeypatch)
    bridge._handle_media(FakePage(), {"voice": True, "id": "1", "meta": _fresh_meta()})
    assert bridge._confirmed("open chrome") == "open chrome"
    assert bridge._confirmed("yes") == "yes"  # nothing is waiting any more


def test_a_late_yes_does_not_run_an_old_note(monkeypatch):
    import time
    bridge, _ = _unsure_bridge(monkeypatch)
    bridge._unsure = ("volume 100%", time.monotonic() - 600)
    assert bridge._confirmed("yes") == "yes"


def test_a_clear_voice_note_runs_right_away(monkeypatch):
    bridge, handled = _voice_bridge(monkeypatch)
    bridge.heard_clearly = True
    bridge._unsure = None
    bridge._handle_media(FakePage(), {"voice": True, "id": "1", "meta": _fresh_meta()})
    assert handled == ["open chrome"]


def test_the_same_answer_twice_in_a_minute_counts_twice():
    """A game gets plenty of these, and each one is a new turn."""
    stamp = datetime.now().strftime("[%H:%M, %d/%m/%Y] Me: ")
    rows = [{"key": f"{stamp}|no|{count}", "kind": "in", "text": "no", "stamp": stamp} for count in (1, 2)]
    seen = set()
    assert commands_from(rows[:1], seen) == ["no"]
    assert commands_from(rows, seen) == ["no"]
