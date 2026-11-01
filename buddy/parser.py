"""Turns everyday speech into action steps instantly, with no network or LLM.

Returns None when any part of a sentence isn't recognised, so the whole request
goes to the screen-aware agent instead of being half executed.
"""
import re
from dataclasses import dataclass

from .apps import SITES, find_app

Step = dict

LEAD_IN = re.compile(
    r"^\s*(?:(?:hey|hi|ok|okay|yo)[,\s]+)?(?:(?:buddy|body|birdie)\b[,.!:\s]*)?"
    r"(?:(?:please|can you|could you|would you|will you|i want you to|i want to|i need you to)\s+)*",
    re.I,
)
VERBS = (
    r"open|launch|start|go to|goto|navigate to|visit|search|look up|google|play|pause|resume|"
    r"send|take|capture|grab|close|minimi[sz]e|maximi[sz]e|switch to|focus|type|write|press|hit|"
    r"scroll|click|double click|right click|tap|select|choose|read|copy|paste|cut|undo|redo|save|select all|refresh|reload|"
    r"mute|unmute|turn|volume|next|previous|new tab|go back|go forward|lock|unlock|shut ?down|restart|reboot|"
    r"change|skip|put on|listen to|share|stream|stop sharing|run|find|list"
)
CLAUSE_BREAK = re.compile(rf"\s*(?:,\s*)?(?:\band then\b|\bthen\b|\band\b|,)\s+(?=(?:{VERBS})\b)", re.I)
TYPING = re.compile(r"^(?:type|write|run)\b", re.I)  # dictated words and commands keep their "and"s
MEDIA_ACTIONS = {"next_track", "previous_track", "pause_media", "resume_media", "now_playing"}
SONG = r"(?:song|track|video|music)"
BROWSER_NAMES = {"chrome", "google chrome", "browser", "the browser"}
PRONOUN = r"(?:it|this|that|the song|this song|that song|the video|this video|that video|the first (?:one|result|video)|first (?:one|result|video))"

