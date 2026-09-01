"""Bot event handlers and routers."""

from aiogram import Dispatcher

from . import master, booking, payment, subscription


def register_handlers(dp: Dispatcher):
    """Register all handlers and routers."""
    dp.include_router(master.router)
    dp.include_router(booking.router)
    dp.include_router(payment.router)
    dp.include_router(subscription.router)
