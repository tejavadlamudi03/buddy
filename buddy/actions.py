"""Everything Buddy can do to the laptop. Nothing outside this registry can ever run."""
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from . import a11y, apps, awake, config, desktop, fetch, files, guests, location, media, ocr, reminders, share, whatsapp

RISKY = {"shutdown", "restart", "suspend", "logout"}
READ_ONLY_COMMANDS = {"ls", "df", "du", "free", "uptime", "uname", "whoami", "date", "hostname", "lsblk", "nvidia-smi", "ps"}
MAX_SEND_BYTES = 50 * 1000 * 1000
COMMAND_SECONDS = 120
MAX_COMMAND_OUTPUT = 3000
# Never by voice or from the Buddy page: at a lock screen, anyone in the room could say it.
WHATSAPP_ONLY = {"unlock_screen"}
ANSWER_ON_WHATSAPP = {"download", "convert_file", "remind"}  # asked from a phone, so that's where the result goes


class UnknownTarget(LookupError):
    """No app or site by that name. The agent may still find it on screen."""


REGISTRY = {}
_last_screenshot = None


def action(fn):
    REGISTRY[fn.__name__] = fn
    return fn


def run(action_name: str, /, **args) -> str:
    if action_name not in REGISTRY:
        raise ValueError(f"unknown action: {action_name}")
    return REGISTRY[action_name](**args) or ""


def risk_of(step: dict) -> str | None:
    """What a risky step would do, in words for the confirmation question."""
    if step["action"] == "power" and step.get("mode") in RISKY:
        return step["mode"]
    if step["action"] == "empty_trash":
        return "empty the trash"
    if step["action"] == "unlock_screen":
        return "unlock the laptop"
    if step["action"] == "run_command":  # read back, since speech recognition can mishear it
        return f"run this command: {step.get('command')}"
    if step["action"] == "set_option":  # both cut the internet, and any remote session with it
        if step.get("option") == "wifi" and not step.get("on"):
            return "turn off Wi-Fi"
        if step.get("option") == "airplane" and step.get("on"):
            return "turn on airplane mode"
    return None


def is_risky(step: dict) -> bool:
    return risk_of(step) is not None


def _spawn(*cmd: str) -> None:
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, env=apps.session_env())


def _browse(url: str) -> None:
    browser = shutil.which("google-chrome") or shutil.which("firefox") or "xdg-open"
    _spawn(browser, url)


def _window_for(name: str):
    """A window of the named app, by its class first and then by its title."""
    app = apps.find_app(name)
    windows = desktop.list_windows()
    if app:
        for window in windows:
            if app.owns(window.cls):
                return window
    needle = name.lower().strip()
    for window in windows:
        if desktop.BUDDY_TITLE not in window.title and needle in window.title.lower():
            return window
    return None


@action
def open(name: str, search_fallback: bool = False) -> str:
    if files.looks_like_file(name):
        return open_file(name)
    url = apps.site_url(name)
    app = apps.find_app(name)
    if app:
        for window in desktop.list_windows():
            if app.owns(window.cls):
                desktop.activate(window.id)
                return f"Switched to {app.name}"
        _spawn("gio", "launch", str(app.path))
        return f"Opening {app.name}"
    if url:
        _browse(url)
        return f"Opening {name}"
    if search_fallback:
        return google_search(name)
    raise UnknownTarget(f"I couldn't find an app or site called {name}")


@action
def open_url(url: str) -> str:
    if not re.match(r"https?://", url):
        url = f"https://{url}"
    _browse(url)
    return f"Opening {url}"


@action
def google_search(query: str) -> str:
    _browse("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))
    return f"Searching for {query}"


@action
def youtube_search(query: str) -> str:
    _browse("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query))
    return f"Searching YouTube for {query}"


def first_youtube_video(query: str) -> str | None:
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
        "Accept-Language": "en",
        "Cookie": "SOCS=CAI",  # declines the EU consent interstitial, which has no results
    })
    with urllib.request.urlopen(request, timeout=10) as response:
        match = re.search(r'"videoId":"([\w-]{11})"', response.read().decode(errors="replace"))
    return match.group(1) if match else None


@action
def youtube_play(query: str) -> str:
    try:
        video = first_youtube_video(query)
    except OSError:
        video = None
    if video is None:
        return youtube_search(query)
    url = f"https://www.youtube.com/watch?v={video}&autoplay=1"
    media.pause_all()  # a new song must never play on top of the old one
    if not _navigate_youtube_tab(url):
        _browse(url)
    return f"Playing {query}"