KEY_COMMANDS = {
    r"(?:open |create )?(?:a )?new tab": "ctrl+t",
    r"close (?:this |the )?(?:current )?tab": "ctrl+w",
    r"(?:reopen|restore) (?:the )?(?:last |closed )*tab": "ctrl+shift+t",
    r"(?:go to )?(?:the )?next tab": "ctrl+tab",
    r"(?:open |create )?(?:a )?new window": "ctrl+n",
    r"find(?: on (?:this |the )?page)?": "ctrl+f",
    r"print(?: this| it)?": "ctrl+p",
    r"zoom in": "ctrl+plus",
    r"zoom out": "ctrl+minus",
    r"reset (?:the )?zoom": "ctrl+0",
    r"(?:go to )?(?:the )?next workspace": "super+pagedown",
    r"(?:go to )?(?:the )?previous workspace": "super+pageup",
    r"show (?:me )?(?:all )?(?:the )?(?:windows|apps|applications)|(?:show |open )?(?:the )?(?:overview|activities)": "super",
    r"show (?:me )?(?:the |my )?notifications": "super+v",
    r"snap (?:this |the )?(?:window )?(?:to the )?left": "super+left",
    r"snap (?:this |the )?(?:window )?(?:to the )?right": "super+right",
    r"(?:start|stop) (?:the )?(?:screen )?recording|record (?:the |my )?screen": "ctrl+shift+alt+r",
    r"(?:go to )?(?:the )?previous tab": "ctrl+shift+tab",
    r"go back|back": "alt+left",
    r"go forward|forward": "alt+right",
    r"refresh|reload|(?:refresh|reload) (?:this |the )?page": "ctrl+r",
    r"copy(?: this| that| it)?": "ctrl+c",
    r"paste(?: this| that| it)?": "ctrl+v",
    r"cut(?: this| that| it)?": "ctrl+x",
    r"undo(?: this| that| it)?": "ctrl+z",
    r"redo(?: this| that| it)?": "ctrl+y",
    r"select all": "ctrl+a",
    r"save(?: this| it| the file| file)?": "ctrl+s",
    r"(?:press |hit )?enter": "enter",
    r"switch windows?|next window|alt tab": "alt+tab",
    r"show (?:the )?desktop": "super+d",
    r"(?:go |enter |exit )?full ?screen": "f11",
}
_OF_LAPTOP = r"(?: of| on| in)?(?: the| my| this)?(?: laptop| computer| system| speakers?)?"
FIXED = {
    r"pause(?: the)?(?: music| song| video| it| this)?|stop the (?:music|song|video)": ("pause_media", {}),
    r"resume(?: the)?(?: music| song| video| it)?|play|continue(?: playing)?|unpause": ("resume_media", {}),
    rf"(?:change|skip)(?: it| this| that| the(?: current)?)?(?: {SONG})?|switch (?:the |this )?{SONG}": ("next_track", {}),
    rf"(?:play |go to |skip to |change to |switch to )?(?:the )?next(?: {SONG}| one)?": ("next_track", {}),
    rf"(?:play |put on |change to |switch to )(?:an?|some)?\s*(?:another|different|other)(?: {SONG}| one)": ("next_track", {}),
    rf"i don'?t like this(?: {SONG})?": ("next_track", {}),
    rf"(?:play |go to |go back to |change to |switch to )?(?:the )?(?:previous|last)(?: {SONG}| one)": ("previous_track", {}),
    r"previous": ("previous_track", {}),
    rf"what(?:'s| is)(?: this)?(?: {SONG})?(?: is)?(?: this| playing)|what {SONG} is (?:this|playing)|which {SONG} is (?:this|playing)|what am i listening to": ("now_playing", {}),
    r"mute|unmute|(?:un)?mute (?:the )?(?:sound|volume|audio)": ("volume", {"change": "toggle"}),
    rf"(?:turn )?(?:the )?volume up{_OF_LAPTOP}|turn up (?:the )?(?:volume|sound|music){_OF_LAPTOP}|(?:increase|raise) (?:the )?(?:volume|sound){_OF_LAPTOP}|turn it up|(?:make (?:it|the sound|the music|the volume) )?(?:a bit |a little |a touch )?(?:louder|higher)": ("volume", {"change": "up"}),
    rf"(?:turn )?(?:the )?volume down{_OF_LAPTOP}|turn down (?:the )?(?:volume|sound|music){_OF_LAPTOP}|(?:decrease|lower|reduce) (?:the )?(?:volume|sound){_OF_LAPTOP}|turn it down|(?:make (?:it|the sound|the music|the volume) )?(?:a bit |a little |a touch )?(?:quieter|softer|lower)": ("volume", {"change": "down"}),
    rf"(?:set )?(?:the )?volume(?: to)? (?:max|maximum|full)|(?:max|maximum|full) volume{_OF_LAPTOP}": ("volume", {"change": "set", "level": 100}),
    r"scroll down(?: a bit)?": ("scroll", {"amount": -5}),
    r"scroll up(?: a bit)?": ("scroll", {"amount": 5}),
    r"scroll down a lot": ("scroll", {"amount": -20}),
    r"scroll up a lot": ("scroll", {"amount": 20}),
    r"click": ("click", {}),
    r"read (?:the |my |this )?(?:screen|page|window)|what(?:'s| is) on (?:the |my )?screen|what does it say": ("read_screen", {}),
    r"double click": ("click", {"count": 2}),
    r"right click": ("click", {"right": True}),
    r"close(?: this| the)?(?: current)?(?: window| app| application)?": ("close_window", {}),
    r"minimi[sz]e(?: this| the)?(?: window| app)?": ("minimize_window", {}),
    r"maximi[sz]e(?: this| the)?(?: window| app)?": ("maximize_window", {}),
    r"lock(?: the| my)?(?: screen| laptop| computer| pc)?": ("power", {"mode": "lock"}),
    r"unlock(?: the| my)?(?: screen| laptop| computer| pc)?": ("unlock_screen", {}),
    r"is (?:the |my )?(?:screen|laptop|computer|pc) locked": ("lock_status", {}),
    r"(?:shut ?down|power off|turn off)(?: the| my)?(?: laptop| computer| pc| system)?": ("power", {"mode": "shutdown"}),
    r"(?:restart|reboot)(?: the| my)?(?: laptop| computer| pc| system)?": ("power", {"mode": "restart"}),
    r"(?:suspend|sleep|go to sleep)(?: the| my)?(?: laptop| computer| pc)?": ("power", {"mode": "suspend"}),
    r"log ?out|sign out(?: of the laptop)?": ("power", {"mode": "logout"}),
}
_LIVE_SCREEN = (
    r"(?:share|stream|show|send)(?: me)?(?: my| the)?(?: live)? screen(?: live)?"
    r"(?: (?:on|to|in|with)(?: me)?(?: on)?(?: my)? (?:phone|mobile))?"
)
FIXED.update({
    _LIVE_SCREEN: ("share_screen", {}),
    _LIVE_SCREEN + r"(?: with (?:full )?control| and let me control it)": ("share_screen", {"control": True}),
    r"stop (?:sharing|streaming)(?: my| the)?(?: screen)?": ("stop_sharing", {}),
    r"what(?:'s| is) the time|what time is it|tell me the time": ("tell_time", {}),
    r"what(?:'s| is) (?:the date|today'?s date)|what day is (?:it|today)": ("tell_date", {}),
    r"(?:what(?:'s| is) )?(?:the |my )?battery(?: level| status)?|how much battery(?: do i have| is left)?": ("battery", {}),
    r"hello|hi|hey|good (?:morning|afternoon|evening)|are you there": ("greet", {}),
    r"thanks|thank you(?: very much| so much)?|thanks a lot|good job|well done": ("reply", {"text": "Anytime"}),
})

