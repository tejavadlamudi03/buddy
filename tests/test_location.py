import io
import json
import subprocess
import urllib.error

import sys

import pytest

from buddy import actions, agent, config, location, whatsapp
from buddy.engine import Engine
from buddy.parser import parse


def listing(monkeypatch, text):
    monkeypatch.setattr(location.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=text))


def service(monkeypatch, reply):
    """Stands in for the lookup, and keeps what would have been sent."""
    sent = {}

    def urlopen(request, timeout):
        sent["body"], sent["agent"] = json.loads(request.data), request.get_header("User-agent")
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setattr(location.urllib.request, "urlopen", urlopen)
    return sent


FOUND = {"location": {"lat": 12.34567, "lng": 56.78901}, "accuracy": 42.0}


@pytest.mark.parametrize("said", [
    "share my location", "Buddy, send my location", "send me my location", "share location", "send the location",
    "share my current location on whatsapp", "send my live location to my phone", "send me the laptop's location",
    "send the location of the laptop", "where is my laptop", "where's the laptop right now", "where am i",
    "what's my location", "what is the laptop's location", "tell me the laptop's location", "show me my location",
    "tell me where the laptop is",
])
def test_phrases(said):
    assert parse(said) == [{"action": "share_location"}]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows lists networks with netsh")
def test_networks_are_read_from_nmcli_and_strongest_come_first(monkeypatch):
    listing(monkeypatch, "Home:A0\\:BB\\:CC\\:00\\:00\\:01:50\nCafe\\:Free:A8\\:BB\\:CC\\:00\\:00\\:02:90\n:A4\\:BB\\:CC\\:00\\:00\\:03:20\n")
    assert location.nearby_networks() == [
        {"macAddress": "a8:bb:cc:00:00:02", "signalStrength": -55},
        {"macAddress": "a0:bb:cc:00:00:01", "signalStrength": -75},
        {"macAddress": "a4:bb:cc:00:00:03", "signalStrength": -90},
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows lists networks with netsh, tested in test_windows.py")
def test_opted_out_and_made_up_addresses_are_never_sent(monkeypatch):
    listing(monkeypatch, "Mine_nomap:A8\\:BB\\:CC\\:00\\:00\\:01:90\nPhone:DA\\:BB\\:CC\\:00\\:00\\:02:90\n"
                         "One:A8\\:BB\\:CC\\:00\\:00\\:03:80\nTwo:A4\\:BB\\:CC\\:00\\:00\\:04:70\n")
    assert [network["macAddress"] for network in location.nearby_networks()] == ["a8:bb:cc:00:00:03", "a4:bb:cc:00:00:04"]


def test_one_network_is_not_enough(monkeypatch):
    listing(monkeypatch, "One:A8\\:BB\\:CC\\:00\\:00\\:03:80\n")
    assert location.nearby_networks() == []


def test_no_nmcli_means_no_networks(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("nmcli")

    monkeypatch.setattr(location.subprocess, "run", missing)
    assert location.nearby_networks() == []


def test_found_from_wifi(monkeypatch):
    monkeypatch.setattr(location, "nearby_networks", lambda: [{"macAddress": "a8:bb:cc:00:00:03", "signalStrength": -60}] * 2)
    sent = service(monkeypatch, FOUND)
    place = location.find()
    assert place.link == "https://www.google.com/maps?q=12.34567,56.78901"
    assert place.accuracy == "Accurate to about 40 metres"
    assert len(sent["body"]["wifiAccessPoints"]) == 2
    assert sent["agent"] == location.USER_AGENT


def test_kilometres(monkeypatch):
    monkeypatch.setattr(location, "nearby_networks", lambda: [{"macAddress": "a8:bb:cc:00:00:03", "signalStrength": -60}] * 2)
    service(monkeypatch, {**FOUND, "accuracy": 2400.0})
    assert location.find().accuracy == "Accurate to about 2 km"


@pytest.mark.parametrize("networks, reply", [
    ([], FOUND),  # no Wi-Fi was sent, so however sure it sounds, this came from the connection
    ([{"macAddress": "a8:bb:cc:00:00:03", "signalStrength": -60}] * 2, {**FOUND, "fallback": "ipf"}),
    ([{"macAddress": "a8:bb:cc:00:00:03", "signalStrength": -60}] * 2, {**FOUND, "accuracy": 25000.0}),
])
def test_a_rough_answer_says_so(monkeypatch, networks, reply):
    monkeypatch.setattr(location, "nearby_networks", lambda: networks)
    service(monkeypatch, reply)
    assert "rough guess" in location.find().accuracy


@pytest.mark.parametrize("reply", [
    urllib.error.URLError("no internet"),
    urllib.error.HTTPError(location.LOOKUP_URL, 404, "Not found", None, None),
    {"error": "nothing"},
    {"location": {"lat": "x", "lng": 1}, "accuracy": 1},
])
def test_a_failed_lookup_says_so(monkeypatch, reply):
    monkeypatch.setattr(location, "nearby_networks", lambda: [])
    service(monkeypatch, reply)
    with pytest.raises(RuntimeError, match="couldn't find the laptop's location"):
        location.find()


@pytest.fixture
def phone(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config.save({"whatsapp_phone": "4900000000", "token": "test"})
    monkeypatch.setattr(location, "find", lambda: location.Place(12.34567, 56.78901, 42.0, False))
    sent = []
    monkeypatch.setattr(whatsapp, "send_text", lambda number, text: sent.append((number, text)))
    return sent


def test_asked_on_the_laptop_it_goes_to_whatsapp_and_the_link_is_not_read_aloud(phone):
    answer = actions.run("share_location")
    assert answer == "Sent the laptop's location to your WhatsApp. Accurate to about 40 metres"
    number, text = phone[0]
    assert number == "4900000000"
    assert "https://www.google.com/maps?q=12.34567,56.78901" in text and "Accurate to about 40 metres." in text


def test_asked_on_whatsapp_the_reply_is_the_only_message(phone):
    engine = Engine()
    engine._source = "whatsapp"
    answer = engine._run_steps([{"action": "share_location"}])
    assert "https://www.google.com/maps?q=" in answer
    assert phone == []


def test_no_number_set_looks_nothing_up(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(location, "find", lambda: pytest.fail("looked up the location"))
    with pytest.raises(LookupError, match="WhatsApp number"):
        actions.run("share_location")


def test_the_agent_can_never_send_the_location():
    assert "share_location" not in agent.AGENT_ACTIONS
