import pytest
from fastapi.testclient import TestClient

from buddy import desktop, share
from buddy.engine import Engine


@pytest.fixture
def phone(monkeypatch):
    clicks = []
    monkeypatch.setattr(desktop, "click", lambda *a, **k: clicks.append((a, k)))
    monkeypatch.setattr(desktop, "screen_rect", lambda: (0, 0, 1001, 501))
    monkeypatch.setattr(desktop, "frame_jpeg", lambda: b"jpeg")
    manager = share.Share(Engine(), None)
    manager.session = share.Session("secret", control=False, expires_at=9e12)
    return manager, TestClient(share.phone_app(manager)), clicks


HEADERS = {"X-Buddy-Token": "secret"}
TAP = {"x": 0.5, "y": 0.5}


def test_no_token_sees_nothing(phone):
    _, client, _ = phone
    assert client.get("/api/frame").status_code == 401
    assert client.get("/api/frame", headers={"X-Buddy-Token": "wrong"}).status_code == 401
    assert client.post("/api/stop").status_code == 401


def test_view_link_can_watch_but_not_touch(phone):
    _, client, clicks = phone
    assert client.get("/api/frame", headers=HEADERS).content == b"jpeg"
    for path, body in [("/api/tap", TAP), ("/api/type", {"text": "x"}), ("/api/key", {"combo": "enter"}),
                       ("/api/scroll", {"amount": 1}), ("/api/command", {"text": "shut down"})]:
        assert client.post(path, json=body, headers=HEADERS).status_code == 403, path
    assert client.get("/api/events", headers=HEADERS).status_code == 403
    assert clicks == []


def test_control_link_clicks_the_right_pixel(phone):
    manager, client, clicks = phone
    manager.session.control = True
    assert client.post("/api/tap", json=TAP, headers=HEADERS).status_code == 200
    assert clicks == [((500, 250), {"right": False})]


def test_taps_outside_the_screen_are_rejected(phone):
    manager, client, clicks = phone
    manager.session.control = True
    assert client.post("/api/tap", json={"x": 2, "y": 0}, headers=HEADERS).status_code == 422
    assert clicks == []


def test_token_dies_when_sharing_stops(phone):
    manager, client, _ = phone
    manager.stop()
    assert client.get("/api/frame", headers=HEADERS).status_code == 401


def test_first_viewer_is_announced_once(phone):
    manager, client, _ = phone
    client.get("/api/frame", headers=HEADERS)
    client.get("/api/frame", headers=HEADERS)
    spoken = [e["text"] for e in manager.engine.events if e["kind"] == "say"]
    assert spoken == ["A device just connected to your screen"]


def test_main_settings_are_not_on_the_phone_server(phone):
    _, client, _ = phone
    assert client.get("/api/settings", headers=HEADERS).status_code == 404
    assert client.post("/api/share", json={}, headers=HEADERS).status_code == 404


def test_view_link_cannot_extend(phone):
    _, client, _ = phone
    assert client.post("/api/extend", headers=HEADERS).status_code == 403


def test_extend_adds_time_up_to_the_cap(phone):
    manager, client, _ = phone
    manager.session.control = True
    manager.session.expires_at = __import__("time").time() + 60
    left = client.post("/api/extend", headers=HEADERS).json()["seconds_left"]
    assert 3600 < left <= 3660
    for _ in range(20):
        left = client.post("/api/extend", headers=HEADERS).json()["seconds_left"]
    assert left <= share.MAX_SHARE_SECONDS
    manager.stop()
