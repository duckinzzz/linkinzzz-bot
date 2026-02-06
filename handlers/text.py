from aiogram import F, Router
from aiogram.types import Message, BufferedInputFile

from utils.logging_utils import log_message
from utils.ytdlp_utils import download_video_bytes

text_router = Router()


@text_router.message(F.content_type == "text", F.chat.type == "private")
async def text_private_handler(message: Message):
    url = message.text
    chat_id = message.chat.id
    ans = await message.reply('ща скачаю...')
    try:
        video_bytes = await download_video_bytes(url)
    except Exception as e:
        await ans.edit_text('ниасилил...( либо слишком длинный видос, либо неподдерживаемый сайт')
        print(e)
        return

    if video_bytes:
        ans = await ans.edit_text('скачал, отправляю...')
        await message.reply_video(BufferedInputFile(video_bytes, filename="video.mp4"))
        await ans.delete()
    else:
        await ans.edit_text('ниасилил...( либо слишком длинный видос, либо неподдерживаемый сайт')
        print('video_bytes', video_bytes)
    log_message(request_type='llm_question', message=message)