def _switch(words: str) -> tuple[str, str]:
    """Every natural way to say turn this on, and to say turn this off."""
    the = r"(?:the |my )?"
    return (rf"(?:(?:turn|switch) on|enable|activate) {the}{words}|(?:turn|switch) {the}{words} on",
            rf"(?:(?:turn|switch) off|disable|deactivate) {the}{words}|(?:turn|switch) {the}{words} off")


for _option, _words in {
    "wifi": r"(?:wi-?fi|wireless)", "bluetooth": r"blue ?tooth", "airplane": r"(?:airplane|flight|aeroplane) mode",
    "dark_mode": r"dark (?:mode|theme)", "night_light": r"night (?:light|mode)",
    "do_not_disturb": r"(?:do not disturb|dnd)", "microphone": r"(?:microphone|mic)",
}.items():
    _on, _off = _switch(_words)
    FIXED[_on] = ("set_option", {"option": _option, "on": True})
    FIXED[_off] = ("set_option", {"option": _option, "on": False})
_on, _off = _switch(r"light (?:mode|theme)")
FIXED[_on] = ("set_option", {"option": "dark_mode", "on": False})
FIXED[_off] = ("set_option", {"option": "dark_mode", "on": True})
_on, _off = _switch(r"notifications")
FIXED[_on] = ("set_option", {"option": "do_not_disturb", "on": False})
FIXED[_off] = ("set_option", {"option": "do_not_disturb", "on": True})
FIXED.update({
    r"(?:turn )?(?:the )?brightness up|(?:increase|raise) (?:the )?brightness|brighter": ("brightness", {"change": "up"}),
    r"(?:turn )?(?:the )?brightness down|(?:decrease|lower|reduce) (?:the )?brightness|dim(?: the)?(?: screen)?|dimmer": ("brightness", {"change": "down"}),
    r"(?:power|battery) sav(?:er|ing)(?: mode)?": ("power_profile", {"profile": "power-saver"}),
    r"balanced(?: power)?(?: mode)?": ("power_profile", {"profile": "balanced"}),
    r"performance mode|high performance(?: mode)?": ("power_profile", {"profile": "performance"}),
    r"empty (?:the |my )?(?:trash|bin|recycle bin)": ("empty_trash", {}),
    r"(?:(?:send me|give me|show me|get me) )?(?:a |the |my )?(?:system |laptop |status |full )*(?:report|status)|how is (?:my |the )?(?:laptop|system|computer)(?: doing)?": ("system_report", {}),
})

