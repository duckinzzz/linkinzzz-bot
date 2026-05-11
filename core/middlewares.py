import asyncio
from typing import Any, Awaitable, Callable

import aiohttp
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from core.config import BOTSTATS_API_TOKEN, BOT_USERNAME
from utils.logging_utils import logger

BOTSTATS_URL = "http://duckinzzz.ru/botstats/api/"


class BotStatsMiddleware(BaseMiddleware):
    async def __call__(
            self,
            handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.text:
            json = {
                "token": BOTSTATS_API_TOKEN,
                "bot_name": BOT_USERNAME,
                "payload": {
                    "username": event.from_user.username,
                    "first_name": event.from_user.first_name,
                    "last_name": event.from_user.last_name,
                    "message": event.text,
                },
            }
            asyncio.create_task(send_stats(json=json))
        return await handler(event, data)


async def send_stats(json: dict[str, Any]) -> None:
    if not BOTSTATS_API_TOKEN:
        return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                BOTSTATS_URL,
                json=json,
                timeout=aiohttp.ClientTimeout(total=5),
            )
    except Exception:
        logger.debug("BotStats request failed", exc_info=True)
