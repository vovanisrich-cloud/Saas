"""WayForPay webhook and payment status processing."""

import json
import logging
import time
from aiohttp import web

logger = logging.getLogger(__name__)


async def process_payment_status(bot, order_reference: str, source: str) -> dict:
    """Process payment status update from WayForPay."""
    from .wayforpay import verify_wayforpay_signature, fetch_wayforpay_invoice_status, build_wayforpay_status_signature
    from saas.notifications import notify_booking_confirmed, notify_master
    from database import BookingDatabase
    
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
        booking_id = await complete_booking_after_payment(bot, pending, transfer_payout=True)
        if booking_id is not None or was_paid:
            return {"ok": True, "status": status, "pending": pending, "payload": status_payload}
        return {"ok": False, "status": status, "reason": "booking_conflict", "pending": pending, "payload": status_payload}

    if status in {"declined", "expired"}:
        BookingDatabase.mark_payment_status(order_reference, status)
    else:
        BookingDatabase.mark_payment_status(order_reference, status or "processing")

    return {"ok": True, "status": status, "pending": pending, "payload": status_payload}


async def complete_booking_after_payment(bot, pending: dict, *, transfer_payout: bool = True) -> int | None:
    """Finalize booking after payment (real webhook or /test_pay)."""
    from .wayforpay import transfer_wayforpay_to_master_card
    from saas.notifications import notify_booking_confirmed, notify_master
    from database import BookingDatabase
    
    was_paid = pending.get("status") == "paid"
    reference = pending.get("payment_invoice_id") or pending.get("request_id")
    booking_id = BookingDatabase.finalize_booking_from_payment(str(reference))
    if booking_id is None:
        return None
    if was_paid:
        return booking_id

    if transfer_payout:
        await transfer_wayforpay_to_master_card(pending)
    await notify_booking_confirmed(bot, pending)
    await notify_master(
        bot,
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
            "card_number": pending.get("card_number"),
        },
    )
    return booking_id


async def wayforpay_service_url(request: web.Request, bot=None):
    """WayForPay webhook handler."""
    from .wayforpay import verify_wayforpay_signature, build_wayforpay_status_signature
    from saas.config import WAYFORPAY_DEBUG
    from database import BookingDatabase
    
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

    if transaction_status == "approved" and bot:
        await process_payment_status(bot, order_reference, source="webhook")
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
