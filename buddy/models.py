"""Which local AI models this laptop can run, and getting new ones. Ollama's cloud models are refused, as they'd send everything off the laptop."""
import functools
import re
import shutil
import subprocess
import threading
from pathlib import Path

from . import ai, config

GB = 1_000_000_000
GPU_ROOM_GB = 1.5  # beyond the download size, for the conversation it holds in memory
MEMORY_ROOM_GB = 4  # beyond the download size, for everything else that's running
DISK_ROOM_GB = 1
SMALL_ENOUGH_FOR_CPU_GB = 5
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)?(?::[A-Za-z0-9][A-Za-z0-9._-]*)?")

# Best first. Sizes are from the Ollama registry, in GB. "sees": it can look at the screen and at pictures.
CATALOG = (
    {"name": "mistral-small3.1:latest", "gb": 15.5, "sees": True, "note": "Large, for graphics cards of 16 GB or more"},
    {"name": "llama3.2-vision:11b", "gb": 7.8, "sees": True, "note": "For graphics cards above 8 GB"},
    {"name": "gemma4:e4b-it-q4_K_M", "gb": 6.6, "sees": True, "note": "Tested with Buddy. Understands Telugu best"},
    {"name": "qwen2.5vl:7b", "gb": 6.0, "sees": True, "note": "Tested with Buddy. Good on screens, weaker at Telugu"},
    {"name": "gemma4:e2b", "gb": 4.6, "sees": True, "note": "A lighter Gemma, for a smaller card or none"},
    {"name": "gemma3:4b", "gb": 3.4, "sees": True, "note": "Older and smaller"},
    {"name": "qwen2.5vl:3b", "gb": 3.2, "sees": True, "note": "Small, for 4 GB cards"},
    {"name": "qwen3:4b", "gb": 2.5, "sees": False, "note": "Quick for chat and games"},
    {"name": "llama3.2:3b", "gb": 2.0, "sees": False, "note": "Quick for chat and games"},
    {"name": "granite3.3:2b", "gb": 1.6, "sees": False, "note": "The smallest here"},
)


def check_name(name: str) -> str:
    """The name, tidied, or ValueError with a message for the user."""
    name = name.strip()
    if not _NAME.fullmatch(name):
        raise ValueError("That doesn't look like a model name. They look like gemma4:e2b")
    if "cloud" in name.lower():
        raise ValueError("That one runs on Ollama's servers, not on this laptop, so Buddy won't use it")
    return name


@functools.cache
def _chips() -> tuple:
    """(graphics card name, its memory in GB, the laptop's memory in GB). These don't change while Buddy runs."""
    memory_kb = next(int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal"))
    card, video = "", 0.0
    try:
        output = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,name", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=10, check=True).stdout
        cards = sorted((float(line.split(",", 1)[0]), line.split(",", 1)[1].strip()) for line in output.splitlines() if line.strip())
        if cards:
            video, card = cards[-1][0] * 1024 * 1024 / GB, cards[-1][1]
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass  # no NVIDIA card, or no working driver for one: the model then runs on the processor
    return card, round(video, 1), round(memory_kb * 1000 / GB, 1)


def hardware() -> dict:
    card, video, memory = _chips()
    return {"card": card, "video_gb": video, "memory_gb": memory, "free_gb": round(shutil.disk_usage(Path.home()).free / GB, 1)}


def fit(gb: float, laptop: dict) -> dict:
    """How well a model of this size will run: "good", "slow" or "big", with the reason."""
    if gb + GPU_ROOM_GB <= laptop["video_gb"]:
        return {"level": "good", "text": "Runs well on this laptop"}
    if gb + MEMORY_ROOM_GB <= laptop["memory_gb"]:
        return {"level": "slow", "text": "Will work, but slowly, as it's too big for the graphics card" if laptop["video_gb"] else "Will work, but slowly, with no graphics card to run it"}
    return {"level": "big", "text": "Too big for this laptop's memory"}


def _recommended(catalog: list) -> str:
    for model in catalog:
        if model["fit"]["level"] == "good":
            return model["name"]
    return next((model["name"] for model in catalog if model["fit"]["level"] == "slow" and model["gb"] <= SMALL_ENOUGH_FOR_CPU_GB), "")


class Download:
    """One model at a time, so a laptop's connection and disk aren't fought over."""

    def __init__(self):
        self.progress = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return bool(self.progress and self.progress["state"] == "downloading")

    def start(self, name: str) -> None:
        name = check_name(name)
        with self._lock:
            if self.active:
                raise RuntimeError(f"Still downloading {self.progress['name']}")
            self.progress = {"name": name, "done": 0, "total": 0, "state": "downloading", "error": ""}
            self._cancel.clear()
        threading.Thread(target=self._run, args=(name,), daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _track(self, done: int, total: int) -> None:
        if total - done > shutil.disk_usage(Path.home()).free - DISK_ROOM_GB * GB:
            raise RuntimeError(f"Not enough free disk: this model needs about {total / GB:.1f} GB")
        self.progress.update(done=done, total=total)

    def _run(self, name: str) -> None:
        try:
            finished = ai.pull(name, self._track, self._cancel.is_set)
            self.progress["state"] = "finished" if finished else "cancelled"
        except (RuntimeError, ValueError, OSError) as error:
            self.progress.update(state="failed", error=str(error)[:200])


download = Download()


def status() -> dict:
    """Everything the settings need: this laptop, the models on it, and those it could get."""
    laptop = hardware()
    settings = config.load()
    try:
        ai.ensure_running()
        present = ai.installed_models()
        problem = ""
    except (RuntimeError, OSError) as error:
        present, problem = {}, str(error)
    known = {model["name"]: model for model in CATALOG}
    installed = [{"name": name, "gb": round(size / GB, 1), "fit": fit(size / GB, laptop),
                  "note": known[name]["note"] if name in known else ""}
                 for name, size in sorted(present.items()) if "cloud" not in name]
    catalog = [{**model, "fit": fit(model["gb"], laptop)} for model in CATALOG]
    return {"current": settings["ai_model"], "laptop": laptop, "installed": installed, "problem": problem,
            "available": [model for model in catalog if model["name"] not in present],
            "recommended": _recommended(catalog), "download": download.progress}


def use(name: str) -> dict:
    """Takes effect from the next thing asked. Only a model that's already here, as a missing one would just fail later."""
    name = check_name(name)
    ai.ensure_running()
    if name not in ai.installed_models():
        raise ValueError(f"{name} isn't downloaded yet")
    settings = config.load()
    settings["ai_model"] = name
    config.save(settings)
    return {"current": name, "sees": ai.can_see(name)}
