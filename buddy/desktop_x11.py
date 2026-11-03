"""Windows on an X11 desktop: listing, focusing, moving. Needs no root or xdotool."""
from contextlib import contextmanager

from ewmh import EWMH
from Xlib import X, display
from Xlib.protocol import event

from .window import HIDDEN_CLASS, Window


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
