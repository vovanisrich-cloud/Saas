"""Client booking flow handlers."""

import logging
from datetime import datetime, timedelta, timezone
from aiogram import Router, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from ..states import BeautyBookingStates, ClientRegistrationStates
from ..keyboards import (
    get_phone_keyboard,
    get_services_keyboard,
    get_date_calendar_keyboard,
    get_time_keyboard,
    get_role_selection_keyboard,
    get_payment_keyboard,
)
from ..config import DEPOSIT_AMOUNT_UAH, RESERVATION_TTL_MINUTES, PAYMENT_PROVIDER
from ..utils import safe_edit_text, parse_start_payload
from ..payments.wayforpay import create_wayforpay_invoice
from database import BookingDatabase

router = Router()
logger = logging.getLogger(__name__)


async def send_start_menu(message: types.Message, text: str):
    """Send start menu message."""
    await safe_edit_text(message, text)
    await message.answer("Спробуй ще раз 🌸", reply_markup=get_role_selection_keyboard())


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    """Handle /start command."""
    payload = parse_start_payload(message.text)
    if payload and payload.startswith("master_"):
        master_id_text = payload.split("_", 1)[1]
        try:
            master_id = int(master_id_text)
        except ValueError:
            await message.answer(
                "Некоректне посилання на майстра. Спробуйте ще раз або зверніться до адміністратора."
            )
            await state.clear()
            return

        profile = BookingDatabase.get_master_profile_by_id(master_id)
        if not profile:
            await message.answer("Майстра не знайдено. Спробуйте інше посилання.")
            await state.clear()
            return

        await state.clear()
        await state.update_data(
            entry_mode="master",
            master_telegram_id=master_id,
            master_profile=profile,
        )
        await message.answer(
            (await get_master_welcome_text(profile)),
            reply_markup=get_services_keyboard(profile.get("services"), prefix="master_service"),
        )
        await state.set_state(BeautyBookingStates.waiting_for_service)
        return

    active_id = BookingDatabase.get_active_profile_id(message.from_user.id)
    profile = BookingDatabase.get_master_profile_by_id(active_id) if active_id else None
    if profile and profile.get("owner_telegram_id") == message.from_user.id and profile.get("is_active"):
        await state.clear()
        await state.update_data(
            entry_mode="master",
            master_telegram_id=profile["id"],
            master_profile=profile,
        )
        from ..keyboards import get_master_menu_keyboard
        await message.answer(
            f"🌸 Вітаю, майстре! Ти працюєш як: {profile['master_name']}\n\nОсь твоє меню:",
            reply_markup=get_master_menu_keyboard(),
        )
        return

    profiles = BookingDatabase.get_master_profiles_by_owner(message.from_user.id)
    role_keyboard = get_role_selection_keyboard(include_profiles=bool(profiles))
    await message.answer(
        "🌸 Привіт! Ласкаво просимо!\n\nХто ви?",
        reply_markup=role_keyboard,
    )
    await state.clear()


async def get_master_welcome_text(profile: dict) -> str:
    """Get master welcome text."""
    from ..keyboards import build_master_welcome_text
    return build_master_welcome_text(profile)


@router.callback_query(F.data == "role_client")
async def start_client_booking(callback: types.CallbackQuery, state: FSMContext):
    """Start standalone client self-registration flow."""
    await state.clear()
    active = BookingDatabase.get_client_profile(callback.from_user.id)
    if active:
        await safe_edit_text(
            callback.message,
            f"✅ Ти вже зареєстрований як клієнт: {active['full_name']}, {active['phone_number']}.\n\n"
            "Щоб записатися до майстра, перейди за його персональним "
            "посиланням (t.me/<bot_username>?start=master_...).",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="✏️ Оновити дані", callback_data="client_registration_edit")],
                [types.InlineKeyboardButton(text="❌ Видалити профіль", callback_data="client_delete_confirm")],
            ]),
        )
        await callback.answer()
        return

    forgotten = BookingDatabase.get_forgotten_client_profile(callback.from_user.id)
    if forgotten:
        await safe_edit_text(
            callback.message,
            f"Раніше в тебе були збережені дані — {forgotten['full_name']}, "
            f"{forgotten['phone_number']}.\n\nВідновити їх чи ввести нові?",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="♻️ Відновити", callback_data="restore_client_profile")],
                [types.InlineKeyboardButton(text="Ввести нові", callback_data="client_registration_edit")],
            ]),
        )
        await callback.answer()
        return

    await safe_edit_text(callback.message, "Добре! Напиши, будь ласка, своє ім'я 👤", reply_markup=None)
    await state.set_state(ClientRegistrationStates.waiting_for_name)
    await callback.answer()


