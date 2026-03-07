import base64
import re
from typing import Any, Dict, List

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, BufferedInputFile, InputMediaPhoto, InputMediaVideo

from core.config import ADMIN_ID
from core.errors import DownloadError, InappropriateContent, NoMedia, UnsupportedSite, TooLarge
from utils.download_utils import download_post_json
from utils.logging_utils import log_message, log_error

text_router = Router()

MAX_MEDIA_GROUP = 10
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm")


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


async def _send_payload(message: Message, payload: Dict[str, Any]):
    caption: str = payload.get("caption") or ""
    if caption:
        if len(caption) > 1024:
            caption = caption[:1023] + "…"
    caption = f"<blockquote expandable>{caption}</blockquote>"
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

    if len(media) == 1:
        m = media[0]
        if isinstance(m, InputMediaPhoto):
            await message.answer_photo(m.media, caption=caption or None)
        else:
            await message.answer_video(m.media, caption=caption or None, width=m.width, height=m.height)
        return

    for group in _chunk(media, MAX_MEDIA_GROUP):
        await message.answer_media_group(group)


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

    if 'youtube' in url or 'youtu.be' in url:
        await message.reply('Ютуп пока не хаваю 🤒')
        log_message(request_type='gallery-dl', message=message)
        return

    status, progress_callback = await _make_progress(message)

    try:
        await progress_callback("⏳Загружаю...")
        payload = await download_post_json(url, progress_callback)

        await progress_callback("📤Отправляю...")
        await _send_payload(message, payload)

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
