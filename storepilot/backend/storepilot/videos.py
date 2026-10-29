"""
Real robot videos for the Real robot page: uploaded files (stored in VIDEOS_DIR) and YouTube links.

Everyone can watch. Uploading, adding a link and deleting need STOREPILOT_UPLOAD_PASSWORD;
without it the page only plays the videos already there.

    VIDEOS_DIR/videos.json   [{"id", "title", "kind": "file" | "youtube", "file" | "url", "size", "created_at"}]
    VIDEOS_DIR/<id>.<ext>    the uploaded file
"""

import hmac
import json
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config
from .db import now_iso

EXTENSIONS = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov", "video/ogg": "ogv"}
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class VideoError(Exception):
    """A request about videos cannot be done (shown as a message)."""


def youtube_id(url):
    """The 11-character id of a YouTube link (watch, youtu.be, shorts, embed), or None."""
    try:
        u = urlparse(url.strip())
    except ValueError:
        return None
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    candidate = None
    if host == "youtu.be":
        candidate = u.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        if u.path == "/watch":
            candidate = (parse_qs(u.query).get("v") or [""])[0]
        elif u.path.startswith(("/shorts/", "/embed/", "/live/")):
            candidate = u.path.split("/")[2]
    return candidate if candidate and YOUTUBE_ID.match(candidate) else None


class VideoStore:
    def __init__(self, folder=None):
        self.folder = Path(folder or config.VIDEOS_DIR)
        self.index = self.folder / "videos.json"
        self.lock = threading.Lock()

    @property
    def upload_enabled(self):
        return bool(config.UPLOAD_PASSWORD)

    def check_password(self, password):
        if not config.UPLOAD_PASSWORD:
            raise VideoError("Uploads are switched off on this server (no STOREPILOT_UPLOAD_PASSWORD).")
        if not hmac.compare_digest((password or "").encode(), config.UPLOAD_PASSWORD.encode()):
            raise VideoError("Wrong team password.")

    def _read(self):
        try:
            return json.loads(self.index.read_text())
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, videos):
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.index.with_suffix(".tmp")
        tmp.write_text(json.dumps(videos, indent=2))
        tmp.replace(self.index)

    def list(self):
        """Newest first, with the URL the browser plays."""
        out = []
        for v in reversed(self._read()):
            if v["kind"] == "file":
                if not (self.folder / v["file"]).is_file():
                    continue
                url = f"/api/videos/file/{v['file']}"
            else:
                url = f"https://www.youtube-nocookie.com/embed/{v['youtube_id']}"
            out.append({"id": v["id"], "title": v["title"], "kind": v["kind"], "url": url,
                        "size": v.get("size"), "created_at": v["created_at"]})
        return out

    def featured(self):
        """The video the Robot camera panel plays when there is no live camera: the newest one."""
        videos = self.list()
        return videos[0] if videos else None

    def path_of(self, name):
        """Path of an uploaded file, only for names this store created."""
        if not re.fullmatch(r"[0-9a-f]{32}\.(mp4|webm|mov|ogv)", name or ""):
            return None
        path = self.folder / name
        return path if path.is_file() else None

    @staticmethod
    def clean_title(title):
        title = " ".join((title or "").split())[:120]
        if not title:
            raise VideoError("Give the video a title.")
        return title

    async def add_file(self, title, content_type, chunks):
        """Save an uploaded video from an async iterator of byte chunks."""
        title = self.clean_title(title)
        ext = EXTENSIONS.get((content_type or "").split(";")[0].strip().lower())
        if not ext:
            raise VideoError("Upload an MP4, WebM, MOV or OGG video.")
        video_id = uuid.uuid4().hex
        self.folder.mkdir(parents=True, exist_ok=True)
        name = f"{video_id}.{ext}"
        part = self.folder / f"{name}.part"
        limit, size = config.MAX_VIDEO_MB * 1024 * 1024, 0
        try:
            with part.open("wb") as f:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > limit:
                        raise VideoError(f"The video is larger than {config.MAX_VIDEO_MB} MB.")
                    f.write(chunk)
            if size == 0:
                raise VideoError("The file is empty.")
            part.replace(self.folder / name)
        finally:
            part.unlink(missing_ok=True)
        with self.lock:
            videos = self._read()
            videos.append({"id": video_id, "title": title, "kind": "file", "file": name, "size": size,
                           "created_at": now_iso()})
            self._write(videos)
        return video_id

    def add_link(self, title, url):
        title = self.clean_title(title)
        vid = youtube_id(url or "")
        if not vid:
            raise VideoError("Paste a YouTube link (youtube.com/watch?v=…, youtu.be/… or /shorts/…).")
        with self.lock:
            videos = self._read()
            video_id = uuid.uuid4().hex
            videos.append({"id": video_id, "title": title, "kind": "youtube", "youtube_id": vid,
                           "created_at": now_iso()})
            self._write(videos)
        return video_id

    def delete(self, video_id):
        with self.lock:
            videos = self._read()
            keep = [v for v in videos if v["id"] != video_id]
            if len(keep) == len(videos):
                raise VideoError("Video not found.")
            for v in videos:
                if v["id"] == video_id and v["kind"] == "file":
                    (self.folder / v["file"]).unlink(missing_ok=True)
            self._write(keep)
