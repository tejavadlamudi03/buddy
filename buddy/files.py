"""Finds and reads the user's files, and keeps private ones out of reach."""
import fnmatch
import functools
import os
import re
import time
from pathlib import Path

from . import config

HOME = Path.home()
FOLDERS = ("Downloads", "Documents", "Desktop", "Pictures", "Music", "Videos", "Screenshots")
SKIP_DIRS = {"build", "install", "log", "node_modules", "__pycache__", "venv", "site-packages", "snap", "miniconda3"}
# Never sent, read or listed. Saying a run command directly is the only way to reach these.
PRIVATE_DIRS = (".ssh", ".gnupg", ".aws", ".config/buddy", ".local/share/buddy", ".config/google-chrome",
                ".mozilla", ".thunderbird", "snap/firefox", "snap/thunderbird", ".claude")
PRIVATE_NAMES = (".env", ".env.*", "id_rsa*", "id_ed25519*", "*.pem", "*.key", "credentials*", ".netrc", ".git-credentials")
OPENABLE = ("pdf", "doc", "docx", "odt", "xls", "xlsx", "ods", "csv", "ppt", "pptx", "odp", "txt", "md", "png", "jpg",
            "jpeg", "gif", "mp3", "wav", "mp4", "mkv", "avi", "zip", "tar", "gz", "py", "cpp", "yaml", "yml", "json", "html")
SEARCH_SECONDS = 5
PARTIAL = (".crdownload", ".part", ".tmp")  # still being downloaded
MAX_READ_CHARS = 6000


@functools.cache
def _owner_private_dirs() -> tuple:
    """Kept in the config, since a folder's name can itself be private. Read once, as walks check every folder."""
    return tuple(config.load()["private_folders"])


def is_private(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    if any(resolved.is_relative_to(HOME / private) for private in PRIVATE_DIRS + _owner_private_dirs()):
        return True
    return any(fnmatch.fnmatch(resolved.name.lower(), pattern) for pattern in PRIVATE_NAMES)


def looks_like_file(name: str) -> bool:
    return bool(re.search(rf"\.(?:{'|'.join(OPENABLE)})$", spoken_name(name)))


def spoken_name(name: str) -> str:
    return re.sub(r"\s+dot\s+", ".", name.strip().strip("'\""), flags=re.I)


def _squeeze(text: str) -> str:
    return re.sub(r"[\s_.-]+", "", text.lower())


def _walk(root: Path, deadline: float):
    for current, directories, names in os.walk(root):
        directories[:] = [d for d in directories if not d.startswith(".") and d.lower() not in SKIP_DIRS
                          and not is_private(Path(current) / d)]
        for name in names:
            if not name.startswith("."):
                yield Path(current) / name
        if time.monotonic() > deadline:
            return


def find(query: str, limit: int = 8) -> list[Path]:
    """Files whose names match, best match first and then newest. Common folders are searched first."""
    wanted = _squeeze(spoken_name(query))
    if not wanted:
        return []
    deadline = time.monotonic() + SEARCH_SECONDS
    found = {}
    for root in [HOME / folder for folder in FOLDERS] + [HOME]:
        if not root.is_dir():
            continue
        for path in _walk(root, deadline):
            name = _squeeze(path.name)
            if wanted in name and path not in found and not is_private(path):
                exact = name == wanted or _squeeze(path.stem) == wanted
                try:
                    found[path] = (not exact, -path.stat().st_mtime)
                except OSError:
                    pass
    return sorted(found, key=found.get)[:limit]


def folder_path(name: str) -> Path:
    return HOME if name == "home" else HOME / name.capitalize()


def newest(folder: Path, limit: int = 15) -> list[Path]:
    entries = []
    for entry in folder.iterdir() if folder.is_dir() else []:
        if not entry.name.startswith(".") and not is_private(entry):
            try:
                entries.append((entry.stat().st_mtime, entry))
            except OSError:
                pass
    return [entry for _, entry in sorted(entries, key=lambda pair: pair[0], reverse=True)[:limit]]


def resolve(name: str) -> Path | None:
    """A path as spoken, 'latest', or the best name match."""
    name = spoken_name(name)
    if name.lower() in ("latest", "latest file", "latest download", "last download"):
        files = [entry for entry in newest(folder_path("downloads"), 50)
                 if entry.is_file() and not entry.name.endswith(PARTIAL)]
        return files[0] if files else None
    direct = Path(name).expanduser()
    if direct.is_absolute() and direct.is_file():
        return direct
    matches = find(name, 1)
    return matches[0] if matches else None


def read_text(path: Path) -> str:
    if is_private(path):
        raise PermissionError(f"{path.name} is private, so I won't read it")
    with path.open("rb") as stream:
        data = stream.read(MAX_READ_CHARS * 2)
    if b"\0" in data:
        raise ValueError(f"{path.name} isn't a text file")
    return data.decode(errors="replace")[:MAX_READ_CHARS]
