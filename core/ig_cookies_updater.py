import asyncio
import os
import random
import traceback

import aiohttp
from playwright.async_api import async_playwright

from core.config import IG_USERNAME, IG_PASSWORD, BOT_TOKEN, ADMIN_ID, BASE_DIR

COOKIE_FILE = os.path.join(BASE_DIR, 'insta_cookies.txt')
CHECK_INTERVAL = 3600


async def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": ADMIN_ID, "text": text}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            await resp.json()


async def send_photo(photo_bytes: bytes, caption: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    data = aiohttp.FormData()
    data.add_field("chat_id", str(ADMIN_ID))
    data.add_field("caption", caption[-1000:])
    data.add_field("photo", photo_bytes, filename="error.png", content_type="image/png")
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as resp:
            await resp.json()


async def save_cookies(context):
    cookies = await context.cookies()
    with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
        f.write("# Netscape HTTP Cookie File\n")
        for cookie in cookies:
            domain = cookie.get('domain', '')
            include_subdomains = 'TRUE' if domain.startswith('.') else 'FALSE'
            path = cookie.get('path', '/')
            secure = 'TRUE' if cookie.get('secure', False) else 'FALSE'

            expires = cookie.get('expires', 0)
            expires_int = str(int(expires)) if expires > 0 else '0'

            name = cookie.get('name', '')
            value = cookie.get('value', '')

            f.write(f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expires_int}\t{name}\t{value}\n")
    print(f"[IG_UPDTR] ✅ Cookies saved in Netscape format in {COOKIE_FILE}.")


async def load_cookies(context):
    if not os.path.exists(COOKIE_FILE):
        return False

    cookies = []
    with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            parts = line.split('\t')
            if len(parts) >= 7:
                cookie = {
                    "domain": parts[0],
                    "path": parts[2],
                    "secure": parts[3].upper() == 'TRUE',
                    "name": parts[5],
                    "value": parts[6]
                }

                if parts[4].isdigit() and int(parts[4]) > 0:
                    cookie["expires"] = float(parts[4])

                cookies.append(cookie)

    if cookies:
        await context.add_cookies(cookies)
        print(f"[IG_UPDTR] 📂 Cookies loaded from {COOKIE_FILE}")
        return True
    return False


async def run_automation():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080",
                "--disable-infobars"
            ]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Europe/Moscow"
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)

        page = await context.new_page()
        print("[IG_UPDTR] Checking session validity...")
        await load_cookies(context)

        try:
            await page.goto('https://www.instagram.com/accounts/edit/', wait_until="domcontentloaded")
            await page.wait_for_timeout(random.randint(3000, 5000))

            current_url = page.url

            username_input = page.locator('form input[type="text"], input[name="username"]').first
            password_input = page.locator('form input[type="password"], input[name="password"]').first

            if "login" in current_url or await username_input.count() > 0:
                print("[IG_UPDTR] ⚠️ Cookies are invalid. Performing human-like login...")

                await username_input.wait_for(state="visible", timeout=10000)

                await username_input.click()
                await page.wait_for_timeout(random.randint(500, 1500))
                await username_input.press_sequentially(IG_USERNAME, delay=random.randint(100, 250))

                await password_input.click()
                await page.wait_for_timeout(random.randint(500, 1500))
                await password_input.press_sequentially(IG_PASSWORD, delay=random.randint(100, 250))

                await page.wait_for_timeout(random.randint(500, 1000))

                await password_input.press("Enter")

                try:
                    # Look for save login info buttons in English too, since we changed locale
                    save_btn = page.locator('button:has-text("Save info"), button:has-text("Сохранить данные")').first
                    await save_btn.wait_for(state="visible", timeout=10000)
                    await save_btn.click()
                    print("[IG_UPDTR] 🔘 Clicked 'Save info' button")
                except Exception:
                    print("[IG_UPDTR] ℹ️ 'Save info' window didn't appear or was skipped.")

                await page.wait_for_timeout(3000)
                await page.goto("https://www.instagram.com/accounts/edit/", wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)

                await save_cookies(context)
                await send_message("✅ Instagram: Cookies successfully updated!")
            elif "accounts/edit" in current_url:
                print("[IG_UPDTR] ✨ Session is still active.")

            else:
                raise Exception(f"Block or unknown page. URL: {current_url}")

        except Exception as e:
            error_msg = str(e).split('\n')[0]
            print(f"[IG_UPDTR] ❌ Error: {error_msg}")
            try:
                screenshot = await page.screenshot(full_page=True)
                await send_photo(screenshot, f"❌ Instagram Error:\n{error_msg}")
            except Exception:
                pass

        finally:
            await browser.close()


async def cookies_updater():
    print('[IG_UPDTR] IG cookies updater started')
    while True:
        try:
            await run_automation()
        except Exception as e:
            print(f"[IG_UPDTR] ❌ Critical Error in updater: {e}")
            traceback.print_exc()
        print(f"[IG_UPDTR] Next check in {CHECK_INTERVAL / 60} minutes...\n")
        await asyncio.sleep(CHECK_INTERVAL)
