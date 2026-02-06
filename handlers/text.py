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

    ans = await message.reply('Загрузка может занять время...')
    try:
        video_bytes = await download_video_bytes(url)
    except Exception as e:
        await ans.edit_text('Ниасилил...( Либо слишком длинный видос, либо неподдерживаемый сайт(')
        log_error(request_type='yt-dlp', message=message, chat_id=chat_id, error=e)
        return

    if video_bytes:
        ans = await ans.edit_text('Скачал, отправляю...')
        await message.reply_video(BufferedInputFile(video_bytes, filename="video.mp4"))
        await ans.delete()
    else:
        await ans.edit_text('Ниасилил...( Либо слишком длинный видос, либо неподдерживаемый сайт(')
        log_error(request_type='yt-dlp', message=message, chat_id=chat_id, error='video_bytes is None')
    log_message(request_type='yt-dlp', message=message)
