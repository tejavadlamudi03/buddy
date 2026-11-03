import sys
from pathlib import Path

import pytest

from buddy import actions, agent, files, whatsapp


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "HOME", tmp_path)
    monkeypatch.setattr(files, "_owner_private_dirs", lambda: ("secret-project",))
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents/Q3 report.pdf").write_text("x")
    (tmp_path / "Downloads/report-old.pdf").write_text("x")
    (tmp_path / "Downloads/notes.txt").write_text("hello notes")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh/report.pdf").write_text("secret")
    (tmp_path / "secret-project").mkdir()
    (tmp_path / "secret-project/report.pdf").write_text("confidential")
    (tmp_path / "Documents/.env").write_text("TOKEN=1")
    (tmp_path / "Documents/server.pem").write_text("key")
    return tmp_path


def test_find_ignores_spacing_and_case(home):
    assert [p.name for p in files.find("q3 REPORT")] == ["Q3 report.pdf"]


def test_find_never_reaches_private_places(home):
    found = files.find("report")
    assert found and not any(".ssh" in str(p) or "secret-project" in str(p) for p in found)
    assert files.find("server.pem") == [] and files.find(".env") == []


def test_private_paths_are_refused_even_by_exact_path(home, monkeypatch):
    sent = []
    monkeypatch.setattr(whatsapp, "send_image", lambda phone, path: sent.append(path))
    monkeypatch.setattr(actions, "_owner_phone", lambda: "1")
    for private in (home / "secret-project/report.pdf", home / ".ssh/report.pdf", home / "Documents/server.pem"):
        assert "private" in actions.run("send_file", name=str(private))
        with pytest.raises(PermissionError):
            files.read_text(private)
    assert sent == []


def test_sends_a_normal_file_by_spoken_name(home, monkeypatch):
    sent = []
    monkeypatch.setattr(whatsapp, "send_image", lambda phone, path: sent.append(path.name))
    monkeypatch.setattr(actions, "_owner_phone", lambda: "1")
    assert actions.run("send_file", name="notes dot txt") == "Sent notes.txt to your WhatsApp"
    assert sent == ["notes.txt"]


def test_latest_download(home):
    newest = home / "Downloads/new.zip"
    newest.write_text("x")
    import os
    os.utime(newest, (4102444800, 4102444800))
    assert files.resolve("latest download") == newest


def test_read_rejects_binary(home):
    (home / "Downloads/a.bin").write_bytes(b"ab\0cd")
    with pytest.raises(ValueError):
        files.read_text(home / "Downloads/a.bin")
    assert files.read_text(home / "Downloads/notes.txt") == "hello notes"


def test_a_filename_is_not_treated_as_a_website():
    assert files.looks_like_file("report dot pdf") and files.looks_like_file("a.docx")
    assert not files.looks_like_file("github.com")


def test_read_only_commands_for_the_ai():
    assert "refused" in actions.run("run_readonly", command="rm -rf x")
    assert "refused" in actions.run("run_readonly", command="ls; rm -rf x")
    assert "refused" in actions.run("run_readonly", command="ls $(rm x)")
    assert "refused" in actions.run("run_readonly", command="ls | sh")
    assert actions.run("run_readonly", command="whoami")


def test_the_ai_cannot_run_arbitrary_commands():
    assert "run_command" not in agent.AGENT_ACTIONS
    assert agent._execute({"action": "run_command", "command": "ls"}, "run ls", 1.0, 0, 0).startswith("refused")


@pytest.mark.skipif(sys.platform == "win32", reason="bash syntax; PowerShell is tested in test_windows_live.py")
def test_run_command_reports_failure_and_output():
    assert actions.run("run_command", command="echo hi") == "hi"
    assert actions.run("run_command", command="echo bad >&2; exit 3").startswith("Exit code 3")
