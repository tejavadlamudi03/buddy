"""The local AI: an open source model run by Ollama, on this machine, on loopback only."""
import base64
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from . import apps, config

MAX_REPLY_TOKENS = 400  # a reply is a short JSON object; more means the model is stuck repeating itself
MAX_TALK_TOKENS = 2000
PULL_STALL_SECONDS = 120  # a download with no news for this long has died
INSTALL_HINT = "The local AI isn't installed yet. Run the install script in Buddy's scripts folder."


class NotReady(RuntimeError):
    """Ollama or the model is missing. Its message is meant to be spoken to the user."""


def _url(path: str) -> str:
    return f"http://{config.load()['ai_host']}{path}"


def _binary() -> str | None:
    bundled = config.DATA_DIR / ("ollama/ollama.exe" if config.WINDOWS else "ollama/bin/ollama")
    return str(bundled) if bundled.exists() else shutil.which("ollama")


def installed() -> bool:
    return _binary() is not None or _is_up()


def _is_up() -> bool:
    try:
        urllib.request.urlopen(_url("/api/version"), timeout=1)
        return True
    except OSError:
        return False


def ensure_running() -> None:
    if _is_up():
        return
    binary = _binary()
    if binary is None:
        raise NotReady(INSTALL_HINT)
    env = {**apps.session_env(), "OLLAMA_HOST": config.load()["ai_host"],
           "OLLAMA_MODELS": str(config.DATA_DIR / "ollama/models")}
    subprocess.Popen([binary, "serve"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, **config.NO_WINDOW)
    deadline = time.monotonic() + 20
    while not _is_up():
        if time.monotonic() > deadline:
            raise NotReady("The local AI didn't start")
        time.sleep(0.25)


def installed_models() -> dict:
    """Each downloaded model's name and size in bytes."""
    with urllib.request.urlopen(_url("/api/tags"), timeout=10) as response:
        return {model["name"]: model["size"] for model in json.load(response)["models"]}


def can_see(model: str) -> bool:
    """Whether it takes pictures. If that can't be found out, the attempt itself will say."""
    request = urllib.request.Request(_url("/api/show"), data=json.dumps({"model": model}).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return "vision" in json.load(response).get("capabilities", ["vision"])
    except (OSError, ValueError):
        return True


def pull(model: str, progress, cancelled) -> bool:
    """Downloads a model, calling progress(done, total) in bytes. False if cancelled. Starting again resumes it."""
    ensure_running()
    request = urllib.request.Request(_url("/api/pull"), data=json.dumps({"model": model}).encode(),
                                     headers={"Content-Type": "application/json"})
    layers = {}
    try:
        with urllib.request.urlopen(request, timeout=PULL_STALL_SECONDS) as response:
            for line in response:
                if cancelled():
                    return False
                news = json.loads(line)
                if news.get("error"):
                    raise RuntimeError(news["error"])
                if news.get("digest") and news.get("total"):
                    layers[news["digest"]] = (news.get("completed", 0), news["total"])
                    progress(sum(done for done, _ in layers.values()), sum(total for _, total in layers.values()))
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read()).get("error")
        except ValueError:
            message = None
        raise RuntimeError(f"Couldn't download {model}: {message or error.code}") from None
    return True


def extract_json(reply: str) -> dict:
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"not JSON: {reply[:100]}")
    return json.loads(reply[start:end + 1])


def chat(model: str, system: str, prompt: str, image_jpeg: bytes | None = None, timeout: float = 240, temperature: float = 0.1) -> str:
    ensure_running()
    user = {"role": "user", "content": prompt}
    if image_jpeg:
        user["images"] = [base64.b64encode(image_jpeg).decode()]
    body = {
        "model": model, "stream": False, "format": "json", "think": False, "keep_alive": "5m",
        "options": {"temperature": temperature, "num_ctx": config.load()["ai_context"], "num_predict": MAX_REPLY_TOKENS},
        "messages": [{"role": "system", "content": system}, user],
    }
    return _post(body, model, timeout)


def talk(model: str, system: str, messages: list, timeout: float = 240, max_tokens: int = MAX_TALK_TOKENS, temperature: float = 0.3) -> str:
    """A free-form conversation, rather than the short JSON that chat() asks for."""
    ensure_running()
    body = {
        "model": model, "stream": False, "think": False, "keep_alive": "5m",
        "options": {"temperature": temperature, "num_ctx": config.load()["ai_context"], "num_predict": max_tokens},
        "messages": [{"role": "system", "content": system}, *messages],
    }
    return _post(body, model, timeout)


def _post(body: dict, model: str, timeout: float) -> str:
    request = urllib.request.Request(_url("/api/chat"), data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)["message"]["content"]
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise NotReady(f"The model {model} isn't downloaded yet. Run the install script again.") from None
        raise RuntimeError(f"The local AI failed: {error.read().decode(errors='replace')[:200]}") from None
