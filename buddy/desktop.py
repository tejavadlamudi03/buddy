"""X11 primitives: keyboard, mouse, windows, screenshots. Needs no root or xdotool."""
import io
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass

import mss
import mss.tools
from ewmh import EWMH
from PIL import Image, ImageDraw
from pynput.keyboard import Controller as Keyboard
from pynput.keyboard import Key
from pynput.mouse import Button
from pynput.mouse import Controller as Mouse
from Xlib import X, display
from Xlib.protocol import event

BUDDY_TITLE = "Buddy Voice Control"
HIDDEN_CLASS = "BuddyWhatsApp"
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


@dataclass
class Window:
    id: int
    title: str
    cls: str


@contextmanager
def _wm():
    wm = EWMH(display.Display())
    try:
        yield wm
    finally:
        wm.display.flush()
        wm.display.close()


def _describe(wm, win) -> Window | None:
    try:
        types = wm.getWmWindowType(win, str=True) or []
        if types and not {"_NET_WM_WINDOW_TYPE_NORMAL", "_NET_WM_WINDOW_TYPE_DIALOG"} & set(types):
            return None
        name = wm.getWmName(win) or win.get_wm_name() or ""
        title = name.decode(errors="replace") if isinstance(name, bytes) else name
        cls = " ".join(win.get_wm_class() or ())
        if HIDDEN_CLASS in cls:  # Buddy's own WhatsApp window is never a target for commands
            return None
        return Window(win.id, title, cls)
    except Exception:  # windows can vanish between listing and inspection
        return None


def list_windows() -> list[Window]:
    """Application windows and their dialogs, topmost first."""
    with _wm() as wm:
        try:
            stacked = wm.getClientListStacking()
        except TypeError:  # ewmh raises this when no window manager publishes the list
            return []
        return [w for w in (_describe(wm, win) for win in reversed(stacked)) if w]


def active_window() -> Window | None:
    with _wm() as wm:
        win = wm.getActiveWindow()
        return _describe(wm, win) if win else None


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


def activate(window_id: int) -> None:
    """Brings a window forward, restoring it if minimized."""
    with _wm() as wm:
        dpy = wm.display
        win = dpy.create_resource_object("window", window_id)
        # Source 2 means a pager, the kind of request window managers honour outright. Without
        # it, GNOME may only flash a "ready" notice instead of showing the window.
        message = event.ClientMessage(window=win, client_type=dpy.intern_atom("_NET_ACTIVE_WINDOW"),
                                      data=(32, [2, X.CurrentTime, 0, 0, 0]))
        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        dpy.screen().root.send_event(message, event_mask=mask)


def window_rect(window_id: int) -> tuple[int, int, int, int]:
    """Left, top, width and height of what's visible on screen."""
    with _wm() as wm:
        win = wm.display.create_resource_object("window", window_id)
        size = win.get_geometry()
        origin = win.translate_coords(wm.display.screen().root, 0, 0)
        # GTK windows include invisible shadow margins, which overlap whatever is next to them.
        margins = win.get_full_property(wm.display.intern_atom("_GTK_FRAME_EXTENTS"), X.AnyPropertyType)
        left, right, top, bottom = margins.value if margins else (0, 0, 0, 0)
        return -origin.x + left, -origin.y + top, size.width - left - right, size.height - top - bottom


def window_pid(window_id: int) -> int | None:
    with _wm() as wm:
        return wm.getWmPid(wm.display.create_resource_object("window", window_id))


def close(window_id: int) -> None:
    with _wm() as wm:
        wm.setCloseWindow(wm.display.create_resource_object("window", window_id))


def maximize(window_id: int) -> None:
    with _wm() as wm:
        win = wm.display.create_resource_object("window", window_id)
        wm.setWmState(win, 1, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")


def minimize(window_id: int) -> None:
    with _wm() as wm:
        dpy = wm.display
        win = dpy.create_resource_object("window", window_id)
        message = event.ClientMessage(
            window=win,
            client_type=dpy.intern_atom("WM_CHANGE_STATE"),
            data=(32, [3, 0, 0, 0, 0]),  # 3 = IconicState
        )
        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        dpy.screen().root.send_event(message, event_mask=mask)


def type_text(text: str) -> None:
    Keyboard().type(text)


def press_keys(combo: str) -> None:
    """Press a chord such as 'ctrl+shift+t'."""
    keyboard = Keyboard()
    keys = []
    for part in re.split(r"\s*\+\s*", combo.strip().lower()):
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
