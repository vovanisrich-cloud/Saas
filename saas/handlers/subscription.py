"""Master subscription handlers and payment flow."""

import logging
import time

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from ..config import SUBSCRIPTION_PRICE_UAH, SUBSCRIPTION_MONTHS
from ..payments.wayforpay import create_wayforpay_invoice, fetch_wayforpay_invoice_status
from database import BookingDatabase

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "master_subscribe")
async def start_subscription_payment(callback: types.CallbackQuery, state: FSMContext):
    """Create a subscription invoice for a master."""
    master_id = callback.from_user.id
    status = BookingDatabase.get_subscription_status(master_id)
    if status.get("plan") == "paid":
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="💳 Продовжити підписку", callback_data="master_subscribe")]]
        )
        paid_until = status.get("paid_until") or ""
        await callback.message.answer(
            f"✅ Підписка активна до {paid_until}. Ви можете продовжити її завчасно.",
            reply_markup=keyboard,
        )
        await callback.answer()
        return

    order_reference = f"sub_{master_id}_{int(time.time())}"
    try:
        invoice = await create_wayforpay_invoice(
            request_id=order_reference,
            full_name=callback.from_user.full_name or "Master",
            phone_number="",
            service="Subscription",
            booking_date="",
            booking_time="",
            master_card_number=None,
            amount=SUBSCRIPTION_PRICE_UAH,
            product_name=f"Підписка BookMe Beauty на {SUBSCRIPTION_MONTHS} місяць" if SUBSCRIPTION_MONTHS == 1 else f"Підписка BookMe Beauty на {SUBSCRIPTION_MONTHS} місяці",
        )
    except Exception:
        logger.exception("Failed to create subscription invoice for master %s", master_id)
        await callback.message.answer("Не вдалося створити платіжну сторінку. Спробуйте ще раз пізніше.")
        await callback.answer()
        return

    invoice_url = invoice.get("invoiceUrl")
    if not invoice_url:
        await callback.message.answer("Не вдалося отримати посилання на оплату. Спробуйте ще раз.")
        await callback.answer()
        return

    BookingDatabase.set_subscription_invoice(master_id, order_reference)
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="💳 Оплатити підписку", url=invoice_url)],
            [types.InlineKeyboardButton(text="🔄 Перевірити оплату", callback_data="check_subscription_payment")],
        ]
    )
    await callback.message.answer(
        "Підписка BookMe Beauty\n\n"
        f"💵 Вартість: {SUBSCRIPTION_PRICE_UAH} грн\n"
        f"📆 Термін: {SUBSCRIPTION_MONTHS} місяць",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "check_subscription_payment")
async def check_subscription_payment(callback: types.CallbackQuery, state: FSMContext):
    """Manually check the current subscription invoice status."""
    master_id = callback.from_user.id
    with BookingDatabase._connect() as conn:
        row = conn.cursor().execute(
            "SELECT subscription_invoice_id FROM masters WHERE telegram_id = ?",
            (master_id,),
        ).fetchone()
    invoice_id = row["subscription_invoice_id"] if row else None
    if not invoice_id:
        await callback.answer("Інвойс не знайдено. Натисніть 'Оформити підписку' знову.", show_alert=True)
        return

    try:
        status_payload = await fetch_wayforpay_invoice_status(invoice_id)
    except Exception:
        logger.exception("Failed to fetch subscription payment status for master %s", master_id)
        await callback.answer("Не вдалося перевірити оплату. Спробуйте ще через хвилину.", show_alert=True)
        return

    status = str(status_payload.get("transactionStatus") or "").lower()
    if status == "approved":
        BookingDatabase.activate_subscription(master_id, months=SUBSCRIPTION_MONTHS)
        paid_until = BookingDatabase.get_subscription_status(master_id).get("paid_until")
        await callback.message.answer(f"🎉 Підписку активовано до {paid_until}!")
        await callback.answer("Платіж підтверджено", show_alert=True)
        return

    await callback.answer("⏳ Оплата ще не надійшла. Спробуй через хвилину.", show_alert=True)


async def handle_subscription_webhook(bot, order_reference: str):
    """Handle subscription webhook updates from WayForPay."""
    master = BookingDatabase.get_master_by_subscription_invoice(order_reference)
    if not master:
        return

    master_id = master.get("telegram_id")
    BookingDatabase.activate_subscription(master_id, months=SUBSCRIPTION_MONTHS)
    paid_until = BookingDatabase.get_subscription_status(master_id).get("paid_until")
    try:
        await bot.send_message(master_id, f"🎉 Оплату отримано! Підписку активовано до {paid_until}. Дякуємо!")
    except Exception:
        logger.exception("Failed to send subscription activation message to master %s", master_id)
