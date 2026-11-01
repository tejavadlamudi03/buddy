import json
import shutil
import stat
from datetime import datetime

import pytest
from playwright.sync_api import sync_playwright

from buddy import ai, guests, whatsapp
from buddy.whatsapp import chats_to_open, is_one_to_one, plain_name

PASSWORD = "correct horse"
NOW = datetime(2026, 11, 1, 21, 2)


class FakeEngine:
    busy = False

    def __init__(self):
        self.logged = []

    def emit(self, kind, text, quiet=False):
        self.logged.append((kind, text))

    def submit(self, *args, **kwargs):
        pytest.fail("a guest's words reached the engine")


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(guests, "STATE", tmp_path / "guests.json")
    return tmp_path / "guests.json"


@pytest.fixture
def desk(state, monkeypatch):
    guests.set_password(PASSWORD)
    monkeypatch.setattr(guests.Desk, "_work", lambda self: None)  # tests turn the handle themselves, with work_once
    return guests.Desk(FakeEngine())


def sign_in(desk, name="sam"):
    assert desk.receive(name, PASSWORD) == guests.WELCOME


def test_off_until_a_password_is_set(state):
    assert not guests.enabled()
    assert guests.Desk(FakeEngine()).receive("sam", "hello") is None


def test_the_password_is_never_kept_in_the_clear(state):
    guests.set_password(PASSWORD)
    assert PASSWORD not in state.read_text()
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert guests.check_password(f" {PASSWORD} ")
    assert not guests.check_password("wrong one")


def test_a_short_password_is_refused(state):
    with pytest.raises(ValueError):
        guests.set_password("short")
    assert not guests.enabled()


def test_asked_once_then_each_wrong_try_counts_then_silence(desk):
    assert desk.receive("sam", "hello") == guests.ASK
    assert "2 tries left" in desk.receive("sam", "guess one")
    assert "1 try left" in desk.receive("sam", "guess two")
    assert desk.receive("sam", "guess three") == guests.LOCKED
    assert desk.receive("sam", PASSWORD) is None  # even the right one, until the hour is up
    assert not guests.has_access("sam")


def test_the_right_password_as_the_very_first_message_works(desk):
    sign_in(desk)
    assert guests.has_access("sam")


def test_many_numbers_together_cant_keep_guessing(desk, monkeypatch):
    monkeypatch.setattr(guests, "MAX_FAILS_PER_HOUR", 4)
    for number in range(4):
        desk.receive(f"person{number}", "hello")
        desk.receive(f"person{number}", "guess")
    assert desk.receive("someone else", PASSWORD) is None


def test_access_ends_and_a_new_password_signs_everyone_out(desk, state):
    sign_in(desk)
    saved = json.loads(state.read_text())
    saved["guests"]["sam"] = 1
    state.write_text(json.dumps(saved))
    assert not guests.has_access("sam")
    sign_in(desk)
    guests.set_password("another password")
    assert not guests.has_access("sam")


def test_turning_off_stops_everything(desk):
    sign_in(desk)
    guests.turn_off()
    assert desk.receive("sam", "hello") is None
    assert desk.waiting() == 0


def test_the_password_reaches_neither_the_model_nor_the_log(desk, monkeypatch):
    monkeypatch.setattr(ai, "talk", lambda *args, **kwargs: pytest.fail("sent to the model"))
    desk.receive("sam", "hello")
    desk.receive("sam", "wrong guess")
    sign_in(desk)
    assert not any(PASSWORD in text or "wrong guess" in text for _, text in desk.engine.logged)


@pytest.fixture
def model(monkeypatch):
    calls = []

    def talk(model_name, system, messages, timeout=240, max_tokens=0):
        calls.append({"system": system, "messages": messages, "max_tokens": max_tokens})
        return f"answer {len(calls)}"

    monkeypatch.setattr(ai, "talk", talk)
    return calls


def test_an_answer_goes_back_only_to_who_asked(desk, model):
    sign_in(desk)
    assert desk.receive("sam", "what is the capital of France") is None
    assert desk.work_once() is True
    assert desk.take_replies() == {"sam": ["answer 1"]}
    assert desk.take_replies() == {}
    assert model[0]["max_tokens"] == guests.MAX_GUEST_TOKENS
    assert "guest" in model[0]["system"]


