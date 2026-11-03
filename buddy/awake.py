"""Keeps the laptop from sleeping while nobody is there to wake it. The screen still locks as usual."""
import json
import subprocess
import time

from . import apps, config, winsys

STATE = config.DATA_DIR / "stay-awake.json"
DEFAULT_SECONDS = 8 * 3600
MAX_SECONDS = 24 * 3600
BLOCK = ["systemd-inhibit", "--what=sleep:handle-lid-switch", "--who=Buddy", "--why=You asked me to stay awake"]


def _read() -> dict | None:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None


def _running(pid: int) -> bool:
    """By name too, since after a reboot the same number can belong to something else."""
    if config.WINDOWS:
        return winsys.is_keeping_awake(pid)
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as stream:
            return BLOCK[0].encode() in stream.read()
    except OSError:
        return False


def start(seconds: int = DEFAULT_SECONDS) -> float:
    """The block ends by itself when its sleep does, so a crash can't leave the laptop awake forever."""
    stop()
    seconds = min(seconds, MAX_SECONDS)
    process = winsys.keep_awake(seconds) if config.WINDOWS else subprocess.Popen(
        [*BLOCK, "sleep", str(seconds)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    until = time.time() + seconds
    STATE.write_text(json.dumps({"until": until, "pid": process.pid}))
    return until


def until() -> float | None:
    """When the block ends, or None if there isn't one."""
    state = _read()
    return state["until"] if state and state["until"] > time.time() and _running(state["pid"]) else None


def stop() -> bool:
    """Whether a block was actually running."""
    state = _read()
    STATE.unlink(missing_ok=True)
    if not state or not _running(state["pid"]):
        return False
    try:
        apps.end_tree(state["pid"])
    except ProcessLookupError:
        return False
    return True


def restore() -> None:
    """After a reboot the block is gone, but the promise to stay awake may still have time left."""
    state = _read()
    if state and state["until"] > time.time() and not _running(state["pid"]):
        start(int(state["until"] - time.time()))
