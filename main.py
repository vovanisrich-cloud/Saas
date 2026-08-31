import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import ClientSession, web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from dotenv import load_dotenv

from database import BookingDatabase


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WAYFORPAY_MERCHANT_ACCOUNT = os.getenv("WAYFORPAY_MERCHANT_ACCOUNT", "test_merch_n1").strip()
WAYFORPAY_SECRET_KEY = os.getenv("WAYFORPAY_SECRET_KEY", "").strip()
WAYFORPAY_DOMAIN_NAME = os.getenv("WAYFORPAY_DOMAIN_NAME", "localhost").strip()
PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "wayforpay-webhook-secret").strip()
WAYFORPAY_SERVICE_URL = os.getenv("WAYFORPAY_SERVICE_URL", "").strip()
if not WAYFORPAY_SERVICE_URL:
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway_domain:
        WAYFORPAY_SERVICE_URL = f"https://{railway_domain}/payments/wayforpay/{PAYMENT_WEBHOOK_SECRET}"
APP_HOST = os.getenv("APP_HOST", "0.0.0.0").strip()
APP_PORT = int(os.getenv("APP_PORT", "8080").strip())
PAYMENT_PROVIDER = "wayforpay"

DEPOSIT_AMOUNT_UAH = 200
RESERVATION_TTL_MINUTES = int(os.getenv("RESERVATION_TTL_MINUTES", "30"))
WAYFORPAY_API_URL = "https://api.wayforpay.com/api"
WAYFORPAY_DEBUG = os.getenv("WAYFORPAY_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Add it to D:/MicroSAAS/.env")
if not WAYFORPAY_SECRET_KEY:
    raise RuntimeError("WAYFORPAY_SECRET_KEY is not set. Add it to D:/MicroSAAS/.env")
if WAYFORPAY_DOMAIN_NAME.lower() == "localhost":
    logger.warning(
        "WAYFORPAY_DOMAIN_NAME is set to localhost. WayForPay usually expects the merchant domain configured in your store."
    )

BookingDatabase.init_db()


class BeautyBookingStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_service = State()
    waiting_for_date = State()
    waiting_for_time = State()


class MasterOnboardingStates(StatesGroup):
    waiting_for_master_name = State()
    waiting_for_service_input = State()
    waiting_for_duration = State()
    waiting_for_schedule = State()
    confirmation = State()


async def safe_edit_text(message: types.Message, text: str, **kwargs):
    """Edit message only if text or reply markup actually changed."""
    reply_markup = kwargs.get("reply_markup")
    current_text = message.text or ""
    if current_text == text and message.reply_markup == reply_markup:
        return
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


storage = MemoryStorage()
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=storage)