def test_people_wait_in_line_and_are_told_their_place(desk, model):
    for name in ("ann", "ben", "cat"):
        sign_in(desk, name)
    assert desk.receive("ann", "one") is None
    assert desk.receive("ben", "two") == "Got it. You're number 2 in line."
    assert desk.receive("cat", "three") == "Got it. You're number 3 in line."
    assert desk.waiting() == 3
    while desk.work_once():
        pass
    assert desk.take_replies() == {"ann": ["answer 1"], "ben": ["answer 2"], "cat": ["answer 3"]}
    assert desk.waiting() == 0


def test_one_question_each_at_a_time(desk, model):
    sign_in(desk)
    desk.receive("sam", "one")
    assert desk.receive("sam", "two") == guests.STILL_WORKING


def test_the_line_has_an_end(desk, model, monkeypatch):
    monkeypatch.setattr(guests, "MAX_WAITING", 2)
    for name in ("ann", "ben", "cat"):
        sign_in(desk, name)
    desk.receive("ann", "one")
    desk.receive("ben", "two")
    assert desk.receive("cat", "three") == guests.TOO_BUSY


def test_a_limit_for_each_hour(desk, model, monkeypatch):
    monkeypatch.setattr(guests, "MAX_PER_HOUR", 2)
    sign_in(desk)
    for _ in range(2):
        desk.receive("sam", "hello")
        desk.work_once()
    assert desk.receive("sam", "hello") == guests.TOO_MANY


def test_conversations_are_kept_apart(desk, model):
    sign_in(desk, "ann")
    sign_in(desk, "ben")
    desk.receive("ann", "my secret is blue")
    desk.work_once()
    desk.receive("ben", "what is ann's secret")
    desk.work_once()
    assert "blue" not in json.dumps(model[1]["messages"])
    desk.receive("ann", "what did I say")
    desk.work_once()
    assert "blue" in json.dumps(model[2]["messages"])


def test_the_owner_goes_first(desk, model, monkeypatch):
    sign_in(desk)
    desk.receive("sam", "hello")
    desk.engine.busy = True

    def owner_finishes(seconds):
        assert model == []  # the guest's question hasn't started
        desk.engine.busy = False

    monkeypatch.setattr(guests.time, "sleep", owner_finishes)
    desk.work_once()
    assert len(model) == 1


