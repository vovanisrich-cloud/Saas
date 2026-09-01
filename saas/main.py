"""Bot entry point and web server startup."""

import asyncio
import logging
from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update

from saas.config import TOKEN, APP_HOST, APP_PORT, PAYMENT_WEBHOOK_SECRET, logger
from saas.handlers import register_handlers
from saas.handlers.payment import set_bot
from saas.payments.webhook import wayforpay_service_url
from database import BookingDatabase

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Register all handlers
register_handlers(dp)

# Set bot instance for handlers
set_bot(bot)

# Initialize database
BookingDatabase.init_db()


async def start_web_server() -> web.AppRunner:
    """Start aiohttp web server for payment webhooks."""
    app = web.Application()

    async def webhook_handler(request: web.Request) -> web.Response:
        """Wrap webhook handler with bot instance."""
        return await wayforpay_service_url(request, bot=bot)

    app.router.add_post(f"/payments/wayforpay/{PAYMENT_WEBHOOK_SECRET}", webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=APP_HOST, port=APP_PORT)
    await site.start()

    logger.info(
        "WayForPay webhook server started on http://%s:%s/payments/wayforpay/%s",
        APP_HOST,
        APP_PORT,
        PAYMENT_WEBHOOK_SECRET,
    )
    return runner


async def main():
    """Main bot function."""
    logger.info("Bot is starting")

    # Start web server for webhooks
    runner = await start_web_server()

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
