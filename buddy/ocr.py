"""Reads the text on screen locally, so Buddy can find and press things inside any app."""
import difflib
import re
import threading
from dataclasses import dataclass

import mss
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

from . import desktop

MIN_CONFIDENCE = 0.5
ZOOM_BELOW_WIDTH = 1200
_engine = None
_lock = threading.Lock()


@dataclass
class Item:
    text: str
    x: int
    y: int


def read_screen(rect: tuple[int, int, int, int] | None = None) -> list[Item]:
    """Visible text with its centre in screen pixels, from a region or everywhere.

    A region is enlarged first: OCR misses lone characters, like calculator keys, at normal size.
    Buddy's own page is left out.
    """
    global _engine
    with mss.mss() as grabber:
        monitor = grabber.monitors[0]
        if rect:  # clipped to the screen, since windows can hang off its edges
            left = max(rect[0], monitor["left"])
            top = max(rect[1], monitor["top"])
            right = min(rect[0] + rect[2], monitor["left"] + monitor["width"])
            bottom = min(rect[1] + rect[3], monitor["top"] + monitor["height"])
            region = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        else:
            region = monitor
        shot = grabber.grab(region)
    image = Image.frombytes("RGB", shot.size, shot.rgb)
    scale = 2 if rect and image.width <= ZOOM_BELOW_WIDTH else 1
    if scale > 1:
        image = image.resize((image.width * scale, image.height * scale), Image.LANCZOS)
    pixels = np.array(image)[:, :, ::-1]  # RapidOCR expects BGR
    with _lock:
        _engine = _engine or RapidOCR()
        found, _ = _engine(pixels)
    items = []
    for box, text, confidence in found or []:
        if float(confidence) >= MIN_CONFIDENCE:
            x = round(sum(point[0] for point in box) / 4 / scale) + region["left"]
            y = round(sum(point[1] for point in box) / 4 / scale) + region["top"]
            items.append(Item(text.strip(), x, y))
    return _without_own_page(items)


def _inside(item: Item, rect: tuple[int, int, int, int]) -> bool:
    left, top, width, height = rect
    return left <= item.x < left + width and top <= item.y < top + height


def _without_own_page(items: list[Item]) -> list[Item]:
    for window in desktop.list_windows():  # topmost first, so only a page in front can hide anything
        if desktop.BUDDY_TITLE not in window.title:
            return items
        rect = desktop.window_rect(window.id)
        items = [item for item in items if not _inside(item, rect)]
    return items


def _normal(text: str) -> str:
    return re.sub(r"[^\w+=×÷−-]+", " ", text.lower()).strip()


def find(items: list[Item], wanted: str) -> Item | None:
    """Best match for spoken words: exact, then whole words, then close spelling."""
    target = _normal(wanted)
    if not target:
        return None
    words = re.compile(rf"(?<!\w){re.escape(target)}(?!\w)")
    tiers = (
        lambda text: text == target,
        lambda text: bool(words.search(text)),
        lambda text: len(target) > 3 and difflib.SequenceMatcher(None, text, target).ratio() >= 0.8,
    )
    for matches in tiers:
        hits = [item for item in items if matches(_normal(item.text))]
        if hits:
            return hits[0]
    return None
