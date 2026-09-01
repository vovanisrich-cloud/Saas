"""Notification functions for booking confirmations and updates."""

import logging
from datetime import datetime
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


async def notify_booking_confirmed(bot: Bot, pending: dict):
    """Send confirmation message to client after payment."""
    from .utils import mask_card_last4
    
    date_obj = datetime.strptime(pending["booking_date"], "%Y-%m-%d")
    date_display = date_obj.strftime("%d.%m.%Y")
    message = (
        "🎉 Оплату отримано, запис підтверджено!\n\n"
        f"📅 <b>{date_display}</b>\n"
        f"🕒 <b>{pending['booking_time']}</b>\n"
        f"💅 <b>{pending['service']}</b>\n"
        f"👤 <b>{pending['full_name']}</b>"
    )
    try:
        await bot.send_message(pending["user_id"], message, parse_mode="HTML")
    except Exception as exc:
        logger.warning("Failed to notify user %s about payment success: %s", pending["user_id"], exc)


async def notify_master(bot: Bot, master_telegram_id: int | None, booking_info: dict):
    """Send notification to master about new booking."""
    from .utils import mask_card_last4
    
    if not master_telegram_id:
        return

    client_telegram_id = booking_info.get("client_telegram_id")
    booking_id = booking_info.get("booking_id")
    inline_rows = []
    if client_telegram_id:
        inline_rows.append(
            [
                InlineKeyboardButton(
                    text="Написати клієнту",
                    url=f"tg://user?id={client_telegram_id}",
                )
            ]
        )
    if booking_id:
        inline_rows.append(
            [
                InlineKeyboardButton(
                    text="Скасувати запис",
                    callback_data=f"cancel_booking:{booking_id}",
                )
            ]
        )
    reply_markup = InlineKeyboardMarkup(inline_keyboard=inline_rows) if inline_rows else None

    date_obj = datetime.strptime(booking_info["booking_date"], "%Y-%m-%d")
    date_display = date_obj.strftime("%d.%m.%Y")
    message = (
        "Нове бронювання після оплати ✅\n\n"
        f"👤 <b>{booking_info['full_name']}</b>\n"
        f"📱 <b>{booking_info['phone_number']}</b>\n"
        f"💅 <b>{booking_info['service']}</b>\n"
        f"📅 <b>{date_display}</b>\n"
        f"🕒 <b>{booking_info['booking_time']}</b>\n"
        f"Статус оплати: <b>{booking_info.get('payment_status', 'paid')}</b>"
    )
    last4 = mask_card_last4(booking_info.get("card_number"))
    if last4:
        message += f"\n💳 Передоплату буде переказано на вашу картку •••• {last4}"

    try:
        await bot.send_message(master_telegram_id, message, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as exc:
        logger.warning("Failed to notify master %s about booking success: %s", master_telegram_id, exc)


async def notify_client_about_cancellation(bot: Bot, booking: dict):
    """Send cancellation message to client."""
    date_obj = datetime.strptime(booking["booking_date"], "%Y-%m-%d")
    date_display = date_obj.strftime("%d.%m.%Y")
    message = (
        "❌ Твій запис скасовано майстром.\n\n"
        f"📅 <b>{date_display}</b>\n"
        f"🕒 <b>{booking['booking_time']}</b>\n"
        f"💅 <b>{booking['service']}</b>\n\n"
        "Якщо хочеш, можеш записатися на інший час."
    )
    try:
        await bot.send_message(booking["user_id"], message, parse_mode="HTML")
    except Exception as exc:
        logger.warning("Failed to notify client %s about cancellation: %s", booking["user_id"], exc)
