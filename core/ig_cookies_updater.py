import asyncio
import os
import random
import re
import traceback

import aiohttp
from playwright.async_api import async_playwright

from core.config import IG_USERNAME, IG_PASSWORD, BOT_TOKEN, ADMIN_ID, BASE_DIR

COOKIE_FILE = os.path.join(BASE_DIR, 'insta_cookies.txt')
CHECK_INTERVAL = 86400
INSTAGRAM_EDIT_URL = 'https://www.instagram.com/accounts/edit/'
SAVE_INFO_SELECTOR = 'button:has-text("Save info"), button:has-text("Сохранить данные")'
CONTINUE_SELECTOR = ':text("Continue"), :text("Продолжить")'
DISMISS_SELECTOR = ':text("Dismiss"), :text("Отклонить")'
NOT_NOW_SELECTOR = 'button:has-text("Not Now")'
USERNAME_SELECTOR = 'form input[type="text"], input[name="username"]'
PASSWORD_SELECTOR = 'form input[type="password"], input[name="password"]'
CONTINUE_BUTTON_RE = re.compile(r"^(continue|продолжить)$", re.IGNORECASE)
USE_ANOTHER_PROFILE_RE = re.compile(r"^(use another profile|switch accounts?)$", re.IGNORECASE)


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
    with open(COOKIE_FILE, "w", encoding="utf-8") as file:
        file.write("# Netscape HTTP Cookie File\n")
        for cookie in cookies:
            domain = cookie.get("domain", "")
            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
            path = cookie.get("path", "/")
            secure = "TRUE" if cookie.get("secure", False) else "FALSE"
            expires = cookie.get("expires", 0)
            expires_value = str(int(expires)) if expires > 0 else "0"
            name = cookie.get("name", "")
            value = cookie.get("value", "")

            file.write(
                f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expires_value}\t{name}\t{value}\n"
            )
    print(f"[IG_UPDTR] Cookies saved in Netscape format in {COOKIE_FILE}.")


