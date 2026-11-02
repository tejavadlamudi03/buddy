"""Persistent settings, stored with owner-only permissions in ~/.config/buddy."""
import json
import os
import secrets
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("BUDDY_CONFIG_DIR", Path.home() / ".config/buddy"))
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = Path(os.environ.get("BUDDY_DATA_DIR", Path.home() / ".local/share/buddy"))
SCREENSHOT_DIR = Path(os.environ.get("BUDDY_SCREENSHOT_DIR", Path.home() / "Pictures/Buddy"))

DEFAULTS = {
    "port": 8765,
    "whisper_model": "small.en",  # the laptop mic, where speed matters and it may run continuously
    "voice_note_model": "medium.en",  # WhatsApp voice notes: phone audio, where a few seconds is fine
    "telugu_whisper_model": str(DATA_DIR / "whisper-telugu-medium"),  # made by scripts/install-telugu.sh
    "languages_whisper_model": "medium",  # hears Hindi and German: fetched by scripts/install-languages.sh
    "whatsapp_phone": "",  # international format, digits only, e.g. 4915112345678
    "agent_enabled": True,  # False: requests the rules don't know just get "I didn't understand"
    "whatsapp_control": False,  # True: take commands from whatsapp_phone, and from nobody else
    "ai_model": "gemma4:e4b-it-q4_K_M",  # understands Telugu far better than qwen2.5vl:7b, in the same memory
    "ai_host": "127.0.0.1:11435",
    "ai_context": 12288,  # room for a long text to review, and still fully on an 8 GB GPU
    "agent_max_steps": 25,
    "share_port": 8766,
    "share_minutes": 60,
    "private_folders": [],  # relative to home; added to files.PRIVATE_DIRS. Restart Buddy after changing it
    "voice_style": "normal",  # normal, funny, robot or giant: see tts.STYLES
    "hey_buddy": False,  # True: the mic stays on while Buddy's window is open, listening for "Hey Buddy"
}

# What suits each kind of laptop, chosen by scripts/install.sh. Any of these can be changed afterwards in the config.
PROFILES = {
    "full": {"ai_model": "gemma4:e4b-it-q4_K_M", "ai_context": 12288, "agent_enabled": True,  # 8 GB of video memory
             "whisper_model": "small.en", "voice_note_model": "medium.en"},
    "light": {"ai_model": "gemma4:e2b", "ai_context": 8192, "agent_enabled": True,  # a smaller card, or none at all
              "whisper_model": "base.en", "voice_note_model": "small.en", "languages_whisper_model": "small"},
    "basic": {"agent_enabled": False, "whisper_model": "base.en", "voice_note_model": "small.en"},  # built-in commands only
}


def load() -> dict:
    stored = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    config = {**DEFAULTS, **stored}
    if "token" not in config:
        config["token"] = secrets.token_urlsafe(24)
        save(config)
    return config


def apply_profile(name: str) -> dict:
    config = {**load(), **PROFILES[name], "profile": name}
    save(config)
    return config


def save(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=2)
