import asyncio
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Awaitable

from PIL import Image

from utils.logging_utils import log_event, log_error

MAX_SIZE_MB = 50
CODECS_TO_REFORMAT = ['vp9']

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}


def get_metadata(filepath: str):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                filepath,
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        data = json.loads(result.stdout)
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            None,
        )

        metadata = {
            "container": {
                "file_name": data.get("format", {}).get("filename").split("\\")[-1],
                "format_name": data.get("format", {}).get("format_name"),
                "duration": data.get("format", {}).get("duration"),
                "bit_rate": data.get("format", {}).get("bit_rate"),
                "tags": data.get("format", {}).get("tags"),
            },
            "video_stream": {
                "codec": video_stream.get("codec_name") if video_stream else None,
                "profile": video_stream.get("profile") if video_stream else None,
                "level": video_stream.get("level") if video_stream else None,
                "pix_fmt": video_stream.get("pix_fmt") if video_stream else None,
                "width": video_stream.get("width") if video_stream else None,
                "height": video_stream.get("height") if video_stream else None,
                "sample_aspect_ratio": video_stream.get("sample_aspect_ratio") if video_stream else None,
                "display_aspect_ratio": video_stream.get("display_aspect_ratio") if video_stream else None,
                "rotation": (
                    video_stream.get("tags", {}).get("rotate")
                    if video_stream and video_stream.get("tags")
                    else None
                ),
                "avg_frame_rate": video_stream.get("avg_frame_rate") if video_stream else None,
                "time_base": video_stream.get("time_base") if video_stream else None,
            },
        }

        return metadata

    except Exception as e:
        log_error(request_type='get_metadata', error=e)


async def fix_video(input_path: str):
    input_path = Path(input_path)
    output_path = input_path.with_stem(input_path.stem + "_fixed")

    ffmpeg = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-i", str(input_path),
        "-movflags", "+faststart",
        str(output_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await ffmpeg.communicate()
    if ffmpeg.returncode != 0:
        raise RuntimeError(stderr.decode())

    return str(output_path)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _pick_caption(meta: dict) -> str:
    if not meta:
        return ""
    for key in ("caption", "description", "content", "text", "title"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    for path in (
            ("post", "caption"),
            ("post", "description"),
            ("data", "caption"),
            ("data", "description"),
    ):
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


async def download_post(url: str, tmpdir: str) -> tuple[list[str], str]:
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
        msg = str(e)
        if "inappropriate" in msg.lower():
            raise ValueError("INAPPROPRIATE_CONTENT") from e
        if "no video" in msg.lower() or "no results" in msg.lower():
            raise ValueError("NO_VIDEO") from e
        log_error(request_type='download_post', error=e)
        raise ValueError("UNABLE_TO_DOWNLOAD") from e

    meta = None
    for name in os.listdir(tmpdir):
        p = os.path.join(tmpdir, name)
        if name.lower().endswith(".json") and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                break
            except Exception:
                pass

    caption = _pick_caption(meta)

    media_files: list[str] = []
    for name in os.listdir(tmpdir):
        p = os.path.join(tmpdir, name)
        if not os.path.isfile(p):
            continue
        ext = Path(name).suffix.lower()
        if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
            media_files.append(p)

    if not media_files:
        raise ValueError("NO_FILES_IN_DIRECTORY")

    return media_files, caption


async def download_post_json(
        url: str,
        callback: Callable[[str], Awaitable[None]]
) -> dict | None:
    with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
        files, caption = await download_post(url, tmpdir)

        content: list[dict] = []

        for file in files:
            ext = Path(file).suffix.lower()
            size_mb = round(os.path.getsize(file) / 1024 / 1024, 2)

            if size_mb > MAX_SIZE_MB:
                return None

            if ext in IMAGE_EXTS:
                try:
                    with Image.open(file) as im:
                        w, h = im.size
                except Exception:
                    w, h = None, None

                with open(file, "rb") as f:
                    data = f.read()

                content.append({
                    "type": "image",
                    "data": _b64(data),
                    "width": w,
                    "height": h,
                })

            elif ext in VIDEO_EXTS:
                metadata = get_metadata(str(file))
                vs = metadata.get('video_stream') if metadata else None
                codec = vs.get('codec') if vs else None
                w = vs.get("width") if vs else None
                h = vs.get("height") if vs else None

                if codec in CODECS_TO_REFORMAT:
                    await callback('Обработка...')
                    log_event(event='fixing codec', data=json.dumps(metadata))
                    file = await fix_video(file)

                    size_mb = round(os.path.getsize(file) / 1024 / 1024, 2)
                    if size_mb > MAX_SIZE_MB:
                        return None
                    metadata = get_metadata(str(file))
                    vs = metadata.get('video_stream') if metadata else None
                    w = vs.get("width") if vs else w
                    h = vs.get("height") if vs else h

                with open(file, "rb") as f:
                    data = f.read()

                content.append({
                    "type": "video",
                    "data": _b64(data),
                    "width": w,
                    "height": h,
                })

        return {
            "caption": caption,
            "content": content
        }
