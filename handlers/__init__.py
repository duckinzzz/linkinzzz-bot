from aiogram import Router

from .base import base_router
from .text import text_router


def get_main_router() -> Router:
    main_router = Router()

    main_router.include_router(base_router)
    main_router.include_router(text_router)

    return main_router
