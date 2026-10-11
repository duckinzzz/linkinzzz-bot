import base64
import re
from typing import Any, Dict, List

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, BufferedInputFile, InputMediaPhoto, InputMediaVideo

from core.config import ADMIN_ID
from core.errors import DownloadError, InappropriateContent, NoMedia, UnsupportedSite, TooLarge
from utils import cache
from utils.download_utils import download_post_json
from utils.logging_utils import log_message, log_error, log_event

text_router = Router()

MAX_MEDIA_GROUP = 10
MAX_CAPTION = 1024


def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


async def _notify_admin(message: Message, note: str):
    forwarded = await message.forward(chat_id=ADMIN_ID)
    await forwarded.answer(note)


async def _make_progress(message: Message):
    status = await message.reply("⏳Загружаю...")

    async def progress_callback(step: str):
        try:
            await status.edit_text(text=step)
        except TelegramBadRequest:
            pass

    return status, progress_callback


def _format_caption(caption: str) -> str:
    caption = caption or ""
    if len(caption) > MAX_CAPTION:
        caption = caption[:MAX_CAPTION - 1] + "…"
    return f"<blockquote expandable>{caption}</blockquote>"


async def _dispatch_media(message: Message, media: List[Any], caption: str) -> List[Message]:
    """Отправляет готовый список InputMedia*, возвращая отправленные сообщения (в порядке медиа)."""
    sent: List[Message] = []
    if len(media) == 1:
        m = media[0]
        if isinstance(m, InputMediaPhoto):
            sent.append(await message.answer_photo(m.media, caption=caption or None))
        else:
            sent.append(await message.answer_video(m.media, caption=caption or None,
                                                   width=m.width, height=m.height))
        return sent

    for group in _chunk(media, MAX_MEDIA_GROUP):
        sent.extend(await message.answer_media_group(group))
    return sent


def _extract_media(sent: List[Message]) -> List[Dict[str, Any]]:
    """Достаёт file_id отправленных файлов — они и есть то, что кэшируем."""
    media: List[Dict[str, Any]] = []
    for message in sent:
        if message.photo:
            photo = message.photo[-1]
            media.append({"file_id": photo.file_id, "kind": "photo",
                          "width": photo.width, "height": photo.height})
        elif message.video:
            media.append({"file_id": message.video.file_id, "kind": "video",
                          "width": message.video.width, "height": message.video.height})
    return media


async def _send_payload(message: Message, payload: Dict[str, Any]) -> List[Message]:
    caption = _format_caption(payload.get("caption") or "")
    content: List[Dict[str, Any]] = payload.get("content") or []

    if not content:
        raise NoMedia("EMPTY_CONTENT")

    media: List[Any] = []
    for idx, item in enumerate(content):
        t = item.get("type")
        data = base64.b64decode(item.get("data").encode("ascii"))
        w = item.get("width")
        h = item.get("height")

        if not data:
            continue

        if t == "image":
            media.append(InputMediaPhoto(media=BufferedInputFile(data, filename=f"image_{idx}.jpg"),
                                         caption=caption if idx == 0 else None, parse_mode='HTML'))
        elif t == "video":
            media.append(InputMediaVideo(media=BufferedInputFile(data, filename=f"video_{idx}.mp4"), width=w, height=h,
                                         caption=caption if idx == 0 else None, parse_mode='HTML'))

    if not media:
        raise NoMedia("NO_VALID_MEDIA")

    return await _dispatch_media(message, media, caption)


async def _send_cached(message: Message, entry: Dict[str, Any]) -> List[Message]:
    """Отправляет пост из кэша по file_id — без скачивания, ffmpeg и аплоада."""
    caption = _format_caption(entry.get("caption") or "")

    media: List[Any] = []
    for idx, item in enumerate(entry.get("media") or []):
        item_caption = caption if idx == 0 else None
        if item["kind"] == "photo":
            media.append(InputMediaPhoto(media=item["file_id"], caption=item_caption, parse_mode='HTML'))
        else:
            media.append(InputMediaVideo(media=item["file_id"], width=item.get("width"),
                                         height=item.get("height"), caption=item_caption, parse_mode='HTML'))

    if not media:
        raise NoMedia("EMPTY_CACHE")

    return await _dispatch_media(message, media, caption)


