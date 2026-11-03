"""Buddy against a real Windows, which only a real Windows can check. Types into a Notepad it opens, so it only runs
when BUDDY_LIVE_WINDOWS_TEST is set, as the GitHub workflow does on its throwaway machines."""
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

if sys.platform != "win32" or not os.environ.get("BUDDY_LIVE_WINDOWS_TEST"):
    pytest.skip("needs a throwaway Windows machine", allow_module_level=True)

from PIL import Image  # noqa: E402

from buddy import a11y, actions, apps, config, desktop, files, media, models, winsys  # noqa: E402


def _wait_for(check, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.25)
    return None


@pytest.fixture
def notepad():
    process = subprocess.Popen(["notepad.exe"])
    window = _wait_for(lambda: next((w for w in desktop.list_windows() if "notepad" in w.title.lower()), None))
    assert window, [w.title for w in desktop.list_windows()]
    yield window
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
    process.wait(10)


def test_the_screen_can_be_seen():
    image = Image.open(io.BytesIO(desktop.screenshot_png()))
    assert image.width > 100 and image.height > 100
    assert desktop.frame_jpeg()[:2] == b"\xff\xd8"


def test_the_window_list_and_focus(notepad):
    desktop.activate(notepad.id)
    assert _wait_for(lambda: (desktop.active_window() or notepad).id == notepad.id and desktop.active_window())
    assert desktop.window_pid(notepad.id)
    left, top, width, height = desktop.window_rect(notepad.id)
    assert width > 100 and height > 100


def _copied_text(window) -> str:
    """What the window holds, read back the way a person would check: select it all and copy it."""
    desktop.activate(window.id)
    time.sleep(0.3)
    desktop.press_keys("ctrl+a")
    desktop.press_keys("ctrl+c")
    time.sleep(0.3)
    return subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True, encoding="utf-8",
                          errors="replace", timeout=30).stdout


def test_typing_reaches_the_window(notepad):
    desktop.activate(notepad.id)
    time.sleep(0.5)
    desktop.type_text("hello from buddy")
    assert "hello from buddy" in _copied_text(notepad)


def test_holding_the_key_dictates_into_the_window(notepad, monkeypatch):
    """The real keyboard hook and typing, with the microphone and speech model stood in for, as this machine has neither."""
    import types
    from buddy import dictation
    monkeypatch.setattr(dictation.ai, "installed", lambda: False)
    recorder = types.SimpleNamespace(start=lambda: None, stop=lambda: b"audio", cancel=lambda: None)
    ear = types.SimpleNamespace(transcribe_with_confidence=lambda audio: ("um dictated on windows", 0.0, -0.2))
    listener = types.SimpleNamespace(english=ear, telugu=None, wider=None)
    flow = dictation.Dictation(types.SimpleNamespace(emit=lambda *a, **k: None, submit=lambda text: None), listener, recorder)
    from pynput.keyboard import Listener
    Listener(on_press=flow.hotkey.press, on_release=flow.hotkey.release, daemon=True).start()  # the conftest blocks flow.start
    desktop.activate(notepad.id)
    time.sleep(0.5)
    from pynput.keyboard import Controller, Key
    keys = Controller()
    keys.press(Key.ctrl_r)
    time.sleep(1)
    keys.release(Key.ctrl_r)
    time.sleep(3)
    assert "Dictated on windows." in _copied_text(notepad)


def test_maximize_minimize_close(notepad):
    desktop.maximize(notepad.id)
    desktop.minimize(notepad.id)
    desktop.activate(notepad.id)
    desktop.close(notepad.id)
    assert _wait_for(lambda: notepad.id not in {w.id for w in desktop.list_windows()})


def test_buttons_can_be_seen_by_name(notepad):
    from buddy import a11y_windows
    pid = desktop.window_pid(notepad.id)
    started = time.monotonic()
    found = a11y_windows.names(pid)  # straight, so an error shows here rather than being turned into "nothing"
    assert found, f"nothing pressable after {time.monotonic() - started:.1f}s"
    assert a11y.names(pid) == found


def test_a_button_can_be_pressed_by_name(notepad):
    """Maximize is in every window's title bar, and pressing it changes nothing that matters."""
    from buddy import a11y_windows
    pid = desktop.window_pid(notepad.id)
    names = a11y_windows.names(pid)
    button = next((name for name in names if name.lower() in ("maximize", "restore", "minimize")), None)
    assert button, names
    assert a11y.press(pid, button)


def test_the_start_menu_has_apps():
    found = apps.installed_apps()
    assert found and all(app.app_id for app in found)
    assert apps.find_app("files") and apps.find_app("edge")


def test_a_browser_is_there():
    assert apps.browser() and Path(apps.browser()).exists()


def test_known_folders_exist():
    for name in winsys.KNOWN_FOLDERS:
        assert winsys.known_folder(name).is_absolute(), name
    assert files.folder_path("downloads").is_absolute()


def test_it_knows_it_is_not_locked():
    assert winsys.is_locked() is False
    assert actions.lock_status() == "The laptop is unlocked"


def test_the_report_runs():
    report = actions.system_report()
    assert "Memory" in report and "Disk" in report and "CPU load" in report
    assert models.hardware()["memory_gb"] > 1


def test_a_battery_or_its_absence_is_reported():
    assert "attery" in winsys.battery()


def test_keeping_awake_starts_and_stops():
    process = winsys.keep_awake(30)
    assert _wait_for(lambda: winsys.is_keeping_awake(process.pid), 10)
    apps.end_tree(process.pid)
    assert _wait_for(lambda: process.poll() is not None, 10)


def test_commands_run_in_powershell():
    assert actions.run_command("Write-Output hello") == "hello"
    assert actions.run_readonly("whoami")


def test_dark_mode_can_change():
    assert winsys.set_option("dark_mode", True) == "Dark mode is on"
    assert winsys.set_option("dark_mode", False) == "Dark mode is off"


def test_media_controls_answer_even_with_nothing_playing():
    assert isinstance(media.players(), list)
    media.pause_all()


def test_the_recycle_bin_can_be_emptied():
    winsys.empty_recycle_bin()


def test_the_paths_are_windows_ones():
    assert "AppData" in str(config.DATA_DIR) or os.environ.get("BUDDY_DATA_DIR")
