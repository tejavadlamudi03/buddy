"""The local web app. Binds to loopback; every API call needs the secret token."""
import secrets
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import actions, awake, config, reminders, share, whatsapp
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
        return FileResponse(STATIC / "index.html")

    @app.post("/api/command", dependencies=guarded)
    def command(body: Command):
        return {"accepted": engine.submit(body.text, body.require_wake)}

    @app.post("/api/audio", dependencies=guarded)
    def audio(file: UploadFile, require_wake: bool = False):
        text = transcriber.transcribe(file.file.read(), hands_free=require_wake and not engine.listening())
        return {"text": text, "accepted": engine.submit(text, require_wake)}

    @app.post("/api/stop", dependencies=guarded)
    def stop():
        engine.stop()
        return {"ok": True}

    @app.get("/api/events", dependencies=guarded)
    def events(after: int = 0):
        speaking = bool(engine.speaker and engine.speaker.speaking)
        return {"events": engine.events_after(after), "busy": engine.busy, "speaking": speaking}

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
        return {key: current[key] for key in ("whatsapp_phone", "agent_enabled", "whatsapp_control", "ai_model")}

    @app.post("/api/settings", dependencies=guarded)
    def write_settings(body: Settings):
        current = config.load()
        if body.whatsapp_phone is not None:
            current["whatsapp_phone"] = "".join(c for c in body.whatsapp_phone if c.isdigit())
        if body.agent_enabled is not None:
            current["agent_enabled"] = body.agent_enabled
        if body.whatsapp_control is not None:
            current["whatsapp_control"] = body.whatsapp_control
        config.save(current)
        return {key: current[key] for key in ("whatsapp_phone", "agent_enabled", "whatsapp_control")}

    return app
