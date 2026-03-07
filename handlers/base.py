from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from utils.logging_utils import log_event

base_router = Router()


@base_router.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    username = f"@{user.username}" if user.username else f"{user.first_name or user.id}"
    uid = message.from_user.id

    welcome_text = (
        "🔗 *Linkinzzz* – бот для скачивания контента.\n"
        "Просто отправьте ссылку!\n\n"
        "Использует [gallery-dl](https://github.com/mikf/gallery-dl), [yt-dlp](https://github.com/yt-dlp/yt-dlp) и [ffmpeg](https://github.com/FFmpeg/FFmpeg)"
    )

    await message.answer(welcome_text, parse_mode="Markdown", disable_web_page_preview=True)
    log_event(event='bot_start', username=username, user_id=uid, chat_id=message.chat.id)
