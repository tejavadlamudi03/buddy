"""Controls whatever is playing through MPRIS: no focus change, keystrokes or network."""
import re
import subprocess
import time

BUS = "/org/freedesktop/DBus"
PLAYER_PATH = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
TITLE = re.compile(r"""'xesam:title': <(['"])(.*?)\1>""")


def _gdbus(dest: str, path: str, method: str, *args: str) -> str:
    return subprocess.run(
        ["gdbus", "call", "--session", "--dest", dest, "--object-path", path, "--method", method, *args],
        capture_output=True, text=True, timeout=3, check=True,
    ).stdout


def _get(player: str, prop: str) -> str:
    return _gdbus(player, PLAYER_PATH, "org.freedesktop.DBus.Properties.Get", PLAYER, prop)


def players() -> list[str]:
    names = _gdbus("org.freedesktop.DBus", BUS, "org.freedesktop.DBus.ListNames")
    return sorted(set(re.findall(r"org\.mpris\.MediaPlayer2\.[\w.-]+", names)))


def is_playing(player: str) -> bool:
    return "Playing" in _get(player, "PlaybackStatus")


def active() -> str | None:
    """The player that's playing now, or else the first one that exists."""
    found = players()
    return next((player for player in found if is_playing(player)), found[0] if found else None)


def title(player: str) -> str:
    match = TITLE.search(_get(player, "Metadata"))
    return match.group(2) if match else ""


def command(player: str, method: str) -> None:
    _gdbus(player, PLAYER_PATH, f"{PLAYER}.{method}")


def pause_all() -> None:
    for player in players():
        if is_playing(player):
            command(player, "Pause")


def wait_for_new_title(player: str, before: str, seconds: float = 5) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        current = title(player)
        if current and current != before:
            return current
        time.sleep(0.2)
    return ""
