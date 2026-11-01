"""Command line entry point: `buddy`, `buddy whatsapp-login`, `buddy install-launcher`, `buddy install-autostart`."""
import argparse
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import config


def _page_url(settings: dict) -> str:
    return f"http://localhost:{settings['port']}/#token={settings['token']}"


def _is_running(settings: dict) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{settings['port']}/", timeout=1)
        return True
    except OSError:
        return False


def _open_when_ready(settings: dict, focus: bool = True) -> None:
    for _ in range(50):
        if _is_running(settings):
            break
        time.sleep(0.2)
    _show_page(settings, focus)


def _show_page(settings: dict, focus: bool = True) -> None:
    """Focuses Buddy's window if it has one: two open pages would both listen and both talk."""
    from . import desktop
    for window in desktop.list_windows():
        if desktop.BUDDY_TITLE in window.title:
            if focus:
                desktop.activate(window.id)
            return
    browser = shutil.which("google-chrome")
    command = [browser, f"--app={_page_url(settings)}"] if browser else ["xdg-open", _page_url(settings)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def install_launcher() -> None:
    executable = Path(sys.argv[0]).resolve()
    entry = Path.home() / ".local/share/applications/buddy.desktop"
    entry.write_text(
        "[Desktop Entry]\nType=Application\nName=Buddy\nComment=Control this laptop by voice\n"
        f"Exec={executable}\nIcon=audio-input-microphone\nTerminal=false\nCategories=Utility;\n"
    )
    print(f"Installed {entry}")


def install_autostart() -> None:
    """A user service, so Buddy starts with the desktop session and comes back after a crash."""
    executable = Path(sys.argv[0]).resolve()
    unit = Path.home() / ".config/systemd/user/buddy.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    # KillMode=process: stopping Buddy must not close the apps it opened for you.
    unit.write_text(
        "[Unit]\nDescription=Buddy voice assistant\nPartOf=graphical-session.target\n"
        "After=graphical-session.target\n\n"
        f"[Service]\nExecStart={executable} --background\nRestart=on-failure\nRestartSec=5\nKillMode=process\n\n"
        "[Install]\nWantedBy=graphical-session.target\n"
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "buddy.service"], check=True)
    print(f"Installed {unit}. Buddy now starts whenever you log in.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="buddy", description="Control your laptop by voice.")
    parser.add_argument("command", nargs="?", choices=["whatsapp-login", "install-launcher", "install-autostart"])
    parser.add_argument("--no-open", action="store_true", help="start the server without opening the page")
    parser.add_argument("--background", action="store_true", help="open the page only if it isn't already open, unfocused")
    parser.add_argument("--url", action="store_true", help="print the private link and exit")
    args = parser.parse_args()
    settings = config.load()

    if args.url:
        print(_page_url(settings))
    elif args.command == "whatsapp-login":
        from .whatsapp import login
        login()
    elif args.command == "install-launcher":
        install_launcher()
    elif args.command == "install-autostart":
        install_autostart()
    elif _is_running(settings):
        if not args.no_open:
            _show_page(settings, focus=not args.background)
    else:
        import uvicorn
        from .server import create_app
        if not args.no_open:
            threading.Thread(target=_open_when_ready, args=(settings, not args.background), daemon=True).start()
        print(f"Buddy is running on port {settings['port']}", flush=True)  # the link holds a secret, so not logged
        uvicorn.run(create_app(), host="127.0.0.1", port=settings["port"], log_level="warning")


if __name__ == "__main__":
    main()
