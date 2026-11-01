"""Live screen on the phone, through a temporary public HTTPS link.

The phone gets its own small server on its own port, and the tunnel only reaches that
one, so Buddy's main token and settings can't be reached from the internet. Sharing is
off until asked for, expires by itself, and its token dies with the session. A link is
view-only unless control is explicitly allowed.
"""
import atexit
import base64
import hashlib
import re
import os
import secrets
import signal
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import qrcode
import qrcode.image.svg
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import apps, config, desktop

CLOUDFLARED_URL = "https://github.com/cloudflare/cloudflared/releases/download/2026.10.7/cloudflared-linux-amd64"
CLOUDFLARED_SHA256 = "1f99fb518b83dec2ba8d870a7cc991d82a77e6196295a3d2754ba17ab9f91872"
STATIC = Path(__file__).parent / "static"
TUNNEL_URL = re.compile(r"https://[\w-]+\.trycloudflare\.com")
MIN_FRAME_SECONDS = 0.15
CONNECTED_SECONDS = 5
EXTEND_SECONDS = 3600
MAX_SHARE_SECONDS = 12 * 3600


def _cloudflared() -> Path:
    path = config.DATA_DIR / "bin/cloudflared"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    download = path.with_name("cloudflared.download")
    if not download.exists():
        urllib.request.urlretrieve(CLOUDFLARED_URL, download)
    if hashlib.sha256(download.read_bytes()).hexdigest() != CLOUDFLARED_SHA256:
        download.unlink()
        raise RuntimeError("The tunnel program failed its checksum, so I didn't run it")
    download.chmod(0o700)
    download.rename(path)
    return path


@dataclass
class Session:
    token: str
    control: bool
    expires_at: float
    url: str = ""
    last_seen: float = 0.0
    frame: bytes = b""
    frame_time: float = 0.0
    frame_lock: threading.Lock = field(default_factory=threading.Lock)


