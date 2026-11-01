import functools
import http.server
import shutil
import threading
import time

import pytest
from PIL import Image

from buddy import actions, engine as engine_module, fetch, files, whatsapp
from buddy.engine import Engine
from buddy.parser import parse

needs = lambda tool: pytest.mark.skipif(not shutil.which(tool), reason=f"{tool} isn't installed")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "HOME", tmp_path / "home")
    monkeypatch.setattr(files, "_owner_private_dirs", lambda: ("secret-project",))
    (tmp_path / "home/Downloads").mkdir(parents=True)
    fetch.cancel.clear()
    return tmp_path / "home"


class Site(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/sneaky":
            self.send_response(200)
            self.send_header("Content-Disposition", 'attachment; filename="../../.bashrc"')
            self.end_headers()
            self.wfile.write(b"gotcha")
        elif self.path == "/page":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>a page</body></html>")
        elif self.path == "/slow":
            self.send_response(200)
            self.end_headers()
            for _ in range(200):
                self.wfile.write(b"x" * fetch.CHUNK)
                self.wfile.flush()
                time.sleep(0.02)
        else:
            super().do_GET()


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "notes.txt").write_text("hello from the site")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Site, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.mark.parametrize("said,expected", [
    ("download https://Example.com/A.pdf", {"url": "https://Example.com/A.pdf"}),
    ("Buddy, download this file https://x.com/a.docx?b=1&c=2", {"url": "https://x.com/a.docx?b=1&c=2"}),
    ("download www.x.com/a.pdf", {"url": "https://www.x.com/a.pdf"}),
    ("download https://x.com/a.docx as pdf", {"url": "https://x.com/a.docx", "to": "pdf"}),
    ("download https://x.com/a.docx and send it to me", {"url": "https://x.com/a.docx", "send": True}),
    ("download https://x.com/a.docx and send it to me on whatsapp", {"url": "https://x.com/a.docx", "send": True}),
    ("download https://x.com/a.docx and convert it to pdf and send it to me", {"url": "https://x.com/a.docx", "to": "pdf", "send": True}),
    ("download https://x.com/a.docx and send it as pdf", {"url": "https://x.com/a.docx", "to": "pdf", "send": True}),
    ("get https://x.com/a.mp4 in mp3 format", {"url": "https://x.com/a.mp4", "to": "mp3"}),
    ("send me https://x.com/a.pdf", {"url": "https://x.com/a.pdf", "send": True}),
    ("Download this link and send as PDF: https://x.com/a.docx", {"url": "https://x.com/a.docx", "to": "pdf", "send": True}),
    ("Download this link and send it to me https://x.com/a.pdf", {"url": "https://x.com/a.pdf", "send": True}),
])
def test_download_phrases(said, expected):
    assert parse(said) == [{"action": "download", **expected}]


@pytest.mark.parametrize("said,expected", [
    ("convert report.docx to pdf", {"name": "report.docx", "to": "pdf"}),
    ("convert the latest download to mp3", {"name": "latest download", "to": "mp3"}),
    ("convert it to pdf and send it to me", {"name": "latest download", "to": "pdf", "send": True}),
    ("send me report.docx as pdf", {"name": "report.docx", "to": "pdf", "send": True}),
    ("send me the latest download as mp3", {"name": "latest download", "to": "mp3", "send": True}),
])
def test_convert_phrases(said, expected):
    assert parse(said) == [{"action": "convert_file", **expected}]


def test_older_phrases_keep_their_meaning():
    assert parse("send me report.pdf") == [{"action": "send_file", "name": "report.pdf"}]
    assert parse("send me the latest download") == [{"action": "send_file", "name": "latest download"}]
    assert parse("go to github.com") and parse("go to github.com")[0]["action"] != "download"
    assert parse("get me a coffee and a cake") is None


def test_download_saves_and_never_overwrites(home, site):
    first, is_page = fetch.download(f"{site}/notes.txt")
    second, _ = fetch.download(f"{site}/notes.txt")
    assert first == home / "Downloads/notes.txt" and first.read_text() == "hello from the site"
    assert second == home / "Downloads/notes (1).txt" and not is_page


def test_a_website_cannot_choose_where_a_file_goes(home, site):
    path, _ = fetch.download(f"{site}/sneaky")
    assert path.parent == home / "Downloads" and not path.name.startswith(".")
    assert not (home / ".bashrc").exists()


def test_a_web_page_is_called_a_web_page(home, site, monkeypatch):
    assert "web page" in actions.run("download", url=f"{site}/page")


def test_only_web_links(home):
    with pytest.raises(ValueError):
        fetch.download("file:///etc/passwd")


def test_a_broken_link_says_so(home, site):
    with pytest.raises(RuntimeError, match="404"):
        fetch.download(f"{site}/missing.pdf")
    assert list((home / "Downloads").iterdir()) == []


def test_stop_ends_a_download_and_leaves_nothing_behind(home, site):
    threading.Timer(0.2, fetch.cancel.set).start()
    with pytest.raises(fetch.Cancelled):
        fetch.download(f"{site}/slow")
    assert list((home / "Downloads").iterdir()) == []


def test_latest_download_skips_a_file_still_arriving(home):
    finished = home / "Downloads/done.pdf"
    finished.write_text("x")
    partial = home / "Downloads/movie.mp4.crdownload"
    partial.write_text("x")
    import os
    os.utime(partial, (4102444800, 4102444800))
    assert files.resolve("latest download") == finished


