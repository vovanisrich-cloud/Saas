"""Payment handling and test payment command."""

import logging
from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext

from ..utils import safe_edit_text, _can_use_test_pay
from ..keyboards import get_role_selection_keyboard
from ..payments.webhook import process_payment_status, complete_booking_after_payment
from ..notifications import notify_client_about_cancellation
from database import BookingDatabase

router = Router()
logger = logging.getLogger(__name__)

# Global bot reference (will be set in main.py)
_bot = None


def set_bot(bot):
    """Set global bot instance for handlers."""
    global _bot
    _bot = bot


@router.callback_query(F.data.startswith("cancel_booking:"))
async def cancel_booking_handler(callback: types.CallbackQuery, state: FSMContext):
    """Handle booking cancellation by master."""
    try:
        booking_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Некоректний ідентифікатор запису.", show_alert=True)
        return

    deleted = BookingDatabase.cancel_booking(booking_id, callback.from_user.id)
    if not deleted:
        await callback.answer("Запис не знайдено або він належить іншому майстру.", show_alert=True)
        return

    await safe_edit_text(callback.message, "Запис скасовано ✅", reply_markup=None)
    await callback.answer()
    if _bot:
        await notify_client_about_cancellation(_bot, deleted)


@router.message(Command("test_pay"))
async def cmd_test_pay(message: types.Message, command: CommandObject):
    """Emulate successful payment without WayForPay (admin only)."""
    if not _can_use_test_pay(message.from_user.id):
        await message.answer("Команда не найдена")
        return

    lookup = (command.args or "").strip() or None
    pending = BookingDatabase.get_pending_payment_for_test(message.from_user.id, lookup)
    if pending is None and not lookup:
        pending = BookingDatabase.get_pending_payment_for_test(None, None)
    if not pending:
        if lookup:
            await message.answer(f"Бронь {lookup} не найдена.")
        else:
            await message.answer("Нет активной PENDING-брони. Сначала выбери слот и дойди до оплаты.")
        return

    if pending.get("status") == "paid":
        await message.answer(
            f"Бронь {pending.get('booking_id') or pending.get('id')} уже в статусе PAID."
        )
        return

    if not _bot:
        await message.answer("Bot instance not available")
        return

    booking_id = await complete_booking_after_payment(_bot, pending, transfer_payout=False)
    if booking_id is None:
        await message.answer(
            "Не удалось подтвердить бронь (слот занят или запись уже в конфликте)."
        )
        return

    await message.answer(
        f"✅ [TEST] Бронь {booking_id} переведена в PAID. Уведомления отправлены."
    )


@router.callback_query(F.data.startswith("check_payment:"))
async def check_payment(callback: types.CallbackQuery):
    """Check payment status and update booking."""
    if not _bot:
        await callback.answer("Bot instance not available")
        return

    order_reference = callback.data.split(":", 1)[1]
    result = await process_payment_status(_bot, order_reference, source="manual_check")

    if not result.get("pending"):
        await callback.answer("Платіж не знайдено або він уже оброблений", show_alert=True)
        return

    status = (result.get("status") or "").lower()
    pending = result["pending"]

    if status == "approved":
        if result.get("ok"):
            await safe_edit_text(callback.message, "Оплату отримано, запис підтверджено ✅", reply_markup=None)
            await callback.answer("Оплату підтверджено", show_alert=True)
            return

        await safe_edit_text(
            callback.message,
            "Оплату отримано, але слот уже зайнятий іншим записом.\n\nНапишіть адміністратору, щоб вирішити це вручну.",
            reply_markup=None,
        )
        await callback.message.answer("Спробуй ще раз 🌸", reply_markup=get_role_selection_keyboard())
        await callback.answer("Потрібна ручна перевірка", show_alert=True)
        return

    if status in {"declined", "expired"}:
        await safe_edit_text(
            callback.message,
            "Оплата не пройшла або строк рахунку закінчився.\n\nСпробуйте записатися ще раз.",
            reply_markup=None,
        )
        await callback.message.answer("Спробуй ще раз 🌸", reply_markup=get_role_selection_keyboard())
        await callback.answer("Платіж неуспішний", show_alert=True)
        return

    await callback.answer(
        f"Статус платежу: {status or pending['status']}. Спробуйте ще раз через хвилину.",
        show_alert=True,
    )
