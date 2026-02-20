from __future__ import annotations


class DownloadError(Exception):
    """Базовая ошибка скачивания/обработки."""


class InappropriateContent(DownloadError):
    """Контент помечен как неподходящий/ограниченный (часто проблема cookies/age-gate)."""


class NoMedia(DownloadError):
    """В посте не найдено медиа (ни видео, ни изображения) или нечего отправлять."""


class NoVideo(NoMedia):
    """Совместимость со старым кодом: 'в посте нет видео' (теперь может быть NoMedia)."""


class UnsupportedSite(DownloadError):
    """Сайт/источник не поддерживается или не удалось извлечь медиа."""


class TooLarge(DownloadError):
    """Файл(ы) слишком большие по внутренним ограничениям."""