async def _deliver(message: Message, url: str, cache_key: str, progress_callback) -> None:
    """Отдаёт пост в чат: из кэша, иначе качает, отправляет и кэширует результат."""
    entry = await cache.get_post(cache_key) if cache_key else None
    if entry:
        try:
            await progress_callback("📤Отправляю...")
            await _send_cached(message, entry)
            log_event(event="cache_hit", data=cache_key)
            return
        except TelegramBadRequest as e:
            # file_id протух — чистим запись и идём обычным путём
            log_event(event="cache_stale", data=f"{cache_key} | {e}")
            await cache.delete_post(cache_key)

    if cache_key:
        log_event(event="cache_miss", data=cache_key)

    await progress_callback("⏳Загружаю...")
    payload = await download_post_json(
        url, progress_callback,
        on_cookies_expired=lambda: _notify_admin(message, "стухли куки"),
    )

    await progress_callback("📤Отправляю...")
    sent = await _send_payload(message, payload)

    if not cache_key:
        return

    media = _extract_media(sent)
    if media and len(media) == len(sent):
        await cache.put_post(cache_key, payload.get("caption") or "", media)
        log_event(event="cache_store", data=cache_key)
    else:
        log_event(event="cache_skip_incomplete",
                  data=f"{cache_key} | sent={len(sent)} extracted={len(media)}")


@text_router.message(F.content_type == "text", F.chat.type == "private")
async def text_private_handler(message: Message):
    chat_id = message.chat.id
    url = (message.text or "").strip()

    pattern = re.compile(r'^(https?://)?'
                         r'(www\.)?'
                         r'([a-zA-Z0-9-]+\.)+'
                         r'([a-zA-Z]{2,})'
                         r'(/\S*)?$')

    if not pattern.match(url):
        await message.reply('Это не ссылка')
        log_message(request_type='gallery-dl', message=message)
        return

    status, progress_callback = await _make_progress(message)
    cache_key = cache.normalize_url(url)

    try:
        if cache_key:
            async with cache.lock_for(cache_key):
                await _deliver(message, url, cache_key, progress_callback)
        else:
            log_event(event="cache_skip_unnormalizable", data=url)
            await _deliver(message, url, "", progress_callback)
        try:
            await status.delete()
        except TelegramBadRequest:
            pass

        log_message(request_type="gallery-dl", message=message)

    except InappropriateContent as e:
        await progress_callback("Это видео пока нельзя скачать, попробуйте позже.\n"
                                "Разработчику отправлено уведомление об ошибке.")
        await _notify_admin(message, "стухли куки")
        log_error(request_type='gallery-dl', message=message, chat_id=chat_id, error=e)

    except NoMedia as e:
        await progress_callback("В посте нет медиа 😥")
        log_error(request_type='gallery-dl', message=message, chat_id=chat_id, error=e)

    except TooLarge as e:
        await progress_callback("Слишком большой файл(ы) 😥")
        log_error(request_type='gallery-dl', message=message, chat_id=chat_id, error=e)

    except UnsupportedSite as e:
        await progress_callback("Ниасилил😥 Неподдерживаемый сайт")
        await _notify_admin(message, "пытались скачать")
        log_error(request_type='gallery-dl', message=message, chat_id=chat_id, error=e)

    except DownloadError as e:
        await progress_callback("Ошибка загрузки 😥 Попробуйте позже")
        await _notify_admin(message, f"download error: {type(e).__name__}")
        log_error(request_type='gallery-dl', message=message, chat_id=chat_id, error=e)

    except Exception as e:
        await progress_callback("Телеграм не пускает или произошла ошибка. Попробуйте снова")
        await _notify_admin(message, "пытались скачать (unknown error)")
        log_error(request_type='gallery-dl', message=message, chat_id=chat_id, error=e)

    finally:
        if cache_key:
            cache.release_lock(cache_key)
