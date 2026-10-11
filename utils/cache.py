"""Кэш медиа: нормализованный URL поста -> file_id уже отправленных в Telegram файлов."""

import asyncio
import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from core.config import CACHE_DB_PATH, CACHE_TTL_DAYS
from utils.logging_utils import logger

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    key        TEXT PRIMARY KEY,
    caption    TEXT,
    media      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

_locks: Dict[str, asyncio.Lock] = {}


def normalize_url(url: str) -> Optional[str]:
    """Канонизирует ссылку так, чтобы разные написания одного поста дали один ключ.

    Возвращает None, если из ссылки не выходит ничего осмысленного.
    Промах нормализации безопасен: это повторная выкачка, но не чужой файл.
    """
    raw = (url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw

    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None

    path = unquote(parts.path).rstrip("/")

    if host.endswith("instagram.com"):
        match = re.match(r"^/(?:p|reel|reels|tv)/([^/]+)", path)
        if match:
            return f"instagram.com/{match.group(1)}"

    if host.endswith("youtube.com"):
        if path == "/watch":
            video_id = parse_qs(parts.query).get("v", [None])[0]
            if video_id:
                return f"youtube.com/{video_id}"
        match = re.match(r"^/(?:shorts|embed|live|v)/([^/]+)", path)
        if match:
            return f"youtube.com/{match.group(1)}"

    if host == "youtu.be":
        video_id = path.lstrip("/").split("/")[0]
        if video_id:
            return f"youtube.com/{video_id}"

    if host.endswith("tiktok.com"):
        match = re.search(r"/video/(\d+)", path)
        if match:
            return f"tiktok.com/{match.group(1)}"

    return f"{host}{path}" if path else host


def lock_for(key: str) -> asyncio.Lock:
    """Сериализует одновременные запросы одной и той же ссылки."""
    return _locks.setdefault(key, asyncio.Lock())


def release_lock(key: str) -> None:
    lock = _locks.get(key)
    if lock is not None and not lock.locked() and not lock._waiters:
        _locks.pop(key, None)


def _connect() -> sqlite3.Connection:
    directory = os.path.dirname(CACHE_DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _get_post(key: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT caption, media FROM posts WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        media = json.loads(row["media"])
        if not media:
            return None
        return {"caption": row["caption"] or "", "media": media}
    finally:
        conn.close()


def _put_post(key: str, caption: str, media: List[Dict[str, Any]]) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO posts (key, caption, media, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET caption = excluded.caption, "
                "media = excluded.media, updated_at = excluded.updated_at",
                (key, caption, json.dumps(media), time.time()),
            )
    finally:
        conn.close()


def _delete_post(key: str) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute("DELETE FROM posts WHERE key = ?", (key,))
    finally:
        conn.close()


def _purge_expired(ttl_days: int) -> int:
    cutoff = time.time() - ttl_days * 86400
    conn = _connect()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM posts WHERE updated_at < ?", (cutoff,))
            return cursor.rowcount or 0
    finally:
        conn.close()


async def init_db() -> None:
    await asyncio.to_thread(_init_db)


async def get_post(key: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_get_post, key)


async def put_post(key: str, caption: str, media: List[Dict[str, Any]]) -> None:
    await asyncio.to_thread(_put_post, key, caption, media)


async def delete_post(key: str) -> None:
    await asyncio.to_thread(_delete_post, key)


async def purge_expired(ttl_days: int = CACHE_TTL_DAYS) -> int:
    removed = await asyncio.to_thread(_purge_expired, ttl_days)
    if removed:
        logger.info(f"cache: purged {removed} expired posts (ttl {ttl_days}d)")
    return removed