SCREENSHOT = r"(?:screen ?shot|screen ?capture|snapshot|picture of (?:the |my )?screen)"
WHATSAPP = r"what'?s ?app"


@dataclass
class Context:
    """What 'it' and a bare 'search' refer to, carried across clauses and sentences."""
    last_query: str = ""
    on_youtube: bool = False
    last_level: str = "volume"  # what a bare "80 percent" refers to


def clean(text: str) -> str:
    return LEAD_IN.sub("", text.strip()).strip().rstrip(".!?, ").strip()


def split_clauses(text: str) -> list[str]:
    clauses, start = [], 0
    for match in CLAUSE_BREAK.finditer(text):
        clause = text[start:match.start()]
        if TYPING.match(clause):  # dictated text keeps its "and"s
            break
        clauses.append(clause)
        start = match.end()
    clauses.append(text[start:])
    return [clause.strip() for clause in clauses if clause.strip()]


def step(action: str, **args) -> Step:
    return {"action": action, **args}


def _search(query: str, ctx: Context, site: str | None = None) -> Step:
    query = query.strip(" '\"")
    ctx.last_query = query
    if site == "youtube" or (site is None and ctx.on_youtube):
        ctx.on_youtube = True
        return step("youtube_search", query=query)
    return step("google_search", query=query)


