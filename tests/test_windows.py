"""The Windows side of Buddy, run anywhere: Windows' own libraries are faked here, so this checks Buddy's logic.
Whether Windows really behaves that way is what test_windows_live.py checks, on a real Windows machine."""
import asyncio
import ctypes
import importlib
import re
import sys
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from buddy import actions, agent, apps, config, desktop, engine, fetch, files, location, media_windows, reminders, share, tts, winsys
from buddy import __main__ as command_line

START_MENU = [
    {"name": "Google Chrome", "id": "Chrome", "target": r"C:\Program Files\Google\Chrome\Application\chrome.exe"},
    {"name": "Microsoft Edge", "id": "MSEdge", "target": None},
    {"name": "File Explorer", "id": "Microsoft.Windows.Explorer", "target": None},
    {"name": "Word", "id": "Microsoft.Office.WINWORD.EXE.15", "target": r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"},
    {"name": "Calculator", "id": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", "target": None},
    {"name": "Notepad", "id": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App", "target": None},
]
NETWORKS = """
Interface name : Wi-Fi
There are 3 networks currently visible.

SSID 1 : Home Wi-Fi
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    BSSID 1                 : A4:BB:CC:00:11:22
         Signal             : 85%
         Radio type         : 802.11ax
    BSSID 2                 : 02:BB:CC:00:11:33
         Signal             : 40%

SSID 2 : 
    Network type            : Infrastructure
    BSSID 1                 : 10:20:30:40:50:60
         Signal             : 55%

SSID 3 : Cafe_nomap
    BSSID 1                 : 70:80:90:a0:b0:c0
         Signal             : 70%
"""


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(config, "WINDOWS", True)
    monkeypatch.setattr(config, "NO_WINDOW", {})
    monkeypatch.setattr(winsys, "start_apps", lambda: START_MENU)
    monkeypatch.setattr(apps, "_start_menu", (0.0, []))
    monkeypatch.setattr(winsys, "is_locked", lambda: False)
    monkeypatch.setattr(apps, "chrome", lambda: r"C:\chrome.exe")


@pytest.mark.parametrize("hour, expected", [(21, "9:05 PM"), (9, "9:05 AM"), (0, "12:05 AM"), (12, "12:05 PM"), (10, "10:05 AM")])
def test_clock_needs_no_linux_only_time_codes(hour, expected):
    assert config.clock(datetime(2026, 11, 2, hour, 5)) == expected


def test_no_linux_only_time_codes_are_left():
    for path in Path(config.__file__).parent.glob("*.py"):
        assert not re.search(r"%-[a-zA-Z]", path.read_text(encoding="utf-8")), path.name


def test_reminder_dates_read_the_same():
    moment = datetime(2026, 11, 9, 14, 30)
    assert reminders.spoken(moment, datetime(2026, 11, 2, 9, 0)) == "on Monday 9 November at 2:30 PM"


@pytest.mark.parametrize("spoken, name", [("word", "Word"), ("files", "File Explorer"), ("explorer", "File Explorer"),
                                          ("notes", "Notepad"), ("chrome", "Google Chrome"), ("calculator", "Calculator"),
                                          ("browser", "Google Chrome"), ("pdf viewer", "Microsoft Edge")])
def test_everyday_names_find_windows_apps(on_windows, spoken, name):
    assert apps.find_app(spoken).name == name


def test_browser_means_edge_without_chrome(on_windows, monkeypatch):
    monkeypatch.setattr(apps, "chrome", lambda: None)
    assert apps.find_app("browser").name == "Microsoft Edge"


def test_start_menu_is_read_once(on_windows, monkeypatch):
    reads = []
    monkeypatch.setattr(winsys, "start_apps", lambda: reads.append(1) or START_MENU)
    apps.find_app("word"), apps.find_app("excel"), apps.installed_apps()
    assert len(reads) == 1


def test_windows_belong_to_their_program(on_windows):
    word = apps.find_app("word")
    assert word.owns("winword OpusApp", "Report - Word")
    assert not word.owns("chrome Chrome_WidgetWin_1", "How to use Word - Google Chrome")


def test_store_apps_belong_by_title(on_windows):
    notepad, calculator = apps.find_app("notepad"), apps.find_app("calculator")
    assert notepad.owns("notepad Notepad", "notes.txt - Notepad")
    assert calculator.owns("applicationframehost ApplicationFrameWindow", "Calculator")
    assert not calculator.owns("applicationframehost ApplicationFrameWindow", "Settings")


def test_explorer_is_known_by_program(on_windows):
    assert apps.find_app("files").owns("explorer CabinetWClass", "Downloads")


def test_only_real_browsers_count_as_chrome(on_windows):
    assert apps.is_chrome("chrome Chrome_WidgetWin_1")
    assert apps.is_chrome("msedge Chrome_WidgetWin_1")
    assert not apps.is_chrome("code Chrome_WidgetWin_1")  # VS Code and every Electron app share this class


def test_session_env_leaves_out_what_breaks_programs(on_windows, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "x")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    env = apps.session_env()
    assert "PYTHONPATH" not in env and env["SYSTEMROOT"] == r"C:\Windows"


def test_opening_an_app_goes_through_the_start_menu(on_windows, monkeypatch):
    started = []
    monkeypatch.setattr(actions, "_spawn", lambda *cmd: started.append(cmd))
    monkeypatch.setattr(actions.desktop, "list_windows", lambda: [])
    assert actions.open("calculator") == "Opening Calculator"
    assert started == [("explorer.exe", "shell:AppsFolder\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App")]


def test_every_settings_panel_has_a_windows_page():
    assert set(actions._SETTINGS_PANELS.values()) <= set(winsys.SETTINGS_PAGES)


def test_every_option_is_handled_or_explained(on_windows, monkeypatch):
    opened = []
    monkeypatch.setattr(winsys.os, "startfile", opened.append, raising=False)
    for option in winsys.ONLY_IN_SETTINGS:
        assert "opened its page" in actions.set_option(option, True)
    assert opened == ["ms-settings:network-airplanemode", "ms-settings:nightlight", "ms-settings:notifications"]
    monkeypatch.setattr(winsys, "_set_radio", lambda wifi, on: None)
    monkeypatch.setattr(winsys, "_set_dark_mode", lambda on: None)
    monkeypatch.setattr(winsys, "_set_microphone", lambda on: None)
    assert set(actions._OPTIONS) == set(winsys.ONLY_IN_SETTINGS) | {"wifi", "bluetooth", "dark_mode", "microphone"}
    assert actions.set_option("wifi", False) == "Wi-Fi is off"
    assert actions.set_option("dark_mode", True) == "Dark mode is on"
    with pytest.raises(LookupError):
        actions.set_option("warp_drive", True)


def test_power_profiles_match_the_commands(on_windows, monkeypatch):
    chosen = []
    monkeypatch.setattr(winsys, "set_power_plan", chosen.append)
    assert actions.power_profile("power-saver") == "Power saver mode is on"
    assert chosen == ["power-saver"] and "power-saver" in agent.SYSTEM_PROMPT
    assert set(winsys.POWER_PLANS) == {"performance", "balanced", "power-saver"}


def test_while_locked_screen_actions_say_why(on_windows, monkeypatch):
    monkeypatch.setattr(winsys, "is_locked", lambda: True)
    for name in actions.NEEDS_DESKTOP:
        assert name in actions.REGISTRY
        with pytest.raises(RuntimeError, match="until someone signs in"):
            actions.run(name)


def test_while_locked_other_actions_still_work(on_windows, monkeypatch):
    monkeypatch.setattr(winsys, "is_locked", lambda: True)
    assert actions.run("lock_status") == "The laptop is locked"
    assert actions.run("reply", text="hi") == "hi"


def test_unlock_explains_what_windows_allows(on_windows, monkeypatch):
    monkeypatch.setattr(winsys, "is_locked", lambda: True)
    assert "signing in" in actions.unlock_screen()
    monkeypatch.setattr(winsys, "is_locked", lambda: False)
    assert actions.unlock_screen() == "The laptop is already unlocked"


@pytest.mark.parametrize("command", ["ls", "df -h", "del report.txt", "ipconfig /release", "tasklist; rm x", "shutdown /s"])
def test_read_only_commands_refuse_everything_else(on_windows, command):
    assert actions.run_readonly(command).startswith("refused")


def test_read_only_commands_are_windows_ones(on_windows, monkeypatch):
    ran = []
    monkeypatch.setattr(actions, "_run", lambda parts, shell: ran.append(parts) or "ok")
    assert actions.run_readonly("tasklist") == "ok" and ran == [["tasklist"]]


def test_commands_run_in_powershell(on_windows, monkeypatch):
    calls = []
    done = types.SimpleNamespace(stdout="hello", stderr="", returncode=0)
    monkeypatch.setattr(actions.subprocess, "run", lambda command, **kw: calls.append((command, kw)) or done)
    assert actions.run_command("Get-Date") == "hello"
    command, options = calls[0]
    assert command[0] == "powershell" and command[-1] == "Get-Date" and not options["shell"]


def test_agent_prompt_has_nothing_only_true_on_linux():
    """What agent.py swaps on Windows must still be in the prompt, or the swap silently stops working."""
    for wanted in ("a Linux GNOME desktop", '{"action":"run_readonly","command":"df -h"}', "installs software or uses sudo"):
        assert wanted in agent.SYSTEM_PROMPT or config.WINDOWS


def test_gnome_shortcuts_become_windows_ones(monkeypatch):
    pressed = []
    fake = types.SimpleNamespace(press=pressed.append, release=lambda key: None)
    monkeypatch.setattr(desktop, "Keyboard", lambda: fake)
    monkeypatch.setattr(desktop, "WINDOWS", True)
    desktop.press_keys("super+v")
    assert pressed == [desktop.Key.cmd, "n"]  # notifications, where plain Win+V would open the clipboard
    pressed.clear()
    desktop.press_keys("ctrl+c")
    assert pressed == [desktop.Key.ctrl, "c"]


def test_every_windows_shortcut_can_be_pressed(monkeypatch):
    monkeypatch.setattr(desktop, "Keyboard", lambda: types.SimpleNamespace(press=lambda key: None, release=lambda key: None))
    monkeypatch.setattr(desktop, "WINDOWS", True)
    for combo in desktop.WINDOWS_SHORTCUTS:
        desktop.press_keys(combo)


@pytest.mark.parametrize("name, safe", [("report: final?.pdf", "report_ final_.pdf"), ("a.txt:hidden", "a.txt_hidden"),
                                        ("NUL.txt", "_NUL.txt"), ("con", "_con"), ("COM1.log", "_COM1.log"),
                                        ("console.txt", "console.txt"), ("..\\..\\evil.exe", "evil.exe"), ("ok.pdf", "ok.pdf")])
def test_names_windows_rejects_are_made_safe(on_windows, name, safe):
    assert fetch._safe_name(name) == safe


def test_linux_keeps_its_file_names(monkeypatch):
    monkeypatch.setattr(config, "WINDOWS", False)
    assert fetch._safe_name("report: final?.pdf") == "report: final?.pdf"


def _engine_while_locked(monkeypatch, on_windows_fixture=None):
    monkeypatch.setattr(winsys, "is_locked", lambda: True)
    monkeypatch.setattr(engine.agent, "run", lambda *a, **k: pytest.fail("the agent can't see a locked Windows"))
    buddy = engine.Engine.__new__(engine.Engine)
    monkeypatch.setattr(buddy, "_talk", lambda text: f"talked about {text}", raising=False)
    return buddy


def test_locked_windows_still_answers_questions(on_windows, monkeypatch):
    assert _engine_while_locked(monkeypatch)._run_agent("what is the capital of France") == "talked about what is the capital of France"


def test_locked_windows_says_why_it_cannot_act(on_windows, monkeypatch):
    assert _engine_while_locked(monkeypatch)._run_agent("rearrange my windows") == actions.LOCKED_ON_WINDOWS


def test_phone_is_told_the_screen_is_locked(on_windows, monkeypatch):
    monkeypatch.setattr(winsys, "is_locked", lambda: True)
    monkeypatch.setattr(desktop, "frame_jpeg", lambda: pytest.fail("Windows hides the screen while locked"))
    session = share.Session("token", False, expires_at=0)
    assert share.Share.frame(types.SimpleNamespace(), session)[:2] == b"\xff\xd8"


def test_pythonw_output_goes_to_a_log(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    command_line._keep_output()
    print("Telugu: తెలుగు")
    sys.stdout.flush()
    log = sys.stdout
    monkeypatch.undo()
    log.close()
    assert "తెలుగు" in (tmp_path / "buddy.log").read_text(encoding="utf-8")


def test_scripts_for_windows_are_plain_text():
    """Windows PowerShell misreads any other character in a file with no byte order mark."""
    root = Path(config.__file__).parent.parent
    scripts = [*root.glob("scripts/*.ps1"), *root.glob("buddy/*.ps1")]
    assert len(scripts) == 5
    for script in scripts:
        script.read_bytes().decode("ascii")


def test_both_installers_fetch_the_same_things():
    scripts = Path(config.__file__).parent.parent / "scripts"
    linux, windows = (scripts / "install-local-ai.sh").read_text(), (scripts / "install-local-ai.ps1").read_text()
    assert re.search(r"VERSION=(\S+)", linux).group(1) == re.search(r"\$Version = '(\S+)'", windows).group(1)
    assert "gemma4:e4b-it-q4_K_M" in linux and "gemma4:e4b-it-q4_K_M" in windows


def test_a_cloudflared_is_pinned_for_each_system():
    source = Path(share.__file__).read_text(encoding="utf-8")
    assert "cloudflared-windows-amd64.exe" in source and "cloudflared-linux-amd64" in source
    assert len(re.findall(r'CLOUDFLARED_SHA256 = "[0-9a-f]{64}"', source)) == 2


def test_wifi_list_is_read(on_windows):
    assert winsys.wifi_networks(NETWORKS) == [("Home Wi-Fi", "a4:bb:cc:00:11:22", 85), ("Home Wi-Fi", "02:bb:cc:00:11:33", 40),
                                              ("", "10:20:30:40:50:60", 55), ("Cafe_nomap", "70:80:90:a0:b0:c0", 70)]


def test_location_filters_windows_networks_like_linux_ones(on_windows, monkeypatch):
    in_range = winsys.wifi_networks(NETWORKS)
    monkeypatch.setattr(winsys, "wifi_networks", lambda: in_range)
    found = location.nearby_networks()
    # the made-up address and the one that opted out are gone, strongest first
    assert [network["macAddress"] for network in found] == ["a4:bb:cc:00:11:22", "10:20:30:40:50:60"]
    assert found[0]["signalStrength"] == 85 // 2 - 100


def test_connected_wifi_ignores_the_base_station_line():
    interfaces = "    Name : Wi-Fi\n    SSID                   : Home Wi-Fi \n    AP BSSID               : aa:bb:cc:00:11:22\n"
    assert winsys.connected_wifi(interfaces) == "Home Wi-Fi"
    assert winsys.connected_wifi("    Name : Wi-Fi\n    State : disconnected\n") == "not connected"


def test_app_data_is_private(monkeypatch, tmp_path):
    monkeypatch.setattr(files, "HOME", tmp_path)
    (tmp_path / "AppData/Local/Google/Chrome").mkdir(parents=True)
    (tmp_path / "AppData/Local/Google/Chrome/Login Data").write_text("secret")
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents/report.txt").write_text("hello")
    assert files.is_private(tmp_path / "AppData/Local/Google/Chrome/Login Data")
    assert not files.is_private(tmp_path / "Documents/report.txt")
    assert [path.name for path in files.find("login data")] == []


def test_folders_follow_windows_when_it_moved_them(on_windows, monkeypatch):
    moved = Path("/OneDrive/Documents")
    monkeypatch.setattr(winsys, "known_folder", lambda name: moved if name == "documents" else None)
    assert files.folder_path("documents") == moved
    assert files.folder_path("Documents") == moved
    assert files.folder_path("music") == files.HOME / "Music"
    assert files.folder_path("home") == files.HOME


class FakeWinSound(types.ModuleType):
    SND_FILENAME, SND_ASYNC, SND_NODEFAULT = 1, 2, 4

    def __init__(self):
        super().__init__("winsound")
        self.calls = []

    def PlaySound(self, sound, flags):
        self.calls.append(sound)


def _speech(path):
    import wave
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1), wav.setsampwidth(2), wav.setframerate(8000), wav.writeframes(b"\0\0" * 400)  # 50 ms


def test_windows_player_waits_for_the_speech_then_lets_go_of_the_file(monkeypatch, tmp_path):
    sound = FakeWinSound()
    monkeypatch.setitem(sys.modules, "winsound", sound)
    _speech(tmp_path / "a.wav")
    tts._WindowsPlayer(str(tmp_path / "a.wav")).wait()
    assert sound.calls == [str(tmp_path / "a.wav"), None]


def test_windows_player_can_be_stopped(monkeypatch, tmp_path):
    sound = FakeWinSound()
    monkeypatch.setitem(sys.modules, "winsound", sound)
    _speech(tmp_path / "a.wav")
    player = tts._WindowsPlayer(str(tmp_path / "a.wav"))
    player._ends_at += 60
    player.terminate()
    player.wait()  # returns straight away
    assert sound.calls[-1] is None


class _Awaitable:
    def __init__(self, value=None):
        self.value = value

    def __await__(self):
        return asyncio.sleep(0, self.value).__await__()


class FakeSession:
    def __init__(self, name, status, title="Song"):
        self.source_app_user_model_id, self.status, self.song, self.done = name, status, title, []

    def get_playback_info(self):
        return types.SimpleNamespace(playback_status=self.status)

    def try_get_media_properties_async(self):
        return _Awaitable(types.SimpleNamespace(title=self.song))

    def __getattr__(self, name):
        if name.startswith("try_"):
            return lambda: (self.done.append(name), _Awaitable(True))[1]
        raise AttributeError(name)


@pytest.fixture
def sessions(monkeypatch):
    found = [FakeSession("Spotify.exe", 5, "Paused Song"), FakeSession("Chrome", 4, "Playing Song")]
    control = types.ModuleType("winrt.windows.media.control")
    control.GlobalSystemMediaTransportControlsSessionManager = types.SimpleNamespace(
        request_async=lambda: _Awaitable(types.SimpleNamespace(get_sessions=lambda: found)))
    control.GlobalSystemMediaTransportControlsSessionPlaybackStatus = types.SimpleNamespace(PLAYING=4)
    monkeypatch.setitem(sys.modules, "winrt.windows.media.control", control)
    monkeypatch.setattr(media_windows, "on_com_thread", lambda work: work())
    return found


def test_media_finds_what_is_playing(sessions):
    assert media_windows.players() == ["Chrome", "Spotify.exe"]
    assert media_windows.active() == "Chrome"
    assert media_windows.is_playing("Chrome") and not media_windows.is_playing("Spotify.exe")
    assert media_windows.title("Chrome") == "Playing Song"
    assert media_windows.title("gone") == ""


def test_media_commands_reach_the_right_player(sessions):
    media_windows.command("Chrome", "Next")
    media_windows.pause_all()
    assert sessions[1].done == ["try_skip_next_async", "try_pause_async"]
    assert sessions[0].done == []  # it wasn't playing


def test_every_media_command_the_actions_use_exists():
    source = Path(actions.__file__).read_text(encoding="utf-8")
    for method in ("Play", "Next", "Previous"):
        assert f'"{method}"' in source and method in media_windows.METHODS


def _fake_speakers(monkeypatch, level=0.5):
    speakers = MagicMock()
    speakers.GetMasterVolumeLevelScalar.return_value = level
    speakers.GetMute.return_value = False
    monkeypatch.setattr(winsys, "_end_point", lambda microphone=False: speakers)
    monkeypatch.setattr(winsys, "on_com_thread", lambda work: work())
    return speakers


@pytest.mark.parametrize("change, level, start, expected", [("up", None, 0.5, 0.6), ("down", None, 0.5, 0.4), ("set", 80, 0.5, 0.8),
                                                            ("up", None, 0.95, 1.0), ("down", None, 0.05, 0.0), ("set", 250, 0.5, 1.0)])
def test_volume_stays_in_range(monkeypatch, change, level, start, expected):
    speakers = _fake_speakers(monkeypatch, start)
    winsys.set_volume(change, level)
    assert speakers.SetMasterVolumeLevelScalar.call_args[0][0] == pytest.approx(expected)


def test_turning_it_up_unmutes(monkeypatch):
    speakers = _fake_speakers(monkeypatch)
    winsys.set_volume("up", None)
    speakers.SetMute.assert_called_with(False, None)


def test_volume_action_says_what_linux_says(on_windows, monkeypatch):
    _fake_speakers(monkeypatch)
    assert actions.volume("set", 40) == "Volume 40%"
    assert actions.volume("up") == "Volume up"
    assert actions.volume("toggle") == "Toggled mute"


class FakeUser32:
    """Just enough of Windows' window list: id -> (title, class, visible, extra style, pid)."""

    def __init__(self, windows, front=None):
        self.windows, self.front, self.moved, self.posted, self.shown, self.iconic = windows, front, [], [], [], set()

    def EnumWindows(self, visit, _):
        for window_id in self.windows:
            visit(window_id, 0)

    def IsWindowVisible(self, window_id):
        return self.windows[window_id][2]

    def GetWindowLongW(self, window_id, _):
        return self.windows[window_id][3]

    def GetWindowTextLengthW(self, window_id):
        return len(self.windows[window_id][0])

    def GetWindowTextW(self, window_id, buffer, _):
        buffer.value = self.windows[window_id][0]

    def GetClassNameW(self, window_id, buffer, _):
        buffer.value = self.windows[window_id][1]

    def GetWindowThreadProcessId(self, window_id, pid):
        pid._obj.value = self.windows[window_id][4]

    def GetForegroundWindow(self):
        return self.front

    def IsIconic(self, window_id):
        return window_id in self.iconic

    def ShowWindow(self, window_id, how):
        self.shown.append((window_id, how))

    def SendInput(self, *_):
        return 1

    def SetForegroundWindow(self, window_id):
        self.moved.append(window_id)
        return True

    def PostMessageW(self, *args):
        self.posted.append(args)


@pytest.fixture
def desktop_windows(monkeypatch):
    """The real module, loaded against fake system libraries, so it never sees Linux's lack of them."""
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: MagicMock(), raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    monkeypatch.setattr(ctypes, "HRESULT", ctypes.c_long, raising=False)
    sys.modules.pop("buddy.desktop_windows", None)
    module = importlib.import_module("buddy.desktop_windows")
    monkeypatch.setattr(module, "_program", lambda window_id: {1: "code", 7: "explorer"}.get(window_id, ""))
    monkeypatch.setattr(module, "_is_cloaked", lambda window_id: window_id == 5)
    monkeypatch.setattr(module, "ENUM_WINDOWS", lambda visit: visit)
    yield module
    sys.modules.pop("buddy.desktop_windows", None)


WINDOW_LIST = {1: ("main.py - Visual Studio Code", "Chrome_WidgetWin_1", True, 0, 11),
               2: ("Hidden helper", "Helper", False, 0, 12),
               3: ("Tray popup", "Tray", True, 0x80, 13),
               4: ("Program Manager", "Progman", True, 0, 14),
               5: ("Suspended Store app", "ApplicationFrameWindow", True, 0, 15),
               6: ("", "NoTitle", True, 0, 16),
               7: ("Downloads", "CabinetWClass", True, 0, 17)}


def test_window_list_is_only_what_a_person_sees(desktop_windows, monkeypatch):
    monkeypatch.setattr(desktop_windows, "user32", FakeUser32(WINDOW_LIST))
    windows = desktop_windows.list_windows()
    assert [(w.id, w.title, w.cls) for w in windows] == [(1, "main.py - Visual Studio Code", "code Chrome_WidgetWin_1"),
                                                         (7, "Downloads", "explorer CabinetWClass")]


def test_front_window_of_nothing_is_none(desktop_windows, monkeypatch):
    monkeypatch.setattr(desktop_windows, "user32", FakeUser32(WINDOW_LIST, front=4))
    assert desktop_windows.active_window() is None
    monkeypatch.setattr(desktop_windows, "user32", FakeUser32(WINDOW_LIST, front=1))
    assert desktop_windows.active_window().id == 1


def test_activate_restores_a_minimized_window(desktop_windows, monkeypatch):
    user32 = FakeUser32(WINDOW_LIST)
    user32.iconic.add(1)
    monkeypatch.setattr(desktop_windows, "user32", user32)
    desktop_windows.activate(1)
    desktop_windows.activate(7)
    assert user32.shown == [(1, desktop_windows.SW_RESTORE)] and user32.moved == [1, 7]


def test_closing_asks_politely(desktop_windows, monkeypatch):
    user32 = FakeUser32(WINDOW_LIST)
    monkeypatch.setattr(desktop_windows, "user32", user32)
    desktop_windows.close(1)
    assert user32.posted == [(1, desktop_windows.WM_CLOSE, 0, 0)]  # so an app can still ask about unsaved work


@pytest.mark.skipif(sys.platform == "win32", reason="the X11 side needs Linux")
def test_both_desktops_offer_the_same_functions(desktop_windows):
    from buddy import desktop_x11
    names = ("activate", "active_window", "close", "list_windows", "maximize", "minimize", "window_pid", "window_rect")
    for name in names:
        assert callable(getattr(desktop_windows, name)) and callable(getattr(desktop_x11, name))


@pytest.mark.parametrize("module", ["engine", "listen", "doctor", "dictation", "actions", "server", "__main__", "whatsapp"])
def test_every_entry_point_imports_on_its_own(module):
    """buddy check loads the engine first, which once failed when actions needed dictation, which needs the engine."""
    import subprocess
    done = subprocess.run([sys.executable, "-c", f"import buddy.{module}"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-800:]
