"""WayForPay payment gateway integration."""

import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal, ROUND_HALF_UP
from aiohttp import ClientSession

logger = logging.getLogger(__name__)


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
    """Build HMAC-MD5 signature for WayForPay request."""
    product_parts = [val for pair in zip(product_names, product_counts, product_prices) for val in pair]
    base_string = ";".join(
        [
            merchant_account,
            merchant_domain_name,
            order_reference,
            str(order_date),
            amount,
            currency,
            *product_parts,
        ]
    )
    from saas.config import WAYFORPAY_SECRET_KEY
    return hmac.new(WAYFORPAY_SECRET_KEY.encode("utf-8"), base_string.encode("utf-8"), hashlib.md5).hexdigest()


def build_wayforpay_status_signature(order_reference: str, status: str, timestamp: int) -> str:
    """Build signature for WayForPay status response."""
    base_string = ";".join([order_reference, status, str(timestamp)])
    from saas.config import WAYFORPAY_SECRET_KEY
    return hmac.new(WAYFORPAY_SECRET_KEY.encode("utf-8"), base_string.encode("utf-8"), hashlib.md5).hexdigest()


def verify_wayforpay_signature(payload: dict) -> bool:
    """Verify WayForPay webhook signature."""
    from saas.config import WAYFORPAY_MERCHANT_ACCOUNT, WAYFORPAY_SECRET_KEY
    
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


async def _post_wayforpay_json(payload: dict, operation: str) -> dict:
    """Send JSON request to WayForPay API."""
    from saas.config import WAYFORPAY_API_URL, WAYFORPAY_DEBUG
    
    async with ClientSession() as session:
        async with session.post(WAYFORPAY_API_URL, json=payload) as response:
            body_text = await response.text()
            if WAYFORPAY_DEBUG:
                logger.debug("WayForPay %s response: status=%s body=%s", operation, response.status, body_text)
            if response.status != 200:
                raise RuntimeError(f"WayForPay {operation} failed ({response.status}): {body_text}")
            try:
                return json.loads(body_text)
            except Exception as exc:
                raise RuntimeError(f"WayForPay returned invalid JSON: {body_text}") from exc


