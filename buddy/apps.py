"""Finds installed applications through their .desktop entries, or the Start menu on Windows."""
import configparser
import difflib
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from . import config, winsys

SEARCH_DIRS = [
    Path.home() / ".local/share/applications",
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path("/var/lib/snapd/desktop/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
]

# What people say -> the name the app is installed under.
SPOKEN_NAMES = {
    "vs code": "visual studio code", "vscode": "visual studio code",
    "visual code": "visual studio code", "code": "visual studio code",
    "chrome": "google chrome", "browser": "google chrome",
    "terminal": "terminal", "command line": "terminal", "console": "terminal",
    "files": "files", "file manager": "files", "explorer": "files",
    "calculator": "calculator", "settings": "settings",
    "text editor": "text editor", "notepad": "text editor", "editor": "text editor", "notes": "text editor",
    "word": "libreoffice writer", "microsoft word": "libreoffice writer",
    "excel": "libreoffice calc", "microsoft excel": "libreoffice calc", "spreadsheet": "libreoffice calc",
    "powerpoint": "libreoffice impress", "microsoft powerpoint": "libreoffice impress",
    "email": "thunderbird mail", "task manager": "system monitor", "photos": "shotwell",
    "music": "rhythmbox", "music player": "rhythmbox", "pdf viewer": "document viewer",
    "store": "app center", "app store": "app center", "software center": "app center",
}

# The same words on Windows, where the apps have other names and Office is usually the real one.
WINDOWS_SPOKEN_NAMES = {
    "files": "file explorer", "file manager": "file explorer", "explorer": "file explorer",
    "text editor": "notepad", "notepad": "notepad", "editor": "notepad", "notes": "notepad",
    "command line": "command prompt", "console": "command prompt",
    "word": "word", "microsoft word": "word", "excel": "excel", "microsoft excel": "excel", "spreadsheet": "excel",
    "powerpoint": "powerpoint", "microsoft powerpoint": "powerpoint",
    "email": "outlook", "task manager": "task manager", "photos": "photos",
    "music": "media player", "music player": "media player", "pdf viewer": "microsoft edge",
    "store": "microsoft store", "app store": "microsoft store", "software center": "microsoft store",
}
# Packaged apps have no shortcut to read their program's name from.
WINDOWS_PROGRAMS = {"file explorer": "explorer", "terminal": "windowsterminal", "windows terminal": "windowsterminal",
                    "microsoft edge": "msedge", "command prompt": "cmd"}
# On Windows, what's left out of Buddy's own environment for the programs it starts: OCR's Qt path crashes other
# Qt apps, the Electron flag stops VS Code opening, and the Python ones confuse any other Python.
BREAKS_PROGRAMS = {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_PLUGIN_PATH", "ELECTRON_RUN_AS_NODE"}
START_MENU_SECONDS = 300  # it takes a second or two to read, and is asked for on every "open"
_start_menu = (0.0, [])

SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "mail": "https://mail.google.com",
    "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com",
    "google maps": "https://maps.google.com",
    "maps": "https://maps.google.com",
    "calendar": "https://calendar.google.com",
    "claude": "https://claude.ai",
    "chatgpt": "https://chatgpt.com",
    "chat gpt": "https://chatgpt.com",
    "github": "https://github.com",
    "gitlab": "https://gitlab.com",
    "linkedin": "https://www.linkedin.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "twitter": "https://x.com",
    "reddit": "https://www.reddit.com",
    "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.de",
    "wikipedia": "https://www.wikipedia.org",
    "whatsapp": "https://web.whatsapp.com",
    "spotify": "https://open.spotify.com",
}


def session_env() -> dict:
    """The environment of the GNOME session, for anything Buddy starts.

    Buddy's own carries things that break other programs: OCR's Qt path crashes VLC, a sourced
    ROS setup clashes with app libraries, and a parent shell may hold secrets.
    """
    if config.WINDOWS:  # no session to ask, and programs there can't start without most of what's set
        return {key: value for key, value in os.environ.items() if key.upper() not in BREAKS_PROGRAMS}
    try:
        lines = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                               text=True, timeout=5, check=True).stdout.splitlines()
        env = dict(line.split("=", 1) for line in lines if "=" in line)
    except (OSError, subprocess.SubprocessError):
        env = {}
    if "DISPLAY" in env:
        return env
    keep = ("DISPLAY", "XAUTHORITY", "HOME", "USER", "LANG", "XDG_RUNTIME_DIR", "XDG_DATA_DIRS",
            "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "DBUS_SESSION_BUS_ADDRESS")
    return {key: os.environ[key] for key in keep if key in os.environ} | {"PATH": "/usr/local/bin:/usr/bin:/bin:/snap/bin"}


def _program_folders() -> list[Path]:
    names = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
    return [Path(os.environ[name]) for name in names if os.environ.get(name)]


def chrome() -> str | None:
    if not config.WINDOWS:
        return shutil.which("google-chrome")
    found = (folder / "Google/Chrome/Application/chrome.exe" for folder in _program_folders())
    return next((str(path) for path in found if path.exists()), None)


def browser() -> str | None:
    """A browser that can show Buddy's page and run WhatsApp. Every Windows has Edge, which can do both."""
    if chrome() or not config.WINDOWS:
        return chrome()
    found = (folder / "Microsoft/Edge/Application/msedge.exe" for folder in _program_folders())
    return next((str(path) for path in found if path.exists()), None)


def browser_channel() -> str:
    """Which of them, in the words of the browser automation library."""
    return "msedge" if config.WINDOWS and not chrome() else "chrome"


def is_chrome(window_class: str) -> bool:
    if config.WINDOWS:  # by program, as VS Code and every other Electron app share Chrome's window class
        return window_class.lower().split(" ")[0] in ("chrome", "msedge")
    return "google-chrome" in window_class.lower()


def tool(name: str) -> str:
    """Where a helper program is. LibreOffice and Poppler on Windows often aren't on the PATH Buddy started with."""
    if shutil.which(name) or not config.WINDOWS:
        return name
    usual = {"soffice": "LibreOffice/program/soffice.exe"}
    found = (folder / usual[name] for folder in _program_folders()) if name in usual else ()
    return next((str(path) for path in found if path.exists()), name)


def end_tree(pid: int, force: bool = False) -> None:
    """Ends a program Buddy started, along with anything it started in turn. Raises ProcessLookupError if it's gone."""
    if config.WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=10, **config.NO_WINDOW)
    else:
        os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)


