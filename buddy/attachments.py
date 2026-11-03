"""Turns a file sent on WhatsApp into what the local AI can read. Nothing in it is ever acted on."""
import io
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import apps, config, fetch

MAX_TEXT_CHARS = 12000  # about what fits in the model's context
MAX_PAGES_AS_PICTURES = 3
MAX_PICTURE_SIDE = 1568
MIN_PDF_TEXT = 40  # less than this and it's a scan, with its words only in the pictures
TEXT_TYPES = {"txt", "md", "py", "cpp", "c", "h", "js", "ts", "java", "json", "yaml", "yml", "xml", "html", "csv", "log", "ini", "toml", "sh", "tex"}
SHEETS = {"xlsx", "xls", "ods"}
OFFICE = {"doc", "docx", "odt", "rtf", "ppt", "pptx", "odp"}


@dataclass
class Attachment:
    name: str
    text: str = ""
    pictures: list = field(default_factory=list)  # JPEG bytes
    note: str = ""  # told to the user, such as how much of it was read


def _jpeg(image: Image.Image) -> bytes:
    image = image.convert("RGB")
    image.thumbnail((MAX_PICTURE_SIDE, MAX_PICTURE_SIDE))
    output = io.BytesIO()
    image.save(output, "JPEG", quality=85)
    return output.getvalue()


def _picture(path: Path) -> Attachment:
    try:
        with Image.open(path) as image:
            return Attachment(path.name, pictures=[_jpeg(image)])
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError(f"I couldn't read {path.name} as a picture") from error


def _output(*command: str) -> str:
    try:
        return subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", timeout=120,
                              env=apps.session_env(), stdin=subprocess.DEVNULL, **config.NO_WINDOW).stdout
    except FileNotFoundError:  # Poppler, which Windows doesn't come with
        raise ValueError(f"I couldn't read that, because {command[0]} isn't installed. Run Buddy's install script again") from None


def _pdf(path: Path) -> Attachment:
    text = _output("pdftotext", "-layout", str(path), "-")
    if len(text.strip()) >= MIN_PDF_TEXT:
        return _with_text(path.name, text)
    with tempfile.TemporaryDirectory(prefix="buddy-read-") as folder:
        _output("pdftoppm", "-jpeg", "-r", "110", "-f", "1", "-l", str(MAX_PAGES_AS_PICTURES), str(path), f"{folder}/page")
        pictures = []
        for page in sorted(Path(folder).glob("page*")):
            with Image.open(page) as image:
                pictures.append(_jpeg(image))
    if not pictures:
        raise ValueError(f"I couldn't read {path.name}. It may be damaged or have a password")
    return Attachment(path.name, pictures=pictures, note=f"This looks like a scan, so I only read up to the first {MAX_PAGES_AS_PICTURES} pages.")


def _with_text(name: str, text: str) -> Attachment:
    text = text.strip()
    if len(text) <= MAX_TEXT_CHARS:
        return Attachment(name, text=text)
    return Attachment(name, text=text[:MAX_TEXT_CHARS],
                      note=f"It's long, so I only read the first {MAX_TEXT_CHARS} of its {len(text)} characters.")


def _through_office(path: Path, wanted: str) -> str:
    with tempfile.TemporaryDirectory(prefix="buddy-read-") as folder:
        output = fetch._office(path, wanted, Path(folder))
        if not output.is_file():
            raise ValueError(f"I couldn't read {path.name}")
        return _output("pdftotext", "-layout", str(output), "-") if wanted == "pdf" else output.read_text(encoding="utf-8", errors="replace")


def read(path: Path) -> Attachment:
    kind = path.suffix.lower().lstrip(".")
    if kind in fetch.IMAGE or kind == "gif":
        return _picture(path)
    if kind == "pdf":
        return _pdf(path)
    if kind in SHEETS:
        return _with_text(path.name, _through_office(path, "csv"))
    if kind in {"ppt", "pptx", "odp"}:
        return _with_text(path.name, _through_office(path, "pdf"))
    if kind in OFFICE:
        return _with_text(path.name, _through_office(path, "txt"))
    if kind in TEXT_TYPES:
        return _with_text(path.name, path.read_text(encoding="utf-8", errors="replace"))
    raise ValueError(f"I can't read .{kind or 'that kind of'} files yet, but pictures, PDFs, documents, sheets, slides and text all work")