async def create_wayforpay_invoice(
    request_id: str,
    full_name: str,
    phone_number: str,
    service: str,
    booking_date: str,
    booking_time: str,
    master_card_number: str | None = None,
) -> dict:
    """Create payment invoice via WayForPay."""
    from saas.config import (
        WAYFORPAY_MERCHANT_ACCOUNT,
        WAYFORPAY_DOMAIN_NAME,
        WAYFORPAY_SERVICE_URL,
        WAYFORPAY_DEBUG,
        DEPOSIT_AMOUNT_UAH,
        RESERVATION_TTL_MINUTES,
    )
    from saas.utils import mask_card_last4, split_full_name
    
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
        signature_base = ";".join(
            [
                WAYFORPAY_MERCHANT_ACCOUNT,
                WAYFORPAY_DOMAIN_NAME,
                request_id,
                str(order_date),
                amount,
                currency,
                *product_names,
                *product_counts,
                *product_prices,
            ]
        )
        last4 = mask_card_last4(master_card_number)
        logger.debug(
            "WayForPay CREATE_INVOICE debug: signature_base=%s merchant_signature=%s payload_fields=%s",
            signature_base,
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
                "receiverLast4": last4,
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
    if master_card_number:
        payload["regularMode"] = "client"
        payload["receiver"] = [
            {
                "type": "card",
                "value": master_card_number,
                "percent": 100,
                "merchantAccount": WAYFORPAY_MERCHANT_ACCOUNT,
            }
        ]

    try:
        result = await _post_wayforpay_json(payload, "CREATE_INVOICE")
    except RuntimeError:
        if not master_card_number or "receiver" not in payload:
            raise
        logger.warning(
            "WayForPay CREATE_INVOICE rejected receiver routing, retrying without it for order %s",
            request_id,
        )
        payload.pop("receiver", None)
        payload.pop("regularMode", None)
        return await _post_wayforpay_json(payload, "CREATE_INVOICE")

    if master_card_number and payload.get("receiver") and not result.get("invoiceUrl"):
        logger.warning(
            "WayForPay CREATE_INVOICE did not return invoiceUrl with receiver routing, retrying without it for order %s",
            request_id,
        )
        payload.pop("receiver", None)
        payload.pop("regularMode", None)
        return await _post_wayforpay_json(payload, "CREATE_INVOICE")
    return result


async def transfer_wayforpay_to_master_card(pending: dict) -> None:
    """Transfer payment to master's card via WayForPay."""
    from saas.config import WAYFORPAY_MERCHANT_ACCOUNT, WAYFORPAY_SECRET_KEY, DEPOSIT_AMOUNT_UAH
    from saas.utils import mask_card_last4
    
    card_number = (pending.get("card_number") or "").strip()
    if not card_number:
        return

    original_reference = str(pending.get("request_id") or pending.get("payment_invoice_id") or "")
    order_reference = f"payout_{original_reference}"
    amount = format_wayforpay_amount(pending.get("amount") or DEPOSIT_AMOUNT_UAH)
    currency = "UAH"
    order_date = int(time.time())
    signature_base = ";".join(
        [WAYFORPAY_MERCHANT_ACCOUNT, order_reference, amount, currency, card_number]
    )
    merchant_signature = hmac.new(
        WAYFORPAY_SECRET_KEY.encode("utf-8"),
        signature_base.encode("utf-8"),
        hashlib.md5,
    ).hexdigest()
    payload = {
        "transactionType": "TRANSFER_TO_CARD",
        "merchantAccount": WAYFORPAY_MERCHANT_ACCOUNT,
        "merchantSignature": merchant_signature,
        "cardNumber": card_number,
        "amount": amount,
        "currency": currency,
        "orderReference": order_reference,
        "orderDate": order_date,
        "apiVersion": 1,
    }
    last4 = mask_card_last4(card_number)
    try:
        result = await _post_wayforpay_json(payload, "TRANSFER_TO_CARD")
        logger.info(
            "WayForPay TRANSFER_TO_CARD sent for %s to card •••• %s: %s",
            original_reference,
            last4,
            result.get("transactionStatus") or result.get("reason") or "ok",
        )
    except Exception as exc:
        logger.exception(
            "WayForPay TRANSFER_TO_CARD failed for %s to card •••• %s: %s",
            original_reference,
            last4,
            exc,
        )


async def fetch_wayforpay_invoice_status(order_reference: str) -> dict:
    """Check payment status from WayForPay."""
    from saas.config import WAYFORPAY_API_URL, WAYFORPAY_MERCHANT_ACCOUNT, WAYFORPAY_SECRET_KEY, WAYFORPAY_DEBUG
    
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
    """Build HMAC-MD5 signature for WayForPay request."""
    product_parts = [val for pair in zip(product_names, product_counts, product_prices) for val in pair]
    base_string = ";".join(
        [
            merchant_account,
            merchant_domain_name,
            order_reference,
            str(order_date),
            amount,
            currency,
            *product_parts,
        ]
    )
    return hmac.new(WAYFORPAY_SECRET_KEY.encode("utf-8"), base_string.encode("utf-8"), hashlib.md5).hexdigest()


def build_wayforpay_status_signature(order_reference: str, status: str, timestamp: int) -> str:
    """Build signature for WayForPay status response."""
    base_string = ";".join([order_reference, status, str(timestamp)])
    return hmac.new(WAYFORPAY_SECRET_KEY.encode("utf-8"), base_string.encode("utf-8"), hashlib.md5).hexdigest()


def verify_wayforpay_signature(payload: dict) -> bool:
    """Verify WayForPay webhook signature."""
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


async def _post_wayforpay_json(payload: dict, operation: str) -> dict:
    """Send JSON request to WayForPay API."""
    async with ClientSession() as session:
        async with session.post(WAYFORPAY_API_URL, json=payload) as response:
            body_text = await response.text()
            if WAYFORPAY_DEBUG:
                logger.debug("WayForPay %s response: status=%s body=%s", operation, response.status, body_text)
            if response.status != 200:
                raise RuntimeError(f"WayForPay {operation} failed ({response.status}): {body_text}")
            try:
                return json.loads(body_text)
            except Exception as exc:
                raise RuntimeError(f"WayForPay returned invalid JSON: {body_text}") from exc


async def create_wayforpay_invoice(
    request_id: str,
    full_name: str,
    phone_number: str,
    service: str,
    booking_date: str,
    booking_time: str,
    master_card_number: str | None = None,
) -> dict:
    """Create payment invoice via WayForPay."""
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
        signature_base = ";".join(
            [
                WAYFORPAY_MERCHANT_ACCOUNT,
                WAYFORPAY_DOMAIN_NAME,
                request_id,
                str(order_date),
                amount,
                currency,
                *product_names,
                *product_counts,
                *product_prices,
            ]
        )
        last4 = mask_card_last4(master_card_number)
        logger.debug(
            "WayForPay CREATE_INVOICE debug: signature_base=%s merchant_signature=%s payload_fields=%s",
            signature_base,
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
                "receiverLast4": last4,
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
        "orderTimeout": 30 * 60,  # RESERVATION_TTL_MINUTES * 60
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
    if master_card_number:
        payload["regularMode"] = "client"
        payload["receiver"] = [
            {
                "type": "card",
                "value": master_card_number,
                "percent": 100,
                "merchantAccount": WAYFORPAY_MERCHANT_ACCOUNT,
            }
        ]

    try:
        result = await _post_wayforpay_json(payload, "CREATE_INVOICE")
    except RuntimeError:
        if not master_card_number or "receiver" not in payload:
            raise
        logger.warning(
            "WayForPay CREATE_INVOICE rejected receiver routing, retrying without it for order %s",
            request_id,
        )
        payload.pop("receiver", None)
        payload.pop("regularMode", None)
        return await _post_wayforpay_json(payload, "CREATE_INVOICE")

    if master_card_number and payload.get("receiver") and not result.get("invoiceUrl"):
        logger.warning(
            "WayForPay CREATE_INVOICE did not return invoiceUrl with receiver routing, retrying without it for order %s",
            request_id,
        )
        payload.pop("receiver", None)
        payload.pop("regularMode", None)
        return await _post_wayforpay_json(payload, "CREATE_INVOICE")
    return result


async def transfer_wayforpay_to_master_card(pending: dict) -> None:
    """Transfer payment to master's card via WayForPay."""
    card_number = (pending.get("card_number") or "").strip()
    if not card_number:
        return

    original_reference = str(pending.get("request_id") or pending.get("payment_invoice_id") or "")
    order_reference = f"payout_{original_reference}"
    amount = format_wayforpay_amount(pending.get("amount") or DEPOSIT_AMOUNT_UAH)
    currency = "UAH"
    order_date = int(time.time())
    signature_base = ";".join(
        [WAYFORPAY_MERCHANT_ACCOUNT, order_reference, amount, currency, card_number]
    )
    merchant_signature = hmac.new(
        WAYFORPAY_SECRET_KEY.encode("utf-8"),
        signature_base.encode("utf-8"),
        hashlib.md5,
    ).hexdigest()
    payload = {
        "transactionType": "TRANSFER_TO_CARD",
        "merchantAccount": WAYFORPAY_MERCHANT_ACCOUNT,
        "merchantSignature": merchant_signature,
        "cardNumber": card_number,
        "amount": amount,
        "currency": currency,
        "orderReference": order_reference,
        "orderDate": order_date,
        "apiVersion": 1,
    }
    last4 = mask_card_last4(card_number)
    try:
        result = await _post_wayforpay_json(payload, "TRANSFER_TO_CARD")
        logger.info(
            "WayForPay TRANSFER_TO_CARD sent for %s to card •••• %s: %s",
            original_reference,
            last4,
            result.get("transactionStatus") or result.get("reason") or "ok",
        )
    except Exception as exc:
        logger.exception(
            "WayForPay TRANSFER_TO_CARD failed for %s to card •••• %s: %s",
            original_reference,
            last4,
            exc,
        )


async def fetch_wayforpay_invoice_status(order_reference: str) -> dict:
    """Check payment status from WayForPay."""
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
