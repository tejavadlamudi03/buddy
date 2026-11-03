"""Presses an app's real buttons by name through the accessibility system, without touching the mouse."""
import json
import subprocess
from pathlib import Path

from . import config
from .apps import session_env

SYSTEM_PYTHON = "/usr/bin/python3"
HELPER = Path(__file__).with_name("a11y_helper.py")
WINDOWS_HELPER = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                  str(Path(__file__).with_name("a11y_helper.ps1"))]
# What apps call the keys people read out as symbols.
ALSO_KNOWN_AS = {"+": ["add", "plus"], "-": ["−", "subtract", "minus"], "*": ["×", "multiply", "multiply by"],
                 "x": ["×", "multiply", "multiply by"], "/": ["÷", "divide", "divide by"], "=": ["calculate result", "equals"],
                 "equals": ["="], "plus": ["+"], "minus": ["−", "-"], "times": ["×", "multiply by"],
                 "divide": ["÷", "divide by"], "divided by": ["÷", "divide by"]}


def _ask(pid: int, *args: str) -> dict:
    try:
        helper = WINDOWS_HELPER if config.WINDOWS else [SYSTEM_PYTHON, str(HELPER)]
        done = subprocess.run([*helper, str(pid), *args], capture_output=True, encoding="utf-8", errors="replace",
                              timeout=8, env=session_env(), **config.NO_WINDOW)
        return json.loads(done.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def press(pid: int | None, text: str) -> bool:
    if not pid:
        return False
    wanted = text.strip()
    return bool(_ask(pid, "press", wanted, *ALSO_KNOWN_AS.get(wanted.lower(), [])).get("pressed"))


def names(pid: int | None) -> list[str]:
    return [item["name"] for item in _ask(pid, "list").get("items", [])] if pid else []