def _navigate_youtube_tab(url: str) -> bool:
    """Reuses the YouTube tab on show, so songs don't pile up as tabs."""
    window = next((w for w in desktop.list_windows()
                   if "youtube" in w.title.lower() and "google-chrome" in w.cls.lower()), None)
    if window is None:
        return False
    desktop.activate(window.id)
    time.sleep(0.3)
    active = desktop.active_window()
    if active is None or active.id != window.id:  # never type a URL into some other app
        return False
    desktop.press_keys("ctrl+l")
    time.sleep(0.1)
    desktop.type_text(url)
    desktop.press_keys("enter")
    return True


def _skip(method: str, fallback_key: str, fallback_message: str) -> str:
    player = media.active()
    if player is None:
        desktop.focus_target()
        desktop.press_keys(fallback_key)
        return fallback_message
    before = media.title(player)
    media.command(player, method)
    current = media.wait_for_new_title(player, before)
    return f"Now playing {_spoken_title(current)}" if current else fallback_message


def _spoken_title(title: str) -> str:
    """YouTube titles run long: drop the '(Official Video)' and '| Artist | Label' parts."""
    short = re.sub(r"\s*[(\[].*?[)\]]", "", title).split("|")[0].strip()
    return short or title


@action
def next_track() -> str:
    return _skip("Next", "nexttrack", "Skipping ahead")


@action
def previous_track() -> str:
    return _skip("Previous", "prevtrack", "Going back")


@action
def pause_media() -> str:
    media.pause_all()
    return "Paused"


@action
def resume_media() -> str:
    player = media.active()
    if player is None:
        return "Nothing is open to play"
    media.command(player, "Play")
    return "Playing"


@action
def now_playing() -> str:
    player = media.active()
    current = media.title(player) if player else ""
    if not current:
        return "Nothing is playing right now"
    current = _spoken_title(current)
    return f"This is {current}" if media.is_playing(player) else f"{current} is paused"


@action
def reply(text: str) -> str:
    return text


@action
def tell_time() -> str:
    return f"It's {datetime.now():%-I:%M %p}"


@action
def stay_awake(seconds: int = awake.DEFAULT_SECONDS) -> str:
    until = awake.start(seconds)
    return (f"Okay, I'll stay awake until {datetime.fromtimestamp(until):%-I:%M %p}, even with the lid closed. "
            "Keep me plugged in and out of a bag. Say allow sleep to undo this.")


@action
def allow_sleep() -> str:
    return "Okay, I can sleep normally again" if awake.stop() else "I wasn't being kept awake"


@action
def tell_date() -> str:
    return f"It's {datetime.now():%A, %B %-d}"


@action
def battery() -> str:
    for supply in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        level = (supply / "capacity").read_text().strip()
        status = (supply / "status").read_text().strip().lower()
        return f"Battery is at {level} percent and {status}"
    return "I can't find a battery"


@action
def greet() -> str:
    hour = datetime.now().hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    return f"Good {part}. What can I do for you?"


@action
def share_screen(control: bool = False) -> str:
    return share.start(control)


@action
def stop_sharing() -> str:
    return share.stop()


@action
def type_text(text: str) -> str:
    desktop.focus_target()
    desktop.type_text(text)
    return "Typed it"


@action
def press_keys(combo: str) -> str:
    desktop.focus_target()
    desktop.press_keys(combo)
    return f"Pressed {combo}"


@action
def click(x: int | None = None, y: int | None = None, count: int = 1, right: bool = False) -> str:
    desktop.click(x, y, count, right)
    return "Clicked"


@action
def move(x: int, y: int) -> str:
    desktop.move(x, y)
    return "Moved the pointer"


@action
def drag(x1: int, y1: int, x2: int, y2: int) -> str:
    desktop.drag(x1, y1, x2, y2)
    return "Dragged"


@action
def click_text(text: str, count: int = 1, right: bool = False) -> str:
    """Presses whatever shows these words in the front window, in any app.

    A real click on visible text comes first, since it does exactly what a person's would. An
    accessibility press covers what OCR can't read, such as lone symbols and icon buttons.
    """
    # Only the front window: a click must never land on matching text in some other app.
    target = desktop.target_window()
    if target:
        desktop.activate(target.id)
        time.sleep(0.2)
    item = ocr.find(ocr.read_screen(desktop.window_rect(target.id) if target else None), text)
    if item is not None:
        desktop.click(item.x, item.y, count, right)
        return f"Clicked {text}"
    if target and count == 1 and not right and a11y.press(desktop.window_pid(target.id), text):
        return f"Pressed {text}"
    raise UnknownTarget(f"I can't see {text} on the screen")