def get_phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Надіслати номер телефону", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_services_keyboard(services: list[str], prefix: str = "service") -> InlineKeyboardMarkup:
    keyboard = []
    for index, service_name in enumerate(services):
        keyboard.append([InlineKeyboardButton(text=service_name, callback_data=f"{prefix}:{index}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def format_master_schedule(schedule: list[str] | None) -> str:
    if not schedule:
        return "Графік: за домовленістю"
    return "Графік:\n" + "\n".join(f"• {item}" for item in schedule)


def build_master_welcome_text(profile: dict) -> str:
    greeting = profile.get("greeting_text") or f"Привіт! Ви записуєтесь до майстра {profile.get('master_name', 'майстра')}."
    services = profile.get("services") or []
    services_text = "\n".join(f"• {item}" for item in services) if services else "• Послуги ще не додані"
    schedule_text = format_master_schedule(profile.get("schedule"))
    return f"{greeting}\n\nПослуги:\n{services_text}\n\n{schedule_text}"


def get_date_calendar_keyboard() -> InlineKeyboardMarkup:
    days_uk = {
        "Mon": "Пн",
        "Tue": "Вт",
        "Wed": "Ср",
        "Thu": "Чт",
        "Fri": "Пт",
        "Sat": "Сб",
        "Sun": "Нд",
    }
    keyboard_buttons = []
    for i in range(7):
        current_date = datetime.now() + timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")
        day_name_uk = days_uk.get(current_date.strftime("%a"), current_date.strftime("%a"))
        date_display = f"{day_name_uk}, {current_date.strftime('%d.%m')}"
        keyboard_buttons.append([InlineKeyboardButton(text=date_display, callback_data=f"date_{date_str}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)


def get_time_keyboard(booking_date: str, master_telegram_id: int | None = None):
    available_times = BookingDatabase.get_available_times(booking_date, master_telegram_id=master_telegram_id)
    if not available_times:
        return None

    keyboard_buttons = []
    for time_slot in available_times:
        keyboard_buttons.append([InlineKeyboardButton(text=f"🕒 {time_slot}", callback_data=f"time_{time_slot}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)


def get_role_selection_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Я майстер, хочу зареєструватися", callback_data="role_master")],
            [InlineKeyboardButton(text="Я клієнт, хочу записатися", callback_data="role_client")],
        ]
    )


def get_master_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Готово", callback_data="master_services_done")]]
    )


def get_master_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отримати моє посилання", callback_data="master_get_link")],
            [InlineKeyboardButton(text="Мій профіль", callback_data="master_view_profile")],
        ]
    )


def get_master_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Зберегти", callback_data="master_save")],
            [InlineKeyboardButton(text="Скасувати", callback_data="master_cancel")],
        ]
    )


async def ask_master_duration(message: types.Message, state: FSMContext):
    await message.answer(
        "Скільки триває одна процедура (в хвилинах)? Наприклад: <b>60</b>",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML",
    )
    await state.set_state(MasterOnboardingStates.waiting_for_duration)


def get_payment_keyboard(page_url: str, order_reference: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Оплатити {DEPOSIT_AMOUNT_UAH} грн", url=page_url)],
            [InlineKeyboardButton(text="Перевірити оплату", callback_data=f"check_payment:{order_reference}")],
        ]
    )


def split_full_name(full_name: str) -> tuple[str, str]:
    parts = [part for part in full_name.split() if part]
    if not parts:
        return "Клієнт", "Клієнт"
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


def parse_start_payload(text: str | None) -> str | None:
    if not text:
        return None
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip()


def format_wayforpay_amount(value: int | float | Decimal | str) -> str:
    """Return a stable money string for WayForPay signatures and payloads."""
    if isinstance(value, str):
        normalized = value.strip().replace(",", ".")
        decimal_value = Decimal(normalized)
    else:
        decimal_value = Decimal(str(value))

    return format(decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def build_wayforpay_signature(
    merchant_account: str,
    merchant_domain_name: str,
    order_reference: str,
    order_date: int,
    amount: str,
    currency: str,
    product_names: list[str],
    product_counts: list[str],
    product_prices: list[str],
) -> str:
    base_string = ";".join(
        [
            merchant_account,
            merchant_domain_name,
            order_reference,
            str(order_date),
            amount,
            currency,
            *product_names,
            *product_counts,
            *product_prices,
        ]
    )
    return hmac.new(WAYFORPAY_SECRET_KEY.encode("utf-8"), base_string.encode("utf-8"), hashlib.md5).hexdigest()


def build_wayforpay_status_signature(order_reference: str, status: str, timestamp: int) -> str:
    base_string = ";".join([order_reference, status, str(timestamp)])
    return hmac.new(WAYFORPAY_SECRET_KEY.encode("utf-8"), base_string.encode("utf-8"), hashlib.md5).hexdigest()


def verify_wayforpay_signature(payload: dict) -> bool:
    signature = payload.get("merchantSignature") or payload.get("signature")
    if not signature:
        return False

    base_string = ";".join(
        [
            WAYFORPAY_MERCHANT_ACCOUNT,
            str(payload.get("orderReference", "")),
            str(payload.get("amount", "")),
            str(payload.get("currency", "")),
            str(payload.get("authCode", "")),
            str(payload.get("cardPan", "")),
            str(payload.get("transactionStatus", "")),
            str(payload.get("reasonCode", "")),
        ]
    )
    expected = hmac.new(WAYFORPAY_SECRET_KEY.encode("utf-8"), base_string.encode("utf-8"), hashlib.md5).hexdigest()
    return signature == expected


async def send_start_menu(message: types.Message, text: str):
    await safe_edit_text(message, text)
    await message.answer("Спробуй ще раз 🌸", reply_markup=get_role_selection_keyboard())


async def create_wayforpay_invoice(
    request_id: str,
    full_name: str,
    phone_number: str,
    service: str,
    booking_date: str,
    booking_time: str,
) -> dict:
    order_date = int(time.time())
    amount = format_wayforpay_amount(DEPOSIT_AMOUNT_UAH)
    currency = "UAH"
    product_names = ["Booking deposit"]
    product_counts = ["1"]
    product_prices = [format_wayforpay_amount(DEPOSIT_AMOUNT_UAH)]
    merchant_signature = build_wayforpay_signature(
        WAYFORPAY_MERCHANT_ACCOUNT,
        WAYFORPAY_DOMAIN_NAME,
        request_id,
        order_date,
        amount,
        currency,
        product_names,
        product_counts,
        product_prices,
    )
    if WAYFORPAY_DEBUG:
        logger.debug(
            "WayForPay CREATE_INVOICE debug: signature=%s payload_fields=%s",
            merchant_signature,
            {
                "merchantAccount": WAYFORPAY_MERCHANT_ACCOUNT,
                "merchantDomainName": WAYFORPAY_DOMAIN_NAME,
                "orderReference": request_id,
                "orderDate": order_date,
                "amount": amount,
                "currency": currency,
                "productName": product_names,
                "productCount": product_counts,
                "productPrice": product_prices,
                "serviceUrl": WAYFORPAY_SERVICE_URL,
            },
        )

    payload = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": WAYFORPAY_MERCHANT_ACCOUNT,
        "merchantAuthType": "SimpleSignature",
        "merchantDomainName": WAYFORPAY_DOMAIN_NAME,
        "merchantSignature": merchant_signature,
        "apiVersion": 1,
        "language": "UA",
        "serviceUrl": WAYFORPAY_SERVICE_URL,
        "orderReference": request_id,
        "orderDate": order_date,
        "amount": amount,
        "currency": currency,
        "orderTimeout": RESERVATION_TTL_MINUTES * 60,
        "productName": product_names,
        "productPrice": product_prices,
        "productCount": product_counts,
        "paymentSystems": "card",
        "clientFirstName": split_full_name(full_name)[0],
        "clientLastName": split_full_name(full_name)[1],
        "clientPhone": phone_number,
    }
    if not payload["serviceUrl"]:
        payload.pop("serviceUrl")

    async with ClientSession() as session:
        async with session.post(WAYFORPAY_API_URL, json=payload) as response:
            body_text = await response.text()
            if WAYFORPAY_DEBUG:
                logger.debug(
                    "WayForPay CREATE_INVOICE response: status=%s body=%s",
                    response.status,
                    body_text,
                )
            if response.status != 200:
                raise RuntimeError(f"WayForPay invoice create failed ({response.status}): {body_text}")
            try:
                return await response.json()
            except Exception as exc:
                raise RuntimeError(f"WayForPay returned invalid JSON: {body_text}") from exc


async def fetch_wayforpay_invoice_status(order_reference: str) -> dict:
    payload = {
        "transactionType": "CHECK_STATUS",
        "merchantAccount": WAYFORPAY_MERCHANT_ACCOUNT,
        "orderReference": order_reference,
        "merchantSignature": hmac.new(
            WAYFORPAY_SECRET_KEY.encode("utf-8"),
            ";".join([WAYFORPAY_MERCHANT_ACCOUNT, order_reference]).encode("utf-8"),
            hashlib.md5,
        ).hexdigest(),
        "apiVersion": 1,
    }

    async with ClientSession() as session:
        async with session.post(WAYFORPAY_API_URL, json=payload) as response:
            body_text = await response.text()
            if WAYFORPAY_DEBUG:
                logger.debug(
                    "WayForPay CHECK_STATUS response: status=%s body=%s",
                    response.status,
                    body_text,
                )
            if response.status != 200:
                raise RuntimeError(f"WayForPay status lookup failed ({response.status}): {body_text}")
            try:
                return await response.json()
            except Exception as exc:
                raise RuntimeError(f"WayForPay returned invalid JSON: {body_text}") from exc


async def notify_booking_confirmed(pending: dict):
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


async def notify_master(master_telegram_id: int | None, booking_info: dict):
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

    try:
        await bot.send_message(master_telegram_id, message, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as exc:
        logger.warning("Failed to notify master %s about booking success: %s", master_telegram_id, exc)


async def notify_client_about_cancellation(booking: dict):
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


@dp.callback_query(F.data.startswith("cancel_booking:"))
async def cancel_booking_handler(callback: types.CallbackQuery, state: FSMContext):
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
    await notify_client_about_cancellation(deleted)


async def process_payment_status(order_reference: str, source: str) -> dict:
    pending = BookingDatabase.get_pending_payment_by_request(order_reference)
    if not pending:
        return {"ok": False, "reason": "pending_not_found"}

    try:
        status_payload = await fetch_wayforpay_invoice_status(order_reference)
    except Exception as exc:
        logger.exception("Failed to fetch invoice status for %s from %s", order_reference, source)
        return {"ok": False, "reason": "status_fetch_failed", "error": str(exc)}

    status = str(status_payload.get("transactionStatus") or "").lower()
    logger.info("Payment status update from %s: order=%s status=%s", source, order_reference, status)

    if status == "approved":
        was_paid = pending["status"] == "paid"
        booking_id = BookingDatabase.finalize_booking_from_payment(order_reference)
        if booking_id is not None and not was_paid:
            await notify_booking_confirmed(pending)
            await notify_master(
                pending.get("master_telegram_id"),
                {
                    "client_telegram_id": pending.get("user_id"),
                    "full_name": pending.get("full_name"),
                    "phone_number": pending.get("phone_number"),
                    "service": pending.get("service"),
                    "booking_date": pending.get("booking_date"),
                    "booking_time": pending.get("booking_time"),
                    "payment_status": "paid",
                    "booking_id": booking_id,
                },
            )
        if booking_id is not None or was_paid:
            return {"ok": True, "status": status, "pending": pending, "payload": status_payload}
        return {"ok": False, "status": status, "reason": "booking_conflict", "pending": pending, "payload": status_payload}

    if status in {"declined", "expired"}:
        BookingDatabase.mark_payment_status(order_reference, status)
    else:
        BookingDatabase.mark_payment_status(order_reference, status or "processing")

    return {"ok": True, "status": status, "pending": pending, "payload": status_payload}


@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
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

        profile = BookingDatabase.get_master_profile(master_id)
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
            build_master_welcome_text(profile),
            reply_markup=get_services_keyboard(profile.get("services"), prefix="master_service"),
        )
        await state.set_state(BeautyBookingStates.waiting_for_service)
        return

    profile = BookingDatabase.get_master_profile(message.from_user.id)
    if profile:
        await state.clear()
        await state.update_data(
            entry_mode="master",
            master_telegram_id=message.from_user.id,
            master_profile=profile,
        )
        await message.answer(
            "🌸 Вітаю, майстре! Ось ваше меню:",
            reply_markup=get_master_menu_keyboard(),
        )
        return

    await message.answer(
        "🌸 Привіт! Ласкаво просимо!\n\nХто ви?",
        reply_markup=get_role_selection_keyboard(),
    )
    await state.clear()


@dp.callback_query(F.data == "role_master")
async def start_master_registration(callback: types.CallbackQuery, state: FSMContext):
    await safe_edit_text(callback.message, "Добре! Давай зареєструємо твій профіль майстра.\n\nНапиши, будь ласка, своє ім'я 👤")
    await state.set_state(MasterOnboardingStates.waiting_for_master_name)
    await callback.answer()


@dp.callback_query(F.data == "role_client")
async def start_client_booking(callback: types.CallbackQuery, state: FSMContext):
    await safe_edit_text(
        callback.message,
        "Щоб записатися, скористайся персональним посиланням свого майстра — попроси його надіслати тобі посилання виду "
        "t.me/<bot_username>?start=master_...\n"
        "Якщо в тебе його ще немає, звернись до майстра.",
    )
    await state.clear()
    await callback.answer()


@dp.message(MasterOnboardingStates.waiting_for_master_name)
async def process_master_name(message: types.Message, state: FSMContext):
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


@dp.message(MasterOnboardingStates.waiting_for_service_input)
async def process_master_service_input(message: types.Message, state: FSMContext):
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


@dp.callback_query(MasterOnboardingStates.waiting_for_service_input, F.data == "master_services_done")
async def finish_master_services(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    services = data.get("master_services", [])
    if not services:
        await callback.answer("Додай хоча б одну послугу перед завершенням.", show_alert=True)
        return
    await ask_master_duration(callback.message, state)
    await callback.answer()


@dp.message(MasterOnboardingStates.waiting_for_duration)
async def process_master_duration(message: types.Message, state: FSMContext):
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


@dp.message(MasterOnboardingStates.waiting_for_schedule)
async def process_master_schedule(message: types.Message, state: FSMContext):
    schedule_text = (message.text or "").strip()
    if not schedule_text:
        await message.answer("Напиши, будь ласка, графік роботи текстом.")
        return
    schedule_lines = [line.strip() for line in schedule_text.splitlines() if line.strip()]
    if not schedule_lines:
        schedule_lines = [schedule_text]
    await state.update_data(schedule=schedule_lines)
    data = await state.get_data()
    preview = (
        f"<b>Перевір профіль майстра:</b>\n\n"
        f"👤 Ім'я: <b>{data.get('master_name', '')}</b>\n"
        f"💅 Послуги:\n"
        + "\n".join(f"• {s}" for s in data.get("master_services", []))
        + f"\n\n⏱ Тривалість: <b>{data.get('duration_minutes', 60)} хв</b>\n"
        f"📅 Графік:\n"
        + "\n".join(f"• {s}" for s in data.get("schedule", []))
    )
    await message.answer(preview, reply_markup=get_master_confirmation_keyboard(), parse_mode="HTML")
    await state.set_state(MasterOnboardingStates.confirmation)


@dp.callback_query(MasterOnboardingStates.confirmation, F.data == "master_save")
async def save_master_profile(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    master_name = data.get("master_name", "")
    services = data.get("master_services", [])
    schedule = data.get("schedule", [])
    duration_minutes = int(data.get("duration_minutes") or 60)
    master_telegram_id = callback.from_user.id

    saved = BookingDatabase.upsert_master_profile(
        master_telegram_id=master_telegram_id,
        master_name=master_name,
        services=services,
        schedule=schedule,
        greeting_text=None,
        duration_minutes=duration_minutes,
    )
    if not saved:
        await safe_edit_text(callback.message, "Не вдалося зберегти профіль. Спробуйте ще раз.", reply_markup=None)
        await callback.answer()
        await state.clear()
        return

    await safe_edit_text(callback.message, "✅ Профіль майстра збережено!", reply_markup=get_master_menu_keyboard())
    await callback.answer("Збережено")
    await state.clear()


@dp.callback_query(MasterOnboardingStates.confirmation, F.data == "master_cancel")
async def cancel_master_registration(callback: types.CallbackQuery, state: FSMContext):
    await safe_edit_text(callback.message, "Реєстрацію скасовано. Почнемо заново?", reply_markup=get_role_selection_keyboard())
    await callback.answer()
    await state.clear()


@dp.callback_query(F.data == "master_get_link")
async def send_master_link(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    profile = data.get("master_profile")
    if not profile:
        profile = BookingDatabase.get_master_profile(callback.from_user.id)
    if not profile:
        await callback.answer("Профіль не знайдено. Спочатку зареєструйтеся.", show_alert=True)
        return

    me = await bot.get_me()
    username = me.username or str(me.id)
    link = f"https://t.me/{username}?start=master_{callback.from_user.id}"
    await safe_edit_text(
        callback.message,
        f"Ось твоє посилання для клієнтів:\n\n{link}\n\nНадішли його клієнтам — вони потраплять безпосередньо до твого запису.",
        reply_markup=get_master_menu_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "master_view_profile")
async def view_master_profile(callback: types.CallbackQuery, state: FSMContext):
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
    text = (
        f"👤 <b>{profile.get('master_name', 'Майстер')}</b>\n"
        f"⏱ Тривалість: <b>{duration_minutes} хв</b>\n\n"
        f"💅 Послуги:\n"
        + "\n".join(f"• {s}" for s in services)
        + f"\n\n📅 Графік:\n"
        + "\n".join(f"• {s}" for s in schedule)
    )
    await safe_edit_text(callback.message, text, reply_markup=get_master_menu_keyboard(), parse_mode="HTML")
    await callback.answer()


@dp.message(BeautyBookingStates.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    full_name = (message.text or "").strip()
    if not full_name:
        await message.answer("Напишіть, будь ласка, ім'я та прізвище текстом.")
        return

    await state.update_data(full_name=full_name)
    await message.answer("Тепер поділись номером телефону 📱", reply_markup=get_phone_keyboard())
    await state.set_state(BeautyBookingStates.waiting_for_phone)


@dp.message(BeautyBookingStates.waiting_for_phone, F.contact)
async def process_phone(message: types.Message, state: FSMContext):
    await state.update_data(phone_number=message.contact.phone_number)
    await message.answer("Оберіть дату запису 📅", reply_markup=get_date_calendar_keyboard())
    await state.set_state(BeautyBookingStates.waiting_for_date)


@dp.message(BeautyBookingStates.waiting_for_phone)
async def process_phone_fallback(message: types.Message):
    await message.answer("Надішліть номер телефону через кнопку нижче 📱", reply_markup=get_phone_keyboard())


@dp.callback_query(BeautyBookingStates.waiting_for_service)
async def process_service(callback: types.CallbackQuery, state: FSMContext):
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
    await safe_edit_text(
        callback.message,
        f"Послуга обрана: <b>{service_name}</b>\n\nТепер напишіть своє ім'я.",
        reply_markup=None,
        parse_mode="HTML",
    )
    await callback.answer()
    await state.set_state(BeautyBookingStates.waiting_for_name)


@dp.callback_query(BeautyBookingStates.waiting_for_date, F.data.startswith("date_"))
async def process_date(callback: types.CallbackQuery, state: FSMContext):
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


@dp.callback_query(BeautyBookingStates.waiting_for_time, F.data.startswith("time_"))
async def process_time(callback: types.CallbackQuery, state: FSMContext):
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
        invoice = await create_wayforpay_invoice(
            request_id=request_id,
            full_name=user_data["full_name"],
            phone_number=user_data["phone_number"],
            service=user_data["service"],
            booking_date=booking_date,
            booking_time=booking_time,
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


@dp.callback_query(F.data.startswith("check_payment:"))
async def check_payment(callback: types.CallbackQuery):
    order_reference = callback.data.split(":", 1)[1]
    result = await process_payment_status(order_reference, source="manual_check")

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


@dp.message(F.contact)
async def wrong_contact_state(message: types.Message):
    await message.answer("Контакт потрібно надсилати під час кроку з номером телефону.", reply_markup=get_role_selection_keyboard())


async def wayforpay_service_url(request: web.Request):
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    if WAYFORPAY_DEBUG:
        logger.debug("WayForPay webhook payload: %s", payload)

    if not verify_wayforpay_signature(payload):
        return web.json_response({"error": "invalid signature"}, status=403)

    order_reference = str(payload.get("orderReference", ""))
    transaction_status = str(payload.get("transactionStatus", "")).lower()
    if not order_reference:
        return web.json_response({"error": "orderReference required"}, status=400)

    if transaction_status == "approved":
        await process_payment_status(order_reference, source="webhook")
    else:
        BookingDatabase.mark_payment_status(order_reference, transaction_status or "processing")

    response_status = "accept"
    response_time = int(time.time())
    response_signature = build_wayforpay_status_signature(order_reference, response_status, response_time)
    return web.json_response(
        {
            "orderReference": order_reference,
            "status": response_status,
            "time": response_time,
            "signature": response_signature,
        }
    )


async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_post(f"/payments/wayforpay/{PAYMENT_WEBHOOK_SECRET}", wayforpay_service_url)

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
    if WAYFORPAY_SERVICE_URL:
        logger.info("WayForPay serviceUrl configured: %s", WAYFORPAY_SERVICE_URL)
    else:
        logger.warning("WAYFORPAY_SERVICE_URL is not set; invoice callbacks may not reach this bot")
    return runner


async def main():
    logger.info("Bot is starting")
    runner = await start_web_server()
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
