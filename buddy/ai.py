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
INSTALL_HINT = "The local AI isn't installed yet. Run the install script in Buddy's scripts folder."


class NotReady(RuntimeError):
    """Ollama or the model is missing. Its message is meant to be spoken to the user."""


def _url(path: str) -> str:
    return f"http://{config.load()['ai_host']}{path}"


def _binary() -> str | None:
    bundled = config.DATA_DIR / "ollama/bin/ollama"
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
                     stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + 20
    while not _is_up():
        if time.monotonic() > deadline:
            raise NotReady("The local AI didn't start")
        time.sleep(0.25)


def extract_json(reply: str) -> dict:
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"not JSON: {reply[:100]}")
    return json.loads(reply[start:end + 1])


def chat(model: str, system: str, prompt: str, image_jpeg: bytes | None = None, timeout: float = 240) -> str:
    ensure_running()
    user = {"role": "user", "content": prompt}
    if image_jpeg:
        user["images"] = [base64.b64encode(image_jpeg).decode()]
    body = {
        "model": model, "stream": False, "format": "json", "think": False, "keep_alive": "5m",
        "options": {"temperature": 0.1, "num_ctx": config.load()["ai_context"], "num_predict": MAX_REPLY_TOKENS},
        "messages": [{"role": "system", "content": system}, user],
    }
    return _post(body, model, timeout)


def talk(model: str, system: str, messages: list, timeout: float = 240) -> str:
    """A free-form conversation, rather than the short JSON that chat() asks for."""
    ensure_running()
    body = {
        "model": model, "stream": False, "think": False, "keep_alive": "5m",
        "options": {"temperature": 0.3, "num_ctx": config.load()["ai_context"], "num_predict": MAX_TALK_TOKENS},
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
