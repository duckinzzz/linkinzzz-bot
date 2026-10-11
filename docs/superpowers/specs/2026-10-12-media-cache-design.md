# Дизайн: кэш медиа по каноническому URL поста

Дата: 2026-10-12
Ветка: `feat/media-cache`

## Цель

Не качать и не заливать в Telegram один и тот же пост дважды. Ценность — `file_id`
уже загруженного файла: переиспользование выкидывает из пути gallery-dl/yt-dlp,
ffmpeg, base64 и аплоад.

## Что проверено эмпирически

Прогон `gallery-dl -j` / `yt-dlp -J` на живых IG-ссылках (2026-10-12):

| вход | gallery-dl `post_url` |
|---|---|
| `/reel/DeToSP9AwFv/?rpxt=c3Jz...` | `https://www.instagram.com/reel/DeToSP9AwFv/` |
| `/p/DeToSP9AwFv/` | `https://www.instagram.com/reel/DeToSP9AwFv/` |

gallery-dl срезает query и нормализует форму пути (`/p/` → `/reel/`), то есть
`post_url` уже канонический. Рядом лежат `post_shortcode`, `post_id`.

| вход | yt-dlp `webpage_url` |
|---|---|
| `/reel/DeToSP9AwFv/?rpxt=...` | то же самое, с `?rpxt=...` |
| `/p/DeToSP9AwFv/` | `.../p/DeToSP9AwFv/` |
| `/reels/DeToSP9AwFv/` | `.../reels/DeToSP9AwFv/` |

yt-dlp возвращает вход как есть: ни query не срезает, ни форму пути не приводит.
Ключом `webpage_url` быть не может — годится только пара `extractor` + `id`.

Медиа-уровень: у каждого файла есть числовой `media_id`, `num`, `extension`,
`width`/`height`; у карусели пост-объект даёт `count`, дальше N файловых.
`video_url`/`display_url` — подписанные CDN-ссылки (`_nc_cat`, `ig_cache_key`),
меняются от прогона к прогону, в ключ не годятся.

Отдельно: yt-dlp для Instagram требует куки, а бот зовёт его без них
(коммит `acc4775`). Значит для IG ветка yt-dlp нерабочая, и объединять ключи
двух экстракторов в один незачем.

## Ключ

- gallery-dl: `post_url` как есть.
- yt-dlp: `"{extractor}:{id}"`.
- Пространство имён в самой таблице (`source`), чтобы ключи не пересекались.

## Важное следствие: кэш не отменяет запрос метаданных

Канонический ключ известен только от экстрактора. Чтобы проверить кэш **до**
скачивания, нужен отдельный дешёвый прогон в режиме «только метаданные»
(`gallery-dl -j`, без загрузки медиа). Без него кэш бесполезен: узнать ключ
можно лишь после полной выкачки.

Поэтому каждый запрос как минимум делает один резолв метаданных, а кэш экономит
именно скачивание медиа, ffmpeg-перекодирование и аплоад в Telegram. Резолв —
один HTTPS-запрос, секунды; выкачка — десятки секунд и десятки мегабайт.

Резолв зеркалит логику скачивания: сначала `gallery-dl -j`, при
`UnsupportedSite`/ошибке — `yt-dlp -J`.

## Схема БД

`sqlite3` из стандартной библиотеки, файл на volume, путь из env `CACHE_DB_PATH`
(по умолчанию `/app/data/cache.db`).

```sql
CREATE TABLE IF NOT EXISTS posts (
    key        TEXT PRIMARY KEY,   -- канонический post_url либо "extractor:id"
    source     TEXT NOT NULL,      -- gallery-dl | yt-dlp
    caption    TEXT,
    media_keys TEXT NOT NULL,      -- JSON-массив ключей в порядке поста
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
    key        TEXT PRIMARY KEY,   -- gallery-dl: media_id; yt-dlp: info["id"]
    file_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,      -- photo | video
    width      INTEGER,
    height     INTEGER,
    updated_at REAL NOT NULL
);
```

Запись поста идёт одной транзакцией: сначала `media`, затем `posts`.

Порядок медиа берётся из `num` у gallery-dl. Если при промахе состав из
резолва и состав из собственных метаданных скачивания расходятся — верим
скачиванию, оно пишет и ключи, и `file_id` из одного прогона.

## Поток

Успешный путь с промахом (первый запрос):

1. Резолв метаданных → `post_key`, caption, список медиа-ключей в порядке поста.
2. `SELECT` из `posts` по `post_key`.
3. Промах → скачивание как сейчас (`download_post_json`) → `_send_payload`.
4. `_send_payload` сейчас выбрасывает возвращённые `Message` — начинаем их
   собирать: у одиночной отправки это `Message`, у `answer_media_group` —
   `list[Message]` в том же порядке, что и `media`.
5. Из каждого сообщения берём `photo[-1].file_id` либо `video.file_id`,
   `width`/`height`, пишем `media` + `posts`.

Путь с попаданием: резолв → `SELECT` → собрать
`InputMediaPhoto/Video(media=file_id, ...)` → тот же `answer_media_group`
(или `answer_photo`/`answer_video` для одного файла). Кэш-ветка в `_send_payload`
по флагу, тело отправки общее.

## Ошибки и инвалидация

- `file_id` может протухнуть. При `TelegramBadRequest` («wrong file identifier»)
  на кэш-ветке — удалить строки поста и пройти обычный путь скачивания.
- Ошибки (`NoMedia`, `UnsupportedSite`, `TooLarge`, `InappropriateContent`)
  не кэшируются: контент может появиться позже.
- TTL: чистка записей старше `CACHE_TTL_DAYS` (по умолчанию 30) на старте.
- Гонка: два одновременных запроса одной ссылки. `asyncio.Lock` на ключ в
  словаре, чтобы второй ждал первый и попал в кэш, а не качал параллельно.

## Затрагиваемые файлы

- `utils/cache.py` — новый: схема, get/set, TTL, ключи.
- `utils/download_utils.py` — резолв метаданных, возврат `post_key` и
  медиа-ключей вместе с payload.
- `handlers/text.py` — проверка кэша, сбор `file_id` из ответов, кэш-ветка
  в `_send_payload`.
- `core/config.py` — `CACHE_DB_PATH`, `CACHE_TTL_DAYS`.
- `docker-compose.yml`, `docker-compose.DEV.yml` — volume под `/app/data`.
- `.dockerignore` — если БД окажется в контексте сборки.

## Тестирование

Тестов в проекте нет вообще. Чистые части (нормализация ключа, get/set, TTL)
тестируемы юнит-тестами без сети и Telegram. Предлагаю добавить `pytest`
отдельной dev-группой в `pyproject.toml` — **решение за тобой**, в фичу молча
не тяну.

Интеграционная проверка на деве: два запроса одной ссылки подряд, в логах
`cache MISS` затем `cache HIT`, второй без скачивания; повторный запрос с
`?igsh=...` и с формой `/p/` вместо `/reel/` должен попадать в тот же ключ.

## Вне объёма

- Частичные попадания по карусели (пост есть, но закэширован не весь) — при
  неполном наборе перекачиваем пост целиком.
- Алиасы «нормализованный URL юзера → post_key» для экономии резолва.
- Общий ключ между gallery-dl и yt-dlp.
