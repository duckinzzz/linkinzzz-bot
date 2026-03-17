import asyncio

from core.ig_cookies_updater import cookies_updater


if __name__ == "__main__":
    asyncio.run(cookies_updater())
