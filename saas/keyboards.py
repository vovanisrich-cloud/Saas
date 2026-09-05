"""Keyboard builders and text formatting for user interface."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


MASTER_CARD_PROMPT = (
    "Введи номер картки (13–19 цифр), на яку клієнти будуть переказувати передоплату. "
    "Пробіли та дефіси будуть видалені автоматично."
)


def get_phone_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard for requesting contact."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Надіслати номер телефону", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_services_keyboard(services: list[dict], prefix: str = "service") -> InlineKeyboardMarkup:
    """Keyboard for selecting service."""
    keyboard = []
    for index, service in enumerate(services):
        if isinstance(service, dict):
            service_name = service.get("name", "Послуга")
            service_price = service.get("price")
            label = f"{service_name} — {service_price} грн" if service_price else str(service_name)
        else:
            label = str(service)
        keyboard.append([InlineKeyboardButton(text=label, callback_data=f"{prefix}:{index}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def format_master_schedule(schedule: list[str] | None) -> str:
    """Format master's schedule for display."""
    if not schedule:
        return "Графік: за домовленістю"
    return "Графік:\n" + "\n".join(f"• {item}" for item in schedule)


def build_master_welcome_text(profile: dict) -> str:
    """Build welcome message for master's booking page."""
    greeting = profile.get("greeting_text") or f"Привіт! Ви записуєтесь до майстра {profile.get('master_name', 'майстра')}."
    services = profile.get("services") or []
    services_text = "\n".join(
        f"• {item.get('name', 'Послуга')} — {item.get('price')} грн" if isinstance(item, dict) else f"• {item}"
        for item in services
    ) if services else "• Послуги ще не додані"
    schedule_text = format_master_schedule(profile.get("schedule"))
    return f"{greeting}\n\nПослуги:\n{services_text}\n\n{schedule_text}"


def get_date_calendar_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for date selection (7 days ahead)."""
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
        current_date = datetime.now(ZoneInfo("Europe/Kyiv")) + timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")
        day_name_uk = days_uk.get(current_date.strftime("%a"), current_date.strftime("%a"))
        date_display = f"{day_name_uk}, {current_date.strftime('%d.%m')}"
        keyboard_buttons.append([InlineKeyboardButton(text=date_display, callback_data=f"date_{date_str}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)


def get_time_keyboard(booking_date: str, master_telegram_id: int | None = None):
    """Keyboard for time slot selection."""
    from database import BookingDatabase
    available_times = BookingDatabase.get_available_times(booking_date, master_telegram_id=master_telegram_id)
    if not available_times:
        return None

    keyboard_buttons = []
    for time_slot in available_times:
        keyboard_buttons.append([InlineKeyboardButton(text=f"🕒 {time_slot}", callback_data=f"time_{time_slot}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)


def get_role_selection_keyboard(include_profiles: bool = False) -> InlineKeyboardMarkup:
    """Keyboard for role selection (master or client)."""
    buttons = [
            [InlineKeyboardButton(text="Я майстер, хочу зареєструватися", callback_data="role_master")],
            [InlineKeyboardButton(text="Я клієнт, хочу записатися", callback_data="role_client")],
    ]
    if include_profiles:
        buttons.append([InlineKeyboardButton(text="🔄 Мої профілі", callback_data="master_my_profiles")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_master_done_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for finishing service input."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Готово", callback_data="master_services_done")]]
    )


def get_master_menu_keyboard() -> InlineKeyboardMarkup:
    """Main menu keyboard for master."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отримати моє посилання", callback_data="master_get_link")],
            [InlineKeyboardButton(text="💳 Змінити картку", callback_data="master_set_card")],
            [InlineKeyboardButton(text="Мій профіль", callback_data="master_view_profile")],
            [InlineKeyboardButton(text="🔄 Мої профілі", callback_data="master_my_profiles")],
            [InlineKeyboardButton(text="📱 Записатися як клієнт", callback_data="switch_to_client_mode")],
            [InlineKeyboardButton(text="🚪 Вийти з профілю", callback_data="master_logout")],
        ]
    )


def get_master_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for confirming master profile."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Зберегти", callback_data="master_save")],
            [InlineKeyboardButton(text="Скасувати", callback_data="master_cancel")],
        ]
    )


def get_payment_keyboard(page_url: str, order_reference: str, amount: int) -> InlineKeyboardMarkup:
    """Keyboard for payment actions."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Оплатити {amount} грн", url=page_url)],
            [InlineKeyboardButton(text="Перевірити оплату", callback_data=f"check_payment:{order_reference}")],
        ]
    )


def build_master_confirmation_preview(data: dict) -> str:
    """Build preview of master profile for confirmation."""
    card = data.get("card_number") or ""
    last4 = card[-4:] if len(card) >= 4 else ""
    return (
        f"<b>Перевір профіль майстра:</b>\n\n"
        f"👤 Ім'я: <b>{data.get('master_name', '')}</b>\n"
        f"💅 Послуги:\n"
        + "\n".join(
            f"• {s.get('name', 'Послуга')} — {s.get('price')} грн" if isinstance(s, dict) else f"• {s}"
            for s in data.get("master_services", [])
        )
        + f"\n\n⏱ Тривалість: <b>{data.get('duration_minutes', 60)} хв</b>\n"
        f"📅 Графік:\n"
        + "\n".join(f"• {s}" for s in data.get("schedule", []))
        + f"\n\n💳 Картка: {last4} (останні 4 цифри)"
    )
