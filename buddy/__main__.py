"""Command line entry point: `buddy`, `buddy check`, `buddy whatsapp-login`, `buddy install-launcher`, `buddy install-autostart`."""
import argparse
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import apps, config


def _page_url(settings: dict) -> str:
    version = int((Path(__file__).parent / "static/index.html").stat().st_mtime)  # a changed page is never taken from Chrome's cache
    return f"http://localhost:{settings['port']}/?v={version}#token={settings['token']}"


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
    browser = apps.browser()
    if not browser and config.WINDOWS:
        os.startfile(_page_url(settings))
        return
    command = [browser, f"--app={_page_url(settings)}"] if browser else ["xdg-open", _page_url(settings)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, **config.NO_WINDOW)


def _windowless_python() -> Path:
    """Runs Buddy on Windows without leaving a black console window open."""
    return Path(sys.executable).with_name("pythonw.exe")


def _keep_output() -> None:
    """On Windows: pythonw has nowhere to print, which breaks the web server, and the console may not handle Telugu."""
    if sys.stdout is None or sys.stderr is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        log = open(config.DATA_DIR / "buddy.log", "a", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115 - for the whole run
        sys.stdout, sys.stderr = log, log
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _keep_running() -> None:
    """On Windows, what systemd's Restart=on-failure does on Linux: Buddy comes back after a crash."""
    while subprocess.run([sys.executable, "-m", "buddy", "--background"], **config.NO_WINDOW).returncode != 0:
        time.sleep(5)


def install_launcher() -> None:
    if config.WINDOWS:
        from . import winsys
        print(f"Installed {winsys.add_to_start_menu(_windowless_python())}")
        return
    executable = Path(sys.argv[0]).resolve()
    entry = Path.home() / ".local/share/applications/buddy.desktop"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "[Desktop Entry]\nType=Application\nName=Buddy\nComment=Control this laptop by voice\n"
        f"Exec={executable}\nIcon=audio-input-microphone\nTerminal=false\nCategories=Utility;\n"
    )
    print(f"Installed {entry}")


def install_autostart() -> None:
    """A user service, so Buddy starts with the desktop session and comes back after a crash."""
    if config.WINDOWS:
        from . import winsys
        winsys.start_at_login(_windowless_python())
        print("Buddy now starts whenever you log in.")
        return
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
    parser.add_argument("command", nargs="?", choices=["whatsapp-login", "install-launcher", "install-autostart", "check", "use-profile"])
    parser.add_argument("profile", nargs="?", choices=sorted(config.PROFILES), help="with use-profile: which settings to use")
    parser.add_argument("--no-open", action="store_true", help="start the server without opening the page")
    parser.add_argument("--background", action="store_true", help="open the page only if it isn't already open, unfocused")
    parser.add_argument("--url", action="store_true", help="print the private link and exit")
    parser.add_argument("--keep-running", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if config.WINDOWS:
        _keep_output()
    settings = config.load()

    if args.keep_running:
        _keep_running()
    elif args.url:
        print(_page_url(settings))
    elif args.command == "whatsapp-login":
        from .whatsapp import login
        login()
    elif args.command == "use-profile":
        if not args.profile:
            parser.error("use-profile needs one of: " + ", ".join(sorted(config.PROFILES)))
        chosen = config.apply_profile(args.profile)
        print(chosen.get("ai_model", "") if chosen["agent_enabled"] else "")  # scripts/install.sh reads which model to fetch
    elif args.command == "check":
        from .doctor import run
        sys.exit(1 if run() else 0)
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
