import re

from fastapi.testclient import TestClient

from buddy import config, server
from buddy.server import STATIC


class Ear:
    def load(self):
        pass


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(server.whatsapp, "start_bridge", lambda *args: None)
    monkeypatch.setattr(server.reminders, "start", lambda *args: None)
    monkeypatch.setattr(server, "Listener", lambda *args: Ear())
    monkeypatch.setattr(server, "Transcriber", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "Speaker", lambda: None)
    return TestClient(server.create_app())


def test_the_page_can_load_the_3d_files(tmp_path, monkeypatch):
    app = client(tmp_path, monkeypatch)
    for name in server.PAGE_FILES:
        answer = app.get(f"/static/{name}")
        assert answer.status_code == 200, name
        assert "javascript" in answer.headers["content-type"], name  # browsers refuse a module of any other type
        assert answer.headers["cache-control"] == "no-cache", name


def test_nothing_else_is_served_from_there(tmp_path, monkeypatch):
    app = client(tmp_path, monkeypatch)
    for name in ["phone.html", "three.LICENSE", "..%2Fserver.py", "%2e%2e%2fconfig.py"]:
        assert app.get(f"/static/{name}").status_code == 404, name


def test_the_flat_drawing_stays_for_when_3d_cant_run():
    page = (STATIC / "index.html").read_text()
    assert 'id="face"' in page
    assert re.search(r'<script type="module" src="/static/buddy3d\.js"></script>', page)
    assert "try {\n  start();" in (STATIC / "buddy3d.js").read_text()


def test_the_3d_library_comes_from_this_laptop():
    code = (STATIC / "buddy3d.js").read_text()
    assert re.findall(r"from '([^']+)'", code) == ["./three.module.min.js"]
    assert "http" not in code