async def load_cookies(context):
    if not os.path.exists(COOKIE_FILE):
        return False

    cookies = []
    with open(COOKIE_FILE, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split("\t")
            if len(parts) < 7:
                continue

            cookie = {
                "domain": parts[0],
                "path": parts[2],
                "secure": parts[3].upper() == "TRUE",
                "name": parts[5],
                "value": parts[6],
            }

            if parts[4].isdigit() and int(parts[4]) > 0:
                cookie["expires"] = float(parts[4])

            cookies.append(cookie)

    if not cookies:
        return False

    await context.add_cookies(cookies)
    print(f"[IG_UPDTR] Cookies loaded from file")
    return True


async def wait_random(page, start: int, end: int):
    await page.wait_for_timeout(random.randint(start, end))


async def click_if_visible(page, selector: str, success_log: str, skip_log: str):
    try:
        button = page.locator(selector).first
        await button.wait_for(state="visible", timeout=10000)
        await button.click()
        print(success_log)
        return True
    except Exception:
        print(skip_log)
        return False


async def click_locator_if_visible(locator, success_log: str, skip_log: str, timeout: int = 10000):
    try:
        button = locator.first
        await button.wait_for(state="visible", timeout=timeout)
        await button.click()
        print(success_log)
        return True
    except Exception:
        print(skip_log)
        return False


async def submit_password(password_input, page):
    await password_input.wait_for(state="visible", timeout=15000)
    await password_input.click()
    await wait_random(page, 500, 1500)
    await password_input.press_sequentially(IG_PASSWORD, delay=random.randint(100, 250))
    await wait_random(page, 500, 1000)
    await password_input.press("Enter")


async def finish_login_flow(context, page):
    await click_if_visible(
        page,
        SAVE_INFO_SELECTOR,
        "[IG_UPDTR] Clicked 'Save info' button",
        "[IG_UPDTR] 'Save info' window didn't appear or was skipped.",
    )
    await page.wait_for_timeout(3000)
    await page.goto(INSTAGRAM_EDIT_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)
    await save_cookies(context)
    await send_message("✅ Instagram: Cookies successfully updated!")


async def is_continue_page(page):
    continue_button = page.get_by_role("button", name=CONTINUE_BUTTON_RE).first
    alt_profile_button = page.get_by_role("button", name=USE_ANOTHER_PROFILE_RE).first
    return await continue_button.count() > 0 or await alt_profile_button.count() > 0


async def locator_is_visible(locator):
    try:
        return await locator.is_visible()
    except Exception:
        return False


async def debug_page_state(page, username_input, password_input):
    title = await page.title()
    username_count = await username_input.count()
    password_count = await password_input.count()
    username_visible = await locator_is_visible(username_input)
    password_visible = await locator_is_visible(password_input)
    body_text = ((await page.locator("body").inner_text())[:500]).replace("\n", " ")

    print(f"[IG_UPDTR] URL: {page.url}")
    print(f"[IG_UPDTR] Title: {title}")
    print(
        f"[IG_UPDTR] Login fields: username_count={username_count}, "
        f"username_visible={username_visible}, password_count={password_count}, "
        f"password_visible={password_visible}"
    )
    print(f"[IG_UPDTR] Body preview: {body_text}")


async def send_page_error(page, error: Exception, log_message: str):
    error_msg = str(error).split("\n")[0]
    print(log_message)
    screenshot = await page.screenshot(full_page=True)
    await send_photo(screenshot, f"❌ Instagram Error:\n{error_msg}")


async def run_automation():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080",
                "--disable-infobars",
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Europe/Moscow",
        )

        await context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """
        )

        page = await context.new_page()
        print("[IG_UPDTR] Checking session validity...")
        await load_cookies(context)

        try:
            await page.goto(INSTAGRAM_EDIT_URL, wait_until="domcontentloaded")
            await wait_random(page, 3000, 5000)

            current_url = page.url
            username_input = page.locator(USERNAME_SELECTOR).first
            password_input = page.locator(PASSWORD_SELECTOR).first
            username_visible = await locator_is_visible(username_input)

            await debug_page_state(page, username_input, password_input)

            if "accounts/edit" in current_url:
                print("[IG_UPDTR] Session is still active.")

            elif await is_continue_page(page):
                print("[IG_UPDTR] 'Continue' page")

                try:
                    continue_clicked = await click_locator_if_visible(
                        page.get_by_role("button", name=CONTINUE_BUTTON_RE),
                        "[IG_UPDTR] Clicked 'Continue' button",
                        "[IG_UPDTR] 'Continue' button not found by role.",
                    )
                    if not continue_clicked:
                        continue_clicked = await click_if_visible(
                            page,
                            'button:has-text("Continue"), button:has-text("Continue as"), button:has-text("Продолжить")',
                            "[IG_UPDTR] Clicked 'Continue' button via text selector",
                            "[IG_UPDTR] 'Continue' button not found by text selector.",
                        )
                    if not continue_clicked:
                        raise Exception(f"Continue button was not found. URL: {page.url}")

                    password_input = page.locator(PASSWORD_SELECTOR).first
                    await submit_password(password_input, page)
                    await click_if_visible(
                        page,
                        NOT_NOW_SELECTOR,
                        "[IG_UPDTR] Clicked 'Not Now' button",
                        "[IG_UPDTR] 'Turn on Notifications' window didn't appear or was skipped.",
                    )
                    await finish_login_flow(context, page)
                except Exception as error:
                    await send_page_error(page, error, "[IG_UPDTR] 'Continue' window didn't appear or was skipped.")

            elif "login" in current_url or username_visible:
                print("[IG_UPDTR] Cookies are invalid. Performing human-like login...")

                await username_input.wait_for(state="visible", timeout=15000)
                await username_input.click()
                await wait_random(page, 500, 1500)
                await username_input.press_sequentially(IG_USERNAME, delay=random.randint(100, 250))
                await submit_password(password_input, page)
                await finish_login_flow(context, page)

            elif "challenge/" in current_url:
                print("[IG_UPDTR] 'Automated behaviour' page")

                try:
                    dismiss_button = page.locator(DISMISS_SELECTOR).first
                    await dismiss_button.wait_for(state="visible", timeout=10000)
                    await dismiss_button.click()
                    print("[IG_UPDTR] Clicked 'Dismiss' button")
                    await page.wait_for_timeout(3000)
                    await page.goto(INSTAGRAM_EDIT_URL, wait_until="domcontentloaded")
                    await page.wait_for_timeout(5000)
                    await save_cookies(context)
                    await send_message("✅ Instagram: Cookies successfully updated!")
                except Exception as error:
                    await send_page_error(page, error, "[IG_UPDTR] 'Automated behaviour' window error.")

            else:
                raise Exception(f"Block or unknown page. URL: {current_url}")

        except Exception as error:
            error_msg = str(error).split("\n")[0]
            print(f"[IG_UPDTR] Error: {error_msg}")
            try:
                screenshot = await page.screenshot(full_page=True)
                await send_photo(screenshot, f"❌ Instagram Error:\n{error_msg}")
            except Exception:
                pass

        finally:
            await browser.close()


async def cookies_updater():
    print("[IG_UPDTR] IG cookies updater started")
    while True:
        try:
            await run_automation()
        except Exception as error:
            print(f"[IG_UPDTR] Critical Error in updater: {error}")
            traceback.print_exc()
        print(f"[IG_UPDTR] Next check in {CHECK_INTERVAL / 60} minutes...\n")
        await asyncio.sleep(CHECK_INTERVAL)