@router.callback_query(F.data == "client_delete_confirm")
async def confirm_delete_client(callback: types.CallbackQuery, state: FSMContext):
    """Ask for confirmation before permanently deleting client data."""
    await safe_edit_text(
        callback.message,
        "⚠️ Видалити твої збережені дані (ім'я, телефон) НАЗАВЖДИ?\n\n"
        "Історія попередніх бронювань залишиться в базі, але автопідстановка "
        "даних більше працювати не буде. Це незворотньо.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Так, видалити", callback_data="client_delete_do")],
            [types.InlineKeyboardButton(text="Скасувати", callback_data="role_client")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "client_delete_do")
async def do_delete_client(callback: types.CallbackQuery, state: FSMContext):
    """Permanently delete the current client profile."""
    BookingDatabase.delete_client_profile(callback.from_user.id)
    await state.clear()
    await safe_edit_text(
        callback.message,
        "🗑 Дані видалено.",
        reply_markup=get_role_selection_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "client_registration_edit")
async def client_registration_edit(callback: types.CallbackQuery, state: FSMContext):
    """Restart client self-registration to overwrite saved data."""
    await safe_edit_text(callback.message, "Напиши, будь ласка, своє ім'я 👤", reply_markup=None)
    await state.set_state(ClientRegistrationStates.waiting_for_name)
    await callback.answer()


@router.message(ClientRegistrationStates.waiting_for_name, ~F.text.startswith("/"))
async def process_client_registration_name(message: types.Message, state: FSMContext):
    """Collect name for standalone client registration."""
    full_name = (message.text or "").strip()
    if not full_name:
        await message.answer("Напишіть, будь ласка, ім'я та прізвище текстом.")
        return
    await state.update_data(full_name=full_name)
    await message.answer("Тепер поділись номером телефону 📱", reply_markup=get_phone_keyboard())
    await state.set_state(ClientRegistrationStates.waiting_for_phone)