@action
def read_screen() -> str:
    target = desktop.target_window()
    items = ocr.read_screen(desktop.window_rect(target.id) if target else None)
    text = ". ".join(item.text for item in items)
    return text[:600] if text else "I can't see any text on the screen"


@action
def scroll(amount: int) -> str:
    desktop.scroll(amount)
    return "Scrolled"


@action
def wait(seconds: float) -> str:
    time.sleep(min(float(seconds), 30))
    return ""


def _require_target():
    window = desktop.target_window()
    if window is None:
        raise LookupError("There's no window to act on")
    return window


def _close_and_check(window, label: str) -> str:
    """Some apps stay open to ask about unsaved work, so 'closed' is only said when it's true."""
    desktop.close(window.id)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if window.id not in {w.id for w in desktop.list_windows()}:
            return f"Closed {label}"
        time.sleep(0.15)
    return f"{label} is still open. It may be asking you something"


@action
def close_window() -> str:
    window = _require_target()
    return _close_and_check(window, window.title)


@action
def minimize_window() -> str:
    desktop.minimize(_require_target().id)
    return "Minimized"


@action
def maximize_window() -> str:
    desktop.maximize(_require_target().id)
    return "Maximized"


@action
def close_app(name: str) -> str:
    window = _window_for(name)
    if window is None:
        raise LookupError(f"{name} isn't open")
    return _close_and_check(window, name)


@action
def focus_window(name: str) -> str:
    window = _window_for(name)
    if window is None:
        return open(name)
    desktop.activate(window.id)
    return f"Switched to {name}"


@action
def volume(change: str, level: int | None = None) -> str:
    sink = "@DEFAULT_AUDIO_SINK@"
    if change == "toggle":
        subprocess.run(["wpctl", "set-mute", sink, "toggle"], check=True)
        return "Toggled mute"
    target = {"up": "10%+", "down": "10%-"}.get(change, f"{max(0, min(int(level or 0), 100))}%")
    subprocess.run(["wpctl", "set-volume", "-l", "1.0", sink, target], check=True)
    return f"Volume {level}%" if change == "set" else f"Volume {change}"


def _gsettings(schema: str, key: str, value: str) -> None:
    subprocess.run(["gsettings", "set", schema, key, value], check=True, timeout=5)


def _set_rfkill_airplane(on: bool) -> None:
    subprocess.run(
        ["gdbus", "call", "--session", "--dest", "org.gnome.SettingsDaemon.Rfkill",
         "--object-path", "/org/gnome/SettingsDaemon/Rfkill",
         "--method", "org.freedesktop.DBus.Properties.Set", "org.gnome.SettingsDaemon.Rfkill",
         "AirplaneMode", f"<{str(on).lower()}>"],
        check=True, capture_output=True, timeout=5,
    )


def _set_dark_mode(on: bool) -> None:
    schema = "org.gnome.desktop.interface"
    _gsettings(schema, "color-scheme", "prefer-dark" if on else "default")
    theme = subprocess.run(["gsettings", "get", schema, "gtk-theme"], capture_output=True, text=True, timeout=5).stdout.strip(" '\n")
    light = theme.removesuffix("-dark")
    _gsettings(schema, "gtk-theme", f"{light}-dark" if on else light)


_OPTIONS = {
    "wifi": ("Wi-Fi", lambda on: subprocess.run(["nmcli", "radio", "wifi", "on" if on else "off"], check=True, timeout=10)),
    "bluetooth": ("Bluetooth", lambda on: subprocess.run(["bluetoothctl", "power", "on" if on else "off"], check=True, capture_output=True, timeout=10)),
    "airplane": ("Airplane mode", _set_rfkill_airplane),
    "dark_mode": ("Dark mode", _set_dark_mode),
    "night_light": ("Night light", lambda on: _gsettings("org.gnome.settings-daemon.plugins.color", "night-light-enabled", str(on).lower())),
    "do_not_disturb": ("Do not disturb", lambda on: _gsettings("org.gnome.desktop.notifications", "show-banners", str(not on).lower())),
    "microphone": ("Microphone", lambda on: subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "0" if on else "1"], check=True, timeout=5)),
}
_SETTINGS_PANELS = {
    "wi-fi": "wifi", "wifi": "wifi", "wireless": "wifi", "network": "network", "internet": "network",
    "bluetooth": "bluetooth", "sound": "sound", "audio": "sound", "volume": "sound", "power": "power",
    "battery": "power", "display": "display", "displays": "display", "screen": "display",
    "monitor": "display", "background": "background", "wallpaper": "background", "appearance": "ubuntu",
    "theme": "ubuntu", "notification": "notifications", "notifications": "notifications",
    "search": "search", "multitasking": "multitasking", "apps": "applications", "app": "applications",
    "application": "applications", "applications": "applications", "privacy": "privacy",
    "online accounts": "online-accounts", "accounts": "online-accounts", "sharing": "sharing",
    "mouse": "mouse", "touchpad": "mouse", "keyboard": "keyboard", "printer": "printers",
    "printers": "printers", "removable media": "removable-media", "usb": "removable-media",
    "color": "color", "accessibility": "universal-access", "system": "system", "about": "system",
    "language": "system", "date": "system", "time": "system", "users": "system",
}