@dataclass
class App:
    name: str
    path: Path
    wm_class: str
    exec_name: str = ""
    app_id: str = ""  # Windows: what the Start menu knows it by

    def owns(self, window_class: str, title: str = "") -> bool:
        """Whether a window belongs to this app, going by its X11 class or its program on Windows."""
        classes = set(window_class.lower().split())
        if config.WINDOWS:
            if self.exec_name:
                return self.exec_name in classes
            # Store apps can all run inside one shared program, so only "Report - Notepad" says whose window it is.
            return title.lower().endswith(self.name.lower())
        if self.wm_class:  # exact when declared: Writer and Calc share a program but not a class
            return self.wm_class.lower() in classes
        stem = self.path.stem.lower()
        return bool(classes & {stem, stem.rsplit(".", 1)[-1], self.exec_name})


def _start_menu_apps() -> list[App]:
    global _start_menu
    if time.monotonic() - _start_menu[0] > START_MENU_SECONDS or not _start_menu[1]:
        found = []
        for entry in winsys.start_apps():
            target = entry.get("target") or ""
            program = PureWindowsPath(target).stem.lower() if target else WINDOWS_PROGRAMS.get(entry["name"].lower(), "")
            found.append(App(entry["name"], Path(entry["id"]), "", program, entry["id"]))
        _start_menu = (time.monotonic(), found)
    return _start_menu[1]


def installed_apps() -> list[App]:
    if config.WINDOWS:
        return _start_menu_apps()
    apps = {}
    for directory in SEARCH_DIRS:
        for path in sorted(directory.glob("*.desktop")) if directory.is_dir() else []:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(path, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            hidden = entry.get("NoDisplay", "").lower() == "true" or entry.get("Hidden", "").lower() == "true"
            if hidden or entry.get("Type") != "Application" or "Name" not in entry:
                continue
            try:
                exec_name = Path(shlex.split(entry.get("Exec", ""))[0]).name.lower()
            except (ValueError, IndexError):
                exec_name = ""
            apps.setdefault(path.name, App(entry["Name"], path, entry.get("StartupWMClass", ""), exec_name))
    return list(apps.values())


def find_app(spoken: str) -> App | None:
    spoken = spoken.lower().strip()
    names = {**SPOKEN_NAMES, **WINDOWS_SPOKEN_NAMES} if config.WINDOWS else SPOKEN_NAMES
    wanted = names.get(spoken, spoken).replace("&", "and")
    if config.WINDOWS and spoken == "browser" and not chrome():
        wanted = "microsoft edge"
    apps = installed_apps()
    names = {app.name.lower().replace("&", "and"): app for app in apps}
    if wanted in names:
        return names[wanted]
    whole_word = re.compile(rf"\b{re.escape(wanted)}\b")
    partial = [app for app in apps if whole_word.search(app.name.lower().replace("&", "and"))]
    if partial:
        return min(partial, key=lambda app: len(app.name))
    close = difflib.get_close_matches(wanted, names, n=1, cutoff=0.82)
    return names[close[0]] if close else None


def site_url(spoken: str) -> str | None:
    """A known site, or a URL when the words look like a domain ('news dot com')."""
    name = spoken.lower().strip()
    if name in SITES:
        return SITES[name]
    domain = re.sub(r"\s+dot\s+", ".", name).replace(" ", "")
    if re.fullmatch(r"(https?://)?[\w-]+(\.[\w-]+)+(/\S*)?", domain):
        return domain if domain.startswith("http") else f"https://{domain}"
    return None