def _parse_clause(clause: str, ctx: Context) -> list[Step] | None:
    text = clause.strip().rstrip(".!?, ")
    low = text.lower()

    # "Write a poem" asks for something to be composed, which is for the model. "Write down a poem" is dictation.
    if match := re.fullmatch(r"(?:type|write(?!\s+(?:me|us|a|an|some|the|my|code|python|java\w*)\b))(?: down| out)?[:,]?\s+(.+)",
                             text, re.I | re.S):
        return [step("type_text", text=match.group(1))]
    if match := re.fullmatch(r"(?:press|hit)\s+(.+)", low):
        combo = re.sub(r"\s*(?:\+|\bplus\b|\s)\s*", "+", match.group(1).strip())
        combo = combo.replace("page+up", "pageup").replace("page+down", "pagedown")
        return [step("press_keys", combo=combo)]

    if match := re.fullmatch(r"run(?: the)?(?: command)?[:,]?\s+(.+)", text, re.I | re.S):
        target = match.group(1)
        if find_app(target):  # "run chrome" still just opens the app
            return [step("open", name=target, search_fallback=False)]
        return [step("run_command", command=target)]

    # Files, so "send me report.pdf" isn't mistaken for the WhatsApp screenshot phrases below.
    wants_report = re.fullmatch(r"(?:send|share) me (?:a |my )?(?:system |laptop |status |full )*(?:report|status)", low)
    if not wants_report and not re.search(SCREENSHOT + r"|\b(?:it|this|that)\b(?: to me)?(?: (?:on|to|via|over|through|in))?(?: my)? " + WHATSAPP, low):
        if match := re.fullmatch(
            rf"(?:send|share)(?: me| to me)(?: the| my| a)?(?: file| document| pdf| photo| picture)?\s+(.+?)(?:\s+(?:on|to|via|over|through|in)(?: my)?\s+{WHATSAPP})?",
            text, re.I,
        ):
            return [step("send_file", name=match.group(1).strip(" '\""))]
    if match := re.fullmatch(r"(?:list|show(?: me)?|what(?:'s| is) in)(?: my| the)? (downloads|documents|desktop|pictures|music|videos|home)(?: folder)?", low):
        return [step("list_folder", name=match.group(1))]
    if match := re.fullmatch(r"(?:open|show(?: me)?)(?: the| my)? (?:file|document|pdf|photo|picture)\s+(.+)", text, re.I):
        return [step("open_file", name=match.group(1).strip(" '\""))]
    if match := re.fullmatch(r"find(?: me)?(?: the| my)?(?: files?| documents?| pdfs?| photos?)?(?: called| named)?\s+(.+)", text, re.I):
        if not re.search(r"\s(?:on|in)\s+(?:youtube|google)$", low):
            return [step("find_files", query=match.group(1).strip(" '\""))]

    # Screenshots and WhatsApp. "send it" reuses the last capture, otherwise take a fresh one.
    if re.search(WHATSAPP, low) and low.startswith(("send", "share", "take", "capture", "grab")):
        if re.search(SCREENSHOT + r"|\bscreen\b", low):
            return [step("screenshot"), step("whatsapp_send_screenshot")]
        if re.fullmatch(rf"(?:send|share)(?: me)? (?:it|this|that)(?: to me)?(?: (?:on|to|via|over|through|in))?(?: my)? {WHATSAPP}", low):
            return [step("whatsapp_send_screenshot")]
        return None
    if re.search(SCREENSHOT, low) and low.startswith(("send", "share")):  # "send me a screenshot"
        return [step("screenshot"), step("whatsapp_send_screenshot")]
    if re.search(SCREENSHOT, low) and low.startswith(("take", "capture", "grab", "get", "make", "screen", "snapshot")):
        return [step("screenshot")]

    for pattern, combo in KEY_COMMANDS.items():
        if re.fullmatch(pattern, low):
            return [step("press_keys", combo=combo)]
    for pattern, (action, args) in FIXED.items():
        if re.fullmatch(pattern, low):
            return [step(action, **args)]
    if match := re.fullmatch(r"(?:set |change |make )?(?:the )?(?:volume|sound)(?: level)?(?: to| at)? (\d{1,3})(?: ?(?:%|percent))?", low):
        ctx.last_level = "volume"
        return [step("volume", change="set", level=int(match.group(1)))]
    if match := re.fullmatch(r"(?:to |at |make it |set it to )?(\d{1,3}) ?(?:%|percent)", low):
        return [step(ctx.last_level, change="set", level=int(match.group(1)))]
    if match := re.fullmatch(r"(?:set )?(?:the )?(?:screen )?brightness(?: to)? (\d{1,3})(?: ?(?:%|percent))?", low):
        ctx.last_level = "brightness"
        return [step("brightness", change="set", level=int(match.group(1)))]
    if match := re.fullmatch(r"(?:open |go to |show (?:me )?)(?:the )?(.+?) settings", low):
        return [step("open_settings", panel=match.group(1))]
    if match := re.fullmatch(r"(?:open |go to |show (?:me )?)(?:my |the )?(?:(downloads|documents|desktop|home)(?: folder)?|(pictures|music|videos) folder)", low):
        return [step("open_folder", name=match.group(1) or match.group(2))]

    if match := re.fullmatch(
        r"(double click|right click|click|tap|select|choose)(?: on)?(?: the)?\s+(.+?)(?:\s+(?:button|link|icon|tab|menu|option))?",
        text, re.I,
    ):
        kind = match.group(1).lower()
        args = {"count": 2} if kind == "double click" else {"right": True} if kind == "right click" else {}
        return [step("click_text", text=match.group(2).strip(" '\""), **args)]

    if match := re.fullmatch(r"(?:search|look up|find)(?: for)?\s+(.+?)\s+(?:on|in)\s+youtube", text, re.I):
        return [_search(match.group(1), ctx, "youtube")]
    if match := re.fullmatch(r"(?:search|look up|find)(?: for)?\s+(.+?)\s+(?:on|in|with)\s+google", text, re.I):
        return [_search(match.group(1), ctx, "google")]
    if match := re.fullmatch(r"(?:search(?: for)?|look up|google)\s+(.+)", text, re.I):
        return [_search(match.group(1), ctx, "google" if low.startswith("google") else None)]

    if match := re.fullmatch(rf"(?:change|switch)(?: the| this)?(?: {SONG}| it)? to\s+(?:the\s+song\s+|song\s+)?(.+)", text, re.I) or re.fullmatch(
        r"(?:put on|listen to)\s+(?:the\s+song\s+|song\s+)?(.+)", text, re.I,
    ):
        if not re.match(r"switch to\b", low):  # bare "switch to X" means a window, handled below
            ctx.last_query, ctx.on_youtube = match.group(1).strip(" '\""), True
            return [step("youtube_play", query=ctx.last_query)]

    if re.fullmatch(rf"play {PRONOUN}", low):
        return [step("youtube_play", query=ctx.last_query)] if ctx.last_query else [step("resume_media")]
    if match := re.fullmatch(r"play\s+(?:the\s+song\s+|the\s+video\s+|song\s+)?(.+?)(?:\s+(?:on|in)\s+youtube)?", text, re.I):
        ctx.last_query, ctx.on_youtube = match.group(1).strip(" '\""), True
        return [step("youtube_play", query=ctx.last_query)]

    if match := re.fullmatch(r"(?:close|quit|exit)\s+(?:the\s+)?(.+?)(?:\s+(?:app|application|window))?", text, re.I):
        return [step("close_app", name=match.group(1))]
    if match := re.fullmatch(r"(?:switch to|focus(?: on)?)\s+(?:the\s+)?(.+?)(?:\s+(?:app|window))?", text, re.I):
        return [step("focus_window", name=match.group(1))]
    if match := re.fullmatch(
        r"(?:(open|launch|start|run)(?: up)?|(go to|goto|navigate to|visit))\s+(?:the\s+)?(.+?)"
        r"(?:\s+(?:app|application|website|site|page))?(?:\s+(?:in|on|using|with)\s+(?:the\s+)?(?:google\s+)?(?:chrome|browser))?",
        text, re.I,
    ):
        name = match.group(3).strip(" '\"")
        if re.search(r"\b(?:and|then)\b", name, re.I) and not find_app(name):  # a second, unknown request
            return None
        if name.lower() == "youtube":
            ctx.on_youtube = True
        elif name.lower() in SITES:
            ctx.on_youtube = False
        return [step("open", name=name, search_fallback=bool(match.group(2)))]
    return None


