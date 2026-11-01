"""Presses an app's real buttons by name through the accessibility system, without touching the mouse."""
import json
import subprocess
from pathlib import Path

from .apps import session_env

SYSTEM_PYTHON = "/usr/bin/python3"
HELPER = Path(__file__).with_name("a11y_helper.py")
# What apps call the keys people read out as symbols.
ALSO_KNOWN_AS = {"+": ["add"], "-": ["−", "subtract"], "*": ["×", "multiply"], "x": ["×", "multiply"],
                 "/": ["÷", "divide"], "=": ["calculate result"], "equals": ["="], "plus": ["+"],
                 "minus": ["−", "-"], "times": ["×"], "divide": ["÷"], "divided by": ["÷"]}


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
    return bool(_ask(pid, "press", wanted, *ALSO_KNOWN_AS.get(wanted.lower(), [])).get("pressed"))


def names(pid: int | None) -> list[str]:
    return [item["name"] for item in _ask(pid, "list").get("items", [])] if pid else []
