import asyncio
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from PIL import Image
from yt_dlp import YoutubeDL

from core.errors import InappropriateContent, NoMedia, UnsupportedSite, TooLarge
from utils.logging_utils import log_event, log_error

MAX_SIZE_MB = 50
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
CODECS_TO_REFORMAT = {"vp9"}
ProgressCallback = Callable[[str], Awaitable[None]]
BASE_YDL_OPTS = {
    "cookiefile": "insta_cookies.txt",
    "quiet": True,
    "enable_file_urls": True,
    "remote_components": ["ejs:github"],
}


def _b64_from_path(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _probe_video(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-print_format",
            "json",
            "-show_streams",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data = json.loads(out)
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    return {
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
    }


def _pick_caption(meta: dict) -> str:
    if not meta:
        return ""
    for key in ("caption", "description", "content", "text", "title"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    for path in (("post", "caption"), ("post", "description"), ("data", "caption"), ("data", "description")):
        cur = meta
        ok = True
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok and isinstance(cur, str) and cur.strip():
            return cur.strip()
    return ""


def _find_meta(tmpdir: Path) -> dict:
    for name in os.listdir(tmpdir):
        p = tmpdir / name
        if p.suffix.lower() == ".json" and p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {}


async def fix_video(input_path: str) -> str:
    input_path = Path(input_path)
    output_path = input_path.with_stem(input_path.stem + "_fixed")

    ffmpeg = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-i",
        str(input_path),
        "-movflags",
        "+faststart",
        str(output_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await ffmpeg.communicate()
    if ffmpeg.returncode != 0:
        raise RuntimeError(stderr.decode())

    return str(output_path)


async def download_post(url: str, tmpdir: str) -> tuple[list[Path], str]:
    cmd = [
        "gallery-dl",
        "-D", tmpdir,
        "--write-metadata",
        "--cookies", "insta_cookies.txt",
        # downloading merged format is more likely to be compatible with TG, but might be a lot heavier in some cases
        "-o", "extractor.instagram.videos=merged",
        url,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError((err or out).decode(errors="ignore"))
    except Exception as e:
        msg = str(e).lower()
        if "inappropriate" in msg:
            raise InappropriateContent from e
        if "no video" in msg or "no results" in msg:
            raise NoMedia from e
        log_error(request_type="download_post", error=e)
        raise UnsupportedSite from e

    caption = _pick_caption(_find_meta(Path(tmpdir)))

    media_files: list[Path] = []
    for name in os.listdir(tmpdir):
        p = Path(tmpdir) / name
        if not p.is_file():
            continue
        if p.suffix.lower() in IMAGE_EXTS or p.suffix.lower() in VIDEO_EXTS:
            media_files.append(p)

    if not media_files:
        raise NoMedia

    return media_files, caption


async def download_post_ytdlp(url: str, tmpdir: str) -> tuple[list[Path], str]:
    outtmpl = str(Path(tmpdir) / "%(title)s.%(ext)s")
    ydl_opts = {
        **BASE_YDL_OPTS,
        "format": "mp4",
        "outtmpl": outtmpl,
        "socket_timeout": 30,
    }

    try:
        info = await asyncio.to_thread(lambda: YoutubeDL(ydl_opts).extract_info(url, download=True))
    except Exception as e:
        log_error(request_type="download_post_ytdlp", error=e)
        raise UnsupportedSite from e

    caption = _pick_caption(info)

    media_files: list[Path] = []
    for name in os.listdir(tmpdir):
        p = Path(tmpdir) / name
        if not p.is_file():
            continue
        if p.suffix.lower() in IMAGE_EXTS or p.suffix.lower() in VIDEO_EXTS:
            media_files.append(p)

    if not media_files:
        raise NoMedia

    return media_files, caption


async def _build_payload(
        files: list[Path],
        caption: str,
        callback: ProgressCallback,
) -> dict:
    content: list[dict] = []

    for path in files:
        size_mb = path.stat().st_size / 1024 / 1024
        if size_mb > MAX_SIZE_MB:
            raise TooLarge

        ext = path.suffix.lower()
        if ext in IMAGE_EXTS:
            try:
                with Image.open(path) as im:
                    width, height = im.size
            except Exception:
                width, height = None, None

            content.append(
                {
                    "type": "image",
                    "data": _b64_from_path(path),
                    "width": width,
                    "height": height,
                }
            )
            continue

        if ext in VIDEO_EXTS:
            meta = _probe_video(path)
            codec = meta.get("codec")
            width, height = meta.get("width"), meta.get("height")

            if codec in CODECS_TO_REFORMAT:
                await callback("Обработка...")
                log_event(event="fixing codec", data=json.dumps(meta))
                path = Path(await fix_video(str(path)))
                size_mb = path.stat().st_size / 1024 / 1024
                if size_mb > MAX_SIZE_MB:
                    raise TooLarge
                meta = _probe_video(path)
                width, height = meta.get("width") or width, meta.get("height") or height

            content.append(
                {
                    "type": "video",
                    "data": _b64_from_path(path),
                    "width": width,
                    "height": height,
                }
            )
            continue

    if not content:
        raise NoMedia

    return {
        "caption": caption,
        "content": content
    }


async def download_post_json(url: str, callback: ProgressCallback) -> dict:
    with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
        try:
            files, caption = await download_post(url, tmpdir)
        except (UnsupportedSite, NoMedia):
            await callback("⌛Еще немного...")
            log_event(event="fallback_ytdlp", data=url)
            files, caption = await download_post_ytdlp(url, tmpdir)
        return await _build_payload(files, caption, callback)
