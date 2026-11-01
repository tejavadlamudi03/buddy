"""Downloads files from links and converts between formats, with tools already on the laptop."""
import email.message
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import apps, files

MAX_DOWNLOAD_BYTES = 5 * 1000 * 1000 * 1000
KEEP_FREE_BYTES = 1000 * 1000 * 1000
DOWNLOAD_SECONDS = 30 * 60
CONVERT_SECONDS = 10 * 60
CHUNK = 1024 * 1024
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
AUDIO = {"mp3", "wav", "ogg", "m4a", "flac", "aac", "opus"}
VIDEO = {"mp4", "mkv", "webm", "avi", "mov", "gif"}
IMAGE = {"png", "jpg", "jpeg", "webp", "bmp", "tiff"}
OFFICE = {"pdf", "doc", "docx", "odt", "rtf", "txt", "html", "md", "xlsx", "xls", "ods", "csv", "ppt", "pptx", "odp"}
FORMATS = "audio (mp3, wav, ogg, m4a, flac), video (mp4, mkv, webm, avi, mov, gif), images (png, jpg, webp), " \
          "documents (pdf, docx, odt, txt, html), sheets (xlsx, csv), slides (pptx, odp) and zip"
cancel = threading.Event()  # set by Stop, since a long download or conversion is otherwise out of reach


class Cancelled(RuntimeError):
    def __init__(self):
        super().__init__("Stopped")


def _safe_name(name: str) -> str:
    """The server chooses this name, so it never gets to choose a folder or a hidden file."""
    name = re.sub(r"[\x00-\x1f/\\]", "_", Path(name.replace("\\", "/")).name).strip(" .")
    if len(name) > 120:
        stem, suffix = os.path.splitext(name)
        name = stem[:120 - len(suffix)] + suffix
    return name or "download"


def _unique(folder: Path, name: str) -> Path:
    path, count = folder / name, 0
    while path.exists():
        count += 1
        path = folder / f"{Path(name).stem} ({count}){Path(name).suffix}"
    return path


def _downloads() -> Path:
    folder = files.folder_path("downloads")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _name_for(url: str, response) -> str:
    header = email.message.Message()
    header["Content-Disposition"] = response.headers.get("Content-Disposition", "")
    name = header.get_filename() or urllib.parse.unquote(urllib.parse.urlparse(response.url or url).path)
    name = _safe_name(name)
    if not Path(name).suffix:
        name += mimetypes.guess_extension(response.headers.get_content_type()) or ""
    return name


def download(url: str) -> tuple[Path, bool]:
    """(where it's saved, whether the link was a web page rather than a file)."""
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ValueError("I can only download http and https links")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    folder = _downloads()
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            length = int(response.headers.get("Content-Length") or 0)
            limit = min(MAX_DOWNLOAD_BYTES, shutil.disk_usage(folder).free - KEEP_FREE_BYTES)
            if length > limit:
                raise RuntimeError(f"That file is {length / 1e6:.0f} MB, which is more than I can download here")
            target = _unique(folder, _name_for(url, response))
            is_page = response.headers.get_content_type() in ("text/html", "application/xhtml+xml")
            # Hidden until it's whole, so "latest download" can't pick up half a file.
            partial = folder / f".{target.name}.part"
            size = 0
            try:
                with partial.open("wb") as stream:
                    while chunk := response.read(CHUNK):
                        size += len(chunk)
                        if cancel.is_set():
                            raise Cancelled
                        if size > limit or time.monotonic() > deadline:
                            raise RuntimeError("That download got too big or took too long, so I stopped it")
                        stream.write(chunk)
                partial.rename(target)
            finally:
                partial.unlink(missing_ok=True)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"That link didn't work: the website answered {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"I couldn't download that: {getattr(error, 'reason', error)}") from None
    return target, is_page


def save_received(name: str, data: bytes) -> Path:
    target = _unique(_downloads(), _safe_name(name))
    target.write_bytes(data)
    return target


def _tool(*command: str) -> None:
    """Runs a converter so that Stop can end it, along with anything it started."""
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=apps.session_env(), start_new_session=True)
    deadline = time.monotonic() + CONVERT_SECONDS
    try:
        while process.poll() is None:
            if cancel.is_set():
                raise Cancelled
            if time.monotonic() > deadline:
                raise RuntimeError("That conversion took too long, so I stopped it")
            time.sleep(0.1)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def _image(source: Path, output: Path) -> None:
    try:
        with Image.open(source) as image:
            has_no_alpha = output.suffix.lower() in (".jpg", ".jpeg", ".pdf", ".bmp")
            (image.convert("RGB") if has_no_alpha else image).save(output)
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise RuntimeError(f"I couldn't read {source.name} as a picture") from error


def _pdf_pages(source: Path, wanted: str, folder: Path) -> Path:
    """A picture of each page: one page gives a single picture, more give a zip of them."""
    tool_format = "jpeg" if wanted in ("jpg", "jpeg") else "png"
    _tool("pdftoppm", f"-{tool_format}", "-r", "150", str(source), str(folder / "page"))
    pages = sorted(folder.glob("page*"))
    if len(pages) == 1:
        return pages[0].rename(folder / f"{source.stem}.{wanted}")
    if not pages:
        raise RuntimeError(f"I couldn't read the pages of {source.name}")
    archive = folder / f"{source.stem}.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        for page in pages:
            stream.write(page, page.name)
    return archive


def _office(source: Path, wanted: str, folder: Path) -> Path:
    # Its own profile, because with LibreOffice already open the job would go there and leave nothing here.
    command = ["soffice", f"-env:UserInstallation=file://{folder}/profile", "--headless", "--norestore"]
    if source.suffix.lower() == ".pdf":
        command.append("--infilter=writer_pdf_import")
    _tool(*command, "--convert-to", wanted, "--outdir", str(folder), str(source))
    return folder / f"{source.stem}.{wanted}"


def _convert_into(source: Path, wanted: str, folder: Path) -> Path:
    kind = source.suffix.lower().lstrip(".")
    output = folder / f"{source.stem}.{wanted}"
    if wanted == "zip":
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as stream:
            stream.write(source, source.name)
    elif kind in AUDIO | VIDEO and wanted in AUDIO | VIDEO:
        _tool("ffmpeg", "-nostdin", "-y", "-i", str(source), str(output))
    elif kind in IMAGE and wanted in IMAGE | {"pdf"}:
        _image(source, output)
    elif kind == "pdf" and wanted in IMAGE:
        return _pdf_pages(source, wanted, folder)
    elif kind == "pdf" and wanted == "txt":
        _tool("pdftotext", "-layout", str(source), str(output))
    elif kind in OFFICE and wanted in OFFICE:
        return _office(source, wanted, folder)
    else:
        raise ValueError(f"I can't turn .{kind or 'that'} into .{wanted}. I can convert {FORMATS}")
    return output


def convert(source: Path, wanted: str) -> Path:
    """Saves the converted copy in Downloads and leaves the original alone."""
    wanted = wanted.lower().lstrip(".")
    if source.suffix.lower().lstrip(".") == wanted:
        return source
    with tempfile.TemporaryDirectory(prefix="buddy-convert-") as folder:
        result = _convert_into(source, wanted, Path(folder))
        if not result.is_file() or not result.stat().st_size:
            raise RuntimeError(f"I couldn't turn {source.name} into .{wanted}")
        target = _unique(_downloads(), result.name)
        shutil.move(result, target)
    return target