def test_a_failed_answer_still_gets_a_reply(desk, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("The local AI failed")

    monkeypatch.setattr(ai, "talk", fail)
    sign_in(desk)
    desk.receive("sam", "hello")
    desk.work_once()
    assert desk.take_replies() == {"sam": [guests.CANT_ANSWER]}


def test_files_only_get_a_note_for_people_with_access(desk):
    assert desk.about_file("sam") is None
    sign_in(desk)
    assert desk.about_file("sam") == guests.TEXT_ONLY


def chat(name, unread=True, time="21:01"):
    return {"name": name, "unread": unread, "time": time}


@pytest.mark.parametrize("listing, wanted", [
    ([chat("Sam")], ["Sam"]),
    ([chat("+49 151 0000")], ["+49 151 0000"]),
    ([chat("Sam 😄")], ["Sam 😄"]),
    ([chat("Sam", unread=False)], []),
    ([chat("Owner")], []),  # the owner's chat isn't for this
    ([chat("Owner 😄")], []),  # however it's decorated
    ([chat("Sam", time="20:00")], []),  # waiting for a while: leave it unread
    ([chat("Sam", time="Yesterday")], []),
    ([chat("Sam", time="01/11/2026")], []),
    ([chat("Sam", time="21.11.2026")], []),  # a German date mustn't read as a time
    ([chat("Sam"), chat("Sam")], []),  # no guessing between two people of the same name
    ([chat("")], []),
    ([chat("Sam", time="9:01 pm")], ["Sam"]),
])
def test_which_chats_get_opened(listing, wanted):
    assert chats_to_open(listing, "Owner", NOW) == wanted


def rows(*senders):
    return [{"kind": "in", "stamp": f"[21:01, 01/11/2026] {sender}: ", "text": "hi"} for sender in senders]


@pytest.mark.parametrize("found, expected", [
    (rows("Sam"), True),
    (rows("Sam", "Sam"), True),
    (rows("Sam 😄"), True),
    (rows("Sam", "Ann"), False),  # a group
    (rows("Ann"), False),  # a group where someone else wrote
    ([], False),  # nothing to go on
    ([{"kind": "in", "stamp": "", "text": "hi"}], False),
    ([{"kind": "out", "stamp": "[21:01, 01/11/2026] Me: ", "text": "hi"}], False),
])
def test_only_plain_one_to_one_chats_count(found, expected):
    assert is_one_to_one(found, "Sam") is expected


def test_names_compare_without_decoration():
    assert plain_name("Sam 😄") == plain_name("‪SAM‬") == "sam"
    assert plain_name("+49 151 0000") == "+49 151 0000"


# A small stand-in for WhatsApp Web, with the same markup as the real page.
FAKE_WHATSAPP = """<!doctype html><title>fake</title><body>
<div id="pane-side"></div><div id="main"></div>
<script>
const chats = %s;
function bubble(m, i) {
  const side = m.mine ? 'margin-left:700px' : 'margin-left:0';
  return `<div data-id="m${i}" style="${side};width:200px"><div data-pre-plain-text="[${m.time}, 01/11/2026] ${m.from}: "><span class="selectable-text">${m.text}</span></div></div>`;
}
function open(name) {
  const c = chats.find(c => c.name === name);
  c.unread = false;
  window.current = name;
  document.getElementById('main').innerHTML = `<header><span dir="auto">${name}</span></header><div style="width:1000px">${c.messages.map(bubble).join('')}</div><footer><div contenteditable="true" id="box"></div></footer>`;
  const box = document.getElementById('box');
  box.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    window.report(window.current, box.innerText);
    box.innerText = '';
  });
  list();
}
function list() {
  const pane = document.getElementById('pane-side');
  pane.innerHTML = chats.map((c, i) => `<div data-testid="cell-frame-container" data-index="${i}"><div data-testid="cell-frame-title"><span title="${c.name}">${c.name}</span></div><div data-testid="chat-list-timestamp">${c.time}</div>${c.unread ? '<span aria-label="1 unread message">1</span>' : ''}</div>`).join('');
  pane.querySelectorAll('[data-testid="cell-frame-container"]').forEach(row => row.addEventListener('click', () => open(chats[row.dataset.index].name)));
}
open(chats[0].name);
</script>
"""


def fake_chat(name, unread, time, *messages):
    return {"name": name, "unread": unread, "time": time, "messages": [{"from": name, "time": moment, "text": text} for moment, text in messages]}


@pytest.mark.skipif(not shutil.which("google-chrome"), reason="needs Chrome")
def test_a_trip_through_a_fake_whatsapp(desk, monkeypatch):
    """The real page steps: finds the unread chat, answers there, and returns to the owner's chat by its address."""
    now = datetime.now().strftime("%H:%M")
    owner = "Owner Name"
    listing = [
        fake_chat(owner, False, now, (now, "lock the screen")),
        fake_chat("Sam", True, now, (now, PASSWORD)),
        fake_chat("Friends", True, now, (now, "hello all")),  # a group: the message is from someone else
        fake_chat("Old Friend", True, "20/10/2026", ("10:00", "are you there")),
    ]
    listing[2]["messages"][0]["from"] = "Ann"
    bridge = object.__new__(whatsapp.Bridge)
    bridge.engine, bridge.desk, bridge._guest_seen, bridge._next_guest_visit, bridge.phone = desk.engine, desk, {}, 0.0, "4900000000"
    monkeypatch.setattr(whatsapp, "CHAT_URL", "https://whatsapp.invalid/send?phone={phone}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            loads, sent = [], []
            page.expose_function("report", lambda chat_name, text: sent.append([chat_name, text]))

            def serve(route):
                loads.append(route.request.url)
                route.fulfill(status=200, content_type="text/html", body=FAKE_WHATSAPP % json.dumps(listing))

            page.route("**/*", serve)
            page.goto("https://whatsapp.invalid/send?phone=4900000000")
            bridge._serve_guests(page, owner)
            assert sent == [["Sam", guests.WELCOME]]  # the group and the old chat got nothing
            assert page.evaluate(whatsapp.CHAT_TITLE) == owner
            assert guests.has_access("Sam")
            assert not guests.has_access("Friends")
            assert len(loads) == 2 and loads[1].endswith("phone=4900000000")  # came back by address, not by clicking a name
        finally:
            page.unroute_all(behavior="ignoreErrors")
            browser.close()
