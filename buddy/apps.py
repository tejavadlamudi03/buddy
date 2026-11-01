"""Finds installed applications through their .desktop entries."""
import configparser
import difflib
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

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


@dataclass
class App:
    name: str
    path: Path
    wm_class: str
    exec_name: str = ""

    def owns(self, window_class: str) -> bool:
        """Whether a window belongs to this app, going by its X11 class."""
        classes = set(window_class.lower().split())
        if self.wm_class:  # exact when declared: Writer and Calc share a program but not a class
            return self.wm_class.lower() in classes
        stem = self.path.stem.lower()
        return bool(classes & {stem, stem.rsplit(".", 1)[-1], self.exec_name})


def installed_apps() -> list[App]:
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
    wanted = SPOKEN_NAMES.get(spoken.lower().strip(), spoken.lower().strip()).replace("&", "and")
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