@action
def set_option(option: str, on: bool) -> str:
    if option not in _OPTIONS:
        raise LookupError(f"I can't change {option} yet")
    label, change = _OPTIONS[option]
    change(on)
    return f"{label} is {'on' if on else 'off'}"


def _screen_brightness(method: str, *args: str) -> str:
    return subprocess.run(
        ["gdbus", "call", "--session", "--dest", "org.gnome.SettingsDaemon.Power",
         "--object-path", "/org/gnome/SettingsDaemon/Power",
         "--method", f"org.freedesktop.DBus.Properties.{method}",
         "org.gnome.SettingsDaemon.Power.Screen", "Brightness", *args],
        check=True, capture_output=True, text=True, timeout=5,
    ).stdout


@action
def brightness(change: str, level: int | None = None) -> str:
    current = int(re.search(r"\d+", _screen_brightness("Get")).group())
    target = {"up": current + 15, "down": current - 15}.get(change, level or 0)
    target = max(5, min(100, target))  # 5, so a mistake can't leave a remote user with a black screen
    _screen_brightness("Set", f"<int32 {target}>")
    return f"Brightness {target} percent"


@action
def power_profile(profile: str) -> str:
    subprocess.run(["powerprofilesctl", "set", profile], check=True, capture_output=True, timeout=5)
    return f"{profile.replace('-', ' ').capitalize()} mode is on"


@action
def open_settings(panel: str) -> str:
    wanted = panel.lower().strip()
    if wanted not in _SETTINGS_PANELS:  # could be an app of that name, like "NVIDIA X Server Settings"
        return open(f"{panel} settings")
    _spawn("gnome-control-center", _SETTINGS_PANELS[wanted])
    return f"Opening {panel} settings"


@action
def open_folder(name: str) -> str:
    kinds = {"downloads": "DOWNLOAD", "documents": "DOCUMENTS", "desktop": "DESKTOP",
             "pictures": "PICTURES", "music": "MUSIC", "videos": "VIDEOS"}
    path = str(Path.home())
    if name in kinds:
        path = subprocess.run(["xdg-user-dir", kinds[name]], capture_output=True, text=True, timeout=5).stdout.strip()
    _spawn("gio", "open", path)
    return f"Opening {name}"


def _describe_file(path: Path) -> str:
    return f"{path.name}  ({path.parent.as_posix().replace(str(Path.home()), '~')})"


@action
def find_files(query: str) -> str:
    matches = files.find(query)
    if not matches:
        return f"I couldn't find a file called {query}"
    return "\n".join(_describe_file(path) for path in matches)


@action
def list_folder(name: str) -> str:
    entries = files.newest(files.folder_path(name))
    if not entries:
        return f"Your {name} folder is empty"
    return f"Newest in {name}:\n" + "\n".join(entry.name for entry in entries)


@action
def open_file(name: str) -> str:
    path = files.resolve(name)
    if path is None:
        raise UnknownTarget(f"I couldn't find a file called {name}")
    _spawn("gio", "open", str(path))
    return f"Opening {path.name}"


@action
def read_file(name: str) -> str:
    path = files.resolve(name)
    if path is None:
        raise UnknownTarget(f"I couldn't find a file called {name}")
    return files.read_text(path)


@action
def send_file(name: str) -> str:
    path = files.resolve(name)
    if path is None:  # plainly said, not passed to the AI: guessing could send the wrong file
        return f"I couldn't find a file called {name}"
    if files.is_private(path):
        return f"{path.name} is private, so I won't send it"
    if path.stat().st_size > MAX_SEND_BYTES:
        return f"{path.name} is too big to send on WhatsApp"
    whatsapp.send_image(_owner_phone(), path)
    return f"Sent {path.name} to your WhatsApp"


