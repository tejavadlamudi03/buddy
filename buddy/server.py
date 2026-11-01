"""The local web app. Binds to loopback; every API call needs the secret token."""
import secrets
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import actions, awake, config, guests, reminders, share, whatsapp
from .engine import Engine
from .listen import Listener, telugu_ear
from .stt import Transcriber
from .tts import Speaker

STATIC = Path(__file__).parent / "static"


class Command(BaseModel):
    text: str
    require_wake: bool = False


class ShareRequest(BaseModel):
    control: bool = False


class Settings(BaseModel):
    whatsapp_phone: str | None = None
    agent_enabled: bool | None = None
    whatsapp_control: bool | None = None
    hey_buddy: bool | None = None
    guest_password: str | None = None  # empty turns guests off


def hear(engine: Engine, transcriber, audio: bytes, require_wake: bool) -> dict:
    """require_wake: from always-on listening, so it may not be meant for Buddy at all."""
    # At a lock screen, anyone in the room could say it. And while talking, this is Buddy hearing itself.
    if require_wake and (actions.is_locked() or (engine.speaker and engine.speaker.speaking)):
        return {"text": "", "accepted": False}
    # Settled on arrival: the window to answer shouldn't close while the answer is being transcribed.
    unaddressed = require_wake and not engine.listening()
    text = transcriber.transcribe(audio, hands_free=unaddressed)
    return {"text": text, "accepted": engine.submit(text, unaddressed)}


def create_app(engine: Engine | None = None, transcriber: Transcriber | None = None) -> FastAPI:
    settings = config.load()
    awake.restore()
    engine = engine or Engine(speaker=Speaker())
    telugu = telugu_ear()
    transcriber = transcriber or Listener(Transcriber(settings["whisper_model"]), telugu)
    threading.Thread(target=transcriber.load, daemon=True).start()
    share.configure(engine, transcriber)
    voice_notes = Listener(Transcriber(settings["voice_note_model"], beam_size=5, skip_silence=False), telugu)
    threading.Thread(target=voice_notes.load, daemon=True).start()
    whatsapp.start_bridge(engine, voice_notes)
    reminders.start(engine)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    # Stops other websites reaching this server by pointing their own domain at 127.0.0.1.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    def authorized(x_buddy_token: str = Header(default="")) -> None:
        # A custom header also forces a CORS preflight, which this server never approves.
        if not secrets.compare_digest(x_buddy_token.encode(), settings["token"].encode()):
            raise HTTPException(status_code=401, detail="bad token")

    guarded = [Depends(authorized)]

    @app.get("/")
    def page():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})  # or Chrome keeps showing an old page

    @app.post("/api/command", dependencies=guarded)
    def command(body: Command):
        return {"accepted": engine.submit(body.text, body.require_wake)}

    @app.post("/api/audio", dependencies=guarded)
    def audio(file: UploadFile, require_wake: bool = False):
        return hear(engine, transcriber, file.file.read(), require_wake)

    @app.post("/api/stop", dependencies=guarded)
    def stop():
        engine.stop()
        return {"ok": True}

    @app.get("/api/events", dependencies=guarded)
    def events(after: int = 0):
        speaking = bool(engine.speaker and engine.speaker.speaking)
        return {"events": engine.events_after(after), "busy": engine.busy, "speaking": speaking, "listening": engine.listening()}

    @app.get("/api/share", dependencies=guarded)
    def share_status():
        return share.current.status()

    @app.post("/api/share", dependencies=guarded)
    def share_start(body: ShareRequest):
        try:
            engine.emit("say", actions.run("share_screen", control=body.control))
        except Exception as error:
            engine.emit("error", str(error))
        return share.current.status()

    @app.delete("/api/share", dependencies=guarded)
    def share_stop():
        engine.emit("say", actions.run("stop_sharing"))
        return share.current.status()

    @app.get("/api/settings", dependencies=guarded)
    def read_settings():
        current = config.load()
        return {**{key: current[key] for key in ("whatsapp_phone", "agent_enabled", "whatsapp_control", "ai_model", "voice_style", "hey_buddy")},
                "guests_on": guests.enabled()}

    @app.post("/api/settings", dependencies=guarded)
    def write_settings(body: Settings):
        current = config.load()
        if body.whatsapp_phone is not None:
            current["whatsapp_phone"] = "".join(c for c in body.whatsapp_phone if c.isdigit())
        if body.agent_enabled is not None:
            current["agent_enabled"] = body.agent_enabled
        if body.whatsapp_control is not None:
            current["whatsapp_control"] = body.whatsapp_control
        if body.hey_buddy is not None:
            current["hey_buddy"] = body.hey_buddy
        if body.guest_password is not None:
            try:
                guests.set_password(body.guest_password) if body.guest_password else guests.turn_off()
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from None
        config.save(current)
        return {**{key: current[key] for key in ("whatsapp_phone", "agent_enabled", "whatsapp_control")}, "guests_on": guests.enabled()}

    return app
