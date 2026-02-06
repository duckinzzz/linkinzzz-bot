import inspect
import json
import logging
import sys

from aiogram import types

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("bot_core")


def log_message(message: types.Message, **kwargs):
    caller_name = inspect.stack()[1].function

    user = message.from_user
    username = f"@{user.username}" if user.username else f"{user.first_name or user.id}"
    chat = message.chat

    data = {
        "service": "bot",
        "level": "info",
        "handler": caller_name,
        "user_id": user.id,
        "username": username,
    }
    if message.text:
        data.update({'text': message.text})

    if chat:
        data.update({
            "chatID": chat.id,
            "chat_type": chat.type,
        })

    data.update(kwargs)

    logger.info(json.dumps(data, ensure_ascii=False))


def log_event(event, **kwargs):
    caller_name = inspect.stack()[1].function
    data = {
        "event": event,
        "service": "bot",
        "level": "info",
        "handler": caller_name,
    }
    data.update(kwargs)
    logger.info(json.dumps(data, ensure_ascii=False))


def log_error(request_type, error, message=None, **kwargs):
    caller_name = inspect.stack()[1].function
    data = {
        "request_type": request_type,
        "service": "bot",
        "level": "error",
        "handler": caller_name,
        'error': str(error),
        'error_type': type(error).__name__
    }
    if message:
        user = message.from_user
        username = f"@{user.username}" if user.username else f"{user.first_name or user.id}"
        chat = message.chat
        data.update({
            'user_id': user.id,
            'username': username,
        })
        if message.text:
            data.update({'text': message.text})
        if chat:
            data.update({
                "chatID": chat.id,
                "chat_type": chat.type,
            })

    data.update(kwargs)
    logger.error(json.dumps(data, ensure_ascii=False))