def test_sends_to_whatsapp_only_when_asked_and_small_enough(home, site, monkeypatch):
    sent = []
    monkeypatch.setattr(whatsapp, "send_image", lambda phone, path: sent.append(path.name))
    monkeypatch.setattr(actions, "_owner_phone", lambda: "1")
    assert "Saved notes.txt" in actions.run("download", url=f"{site}/notes.txt")
    assert sent == []
    assert "Sent notes (1).txt" in actions.run("download", url=f"{site}/notes.txt", send=True)
    assert sent == ["notes (1).txt"]
    monkeypatch.setattr(actions, "MAX_SEND_BYTES", 1)
    assert "too big" in actions.run("download", url=f"{site}/notes.txt", send=True)
    assert len(sent) == 1


def test_private_files_cannot_be_converted(home):
    (home / "secret-project").mkdir()
    (home / "secret-project/plan.txt").write_text("x")
    assert "private" in actions.run("convert_file", name=str(home / "secret-project/plan.txt"), to="zip")
    assert list((home / "Downloads").iterdir()) == []


def test_unknown_conversion_is_explained(home):
    source = home / "Downloads/a.mp3"
    source.write_bytes(b"x")
    with pytest.raises(ValueError, match="I can convert"):
        fetch.convert(source, "docx")


def test_same_format_makes_no_copy(home):
    source = home / "Downloads/a.pdf"
    source.write_bytes(b"x")
    assert fetch.convert(source, "PDF") == source


def test_zip_anything(home):
    source = home / "Downloads/a.bin"
    source.write_bytes(b"x" * 100)
    assert fetch.convert(source, "zip").name == "a.zip"


def test_pictures(home):
    source = home / "Downloads/a.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 128)).save(source)
    with Image.open(fetch.convert(source, "jpg")) as result:
        assert result.format == "JPEG"
    assert fetch.convert(source, "pdf").read_bytes().startswith(b"%PDF")
    assert source.exists()


def test_a_fake_picture_fails_clearly(home):
    source = home / "Downloads/a.png"
    source.write_text("not a picture")
    with pytest.raises(RuntimeError, match="as a picture"):
        fetch.convert(source, "jpg")
    assert [p.name for p in (home / "Downloads").iterdir()] == ["a.png"]


@needs("ffmpeg")
def test_audio(home):
    import subprocess
    source = home / "Downloads/tone.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(source)], capture_output=True, check=True)
    result = fetch.convert(source, "mp3")
    assert result.name == "tone.mp3" and result.stat().st_size > 1000


@needs("soffice")
def test_documents_and_pdf_tools(home):
    source = home / "Downloads/doc.txt"
    source.write_text("Buddy conversion check")
    pdf = fetch.convert(source, "pdf")
    assert pdf.read_bytes().startswith(b"%PDF")
    (home / "Downloads/doc.txt").unlink()  # so the text below can only have come out of the PDF
    assert "Buddy conversion check" in fetch.convert(pdf, "txt").read_text()
    assert fetch.convert(pdf, "png").read_bytes().startswith(b"\x89PNG")


def _wait(engine):
    for _ in range(300):
        if not engine.busy:
            return
        time.sleep(0.01)


def test_whatsapp_requests_always_come_back_as_a_file(monkeypatch):
    calls = []
    monkeypatch.setitem(actions.REGISTRY, "download", lambda **args: calls.append(args) or "ok")
    monkeypatch.setattr(engine_module.desktop, "target_window", lambda: None)
    engine = Engine()
    engine.submit("download https://x.com/a.pdf", source="whatsapp")
    _wait(engine)
    engine.submit("download https://x.com/b.pdf")
    _wait(engine)
    assert calls == [{"url": "https://x.com/a.pdf", "send": True}, {"url": "https://x.com/b.pdf"}]


def test_a_very_long_link_is_still_a_command(monkeypatch):
    calls = []
    monkeypatch.setitem(actions.REGISTRY, "download", lambda **args: calls.append(args) or "ok")
    monkeypatch.setattr(engine_module.desktop, "target_window", lambda: None)
    engine = Engine()
    engine.submit("download https://x.com/a.pdf?token=" + "z" * 900)
    _wait(engine)
    assert calls and calls[0]["url"].endswith("z" * 900)


def test_long_pasted_text_with_a_link_in_it_is_still_only_discussed(monkeypatch):
    talked = []
    monkeypatch.setattr(engine_module.ai, "talk", lambda model, system, messages: talked.append(messages) or "ok")
    monkeypatch.setitem(actions.REGISTRY, "download", lambda **args: pytest.fail("pasted text ran as a command"))
    engine = Engine()
    engine.submit("download https://x.com/a.pdf " + "and then some more words " * 20)
    _wait(engine)
    assert talked


def test_a_link_the_model_dropped_is_put_back(monkeypatch):
    calls = []
    monkeypatch.setitem(actions.REGISTRY, "download", lambda **args: calls.append(args) or "ok")
    monkeypatch.setattr(engine_module.desktop, "target_window", lambda: None)
    monkeypatch.setattr(engine_module.language, "understand", lambda text, attached="": ("Download this link and send it to me", "control"))
    engine = Engine()
    engine.submit("ee link download chesi naaku pampu https://x.com/File.pdf", source="whatsapp")
    _wait(engine)
    assert calls == [{"url": "https://x.com/File.pdf", "send": True}]


def test_stop_reaches_a_running_download():
    engine = Engine()
    fetch.cancel.clear()
    engine.stop()
    assert fetch.cancel.is_set()
    fetch.cancel.clear()
