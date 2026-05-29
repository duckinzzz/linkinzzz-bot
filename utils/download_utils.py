import asyncio
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, Optional

from PIL import Image
from yt_dlp import YoutubeDL

from core.errors import InappropriateContent, NoMedia, UnsupportedSite, TooLarge
from core.config import BASE_DIR
from utils.logging_utils import log_event, log_error

MAX_SIZE_MB = 50
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
CODECS_TO_REFORMAT = {"vp9"}
ProgressCallback = Callable[[str], Awaitable[None]]
BASE_YDL_OPTS = {
    "quiet": True,
    "enable_file_urls": True,
    "js_runtimes": {"deno": {}},
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
    ]
    if "instagram.com" in url:
        cmd += [
            "--cookies", os.path.join(BASE_DIR, "cookies", "insta_cookies.txt"),
            # downloading merged format is more likely to be compatible with TG, but might be a lot heavier in some cases
            "-o", "extractor.instagram.videos=merged",
        ]
    cmd.append(url)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _read_stderr():
        """Read stderr line by line, returning (lines, early_exit_reason)."""
        lines: list[str] = []
        while True:
            line = await proc.stderr.readline()
            if not line:
                return lines, None
            decoded = line.decode(errors="ignore")
            lines.append(decoded)
            if "429" in decoded or "Too Many Requests" in decoded:
                return lines, "rate_limit"
            if "redirect to login" in decoded.lower():
                return lines, "auth_error"

    try:
        stderr_lines, early_exit = await asyncio.wait_for(_read_stderr(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        log_error(request_type="download_post", error=TimeoutError("gallery-dl timed out"))
        raise UnsupportedSite from TimeoutError("gallery-dl timed out")

    if early_exit == "rate_limit":
        proc.kill()
        await proc.wait()
        log_event(event="gallery_dl_rate_limited", data=url)
        raise UnsupportedSite(RuntimeError("".join(stderr_lines)))

    if early_exit == "auth_error":
        proc.kill()
        await proc.wait()
        raise InappropriateContent(RuntimeError("".join(stderr_lines)))

    # Normal exit — read remaining buffers
    stdout_data = await proc.stdout.read()
    stderr_remaining = await proc.stderr.read()
    stderr_text = "".join(stderr_lines) + stderr_remaining.decode(errors="ignore")
    stdout_text = stdout_data.decode(errors="ignore")

    if proc.returncode != 0:
        msg = (stderr_text or stdout_text).lower()
        if "inappropriate" in msg:
            raise InappropriateContent(RuntimeError(stderr_text or stdout_text))
        if "no video" in msg or "no results" in msg:
            raise NoMedia(RuntimeError(stderr_text or stdout_text))
        log_error(request_type="download_post", error=RuntimeError(stderr_text or stdout_text))
        raise UnsupportedSite(RuntimeError(stderr_text or stdout_text))

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
        "format": (
            "bv*[ext=mp4][vcodec^=avc1][protocol^=http]+"
            "ba[ext=m4a][protocol^=http]/"
            "b[ext=mp4][vcodec^=avc1][protocol^=http]/"
            "18"
        ),
        "merge_output_format": "mp4",
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


async def download_post_json(
    url: str,
    callback: ProgressCallback,
    on_cookies_expired: Optional[Callable[[], Awaitable[None]]] = None,
) -> dict:
    try:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
            files, caption = await download_post(url, tmpdir)
            return await _build_payload(files, caption, callback)
    except InappropriateContent:
        if on_cookies_expired:
            await on_cookies_expired()
        log_event(event="fallback_ytdlp_cookies", data=url)
        await callback("⌛Еще немного...")
        with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
            files, caption = await download_post_ytdlp(url, tmpdir)
            return await _build_payload(files, caption, callback)
    except (UnsupportedSite, NoMedia, TooLarge):
        await callback("⌛Еще немного...")
        log_event(event="fallback_ytdlp", data=url)
        with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
            files, caption = await download_post_ytdlp(url, tmpdir)
            return await _build_payload(files, caption, callback)