@router.message(ClientRegistrationStates.waiting_for_phone, F.contact)
async def process_client_registration_phone(message: types.Message, state: FSMContext):
    """Finish standalone client registration and save the profile."""
    data = await state.get_data()
    full_name = data.get("full_name", "")
    phone_number = message.contact.phone_number
    BookingDatabase.upsert_client_profile(message.from_user.id, full_name, phone_number)
    await state.clear()
    await message.answer(
        f"✅ Готово! Зберегли тебе як {full_name}, {phone_number}.\n\n"
        "Щоб записатися до майстра, перейди за його персональним "
        "посиланням (t.me/<bot_username>?start=master_...).\n"
        "Дані підставляться автоматично, вводити їх повторно не треба.",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@router.message(ClientRegistrationStates.waiting_for_phone, ~F.text.startswith("/"))
async def client_registration_phone_fallback(message: types.Message):
    """Handle non-contact messages during standalone registration phone step."""
    await message.answer("Надішліть номер телефону через кнопку нижче 📱", reply_markup=get_phone_keyboard())


@router.message(BeautyBookingStates.waiting_for_name, ~F.text.startswith("/"))
async def process_name(message: types.Message, state: FSMContext):
    """Process client name."""
    full_name = (message.text or "").strip()
    if not full_name:
        await message.answer("Напишіть, будь ласка, ім'я та прізвище текстом.")
        return

    await state.update_data(full_name=full_name)
    await message.answer("Тепер поділись номером телефону 📱", reply_markup=get_phone_keyboard())
    await state.set_state(BeautyBookingStates.waiting_for_phone)


@router.message(BeautyBookingStates.waiting_for_phone, F.contact)
async def process_phone(message: types.Message, state: FSMContext):
    """Process phone number."""
    await state.update_data(phone_number=message.contact.phone_number)
    await message.answer("Оберіть дату запису 📅", reply_markup=get_date_calendar_keyboard())
    await state.set_state(BeautyBookingStates.waiting_for_date)


@router.message(BeautyBookingStates.waiting_for_phone, ~F.text.startswith("/"))
async def process_phone_fallback(message: types.Message):
    """Handle non-contact messages during phone step."""
    await message.answer("Надішліть номер телефону через кнопку нижче 📱", reply_markup=get_phone_keyboard())


@router.callback_query(BeautyBookingStates.waiting_for_service, F.data.startswith("master_service:"))
async def process_service(callback: types.CallbackQuery, state: FSMContext):
    """Process service selection."""
    user_data = await state.get_data()
    profile = user_data.get("master_profile") or {}
    master_services = profile.get("services") or []
    service_name = None
    if callback.data and callback.data.startswith("master_service:"):
        try:
            service_index = int(callback.data.split(":", 1)[1])
            service_name = master_services[service_index]
        except (ValueError, IndexError, TypeError):
            service_name = None
    if not service_name:
        await callback.answer("Невідома послуга", show_alert=True)
        return

    await state.update_data(service=service_name)
    client_profile = BookingDatabase.get_client_profile(callback.from_user.id)
    if client_profile:
        await state.update_data(
            full_name=client_profile["full_name"],
            phone_number=client_profile["phone_number"],
        )
        await safe_edit_text(
            callback.message,
            "Використати збережені дані — "
            f"{client_profile['full_name']}, {client_profile['phone_number']}?",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text="Так, це я", callback_data="use_saved_client_profile")],
                    [types.InlineKeyboardButton(text="Ні, ввести заново", callback_data="edit_client_profile")],
                    [types.InlineKeyboardButton(text="🗑 Забути мої дані", callback_data="forget_client_profile")],
                ]
            ),
        )
        await callback.answer()
        return

    forgotten = BookingDatabase.get_forgotten_client_profile(callback.from_user.id)
    if forgotten:
        await safe_edit_text(
            callback.message,
            "Раніше в тебе були збережені дані — "
            f"{forgotten['full_name']}, {forgotten['phone_number']}.\n\n"
            "Відновити їх чи ввести нові?",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text="♻️ Відновити", callback_data="restore_client_profile")],
                    [types.InlineKeyboardButton(text="Ввести нові", callback_data="edit_client_profile")],
                ]
            ),
        )
        await callback.answer()
        return

    await safe_edit_text(
        callback.message,
        f"Послуга обрана: <b>{service_name}</b>\n\nТепер напишіть своє ім'я.",
        reply_markup=None,
        parse_mode="HTML",
    )
    await callback.answer()
    await state.set_state(BeautyBookingStates.waiting_for_name)


@router.callback_query(F.data == "use_saved_client_profile")
async def use_saved_client_profile(callback: types.CallbackQuery, state: FSMContext):
    """Use the saved client profile for this booking."""
    await safe_edit_text(
        callback.message,
        "Оберіть дату запису 📅",
        reply_markup=get_date_calendar_keyboard(),
    )
    await state.set_state(BeautyBookingStates.waiting_for_date)
    await callback.answer()


@router.callback_query(F.data == "edit_client_profile")
async def edit_client_profile(callback: types.CallbackQuery, state: FSMContext):
    """Restart client details input for this booking."""
    await safe_edit_text(callback.message, "Тепер напишіть своє ім'я.", reply_markup=None)
    await state.set_state(BeautyBookingStates.waiting_for_name)
    await callback.answer()


