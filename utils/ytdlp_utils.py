import asyncio
import os
import tempfile
from dataclasses import dataclass
from typing import Optional
from yt_dlp import YoutubeDL

MAX_SIZE_MB = 50
BASE_YDL_OPTS = {
    "quiet": True,
    "enable_file_urls": True,
    "remote_components": ["ejs:github"],
}


@dataclass
class FormatInfo:
    format_id: str
    height: int
    filesize: Optional[int]
    acodec: str
    vcodec: str
    ext: str
    protocol: str

    @property
    def filesize_mb(self) -> Optional[float]:
        return self.filesize / 1024 / 1024 if self.filesize else None

    @property
    def has_audio(self) -> bool:
        return self.acodec not in ("none", None, "")

    @property
    def has_video(self) -> bool:
        return self.vcodec not in ("none", None, "")

    @property
    def is_hls(self) -> bool:
        return self.protocol in ("m3u8", "m3u8_native") or "hls" in self.protocol.lower()


class VideoDownloader:
    def __init__(self, url: str, max_size_mb: int = MAX_SIZE_MB):
        self.url = url
        self.max_size_mb = max_size_mb
        self.tmpdir: Optional[str] = None

    def parse_format(self, fmt: dict) -> Optional[FormatInfo]:
        height = fmt.get("height")
        if not height:
            return None

        return FormatInfo(
            format_id=fmt.get("format_id", "unknown"),
            height=height,
            filesize=fmt.get("filesize") or fmt.get("filesize_approx"),
            acodec=fmt.get("acodec", "none"),
            vcodec=fmt.get("vcodec", "none"),
            ext=fmt.get("ext", "unknown"),
            protocol=fmt.get("protocol", "unknown"),
        )

    def get_youtube_formats(self, formats: list[dict]) -> list[FormatInfo]:
        candidates = []

        for fmt in formats:
            info = self.parse_format(fmt)
            if not info:
                continue

            if info.height <= 1080 and info.has_audio and info.has_video:
                candidates.append(info)

        candidates.sort(key=lambda f: (f.is_hls, -f.height))
        return candidates

    def get_generic_formats(self, formats: list[dict]) -> list[FormatInfo]:
        candidates = []

        for fmt in formats:
            info = self.parse_format(fmt)
            if info:
                candidates.append(info)

        candidates.sort(key=lambda f: (f.is_hls, -f.height))
        return candidates

    def clear_tmpdir(self):
        if not self.tmpdir:
            return

        for name in os.listdir(self.tmpdir):
            path = os.path.join(self.tmpdir, name)
            if os.path.isfile(path):
                os.remove(path)

    async def download_format(self, format_info: FormatInfo) -> Optional[str]:
        self.clear_tmpdir()

        outtmpl = os.path.join(self.tmpdir, f"%(id)s_{format_info.format_id}.%(ext)s")

        ydl_opts = {
            **BASE_YDL_OPTS,
            "format": format_info.format_id,
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
            "socket_timeout": 30,
        }

        try:
            await asyncio.to_thread(
                lambda: YoutubeDL(ydl_opts).download([self.url])
            )
        except Exception as e:
            print(e)
            return None

        files = [
            os.path.join(self.tmpdir, f)
            for f in os.listdir(self.tmpdir)
            if f.endswith((".mp4", ".mkv", ".webm"))
        ]

        if not files:
            return None

        return files[0]

    async def download_with_smart_format(self, max_height: int) -> Optional[str]:
        self.clear_tmpdir()

        outtmpl = os.path.join(self.tmpdir, "video.%(ext)s")

        format_selector = f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={max_height}][ext=mp4]/best[height<={max_height}]"

        ydl_opts = {
            **BASE_YDL_OPTS,
            "format": format_selector,
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
            "socket_timeout": 30,
        }

        try:
            await asyncio.to_thread(
                lambda: YoutubeDL(ydl_opts).download([self.url])
            )
        except Exception as e:
            print(e)
            return None

        files = [
            os.path.join(self.tmpdir, f)
            for f in os.listdir(self.tmpdir)
            if f.endswith((".mp4", ".mkv", ".webm"))
        ]

        return files[0] if files else None

    def check_file_size(self, filepath: str) -> tuple[bool, float]:
        size_mb = os.path.getsize(filepath) / 1024 / 1024
        fits = size_mb <= self.max_size_mb
        return fits, size_mb

    async def try_format(self, format_info: FormatInfo) -> Optional[bytes]:
        if format_info.filesize_mb:
            if format_info.filesize_mb > self.max_size_mb:
                return None

        filepath = await self.download_format(format_info)
        if not filepath:
            return None

        fits, size_mb = self.check_file_size(filepath)

        if fits:
            with open(filepath, "rb") as f:
                return f.read()
        else:
            return None

    async def try_smart_download(self, max_height: int) -> Optional[bytes]:
        filepath = await self.download_with_smart_format(max_height)
        if not filepath:
            return None

        fits, size_mb = self.check_file_size(filepath)

        if fits:
            with open(filepath, "rb") as f:
                return f.read()
        else:
            return None

    async def download_youtube(self, formats: list[FormatInfo]) -> bytes | int:
        for target_height in (1080, 720):
            matching_formats = [f for f in formats if f.height == target_height and not f.is_hls]

            if matching_formats:
                for format_info in matching_formats[:3]:
                    result = await self.try_format(format_info)
                    if result:
                        return result

            hls_formats = [f for f in formats if f.height == target_height and f.is_hls]
            if hls_formats:
                for format_info in hls_formats[:2]:
                    result = await self.try_format(format_info)
                    if result:
                        return result

            result = await self.try_smart_download(target_height)
            if result:
                return result

        return 0

    async def download_generic(self, formats: list[FormatInfo]) -> bytes | int:
        non_hls = [f for f in formats if not f.is_hls][:2]
        for format_info in non_hls:
            result = await self.try_format(format_info)
            if result:
                return result

        if len(non_hls) < 2:
            hls = [f for f in formats if f.is_hls][:2 - len(non_hls)]
            for format_info in hls:
                result = await self.try_format(format_info)
                if result:
                    return result

        return 0

    async def download(self) -> bytes | int:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.tmpdir = tmpdir

            try:
                info = await asyncio.to_thread(
                    lambda: YoutubeDL(BASE_YDL_OPTS).extract_info(self.url, download=False)
                )
            except Exception as e:
                print(e)
                return 0

            ext_key = info.get('extractor_key', '').lower()
            formats = info.get('formats', [])

            if ext_key == "youtube":
                candidates = self.get_youtube_formats(formats)
                if not candidates:
                    return 0
                return await self.download_youtube(candidates)
            else:
                candidates = self.get_generic_formats(formats)
                if not candidates:
                    return 0
                return await self.download_generic(candidates)


async def download_video_bytes(url: str) -> bytes | int:
    downloader = VideoDownloader(url, max_size_mb=MAX_SIZE_MB)
    return await downloader.download()