class Tap(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    right: bool = False


class Typed(BaseModel):
    text: str


class Keys(BaseModel):
    combo: str


class Scroll(BaseModel):
    amount: int = Field(ge=-50, le=50)


class Share:
    def __init__(self, engine, transcriber):
        self.engine = engine
        self.transcriber = transcriber
        self.session: Session | None = None
        self._server = None
        self._tunnel = None
        self._timer = None
        self._awake = None
        self._lock = threading.Lock()
        atexit.register(self.stop)

    def start(self, control: bool = False) -> str:
        with self._lock:
            settings = config.load()
            if self.session:
                self.session.control = self.session.control or control
                return self._ready_message()
            self.session = Session(secrets.token_urlsafe(32), control,
                                   time.time() + settings["share_minutes"] * 60)
            try:
                self._start_server(settings["share_port"])
                self.session.url = f"{self._start_tunnel(settings['share_port'])}/#token={self.session.token}"
            except Exception:
                self._shutdown()
                raise
            self._schedule_expiry(settings["share_minutes"] * 60)
            self._keep_awake()
            return self._ready_message()

    def _schedule_expiry(self, seconds: float) -> None:
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(seconds, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def _keep_awake(self) -> None:
        """A sleeping laptop can't be reached, and nobody is there to wake it."""
        self._awake = subprocess.Popen(
            ["systemd-inhibit", "--what=sleep:handle-lid-switch", "--who=Buddy",
             "--why=Your screen is being shared", "sleep", "infinity"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )

    def extend(self) -> int:
        """Adds time from the phone: once a link expires it can only be restarted at the laptop."""
        with self._lock:
            session = self.session
            if session is None:
                return 0
            session.expires_at = min(session.expires_at + EXTEND_SECONDS, time.time() + MAX_SHARE_SECONDS)
            self._schedule_expiry(session.expires_at - time.time())
            return round(session.expires_at - time.time())

    def _ready_message(self) -> str:
        kind = "with control" if self.session.control else "view only"
        return f"Your screen is ready to share, {kind}. Scan the code on my page with your phone."

    def stop(self) -> str:
        with self._lock:
            active = self.session is not None
            self._shutdown()
        return "Stopped sharing your screen" if active else "Your screen isn't being shared"

    def _expire(self) -> None:
        if self.session:
            self.stop()
            self.engine.emit("say", "Screen sharing expired, so I turned it off")

    def _shutdown(self) -> None:
        self.session = None  # the token stops working first, before anything is torn down
        if self._timer:
            self._timer.cancel()
        if self._tunnel:
            self._tunnel.terminate()
        if self._server:
            self._server.should_exit = True
        if self._awake:
            try:
                os.killpg(self._awake.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self._timer = self._tunnel = self._server = self._awake = None

    def _start_server(self, port: int) -> None:
        server = uvicorn.Server(uvicorn.Config(phone_app(self), host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("The phone server didn't start")
            time.sleep(0.05)
        self._server = server

    def _start_tunnel(self, port: int) -> str:
        # An empty config keeps any personal cloudflared setup from changing where this points.
        self._tunnel = subprocess.Popen(
            [str(_cloudflared()), "tunnel", "--no-autoupdate", "--config", "/dev/null",
             "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, env=apps.session_env(),
        )
        found = {}
        ready = threading.Event()

        def read_log(stream):
            for line in stream:  # keep draining for the tunnel's whole life, or it would block
                if match := TUNNEL_URL.search(line):
                    found["url"] = match.group()
                if "Registered tunnel connection" in line:
                    ready.set()

        threading.Thread(target=read_log, args=(self._tunnel.stderr,), daemon=True).start()
        if not ready.wait(45) or "url" not in found:
            raise RuntimeError("I couldn't open the online link. Check the internet connection")
        return found["url"]

    def status(self) -> dict:
        session = self.session
        if session is None or not session.url:
            return {"active": False}
        code = qrcode.make(session.url, image_factory=qrcode.image.svg.SvgPathImage).to_string()
        return {
            "active": True,
            "url": session.url,
            "control": session.control,
            "seconds_left": max(0, round(session.expires_at - time.time())),
            "connected": time.time() - session.last_seen < CONNECTED_SECONDS,
            "qr": "data:image/svg+xml;base64," + base64.b64encode(code).decode(),
        }

    def frame(self, session: Session) -> bytes:
        """One capture serves every viewer, at a capped rate."""
        with session.frame_lock:
            if time.monotonic() - session.frame_time >= MIN_FRAME_SECONDS:
                session.frame = desktop.frame_jpeg()
                session.frame_time = time.monotonic()
            return session.frame


def phone_app(share: Share) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def viewer(x_buddy_token: str = Header(default="")) -> Session:
        session = share.session
        if session is None or not secrets.compare_digest(x_buddy_token.encode(), session.token.encode()):
            raise HTTPException(status_code=401, detail="sharing has ended")
        if session.last_seen == 0.0:  # announced aloud, so an unexpected viewer gets noticed
            share.engine.emit("say", "A device just connected to your screen")
        session.last_seen = time.time()
        return session

    def controller(session: Session = Depends(viewer)) -> Session:
        if not session.control:
            raise HTTPException(status_code=403, detail="this link is view only")
        return session

    control = [Depends(controller)]

    @app.get("/")
    def page():
        return FileResponse(STATIC / "phone.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @app.get("/api/info")
    def info(session: Session = Depends(viewer)):
        return {"control": session.control, "seconds_left": max(0, round(session.expires_at - time.time())),
                "last_event": len(share.engine.events)}

    @app.get("/api/frame")
    def frame(session: Session = Depends(viewer)):
        return Response(share.frame(session), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/tap", dependencies=control)
    def tap(body: Tap):
        left, top, width, height = desktop.screen_rect()
        desktop.click(left + round(body.x * (width - 1)), top + round(body.y * (height - 1)), right=body.right)
        return {"ok": True}

    @app.post("/api/type", dependencies=control)
    def type_text(body: Typed):
        desktop.focus_target()
        desktop.type_text(body.text)
        return {"ok": True}

    @app.post("/api/key", dependencies=control)
    def key(body: Keys):
        desktop.focus_target()
        try:
            desktop.press_keys(body.combo)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return {"ok": True}

    @app.post("/api/scroll", dependencies=control)
    def scroll(body: Scroll):
        desktop.scroll(body.amount)
        return {"ok": True}

    @app.post("/api/command", dependencies=control)
    def command(body: Typed):
        return {"accepted": share.engine.submit(body.text)}

    @app.post("/api/audio", dependencies=control)
    def audio(file: UploadFile):
        text = share.transcriber.transcribe(file.file.read())
        return {"text": text, "accepted": share.engine.submit(text)}

    @app.get("/api/events", dependencies=control)
    def events(after: int = 0):
        return {"events": share.engine.events_after(after), "busy": share.engine.busy}

    @app.post("/api/extend", dependencies=control)
    def extend():
        return {"seconds_left": share.extend()}

    @app.post("/api/stop")
    def stop(session: Session = Depends(viewer)):
        message = share.stop()
        share.engine.emit("say", message)
        return {"ok": True}

    return app


current: Share | None = None


def configure(engine, transcriber) -> None:
    global current
    current = Share(engine, transcriber)


def start(control: bool = False) -> str:
    if current is None:
        raise RuntimeError("Screen sharing isn't available right now")
    return current.start(control)


def stop() -> str:
    return current.stop() if current else "Your screen isn't being shared"

