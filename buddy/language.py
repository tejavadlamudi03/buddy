"""English and Telugu: which one was spoken, and what a message means. All done by the local model."""
import re

from . import ai, config
from .ai import extract_json

# Short replies are matched exactly: they're the words that matter most, and too short for the model to judge.
TELUGU_STOP = "ఆపు|ఆప్|ఆపండి|ఆపేయి|ఆపేయ్|aapu|apu|aapeyi|apeyi"
TELUGU_YES = "అవును|ఔను|సరే|avunu|aunu|sare"
TELUGU_NO = "వద్దు|కాదు|లేదు|vaddu|vaddhu|kaadu|ledu"
_TELUGU_REPLY = re.compile(rf"(?:{TELUGU_STOP}|{TELUGU_YES}|{TELUGU_NO})[.!\s]*", re.I)
TELUGU_SCRIPT = re.compile(r"[ఀ-౿]")

_PICK = """Someone spoke one short sentence to a laptop assistant, in English or Telugu. Two speech recognisers transcribed it, one that only knows English and one that only knows Telugu. Whichever is wrong forces the sounds into its own language, and the English one can even invent whole sentences.
Look at the Telugu transcript. If any of it is real Telugu grammar or vocabulary, such as a Telugu verb or ending, the person spoke Telugu, however sensible the English transcript looks. If all of it is only English words spelled in Telugu letters, the person spoke English.
Examples:
English: "Mail terrible" Telugu: "మెయిల్ తెరువు" -> telugu
English: "Thanks for watching this video." Telugu: "ఫోటోలు చూపించు" -> telugu
English: "Wi-Fi off check" Telugu: "వైఫై ఆఫ్ చెయ్యి" -> telugu
English: "Show me the photos." Telugu: "షో మీ ద ఫోటోస్" -> english
English: "How is the weather?" Telugu: "హౌ ఇస్ ద వెదర్" -> english
Reply with JSON only: {"language": "english" or "telugu"}"""

_UNDERSTAND = """You read messages sent to an assistant that can control a laptop and can also chat. They may be in English, in Telugu script, or in Telugu written with English letters.
Reply with JSON only: {"english": "...", "kind": "control" or "chat"}
"english": the message in plain English, as a short command if it is one. Keep names of apps, songs and files as they are.
"kind": "control" if it needs the laptop: doing something on it, or looking at its screen, windows, files or settings.
"chat" if it can be answered without the laptop: a question, or a request to write, review, rewrite, translate or explain text or code."""

CHAT_PROMPT = """You are Buddy, a personal assistant chatting with the user, usually over WhatsApp. Help with whatever they ask: answer questions, review or rewrite text, write and explain code, translate, think things through.
Reply in the user's language: Telugu script for Telugu script, Telugu in English letters for Telugu in English letters, otherwise English, unless they ask for another.
Be direct and complete, with no filler before the answer. Format for WhatsApp: *bold* for emphasis and triple backticks for code. No tables, # headings or LaTeX.
In this chat you can't see the laptop's screen, change anything on it, or look things up online. Say so if that's what an answer would need, and say when you're unsure rather than guessing."""


def chat_prompt(text: str) -> str:
    """The model tends to drift into English, so Telugu script gets a firmer instruction."""
    if TELUGU_SCRIPT.search(text):
        return CHAT_PROMPT + "\nThe user's latest message is in Telugu, so reply in Telugu script unless it asks for another language."
    return CHAT_PROMPT


def tidy(answer: str) -> str:
    """Turns the model's Markdown and LaTeX habits into what WhatsApp shows properly. Code is left alone."""
    parts = answer.split("```")
    for index in range(0, len(parts), 2):  # odd parts are inside code blocks
        parts[index] = re.sub(r"\*{2,}", "*", parts[index]).replace("$\\rightarrow$", "→")
    return "```".join(parts)


def pick(english: str, telugu: str) -> str:
    """"english" or "telugu". English when the model can't say, as that's what Buddy already acts on."""
    words = len(english.split())
    if words >= 5 and len(telugu.split()) * 4 <= words:  # most of the sentence meant nothing to the Telugu model
        return "english"
    if _TELUGU_REPLY.fullmatch(telugu):
        return "telugu"
    prompt = f"English recogniser: {english}\nTelugu recogniser: {telugu}"
    try:
        reply = extract_json(ai.chat(config.load()["ai_model"], _PICK, prompt))
    except (RuntimeError, ValueError, OSError):
        return "english"
    return "telugu" if reply.get("language") == "telugu" else "english"


def understand(text: str) -> tuple[str, str]:
    """(the message in English, "control" or "chat"). Unclear messages are control, so the agent reports problems."""
    try:
        reply = extract_json(ai.chat(config.load()["ai_model"], _UNDERSTAND, text))
    except (RuntimeError, ValueError, OSError):
        return text, "control"
    return (reply.get("english") or text).strip(), "chat" if reply.get("kind") == "chat" else "control"
