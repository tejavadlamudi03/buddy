"""Presses an app's real buttons by name through the accessibility system, without touching the mouse."""
import json
import subprocess
from pathlib import Path

from . import config
from .apps import session_env

if config.WINDOWS:
    from . import a11y_windows

SYSTEM_PYTHON = "/usr/bin/python3"
HELPER = Path(__file__).with_name("a11y_helper.py")
# What apps call the keys people read out as symbols.
ALSO_KNOWN_AS = {"+": ["add", "plus"], "-": ["−", "subtract", "minus"], "*": ["×", "multiply", "multiply by"],
                 "x": ["×", "multiply", "multiply by"], "/": ["÷", "divide", "divide by"], "=": ["calculate result", "equals"],
                 "equals": ["="], "plus": ["+"], "minus": ["−", "-"], "times": ["×", "multiply by"],
                 "divide": ["÷", "divide by"], "divided by": ["÷", "divide by"]}


def _ask(pid: int, *args: str) -> dict:
    try:
        done = subprocess.run([SYSTEM_PYTHON, str(HELPER), str(pid), *args], capture_output=True,
                              text=True, timeout=8, env=session_env())
        return json.loads(done.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def press(pid: int | None, text: str) -> bool:
    if not pid:
        return False
    wanted = text.strip()
    choices = [wanted, *ALSO_KNOWN_AS.get(wanted.lower(), [])]
    if config.WINDOWS:
        return bool(_on_windows(lambda: a11y_windows.press(pid, choices)))
    return bool(_ask(pid, "press", *choices).get("pressed"))


def names(pid: int | None) -> list[str]:
    if pid and config.WINDOWS:
        return _on_windows(lambda: a11y_windows.names(pid)) or []
    return [item["name"] for item in _ask(pid, "list").get("items", [])] if pid else []


def _on_windows(work):
    """Like _ask, a failure here only means there's nothing to press, and OCR has already been tried."""
    try:
        return work()
    except Exception as error:  # COM errors, a timeout, or an app that closed meanwhile
        print(f"Couldn't read the app's buttons: {error}", flush=True)
        return None