def _tidy(steps: list[Step]) -> list[Step]:
    """Removes redundant hops: each browser step opens a tab, so one should do the work of two."""
    result: list[Step] = []
    for current in steps:
        previous = result[-1] if result else None
        if previous and previous["action"] == "open":
            name = previous["name"].lower()
            if name == "youtube" and current["action"] in ("youtube_search", "youtube_play"):
                result.pop()
            elif name in BROWSER_NAMES and current["action"] in ("open", "youtube_search", "youtube_play", "google_search"):
                result.pop()
            elif (name in BROWSER_NAMES or name == "youtube") and current["action"] in MEDIA_ACTIONS:
                result.pop()  # media control reaches the player without opening anything
        elif previous and previous["action"] == "youtube_search" and current["action"] == "youtube_play":
            result.pop()
        previous = result[-1] if result else None
        if previous and previous["action"] == "open":
            result.append(step("wait", seconds=3))  # let the window appear before acting on it
        result.append(current)
    return result


def parse(text: str, ctx: Context | None = None) -> list[Step] | None:
    ctx = ctx if ctx is not None else Context()
    cleaned = clean(text)
    if not cleaned:
        return []
    steps: list[Step] = []
    for clause in split_clauses(cleaned):
        parsed = _parse_clause(clause, ctx)
        if parsed is None:
            return None
        steps.extend(parsed)
    return _tidy(steps)