@router.callback_query(F.data == "forget_client_profile")
async def forget_client_profile_handler(callback: types.CallbackQuery, state: FSMContext):
    """Forget the saved client profile without deleting its data."""
    BookingDatabase.forget_client_profile(callback.from_user.id)
    await safe_edit_text(
        callback.message,
        "Дані забуті 🗑 Наступного разу введеш їх заново — "
        "або відновиш ті самі, якщо не встигнеш забути номер напам'ять 😉\n\n"
        "Тепер напишіть своє ім'я.",
        reply_markup=None,
    )
    await state.set_state(BeautyBookingStates.waiting_for_name)
    await callback.answer()


@router.callback_query(F.data == "restore_client_profile")
async def restore_client_profile_handler(callback: types.CallbackQuery, state: FSMContext):
    """Restore and use the forgotten client profile."""
    BookingDatabase.restore_client_profile(callback.from_user.id)
    profile = BookingDatabase.get_client_profile(callback.from_user.id)
    if not profile:
        await callback.answer("Не вдалося відновити дані.", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("service"):
        await state.clear()
        await safe_edit_text(
            callback.message,
            f"✅ Готово! Відновили тебе як {profile['full_name']}, {profile['phone_number']}.\n\n"
            "Щоб записатися до майстра, перейди за його персональним "
            "посиланням (t.me/<bot_username>?start=master_...).\n"
            "Дані підставляться автоматично, вводити їх повторно не треба.",
            reply_markup=None,
        )
        await callback.answer()
        return
    await state.update_data(
        full_name=profile["full_name"],
        phone_number=profile["phone_number"],
    )
    await safe_edit_text(
        callback.message,
        "Оберіть дату запису 📅",
        reply_markup=get_date_calendar_keyboard(),
    )
    await state.set_state(BeautyBookingStates.waiting_for_date)
    await callback.answer()


@router.callback_query(BeautyBookingStates.waiting_for_date, F.data.startswith("date_"))
async def process_date(callback: types.CallbackQuery, state: FSMContext):
    """Process date selection."""
    booking_date = callback.data.replace("date_", "")
    await state.update_data(booking_date=booking_date)
    user_data = await state.get_data()
    master_telegram_id = user_data.get("master_telegram_id")

    time_keyboard = get_time_keyboard(booking_date, master_telegram_id=master_telegram_id)
    if not time_keyboard:
        await safe_edit_text(
            callback.message,
            f"На дату {booking_date} немає вільних слотів.\n\nВиберіть іншу дату 📅",
            reply_markup=get_date_calendar_keyboard(),
        )
        await callback.answer()
        return

    date_obj = datetime.strptime(booking_date, "%Y-%m-%d")
    date_display = date_obj.strftime("%d.%m.%Y")
    await safe_edit_text(
        callback.message,
        f"Дата вибрана: <b>{date_display}</b>\n\nТепер виберіть час запису 🕒",
        reply_markup=time_keyboard,
        parse_mode="HTML",
    )
    await callback.answer()
    await state.set_state(BeautyBookingStates.waiting_for_time)


@router.callback_query(BeautyBookingStates.waiting_for_time, F.data.startswith("time_"))
async def process_time(callback: types.CallbackQuery, state: FSMContext):
    """Process time slot selection and create payment invoice."""
    booking_time = callback.data.replace("time_", "")
    user_data = await state.get_data()
    master_telegram_id = user_data.get("master_telegram_id")
    booking_date = user_data.get("booking_date")

    if not booking_date:
        await send_start_menu(callback.message, "Сесія запису втрачена. Почніть заново.")
        await callback.answer()
        await state.clear()
        return

    if not BookingDatabase.is_slot_available(booking_date, booking_time, master_telegram_id=master_telegram_id):
        await safe_edit_text(
            callback.message,
            "Цей слот уже зайнятий.\n\nОберіть інший час 🕒",
            reply_markup=get_time_keyboard(booking_date, master_telegram_id=master_telegram_id),
        )
        await callback.answer()
        return

    date_obj = datetime.strptime(booking_date, "%Y-%m-%d")
    date_display = date_obj.strftime("%d.%m.%Y")
    reservation_expires_at = (datetime.now(timezone.utc) + timedelta(minutes=RESERVATION_TTL_MINUTES)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    request_id = BookingDatabase.reserve_pending_booking(
        user_id=callback.from_user.id,
        full_name=user_data["full_name"],
        master_telegram_id=master_telegram_id or 0,
        phone_number=user_data["phone_number"],
        service=user_data["service"],
        booking_date=booking_date,
        booking_time=booking_time,
        amount=DEPOSIT_AMOUNT_UAH,
        provider=PAYMENT_PROVIDER,
        expires_at=reservation_expires_at,
    )

    if not request_id:
        await safe_edit_text(
            callback.message,
            "Вибачте, цей слот щойно зайняли.\n\nОберіть інший час 🕒",
            reply_markup=get_time_keyboard(booking_date, master_telegram_id=master_telegram_id),
        )
        await callback.answer()
        return

    try:
        pending = BookingDatabase.get_pending_payment_by_request(request_id)
        master_card_number = (pending.get("card_number") or "").strip() if pending else ""
        if not master_card_number:
            logger.warning(
                "Master %s has no card_number, using global merchant account",
                master_telegram_id,
            )
        invoice = await create_wayforpay_invoice(
            request_id=request_id,
            full_name=user_data["full_name"],
            phone_number=user_data["phone_number"],
            service=user_data["service"],
            booking_date=booking_date,
            booking_time=booking_time,
            master_card_number=master_card_number or None,
        )
    except Exception as exc:
        logger.exception("Failed to create WayForPay invoice for request %s", request_id)
        BookingDatabase.update_pending_status_by_request(request_id, "failed")
        await safe_edit_text(
            callback.message,
            "Не вдалося створити платіжну сторінку. Спробуйте ще раз трохи пізніше.",
            reply_markup=None,
        )
        await callback.message.answer("Спробуй ще раз 🌸", reply_markup=get_role_selection_keyboard())
        await callback.answer()
        return

    order_reference = str(request_id)
    invoice_url = invoice.get("invoiceUrl")
    if not invoice_url:
        logger.error("WayForPay response missing invoiceUrl: %s", invoice)
        BookingDatabase.update_pending_status_by_request(request_id, "failed")
        await safe_edit_text(
            callback.message,
            "Платіжну сторінку не вдалося отримати. Спробуйте ще раз.",
            reply_markup=None,
        )
        await callback.message.answer("Спробуй ще раз 🌸", reply_markup=get_role_selection_keyboard())
        await callback.answer()
        return

    attached = BookingDatabase.attach_payment_invoice(
        request_id=request_id,
        invoice_id=order_reference,
        page_url=invoice_url,
        expires_at=reservation_expires_at,
    )
    if not attached:
        logger.error("Could not attach invoice %s to request %s", order_reference, request_id)
        BookingDatabase.update_pending_status_by_request(request_id, "failed")
        await safe_edit_text(
            callback.message,
            "Не вдалося зберегти платіжний запит. Спробуйте ще раз.",
            reply_markup=None,
        )
        await callback.message.answer("Спробуй ще раз 🌸", reply_markup=get_role_selection_keyboard())
        await callback.answer()
        return

    payment_text = (
        "Для підтвердження запису внесіть передоплату 200 грн\n\n"
        f"📅 <b>{date_display}</b>\n"
        f"🕒 <b>{booking_time}</b>\n"
        f"💅 <b>{user_data['service']}</b>"
    )
    await safe_edit_text(
        callback.message,
        payment_text,
        reply_markup=get_payment_keyboard(invoice_url, order_reference),
        parse_mode="HTML",
    )
    await callback.answer()
    await state.clear()


@router.message(F.contact)
async def wrong_contact_state(message: types.Message):
    """Handle contact in wrong state."""
    await message.answer("Контакт потрібно надсилати під час кроку з номером телефону.", reply_markup=get_role_selection_keyboard())