def _hand_over(path: Path, send: bool, note: str = "") -> str:
    if not send:
        return f"{note}Saved {path.name} in {path.parent.name}"
    if path.stat().st_size > MAX_SEND_BYTES:
        return (f"{note}Saved {path.name} in {path.parent.name}, but at {path.stat().st_size / 1e6:.0f} MB "
                f"it's too big to send on WhatsApp. The limit is {MAX_SEND_BYTES // 1000000} MB")
    whatsapp.send_image(_owner_phone(), path)
    return f"{note}Sent {path.name} to your WhatsApp"


@action
def download(url: str, to: str = "", send: bool = False) -> str:
    path, is_page = fetch.download(url)
    note = "That link is a web page, not a file, so I saved the page. " if is_page else ""
    return _hand_over(fetch.convert(path, to) if to else path, send, note)


@action
def convert_file(name: str, to: str, send: bool = False) -> str:
    path = files.resolve(name)
    if path is None:
        return f"I couldn't find a file called {name}"
    if files.is_private(path):  # a copy in Downloads would no longer be private
        return f"{path.name} is private, so I won't convert it"
    return _hand_over(fetch.convert(path, to), send)


@action
def remind(text: str, seconds: float | None = None, hour: int | None = None, minute: int = 0,
           meridiem: str = "", day: str = "", send: bool = False) -> str:
    now = datetime.now()
    if seconds is not None:
        moment = now + timedelta(seconds=seconds)
    else:
        moment = reminders.clock_time(hour, minute, meridiem, day, now)
    if (moment - now).total_seconds() > reminders.MAX_SECONDS:
        return "That's too far away for me to remember"
    reminders.add(moment.timestamp(), text, send)
    return f"Okay, I'll remind you {reminders.spoken(moment, now)}: {text}"


@action
def list_reminders() -> str:
    items = reminders.upcoming()
    if not items:
        return "You have no reminders"
    return "\n".join(f"{reminders.spoken(datetime.fromtimestamp(item['when']))}: {item['text']}" for item in items)


@action
def cancel_reminders(words: str = "") -> str:
    removed = reminders.cancel(words)
    if not removed:
        return f"I couldn't find a reminder about {words}" if words else "You have no reminders"
    return "Cancelled that reminder" if removed == 1 else f"Cancelled {removed} reminders"


@action
def send_message(text: str) -> str:
    whatsapp.send_text(_owner_phone(), text)
    return "Sent that to your WhatsApp"


@action
def set_voice(style: str) -> str:
    config.save({**config.load(), "voice_style": style})
    return "Okay, back to my normal voice" if style == "normal" else f"Okay, this is my {style} voice. How do I sound?"


@action
def share_location(send: bool = True) -> str:
    phone = _owner_phone() if send else ""  # before looking anything up
    place = location.find()
    if not send:  # asked on WhatsApp, so the answer itself goes there
        return location.message(place)
    whatsapp.send_text(phone, location.message(place))
    return f"Sent the laptop's location to your WhatsApp. {place.accuracy}"


def _owner_phone() -> str:
    phone = config.load()["whatsapp_phone"]
    if not phone:
        raise LookupError("Add your WhatsApp number in Buddy's settings first")
    return phone


@action
def system_report() -> str:
    def output(*command: str) -> str:
        try:
            return subprocess.run(command, capture_output=True, text=True, timeout=5, env=apps.session_env()).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    disk = shutil.disk_usage(Path.home())
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    gigabytes = lambda key: int(memory[key].split()[0]) / 1048576
    # By type, since the "active" column is translated on a non-English system.
    wifi = next((line.split(":", 1)[1] for line in output("nmcli", "-t", "-f", "TYPE,NAME", "connection", "show", "--active").splitlines()
                 if line.startswith("802-11-wireless:")), "not connected")
    lines = [
        battery(),
        f"CPU load {os.getloadavg()[0]:.1f} on {os.cpu_count()} threads",
        f"Memory {gigabytes('MemTotal') - gigabytes('MemAvailable'):.0f} of {gigabytes('MemTotal'):.0f} GB used",
        f"Disk {disk.free / 1e9:.0f} GB free of {disk.total / 1e9:.0f} GB",
        f"Wi-Fi {wifi}",
    ]
    gpu = output("nvidia-smi", "--query-gpu=memory.used,memory.total,temperature.gpu", "--format=csv,noheader")
    if gpu:
        lines.append(f"GPU {gpu}")
    lines.append(lock_status())
    if until := awake.until():
        lines.append(f"Staying awake until {datetime.fromtimestamp(until):%-I:%M %p}")
    if guests.enabled():
        lines.append(f"WhatsApp guests: {guests.with_access()} with access, {guests.current.waiting() if guests.current else 0} waiting")
    windows = [window.title for window in desktop.list_windows() if desktop.BUDDY_TITLE not in window.title]
    lines.append(f"{len(windows)} windows open: " + ", ".join(title[:40] for title in windows[:8]))
    lines.append(now_playing())
    return "\n".join(lines)


