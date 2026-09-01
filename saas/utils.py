"""Utility functions for validation, parsing, and formatting."""

from aiogram import types
from aiogram.exceptions import TelegramBadRequest


def parse_start_payload(text: str | None) -> str | None:
    """Extract payload from /start command."""
    if not text:
        return None
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip()


def is_valid_luhn(card_number: str) -> bool:
    """Validate card number using Luhn algorithm."""
    if not card_number or not card_number.isdigit():
        return False
    total = 0
    for index, char in enumerate(reversed(card_number)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def normalize_card_number(raw: str | None) -> str:
    """Remove spaces and dashes from card number."""
    return (raw or "").replace(" ", "").replace("-", "").strip()


def mask_card_last4(card_number: str | None) -> str | None:
    """Extract last 4 digits of card number."""
    digits = "".join(ch for ch in (card_number or "") if ch.isdigit())
    if len(digits) < 4:
        return None
    return digits[-4:]


def split_full_name(full_name: str) -> tuple[str, str]:
    """Split full name into first and last name."""
    parts = [part for part in full_name.split() if part]
    if not parts:
        return "Client", "Client"
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


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


def _can_use_test_pay(user_id: int) -> bool:
    """Check if user can access /test_pay command."""
    from .config import ADMIN_IDS, WAYFORPAY_DEBUG
    from database import BookingDatabase
    
    if user_id in ADMIN_IDS:
        return True
    if ADMIN_IDS:
        return False
    if WAYFORPAY_DEBUG:
        return True
    return BookingDatabase.get_master_profile(user_id) is not None
