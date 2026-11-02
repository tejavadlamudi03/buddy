"""`buddy check`: whether each part of Buddy works on this laptop, without changing anything."""
import os
import shutil
from pathlib import Path

from . import ai, config, files, listen


def _tools() -> list:
    wanted = {"ffmpeg": "converting audio and video", "soffice": "converting and reading documents",
              "pdftotext": "reading PDFs", "pdftoppm": "reading scanned PDFs", "google-chrome": "WhatsApp and websites",
              "pw-play": "speaking aloud"}
    return [(bool(shutil.which(tool)), tool, use) for tool, use in wanted.items()]


def _desktop() -> tuple:
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return False, "desktop", "this is a Wayland session, so choose 'Ubuntu on Xorg' at login"
    try:
        from . import desktop
        return True, "desktop", f"{len(desktop.list_windows())} windows found"
    except Exception as error:  # no display at all, or X refused the connection
        return False, "desktop", f"can't reach it: {type(error).__name__}. Run this from your desktop session"


def _local_ai(settings: dict) -> tuple:
    if not settings["agent_enabled"]:
        return None, "local AI", "switched off, so only built-in commands work"
    if not ai.installed():
        return None, "local AI", "not installed, so only built-in commands work. Run scripts/install-local-ai.sh"
    try:
        reply = ai.chat(settings["ai_model"], 'Reply with JSON only: {"ok": true}', "Are you working?", timeout=180)
    except Exception as error:
        return False, "local AI", f"didn't answer: {str(error)[:120]}"
    return bool(reply), "local AI", f"{settings['ai_model']} answered"


def run() -> int:
    """How many things are broken. Optional parts that just aren't set up don't count."""
    settings = config.load()
    telugu = Path(settings["telugu_whisper_model"]).exists()
    wider = listen.languages_ear() is not None
    results = [
        _desktop(), *_tools(), _local_ai(settings),
        (True if telugu else None, "Telugu", "ready" if telugu else "not installed, so English only. Run scripts/install-telugu.sh"),
        (True if wider else None, "Hindi and German", "ready" if wider else "not installed, so typed only. Run scripts/install-languages.sh"),
        (True if files.HOME.is_dir() else False, "home folder", str(files.HOME)),
        (True if settings["whatsapp_phone"] else None, "WhatsApp number", "set" if settings["whatsapp_phone"] else "not set yet, under Settings"),
        (True if (config.DATA_DIR / "whatsapp-linked").exists() else None, "WhatsApp link",
         "linked" if (config.DATA_DIR / "whatsapp-linked").exists() else "not linked yet. Run: buddy whatsapp-login"),
    ]
    for passed, name, detail in results:
        print(f"  {'ok' if passed else ('PROBLEM' if passed is False else 'not set up'):<11}{name}: {detail}")
    return sum(passed is False for _, passed in ((r[1], r[0]) for r in results))