@action
def run_readonly(command: str) -> str:
    """For the local AI: only commands that just look at things, with no shell features at all."""
    parts = shlex.split(command)
    if not parts or parts[0] not in READ_ONLY_COMMANDS or re.search(r"[;&|<>`$]", command):
        return "refused: only simple look-only commands are allowed. The user must ask to run anything else"
    return _run(parts, shell=False)


@action
def run_command(command: str) -> str:
    return _run(command, shell=True)


def _run(command, shell: bool) -> str:
    try:
        done = subprocess.run(command, shell=shell, executable="/bin/bash" if shell else None, capture_output=True,
                              text=True, timeout=COMMAND_SECONDS, cwd=Path.home(), env=apps.session_env(),
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"That command was still running after {COMMAND_SECONDS} seconds, so I stopped it"
    combined = (done.stdout + done.stderr).strip() or "(no output)"
    if len(combined) > MAX_COMMAND_OUTPUT:
        combined = "...\n" + combined[-MAX_COMMAND_OUTPUT:]
    return combined if done.returncode == 0 else f"Exit code {done.returncode}\n{combined}"


@action
def empty_trash() -> str:
    subprocess.run(["gio", "trash", "--empty"], check=True, timeout=120)
    return "The trash is empty"


def _graphical_session() -> str:
    """Named explicitly: a background service isn't inside the desktop session itself."""
    return subprocess.run(["loginctl", "show-user", str(os.getuid()), "-p", "Display", "--value"],
                          capture_output=True, text=True, check=True, timeout=5).stdout.strip()


def _screen_saver(method: str) -> str:
    return subprocess.run(
        ["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver", "--object-path", "/org/gnome/ScreenSaver",
         "--method", f"org.gnome.ScreenSaver.{method}"],
        capture_output=True, text=True, check=True, timeout=5,
    ).stdout


def is_locked() -> bool:
    return "true" in _screen_saver("GetActive")


@action
def lock_status() -> str:
    return "The laptop is locked" if is_locked() else "The laptop is unlocked"


@action
def unlock_screen() -> str:
    if not is_locked():
        return "The laptop is already unlocked"
    subprocess.run(["loginctl", "unlock-session", _graphical_session()], check=True, timeout=5)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if not is_locked():
            try:
                desktop.nudge_pointer()  # wakes a blanked display
            except Exception as error:  # the laptop is unlocked either way
                print(f"Couldn't wake the display: {error}", flush=True)
            return "Unlocked. Anyone at the laptop can use it now, so send lock when you're done"
        time.sleep(0.2)
    raise RuntimeError("The laptop is still locked. I couldn't unlock it")


@action
def power(mode: str) -> str:
    commands = {
        "lock": ["loginctl", "lock-session", _graphical_session()],
        "suspend": ["systemctl", "suspend"],
        "shutdown": ["systemctl", "poweroff"],
        "restart": ["systemctl", "reboot"],
        "logout": ["gnome-session-quit", "--logout", "--no-prompt"],
    }
    subprocess.run(commands[mode], check=True)
    return {"lock": "Locked"}.get(mode, f"{mode.capitalize()} started")


@action
def screenshot() -> str:
    global _last_screenshot
    config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SCREENSHOT_DIR / f"buddy-{datetime.now():%Y%m%d-%H%M%S}.png"
    path.write_bytes(desktop.screenshot_png())
    _last_screenshot = path
    return "Screenshot saved"


@action
def whatsapp_send_screenshot() -> str:
    phone = config.load()["whatsapp_phone"]
    if not phone:
        raise LookupError("Add your WhatsApp number in Buddy's settings first")
    if _last_screenshot is None:
        screenshot()
    whatsapp.send_image(phone, _last_screenshot)
    return "Sent the screenshot to your WhatsApp"
