import re

from aiogram import F, Router
from aiogram.types import Message, BufferedInputFile

from utils.logging_utils import log_message, log_error
from utils.ytdlp_utils import download_video_bytes

text_router = Router()


@text_router.message(F.content_type == "text", F.chat.type == "private")
async def text_private_handler(message: Message):
    chat_id = message.chat.id
    url = message.text
    pattern = re.compile(
        r'^(https?://)?'
        r'(www\.)?'
        r'([a-zA-Z0-9-]+\.)+'
        r'([a-zA-Z]{2,})'
        r'(/\S*)?$'
    )

    if not pattern.match(url):
        await message.reply('Это не ссылка')
        log_message(request_type='yt-dlp', message=message)
        return

    if 'youtube' in url or 'youtu.be' in url:
        await message.reply('Ютуп пока не хаваю 🤒')
        log_message(request_type='yt-dlp', message=message)
        return

    ans = await message.reply('⏳Загружаю...')

    async def progress_callback(step: str):
        await ans.edit_text(text=step)

    try:
        video_bytes, width, height = await download_video_bytes(url, progress_callback)
    except Exception as e:
        await progress_callback('Ниасилил😥 Неподдерживаемый сайт')
        log_error(request_type='yt-dlp', message=message, chat_id=chat_id, error=e)
        return

    if video_bytes:
        await progress_callback('Отправляю...')
        try:
            await message.reply_video(BufferedInputFile(video_bytes, filename="video.mp4"),
                                      width=width,
                                      height=height)
        except Exception as e:
            await progress_callback('Телеграм не пускает, попробуйте снова')
            log_error(request_type='yt-dlp', message=message, chat_id=chat_id, error=e)
        await ans.delete()
    else:
        await progress_callback('Cлишком длинное видео')
        log_error(request_type='yt-dlp', message=message, chat_id=chat_id, error='video_bytes is None')
    log_message(request_type='yt-dlp', message=message)
