"""Master registration and profile management handlers."""

import logging
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from ..config import PLATFORM_COMMISSION_PERCENT
from ..states import MasterOnboardingStates
from ..keyboards import (
    get_master_done_keyboard,
    get_master_menu_keyboard,
    get_master_confirmation_keyboard,
    build_master_confirmation_preview,
    MASTER_CARD_PROMPT,
)
from ..utils import safe_edit_text, normalize_card_number, is_valid_luhn, mask_card_last4
from database import BookingDatabase

router = Router()
logger = logging.getLogger(__name__)


async def ask_master_duration(message: types.Message, state: FSMContext):
    """Prompt for service duration."""
    await message.answer(
        "Скільки триває одна процедура (в хвилинах)? Наприклад: <b>60</b>",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML",
    )
    await state.set_state(MasterOnboardingStates.waiting_for_duration)


@router.callback_query(F.data == "role_master")
async def start_master_registration(callback: types.CallbackQuery, state: FSMContext):
    """Start master registration flow."""
    await safe_edit_text(
        callback.message,
        "Добре! Давай зареєструємо твій профіль майстра.\n\n"
        f"⚠️ Комісія платформи: {PLATFORM_COMMISSION_PERCENT}% від передоплати.\n"
        f"Майстер отримує {100 - PLATFORM_COMMISSION_PERCENT}% від суми.\n\n"
        "Напиши, будь ласка, своє ім'я 👤",
    )
    await state.set_state(MasterOnboardingStates.waiting_for_master_name)
    await callback.answer()


