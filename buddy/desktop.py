"""Keyboard, mouse, windows and screenshots, on X11 or Windows. Needs no root or xdotool."""
import io
import re
import time

import mss
import mss.tools
from PIL import Image, ImageDraw
from pynput.keyboard import Controller as Keyboard
from pynput.keyboard import Key
from pynput.mouse import Button
from pynput.mouse import Controller as Mouse

from .config import WINDOWS
from .window import BUDDY_TITLE, HIDDEN_CLASS, Window  # noqa: F401

if WINDOWS:
    from .desktop_windows import activate, active_window, close, list_windows, maximize, minimize, window_pid, window_rect  # noqa: F401
else:
    from .desktop_x11 import activate, active_window, close, list_windows, maximize, minimize, window_pid, window_rect  # noqa: F401

# GNOME's shortcuts, as they're pressed on Windows. Unchanged, "super+v" would open the clipboard instead of notifications.
WINDOWS_SHORTCUTS = {"super": "super+tab", "super+v": "super+n", "super+pagedown": "ctrl+super+right",
                     "super+pageup": "ctrl+super+left", "ctrl+shift+alt+r": "super+alt+r"}
NAMED_KEYS = {
    "ctrl": Key.ctrl, "control": Key.ctrl, "alt": Key.alt, "shift": Key.shift,
    "super": Key.cmd, "win": Key.cmd, "windows": Key.cmd, "enter": Key.enter,
    "return": Key.enter, "esc": Key.esc, "escape": Key.esc, "tab": Key.tab,
    "space": Key.space, "backspace": Key.backspace, "delete": Key.delete,
    "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
    "home": Key.home, "end": Key.end, "pageup": Key.page_up, "pagedown": Key.page_down,
    "plus": "+", "minus": "-",
    "playpause": Key.media_play_pause, "nexttrack": Key.media_next,
    "prevtrack": Key.media_previous,
    **{f"f{i}": getattr(Key, f"f{i}") for i in range(1, 13)},
}


def target_window() -> Window | None:
    """The window the user means: topmost one that isn't Buddy's own page."""
    return next((w for w in list_windows() if BUDDY_TITLE not in w.title), None)


def focus_target() -> None:
    """Keystrokes must never land in Buddy's own page after its button was clicked."""
    active = active_window()
    if active is None or BUDDY_TITLE in active.title:
        target = target_window()
        if target:
            activate(target.id)
            time.sleep(0.25)
            now = active_window()
            if now is None or now.id != target.id:
                raise RuntimeError(f"I couldn't bring {target.title} to the front, so I didn't press anything")


def type_text(text: str) -> None:
    Keyboard().type(text)


def press_keys(combo: str) -> None:
    """Press a chord such as 'ctrl+shift+t'."""
    keyboard = Keyboard()
    keys = []
    combo = combo.strip().lower()
    for part in re.split(r"\s*\+\s*", WINDOWS_SHORTCUTS.get(combo, combo) if WINDOWS else combo):
        if part in NAMED_KEYS:
            keys.append(NAMED_KEYS[part])
        elif len(part) == 1:
            keys.append(part)
        else:
            raise ValueError(f"unknown key: {part}")
    for key in keys:
        keyboard.press(key)
    for key in reversed(keys):
        keyboard.release(key)


def click(x: int | None = None, y: int | None = None, count: int = 1, right: bool = False) -> None:
    mouse = Mouse()
    if x is not None and y is not None:
        mouse.position = (x, y)
        time.sleep(0.05)
    mouse.click(Button.right if right else Button.left, count)


def move(x: int, y: int) -> None:
    Mouse().position = (x, y)


def nudge_pointer() -> None:
    mouse = Mouse()
    x, y = mouse.position
    mouse.position = (x + 1, y)
    mouse.position = (x, y)


def drag(x1: int, y1: int, x2: int, y2: int) -> None:
    mouse = Mouse()
    mouse.position = (x1, y1)
    time.sleep(0.1)
    mouse.press(Button.left)
    time.sleep(0.1)
    for step in range(1, 11):  # in stages: many apps ignore a drag that jumps straight there
        mouse.position = (x1 + (x2 - x1) * step // 10, y1 + (y2 - y1) * step // 10)
        time.sleep(0.02)
    time.sleep(0.1)
    mouse.release(Button.left)


def scroll(amount: int) -> None:
    """Positive scrolls up, negative scrolls down."""
    Mouse().scroll(0, amount)


def screenshot_png() -> bytes:
    """All monitors stitched together, as PNG."""
    with mss.mss() as grabber:
        shot = grabber.grab(grabber.monitors[0])
        return mss.tools.to_png(shot.rgb, shot.size)


def screen_rect() -> tuple[int, int, int, int]:
    with mss.mss() as grabber:
        monitor = grabber.monitors[0]
        return monitor["left"], monitor["top"], monitor["width"], monitor["height"]


def frame_jpeg(max_width: int = 1600) -> bytes:
    """A frame for the phone, with the pointer drawn in since captures leave it out."""
    with mss.mss() as grabber:
        monitor = grabber.monitors[0]
        shot = grabber.grab(monitor)
    image = Image.frombytes("RGB", shot.size, shot.rgb)
    x, y = Mouse().position
    x, y = x - monitor["left"], y - monitor["top"]
    ImageDraw.Draw(image).ellipse((x - 7, y - 7, x + 7, y + 7), fill=(239, 111, 88), outline="white", width=2)
    if image.width > max_width:
        image = image.resize((max_width, round(image.height * max_width / image.width)))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=60)
    return buffer.getvalue()


def screenshot_for_model(max_width: int = 1280) -> tuple[bytes, float, int, int]:
    """JPEG for the agent, plus the scale and offset that map its pixels back to the screen."""
    with mss.mss() as grabber:
        monitor = grabber.monitors[0]
        shot = grabber.grab(monitor)
    image = Image.frombytes("RGB", shot.size, shot.rgb)
    scale = min(1.0, max_width / image.width)
    if scale < 1.0:
        image = image.resize((max_width, round(image.height * scale)))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=75)
    return buffer.getvalue(), scale, monitor["left"], monitor["top"]
