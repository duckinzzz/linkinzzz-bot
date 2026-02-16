import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Callable, Awaitable

from yt_dlp import YoutubeDL

from utils.logging_utils import log_event, log_error

MAX_SIZE_MB = 50
BASE_YDL_OPTS = {
    'cookiefile': 'insta_cookies.txt',
    "quiet": True,
    "enable_file_urls": True,
    "remote_components": ["ejs:github"],
}
CODECS_TO_REFORMAT = ['vp9']


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


async def download_video(url, tmpdir) -> Optional[str]:
    outtmpl = os.path.join(tmpdir, f"%(id)s_.%(ext)s")

    ydl_opts = {
        **BASE_YDL_OPTS,
        "format": "mp4",
        "outtmpl": outtmpl,
        "socket_timeout": 30,
    }

    try:
        await asyncio.to_thread(
            lambda: YoutubeDL(ydl_opts).download([url])
        )
    except Exception as e:
        if "This content may be inappropriate" in str(e):
            raise ValueError("INAPPROPRIATE_CONTENT") from e
        if "No video formats found" in str(e):
            raise ValueError("NO_VIDEO") from e
        log_error(request_type='download_video', error=e)
        raise ValueError("UNABLE_TO_DOWNLOAD") from e

    files = [
        os.path.join(tmpdir, f)
        for f in os.listdir(tmpdir)
        if f.endswith(".mp4")
    ]

    if not files:
        raise ValueError("NO_FILES_IN_DIRECTORY")

    return files[0]


async def download_video_bytes(
        url: str,
        callback: Callable[[str], Awaitable[None]]
) -> tuple[bytes, bool, bool] | tuple[None, None, None]:
    with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
        file = await download_video(url, tmpdir)
        metadata = get_metadata(str(file))
        vs = metadata.get('video_stream') if metadata else None
        codec = vs.get('codec') if vs else None
        w = vs.get("width") if vs else None
        h = vs.get("height") if vs else None
        size = round(os.path.getsize(file) / 1024 / 1024, 2)

        if codec in CODECS_TO_REFORMAT:
            await callback('Обработка...')
            log_event(event='fixing codec', data=json.dumps(metadata))
            file = await fix_video(file)

        if size <= MAX_SIZE_MB:
            with open(file, "rb") as f:
                return f.read(), w, h
        else:
            return None, None, None