@router.message(MasterOnboardingStates.waiting_for_master_name)
async def process_master_name(message: types.Message, state: FSMContext):
    """Process master name input."""
    master_name = (message.text or "").strip()
    if not master_name:
        await message.answer("Напишіть, будь ласка, ім'я текстом.")
        return
    await state.update_data(master_name=master_name, master_services=[])
    await message.answer(
        "Додай послуги, які ти надаєш.\n\nФормат: <b>Назва — ціна</b>\nНаприклад: <b>Манікюр — 300 грн</b>\n\nМожеш надсилати по одній послузі за раз.\nКоли закінчиш — натисни кнопку <b>Готово</b> або напиши <b>готово</b> текстом.",
        reply_markup=get_master_done_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(MasterOnboardingStates.waiting_for_service_input)


@router.message(MasterOnboardingStates.waiting_for_service_input)
async def process_master_service_input(message: types.Message, state: FSMContext):
    """Process service input."""
    if message.text and message.text.strip().lower() in {"готово", "done", "закінчити", "завершити"}:
        data = await state.get_data()
        services = data.get("master_services", [])
        if not services:
            await message.answer("Додай хоча б одну послугу перед завершенням.")
            return
        await ask_master_duration(message, state)
        return

    service = (message.text or "").strip()
    if not service:
        await message.answer("Надішліть назву послуги текстом.")
        return

    data = await state.get_data()
    services = list(data.get("master_services", []))
    services.append(service)
    await state.update_data(master_services=services)
    await message.answer(f"Додано: {service}\nНадсилай наступну, натисни <b>Готово</b> або напиши <b>готово</b> ✅", parse_mode="HTML")


@router.callback_query(MasterOnboardingStates.waiting_for_service_input, F.data == "master_services_done")
async def finish_master_services(callback: types.CallbackQuery, state: FSMContext):
    """Finish service input."""
    data = await state.get_data()
    services = data.get("master_services", [])
    if not services:
        await callback.answer("Додай хоча б одну послугу перед завершенням.", show_alert=True)
        return
    await ask_master_duration(callback.message, state)
    await callback.answer()


@router.message(MasterOnboardingStates.waiting_for_duration)
async def process_master_duration(message: types.Message, state: FSMContext):
    """Process service duration input."""
    duration_text = (message.text or "").strip()
    if not duration_text.isdigit():
        await message.answer("Вкажи, будь ласка, тривалість числом у хвилинах. Наприклад: <b>60</b>", parse_mode="HTML")
        return
    duration_minutes = int(duration_text)
    if duration_minutes <= 0:
        await message.answer("Тривалість має бути більше 0.")
        return
    await state.update_data(duration_minutes=duration_minutes)
    await message.answer(
        "Напиши свій графік роботи текстом.\n\nНаприклад: <b>Пн–Пт: 10:00–18:00, Сб: 10:00–14:00</b>",
        parse_mode="HTML",
    )
    await state.set_state(MasterOnboardingStates.waiting_for_schedule)


@router.message(MasterOnboardingStates.waiting_for_schedule)
async def process_master_schedule(message: types.Message, state: FSMContext):
    """Process schedule input."""
    schedule_text = (message.text or "").strip()
    if not schedule_text:
        await message.answer("Напиши, будь ласка, графік роботи текстом.")
        return
    schedule_lines = [line.strip() for line in schedule_text.splitlines() if line.strip()]
    if not schedule_lines:
        schedule_lines = [schedule_text]
    await state.update_data(schedule=schedule_lines)
    await message.answer(MASTER_CARD_PROMPT)
    await state.set_state(MasterOnboardingStates.waiting_for_card)


@router.message(MasterOnboardingStates.waiting_for_card)
async def process_master_card(message: types.Message, state: FSMContext):
    """Process card number input."""
    card_number = normalize_card_number(message.text)
    if not card_number.isdigit() or not (13 <= len(card_number) <= 19):
        await message.answer(
            "Номер картки має містити лише 13–19 цифр. Пробіли та дефіси можна залишати — їх буде видалено. "
            "Спробуй ввести ще раз."
        )
        return
    if not is_valid_luhn(card_number):
        await message.answer(
            "Схоже, номер картки введено з помилкою (не проходить перевірку). Спробуй ввести ще раз."
        )
        return

    await state.update_data(card_number=card_number)
    data = await state.get_data()
    if data.get("updating_card_only"):
        profile = BookingDatabase.get_master_profile(message.from_user.id)
        if not profile:
            await message.answer("Профіль не знайдено. Спочатку зареєструйтеся.")
            await state.clear()
            return
        BookingDatabase.upsert_master_profile(
            master_telegram_id=message.from_user.id,
            master_name=profile.get("master_name") or "",
            services=profile.get("services") or [],
            schedule=profile.get("schedule") or [],
            greeting_text=profile.get("greeting_text"),
            duration_minutes=int(profile.get("duration_minutes") or 60),
            card_number=card_number,
        )
        last4 = card_number[-4:]
        await message.answer(
            f"Картку збережено ✅ •••• {last4}",
            reply_markup=get_master_menu_keyboard(),
        )
        await state.clear()
        return

    preview = build_master_confirmation_preview(data)
    await message.answer(preview, reply_markup=get_master_confirmation_keyboard(), parse_mode="HTML")
    await state.set_state(MasterOnboardingStates.confirmation)


@router.callback_query(MasterOnboardingStates.confirmation, F.data == "master_save")
async def save_master_profile(callback: types.CallbackQuery, state: FSMContext):
    """Save master profile."""
    data = await state.get_data()
    master_name = data.get("master_name", "")
    services = data.get("master_services", [])
    schedule = data.get("schedule", [])
    duration_minutes = int(data.get("duration_minutes") or 60)
    card_number = data.get("card_number")
    master_telegram_id = callback.from_user.id

    saved = BookingDatabase.upsert_master_profile(
        master_telegram_id=master_telegram_id,
        master_name=master_name,
        services=services,
        schedule=schedule,
        greeting_text=None,
        duration_minutes=duration_minutes,
        card_number=card_number,
    )
    if not saved:
        await safe_edit_text(callback.message, "Не вдалося зберегти профіль. Спробуйте ще раз.", reply_markup=None)
        await callback.answer()
        await state.clear()
        return

    await safe_edit_text(callback.message, "✅ Профіль майстра збережено!", reply_markup=get_master_menu_keyboard())
    await callback.answer("Збережено")
    await state.clear()


@router.callback_query(MasterOnboardingStates.confirmation, F.data == "master_cancel")
async def cancel_master_registration(callback: types.CallbackQuery, state: FSMContext):
    """Cancel master registration."""
    from ..keyboards import get_role_selection_keyboard
    await safe_edit_text(callback.message, "Реєстрацію скасовано. Почнемо заново?", reply_markup=get_role_selection_keyboard())
    await callback.answer()
    await state.clear()


@router.callback_query(F.data == "master_set_card")
async def start_master_card_update(callback: types.CallbackQuery, state: FSMContext):
    """Start card update flow."""
    profile = BookingDatabase.get_master_profile(callback.from_user.id)
    if not profile:
        await callback.answer("Профіль не знайдено. Спочатку зареєструйтеся.", show_alert=True)
        return
    await state.update_data(updating_card_only=True)
    await callback.message.answer(MASTER_CARD_PROMPT)
    await state.set_state(MasterOnboardingStates.waiting_for_card)
    await callback.answer()


@router.callback_query(F.data == "master_get_link")
async def send_master_link(callback: types.CallbackQuery, state: FSMContext):
    """Send master's booking link."""
    data = await state.get_data()
    profile = data.get("master_profile")
    if not profile:
        profile = BookingDatabase.get_master_profile(callback.from_user.id)
    if not profile:
        await callback.answer("Профіль не знайдено. Спочатку зареєструйтеся.", show_alert=True)
        return

    link = f"https://t.me/bookme_beauty_bot?start=master_{callback.from_user.id}"
    await safe_edit_text(
        callback.message,
        f"Ось твоє посилання для клієнтів:\n\n{link}\n\n"
        "Надішли його клієнтам — вони потраплять безпосередньо до твого запису.",
        reply_markup=get_master_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "master_view_profile")
async def view_master_profile(callback: types.CallbackQuery, state: FSMContext):
    """Show master profile."""
    data = await state.get_data()
    profile = data.get("master_profile")
    if not profile:
        profile = BookingDatabase.get_master_profile(callback.from_user.id)
    if not profile:
        await callback.answer("Профіль не знайдено.", show_alert=True)
        return

    services = profile.get("services") or []
    schedule = profile.get("schedule") or []
    duration_minutes = profile.get("duration_minutes") or 60
    last4 = mask_card_last4(profile.get("card_number"))
    card_line = f"💳 Картка: {last4} (останні 4 цифри)\n\n" if last4 else "💳 Картка: не вказана\n\n"
    text = (
        f"👤 <b>{profile.get('master_name', 'Майстер')}</b>\n"
        f"⏱ Тривалість: <b>{duration_minutes} хв</b>\n"
        f"{card_line}"
        f"💅 Послуги:\n"
        + "\n".join(f"• {s}" for s in services)
        + f"\n\n📅 Графік:\n"
        + "\n".join(f"• {s}" for s in schedule)
    )
    await safe_edit_text(callback.message, text, reply_markup=get_master_menu_keyboard(), parse_mode="HTML")
    await callback.answer()
