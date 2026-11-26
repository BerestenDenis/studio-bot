"""Вебхук-версия бота для деплоя в облако (Render / VPS).

Отличия от bot.py: не polling, а HTTP-сервер, который Telegram сам дёргает.
Прокси не нужен — на сервере доступ к api.telegram.org прямой.
"""
import asyncio
import os
import sys
from pathlib import Path

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

sys.path.insert(0, str(Path(__file__).parent))
from bot import router, init_db, TOKEN  # noqa: E402

BASE_URL = (os.getenv("BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", "8080"))
PROXY = os.getenv("PROXY_URL", "").strip()


async def health(request):
    return web.json_response({"ok": True, "service": "studio-bot"})


async def on_startup(bot: Bot):
    if BASE_URL:
        await bot.set_webhook(f"{BASE_URL}{WEBHOOK_PATH}",
                              drop_pending_updates=False)
        print(f"webhook -> {BASE_URL}{WEBHOOK_PATH}", flush=True)


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN не задан")
    init_db()

    session = AiohttpSession(proxy=PROXY) if PROXY else None
    bot = Bot(TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(router)
    dp.startup.register(on_startup)

    app = web.Application()
    app.router.add_get("/", health)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
