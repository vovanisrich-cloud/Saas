import os
import sqlite3
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from typing import List, Optional, Tuple

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

DATABASE_FILE = os.getenv("DATABASE_FILE", "bookings.db")
ACTIVE_PAYMENT_STATUSES = ("creating", "pending_payment", "processing")


def _get_fernet() -> Optional[Fernet]:
    from saas.config import CARD_ENCRYPTION_KEY

    if not CARD_ENCRYPTION_KEY:
        return None
    try:
        return Fernet(CARD_ENCRYPTION_KEY.encode("utf-8"))
    except Exception:
        return None


def _encrypt_card_number(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    cipher = _get_fernet()
    if cipher is None:
        return normalized
    try:
        return cipher.encrypt(normalized.encode("utf-8")).decode("utf-8")
    except Exception:
        return normalized


def _decrypt_card_number(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    cipher = _get_fernet()
    if cipher is None:
        return raw
    try:
        return cipher.decrypt(raw.encode("utf-8")).decode("utf-8")
    except Exception:
        return raw


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_services(services) -> list[dict]:
    """Return services in the current shape while reading legacy strings safely."""
    from saas.config import DEPOSIT_AMOUNT_UAH

    normalized = []
    for service in services or []:
        if isinstance(service, dict):
            name = str(service.get("name") or "").strip()
            try:
                price = int(service.get("price"))
            except (TypeError, ValueError):
                price = DEPOSIT_AMOUNT_UAH
            normalized.append({"name": name, "price": price if price > 0 else DEPOSIT_AMOUNT_UAH})
        elif str(service).strip():
            normalized.append({"name": str(service).strip(), "price": DEPOSIT_AMOUNT_UAH})
    return normalized


class BookingDatabase:
    """SQLite layer for confirmed bookings and pending payment reservations."""

    @staticmethod
    def _connect() -> sqlite3.Connection:
        database_path = Path(DATABASE_FILE)
        if database_path.parent and not database_path.parent.exists():
            database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DATABASE_FILE, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @staticmethod
    def init_db():
        """Create the confirmed bookings table and pending payments table if needed."""
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bookings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    -- This field stores masters.id (business profile), not the owner's Telegram ID.
                    master_telegram_id INTEGER,
                    full_name TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    service TEXT NOT NULL,
                    booking_date TEXT NOT NULL,
                    booking_time TEXT NOT NULL,
                    payment_invoice_id TEXT,
                    payment_provider TEXT DEFAULT 'wayforpay',
                    payment_amount INTEGER DEFAULT 200,
                    service_price INTEGER DEFAULT 200,
                    payment_confirmed_at TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(booking_date, booking_time)
                )
                """
            )

            BookingDatabase._ensure_columns(
                cursor,
                "bookings",
                {
                    "master_telegram_id": "INTEGER",
                    "payment_invoice_id": "TEXT",
                    "payment_provider": "TEXT",
                    "payment_amount": "INTEGER",
                    "service_price": "INTEGER",
                    "payment_confirmed_at": "TEXT",
                },
            )

            BookingDatabase._migrate_bookings_to_per_master_unique(cursor)

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL,
                    -- This field stores masters.id (business profile), not the owner's Telegram ID.
                    master_telegram_id INTEGER,
                    full_name TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    service TEXT NOT NULL,
                    booking_date TEXT NOT NULL,
                    booking_time TEXT NOT NULL,
                    payment_provider TEXT NOT NULL DEFAULT 'wayforpay',
                    payment_invoice_id TEXT UNIQUE,
                    payment_page_url TEXT,
                    amount INTEGER NOT NULL DEFAULT 200,
                    service_price INTEGER NOT NULL DEFAULT 200,
                    status TEXT NOT NULL DEFAULT 'creating',
                    expires_at TEXT NOT NULL,
                    booking_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            BookingDatabase._ensure_columns(
                cursor,
                "pending_payments",
                {
                    "master_telegram_id": "INTEGER",
                    "payment_provider": "TEXT",
                    "payment_invoice_id": "TEXT",
                    "payment_page_url": "TEXT",
                    "amount": "INTEGER",
                    "service_price": "INTEGER",
                    "status": "TEXT",
                    "expires_at": "TEXT",
                    "booking_id": "INTEGER",
                    "created_at": "TEXT",
                    "updated_at": "TEXT",
                    "card_number": "TEXT",
                },
            )

            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_active_slot
                ON pending_payments (booking_date, booking_time)
                WHERE status IN ('creating', 'pending_payment', 'processing')
                """
            )

            BookingDatabase._migrate_pending_active_slot_index(cursor)

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_invoice
                ON pending_payments (payment_invoice_id)
                """
            )

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_status
                ON pending_payments (status)
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS masters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_telegram_id INTEGER NOT NULL,
                    master_name TEXT NOT NULL,
                    services_json TEXT NOT NULL DEFAULT '[]',
                    schedule_json TEXT NOT NULL DEFAULT '[]',
                    greeting_text TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            legacy_master_ids = BookingDatabase._migrate_masters_schema(cursor)

            BookingDatabase._ensure_columns(
                cursor,
                "masters",
                {
                    "duration_minutes": "INTEGER DEFAULT 60",
                    "card_number": "TEXT",
                },
            )

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_masters_active
                ON masters (is_active)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_masters_owner
                ON masters (owner_telegram_id)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS owner_active_profile (
                    owner_telegram_id INTEGER PRIMARY KEY,
                    active_master_id INTEGER
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS client_profiles (
                    telegram_id INTEGER PRIMARY KEY,
                    full_name TEXT,
                    phone_number TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            BookingDatabase._ensure_columns(
                cursor,
                "client_profiles",
                {"is_active": "INTEGER NOT NULL DEFAULT 1"},
            )
            for legacy_owner_id, profile_id in legacy_master_ids.items():
                cursor.execute(
                    """
                    UPDATE bookings
                    SET master_telegram_id = ?
                    WHERE master_telegram_id = ?
                    """,
                    (profile_id, legacy_owner_id),
                )
                cursor.execute(
                    """
                    UPDATE pending_payments
                    SET master_telegram_id = ?
                    WHERE master_telegram_id = ?
                    """,
                    (profile_id, legacy_owner_id),
                )

    @staticmethod
    def _migrate_masters_schema(cursor: sqlite3.Cursor):
        """Migrate the old Telegram-account-keyed masters table to profile IDs."""
        cursor.execute("PRAGMA table_info(masters)")
        columns = {row[1] for row in cursor.fetchall()}
        if "owner_telegram_id" in columns and "id" in columns:
            return {}

        logger.info("Migrating masters table to multi-profile schema")
        cursor.execute("SELECT telegram_id FROM masters")
        legacy_owner_ids = [row[0] for row in cursor.fetchall()]
        cursor.execute("ALTER TABLE masters RENAME TO masters_old")
        cursor.execute(
            """
            CREATE TABLE masters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_telegram_id INTEGER NOT NULL,
                master_name TEXT NOT NULL,
                services_json TEXT NOT NULL DEFAULT '[]',
                schedule_json TEXT NOT NULL DEFAULT '[]',
                greeting_text TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                duration_minutes INTEGER DEFAULT 60,
                card_number TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO masters (
                owner_telegram_id, master_name, services_json, schedule_json,
                greeting_text, is_active, duration_minutes, card_number,
                created_at, updated_at
            )
            SELECT
                telegram_id, master_name, services_json, schedule_json,
                greeting_text, is_active, duration_minutes, card_number,
                created_at, updated_at
            FROM masters_old
            """
        )
        cursor.execute("DROP TABLE masters_old")
        cursor.execute("SELECT id, owner_telegram_id FROM masters")
        return {owner_id: profile_id for profile_id, owner_id in cursor.fetchall()}

    @staticmethod
    def _migrate_bookings_to_per_master_unique(cursor: sqlite3.Cursor):
        cursor.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'bookings'
            """
        )
        row = cursor.fetchone()
        if not row:
            return
        create_sql = row["sql"] or ""
        if "UNIQUE(master_telegram_id, booking_date, booking_time)" in create_sql:
            return

        logger.info("Migrating bookings table to per-master unique constraint")

        cursor.execute("ALTER TABLE bookings RENAME TO bookings_old")
        cursor.execute(
            """
            CREATE TABLE bookings_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                master_telegram_id INTEGER DEFAULT 0,
                full_name TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                service TEXT NOT NULL,
                booking_date TEXT NOT NULL,
                booking_time TEXT NOT NULL,
                payment_invoice_id TEXT,
                payment_provider TEXT DEFAULT 'wayforpay',
                payment_amount INTEGER DEFAULT 200,
                service_price INTEGER DEFAULT 200,
                payment_confirmed_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(master_telegram_id, booking_date, booking_time)
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO bookings_new (
                id, user_id, master_telegram_id, full_name, phone_number, service,
                booking_date, booking_time, payment_invoice_id, payment_provider,
                payment_amount, payment_confirmed_at, created_at
                , service_price
            )
            SELECT
                id, user_id, COALESCE(master_telegram_id, 0), full_name, phone_number, service,
                booking_date, booking_time, payment_invoice_id, payment_provider,
                payment_amount, payment_confirmed_at, created_at
                , payment_amount
            FROM bookings_old
            """
        )
        cursor.execute("DROP TABLE bookings_old")
        cursor.execute("ALTER TABLE bookings_new RENAME TO bookings")

    @staticmethod
    def _migrate_pending_active_slot_index(cursor: sqlite3.Cursor):
        cursor.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'index' AND name = 'idx_pending_active_slot'
            """
        )
        row = cursor.fetchone()
        if not row:
            return
        index_sql = row["sql"] or ""
        if "master_telegram_id" in index_sql:
            return

        logger.info("Migrating idx_pending_active_slot to per-master unique index")

        cursor.execute("DROP INDEX idx_pending_active_slot")
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_active_slot
            ON pending_payments (master_telegram_id, booking_date, booking_time)
            WHERE status IN ('creating', 'pending_payment', 'processing')
            """
        )

    @staticmethod
    def _ensure_columns(cursor: sqlite3.Cursor, table_name: str, columns: dict[str, str]):
        cursor.execute(f"PRAGMA table_info({table_name})")
        existing_columns = {row[1] for row in cursor.fetchall()}
        for column_name, column_definition in columns.items():
            if column_name not in existing_columns:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

    @staticmethod
    def cleanup_expired_pending_payments():
        """Expire temporary payment holds that already passed the timeout."""
        now = _utc_now_str()
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE pending_payments
                SET status = 'expired', updated_at = ?
                WHERE status IN ('creating', 'pending_payment', 'processing')
                  AND expires_at <= ?
                """,
                (now, now),
            )

    @staticmethod
    def is_slot_available(
        booking_date: str,
        booking_time: str,
        master_telegram_id: Optional[int] = None,
    ) -> bool:
        """Check whether a time slot is free for checkout."""
        BookingDatabase.cleanup_expired_pending_payments()

        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()

            if master_telegram_id is None:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM bookings
                    WHERE booking_date = ? AND booking_time = ?
                    """,
                    (booking_date, booking_time),
                )
            else:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM bookings
                    WHERE booking_date = ? AND booking_time = ? AND master_telegram_id = ?
                    """,
                    (booking_date, booking_time, master_telegram_id),
                )
            confirmed_count = cursor.fetchone()["count"]

            if master_telegram_id is None:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM pending_payments
                    WHERE booking_date = ? AND booking_time = ?
                      AND status IN ('creating', 'pending_payment', 'processing')
                    """,
                    (booking_date, booking_time),
                )
            else:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM pending_payments
                    WHERE booking_date = ? AND booking_time = ? AND master_telegram_id = ?
                      AND status IN ('creating', 'pending_payment', 'processing')
                    """,
                    (booking_date, booking_time, master_telegram_id),
                )
            pending_count = cursor.fetchone()["count"]

        return confirmed_count == 0 and pending_count == 0

    @staticmethod
    def get_available_times(
        booking_date: str,
        master_telegram_id: Optional[int] = None,
    ) -> List[str]:
        """Return free time slots for the selected date."""
        BookingDatabase.cleanup_expired_pending_payments()

        available_slots = ["10:00", "12:00", "14:00", "16:00"]

        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()

            if master_telegram_id is None:
                cursor.execute(
                    """
                    SELECT booking_time
                    FROM bookings
                    WHERE booking_date = ?
                    """,
                    (booking_date,),
                )
            else:
                cursor.execute(
                    """
                    SELECT booking_time
                    FROM bookings
                    WHERE booking_date = ? AND master_telegram_id = ?
                    """,
                    (booking_date, master_telegram_id),
                )
            booked_times = {row["booking_time"] for row in cursor.fetchall()}

            if master_telegram_id is None:
                cursor.execute(
                    """
                    SELECT booking_time
                    FROM pending_payments
                    WHERE booking_date = ?
                      AND status IN ('creating', 'pending_payment', 'processing')
                      AND expires_at > ?
                    """,
                    (booking_date, _utc_now_str()),
                )
            else:
                cursor.execute(
                    """
                    SELECT booking_time
                    FROM pending_payments
                    WHERE booking_date = ?
                      AND master_telegram_id = ?
                      AND status IN ('creating', 'pending_payment', 'processing')
                      AND expires_at > ?
                    """,
                    (booking_date, master_telegram_id, _utc_now_str()),
                )
            reserved_times = {row["booking_time"] for row in cursor.fetchall()}

        occupied = booked_times | reserved_times
        return [slot for slot in available_slots if slot not in occupied]

    @staticmethod
    def reserve_pending_booking(
        user_id: int,
        full_name: str,
        phone_number: str,
        service: str,
        booking_date: str,
        booking_time: str,
        amount: int,
        provider: str = "wayforpay",
        request_id: Optional[str] = None,
        expires_at: Optional[str] = None,
        master_telegram_id: int = 0,
        service_price: Optional[int] = None,
    ) -> Optional[str]:
        """Create a temporary hold for a slot before the payment is created."""
        from saas.config import RESERVATION_TTL_MINUTES

        if not request_id:
            request_id = f"wp_{uuid4().hex[:24]}"
        if not expires_at:
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=RESERVATION_TTL_MINUTES)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        master_telegram_id = master_telegram_id or 0
        amount = int(service_price or amount)

        try:
            with BookingDatabase._connect() as conn:
                cursor = conn.cursor()
                master_card_number = None
                if master_telegram_id:
                    cursor.execute(
                        """
                        SELECT card_number
                        FROM masters
                        WHERE id = ?
                        """,
                        (master_telegram_id,),
                    )
                    master_row = cursor.fetchone()
                    if master_row:
                        master_card_number = _encrypt_card_number((master_row["card_number"] or "").strip() or None)
                cursor.execute(
                    """
                    INSERT INTO pending_payments (
                        request_id, user_id, master_telegram_id, full_name, phone_number, service,
                        booking_date, booking_time, payment_provider, amount, service_price,
                        status, expires_at, created_at, updated_at, card_number
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'creating', ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        user_id,
                        master_telegram_id,
                        full_name,
                        phone_number,
                        service,
                        booking_date,
                        booking_time,
                        provider,
                        amount,
                        amount,
                        expires_at,
                        _utc_now_str(),
                        _utc_now_str(),
                        master_card_number,
                    ),
                )
            return request_id
        except sqlite3.IntegrityError:
            return None

    @staticmethod
    def attach_payment_invoice(
        request_id: str,
        invoice_id: str,
        page_url: str,
        expires_at: str,
    ) -> bool:
        """Store the invoice data after it is created by the payment provider."""
        try:
            with BookingDatabase._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE pending_payments
                    SET payment_invoice_id = ?,
                        payment_page_url = ?,
                        status = 'pending_payment',
                        expires_at = ?,
                        updated_at = ?
                    WHERE request_id = ?
                    """,
                    (invoice_id, page_url, expires_at, _utc_now_str(), request_id),
                )
                return cursor.rowcount > 0
        except sqlite3.Error:
            return False

    @staticmethod
    def get_pending_payment_by_invoice(invoice_id: str) -> Optional[dict]:
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT *
                FROM pending_payments
                WHERE payment_invoice_id = ?
                """,
                (invoice_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            profile = dict(row)
            profile["card_number"] = _decrypt_card_number(profile.get("card_number"))
            return profile

    @staticmethod
    def get_pending_payment_by_request(request_id: str) -> Optional[dict]:
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT *
                FROM pending_payments
                WHERE request_id = ?
                """,
                (request_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            profile = dict(row)
            profile["card_number"] = _decrypt_card_number(profile.get("card_number"))
            return profile

    @staticmethod
    def get_pending_payment_for_test(
        user_id: Optional[int] = None,
        lookup: Optional[str] = None,
        *,
        allow_other_users: bool = False,
    ) -> Optional[dict]:
        """Find a pending reservation for the calling user unless admin explicitly asks to search elsewhere."""
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            if lookup:
                raw = lookup.strip()
                token = raw
                if token.lower().startswith("booking_"):
                    token = token.split("_", 1)[1]
                conditions = [
                    "request_id = ?",
                    "payment_invoice_id = ?",
                    "CAST(id AS TEXT) = ?",
                    "CAST(booking_id AS TEXT) = ?",
                ]
                params: list[str] = [raw, raw, token, token]
                where_clause = f"({' OR '.join(conditions)})"
                if user_id is not None and not allow_other_users:
                    where_clause += " AND user_id = ?"
                    params.append(str(user_id))
                cursor.execute(
                    f"""
                    SELECT *
                    FROM pending_payments
                    WHERE {where_clause}
                    ORDER BY datetime(created_at) DESC, id DESC
                    LIMIT 1
                    """,
                    params,
                )
                row = cursor.fetchone()
                if not row:
                    return None
                profile = dict(row)
                profile["card_number"] = _decrypt_card_number(profile.get("card_number"))
                return profile

            placeholders = ",".join("?" for _ in ACTIVE_PAYMENT_STATUSES)
            if user_id is not None and not allow_other_users:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM pending_payments
                    WHERE user_id = ?
                      AND status IN ({placeholders})
                    ORDER BY datetime(created_at) DESC, id DESC
                    LIMIT 1
                    """,
                    (user_id, *ACTIVE_PAYMENT_STATUSES),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM pending_payments
                    WHERE status IN ({placeholders})
                    ORDER BY datetime(created_at) DESC, id DESC
                    LIMIT 1
                    """,
                    ACTIVE_PAYMENT_STATUSES,
                )
            row = cursor.fetchone()
            if not row:
                return None
            profile = dict(row)
            profile["card_number"] = _decrypt_card_number(profile.get("card_number"))
            return profile

    @staticmethod
    def mark_payment_status(invoice_id: str, status: str) -> bool:
        """Update payment status without finalizing the booking."""
        try:
            with BookingDatabase._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE pending_payments
                    SET status = ?, updated_at = ?
                    WHERE payment_invoice_id = ?
                    """,
                    (status, _utc_now_str(), invoice_id),
                )
                return cursor.rowcount > 0
        except sqlite3.Error as exc:
            logger.warning("mark_payment_status failed for invoice_id=%s: %s", invoice_id, exc)
            return False

    @staticmethod
    def update_pending_status_by_request(request_id: str, status: str) -> bool:
        """Update the status of a reservation before the invoice exists or when it fails."""
        try:
            with BookingDatabase._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE pending_payments
                    SET status = ?, updated_at = ?
                    WHERE request_id = ?
                    """,
                    (status, _utc_now_str(), request_id),
                )
                return cursor.rowcount > 0
        except sqlite3.Error as exc:
            logger.warning("update_pending_status_by_request failed for request_id=%s: %s", request_id, exc)
            return False

    @staticmethod
    def delete_pending_by_request(request_id: str) -> bool:
        """Remove a temporary hold entirely."""
        try:
            with BookingDatabase._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    DELETE FROM pending_payments
                    WHERE request_id = ?
                    """,
                    (request_id,),
                )
                return cursor.rowcount > 0
        except sqlite3.Error as exc:
            logger.warning("delete_pending_by_request failed for request_id=%s: %s", request_id, exc)
            return False

    @staticmethod
    def finalize_booking_from_payment(invoice_id: str) -> Optional[int]:
        """Move a paid pending reservation into the confirmed bookings table."""
        pending = BookingDatabase.get_pending_payment_by_invoice(invoice_id)
        if not pending:
            pending = BookingDatabase.get_pending_payment_by_request(invoice_id)
        if not pending:
            return None

        if pending["status"] == "paid":
            return pending.get("booking_id")

        invoice_key = pending.get("payment_invoice_id") or pending["request_id"]
        booking_payload = (
            pending["user_id"],
            pending.get("master_telegram_id") or 0,
            pending["full_name"],
            pending["phone_number"],
            pending["service"],
            pending["booking_date"],
            pending["booking_time"],
            invoice_key,
            pending["payment_provider"],
            pending["amount"],
            pending.get("service_price") or pending["amount"],
            _utc_now_str(),
        )

        try:
            with BookingDatabase._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("BEGIN")

                cursor.execute(
                    """
                    INSERT INTO bookings (
                        user_id, master_telegram_id, full_name, phone_number, service,
                        booking_date, booking_time, payment_invoice_id,
                        payment_provider, payment_amount, service_price, payment_confirmed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    booking_payload,
                )
                booking_id = cursor.lastrowid

                cursor.execute(
                    """
                    UPDATE pending_payments
                    SET status = 'paid',
                        booking_id = ?,
                        payment_invoice_id = COALESCE(payment_invoice_id, ?),
                        expires_at = '2099-01-01 00:00:00',
                        updated_at = ?
                    WHERE request_id = ?
                    """,
                    (booking_id, invoice_key, _utc_now_str(), pending["request_id"]),
                )

                conn.commit()
            BookingDatabase.upsert_client_profile(
                pending["user_id"],
                pending["full_name"],
                pending["phone_number"],
            )
            return booking_id
        except sqlite3.IntegrityError:
            with BookingDatabase._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE pending_payments
                    SET status = 'conflict',
                        updated_at = ?
                    WHERE request_id = ?
                    """,
                    (_utc_now_str(), pending["request_id"]),
                )
            return None
        except sqlite3.Error:
            return None

    @staticmethod
    def cancel_booking(booking_id: int, requester_telegram_id: int) -> Optional[dict]:
        """Delete a booking only if it belongs to the given master."""
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, user_id, master_telegram_id, full_name, phone_number, service, booking_date, booking_time
                FROM bookings
                WHERE id = ?
                """,
                (booking_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            owner_telegram_id = BookingDatabase.get_master_owner(row["master_telegram_id"])
            if owner_telegram_id != requester_telegram_id:
                return None

            cursor.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
            return dict(row)

    @staticmethod
    def save_booking(
        user_id: int,
        full_name: str,
        phone_number: str,
        service: str,
        booking_date: str,
        booking_time: str,
        payment_invoice_id: Optional[str] = None,
        payment_provider: str = "wayforpay",
        payment_amount: int = 200,
        service_price: Optional[int] = None,
        payment_confirmed_at: Optional[str] = None,
        master_telegram_id: int = 0,
    ) -> bool:
        """Backward-compatible helper for directly inserting a confirmed booking."""
        if not payment_confirmed_at:
            payment_confirmed_at = _utc_now_str()

        try:
            with BookingDatabase._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO bookings (
                        user_id, master_telegram_id, full_name, phone_number, service,
                        booking_date, booking_time, payment_invoice_id,
                        payment_provider, payment_amount, service_price, payment_confirmed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        master_telegram_id,
                        full_name,
                        phone_number,
                        service,
                        booking_date,
                        booking_time,
                        payment_invoice_id,
                        payment_provider,
                        payment_amount,
                        service_price or payment_amount,
                        payment_confirmed_at,
                    ),
                )
            BookingDatabase.upsert_client_profile(user_id, full_name, phone_number)
            return True
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def get_client_profile(telegram_id: int) -> Optional[dict]:
        """Return the saved client name and phone number, if available."""
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT full_name, phone_number
                FROM client_profiles
                WHERE telegram_id = ? AND is_active = 1
                """,
                (telegram_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def upsert_client_profile(telegram_id: int, full_name: str, phone_number: str) -> None:
        """Insert or update the saved client profile."""
        with BookingDatabase._connect() as conn:
            conn.execute(
                """
                INSERT INTO client_profiles (telegram_id, full_name, phone_number, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    full_name = excluded.full_name,
                    phone_number = excluded.phone_number,
                    is_active = 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (telegram_id, full_name, phone_number),
            )

    @staticmethod
    def forget_client_profile(telegram_id: int) -> bool:
        """Mark the client profile as forgotten without deleting its data."""
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE client_profiles SET is_active = 0 WHERE telegram_id = ?",
                (telegram_id,),
            )
            return cursor.rowcount > 0

    @staticmethod
    def get_forgotten_client_profile(telegram_id: int) -> Optional[dict]:
        """Return a forgotten client profile, if one exists."""
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT full_name, phone_number
                FROM client_profiles
                WHERE telegram_id = ? AND is_active = 0
                """,
                (telegram_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def restore_client_profile(telegram_id: int) -> bool:
        """Restore a forgotten client profile without changing its data."""
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE client_profiles SET is_active = 1 WHERE telegram_id = ?",
                (telegram_id,),
            )
            return cursor.rowcount > 0

    @staticmethod
    def delete_client_profile(telegram_id: int) -> bool:
        """Permanently delete a client profile without touching bookings."""
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM client_profiles WHERE telegram_id = ?",
                (telegram_id,),
            )
            return cursor.rowcount > 0


    @staticmethod
    def get_booking_by_slot(
        booking_date: str,
        booking_time: str,
        master_telegram_id: Optional[int] = None,
    ) -> Optional[dict]:
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            if master_telegram_id is None:
                cursor.execute(
                    """
                    SELECT *
                    FROM bookings
                    WHERE booking_date = ? AND booking_time = ?
                    """,
                    (booking_date, booking_time),
                )
            else:
                cursor.execute(
                    """
                    SELECT *
                    FROM bookings
                    WHERE booking_date = ? AND booking_time = ? AND master_telegram_id = ?
                    """,
                    (booking_date, booking_time, master_telegram_id),
                )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_master_profile_by_id(master_id: int) -> Optional[dict]:
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT *
                FROM masters
                WHERE id = ?
                """,
                (master_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            profile = dict(row)
            profile["card_number"] = _decrypt_card_number(profile.get("card_number"))
            profile["services"] = _normalize_services(json.loads(profile.get("services_json") or "[]"))
            profile["schedule"] = json.loads(profile.get("schedule_json") or "[]")
            return profile

    @staticmethod
    def delete_master_profile(master_id: int, owner_telegram_id: int) -> bool:
        """Permanently delete an owned master profile and expire its holds."""
        now = _utc_now_str()
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN")
            cursor.execute(
                """
                DELETE FROM masters
                WHERE id = ? AND owner_telegram_id = ?
                """,
                (master_id, owner_telegram_id),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                return False
            cursor.execute(
                """
                UPDATE pending_payments
                SET status = 'expired', updated_at = ?
                WHERE master_telegram_id = ?
                  AND status IN ('creating', 'pending_payment', 'processing')
                """,
                (now, master_id),
            )
            cursor.execute(
                """
                UPDATE owner_active_profile
                SET active_master_id = NULL
                WHERE owner_telegram_id = ? AND active_master_id = ?
                """,
                (owner_telegram_id, master_id),
            )
            conn.commit()
            return True

    @staticmethod
    def get_master_profile(master_id: int) -> Optional[dict]:
        """Backward-compatible alias for profile-ID lookup."""
        return BookingDatabase.get_master_profile_by_id(master_id)

    @staticmethod
    def get_master_profiles_by_owner(owner_telegram_id: int) -> list[dict]:
        with BookingDatabase._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, owner_telegram_id, master_name, is_active, created_at
                FROM masters
                WHERE owner_telegram_id = ?
                ORDER BY is_active DESC, datetime(created_at) ASC, id ASC
                """,
                (owner_telegram_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def get_active_profile_id(owner_telegram_id: int) -> Optional[int]:
        with BookingDatabase._connect() as conn:
            row = conn.execute(
                "SELECT active_master_id FROM owner_active_profile WHERE owner_telegram_id = ?",
                (owner_telegram_id,),
            ).fetchone()
            return row["active_master_id"] if row and row["active_master_id"] is not None else None

    @staticmethod
    def set_active_profile_id(owner_telegram_id: int, master_id: Optional[int]) -> None:
        with BookingDatabase._connect() as conn:
            conn.execute(
                """
                INSERT INTO owner_active_profile (owner_telegram_id, active_master_id)
                VALUES (?, ?)
                ON CONFLICT(owner_telegram_id) DO UPDATE SET active_master_id = excluded.active_master_id
                """,
                (owner_telegram_id, master_id),
            )

    @staticmethod
    def get_master_owner(master_id: int) -> Optional[int]:
        with BookingDatabase._connect() as conn:
            row = conn.execute(
                "SELECT owner_telegram_id FROM masters WHERE id = ?",
                (master_id,),
            ).fetchone()
            return row["owner_telegram_id"] if row else None

    @staticmethod
    def deactivate_master_profile(master_id: int, owner_telegram_id: int) -> bool:
        with BookingDatabase._connect() as conn:
            cursor = conn.execute(
                "UPDATE masters SET is_active = 0, updated_at = ? WHERE id = ? AND owner_telegram_id = ?",
                (_utc_now_str(), master_id, owner_telegram_id),
            )
            return cursor.rowcount > 0

    @staticmethod
    def reactivate_master_profile(master_id: int, owner_telegram_id: int) -> bool:
        with BookingDatabase._connect() as conn:
            cursor = conn.execute(
                "UPDATE masters SET is_active = 1, updated_at = ? WHERE id = ? AND owner_telegram_id = ?",
                (_utc_now_str(), master_id, owner_telegram_id),
            )
            return cursor.rowcount > 0

    @staticmethod
    def upsert_master_profile(
        owner_telegram_id: int,
        master_name: str,
        services: list[dict],
        schedule: list[str],
        greeting_text: Optional[str] = None,
        is_active: bool = True,
        duration_minutes: int = 60,
        card_number: Optional[str] = None,
        master_id: Optional[int] = None,
    ) -> Optional[int]:
        encrypted_card_number = _encrypt_card_number(card_number)

        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            if master_id is not None:
                cursor.execute(
                    """
                    UPDATE masters
                    SET master_name = ?, services_json = ?, schedule_json = ?, greeting_text = ?,
                        is_active = ?, duration_minutes = ?, card_number = COALESCE(?, card_number),
                        updated_at = ?
                    WHERE id = ? AND owner_telegram_id = ?
                    """,
                    (
                        master_name,
                        json.dumps(_normalize_services(services), ensure_ascii=False),
                        json.dumps(schedule, ensure_ascii=False),
                        greeting_text,
                        1 if is_active else 0,
                        duration_minutes,
                        encrypted_card_number,
                        _utc_now_str(),
                        master_id,
                        owner_telegram_id,
                    ),
                )
                return master_id if cursor.rowcount > 0 else None

            cursor.execute(
                """
                INSERT INTO masters (
                    owner_telegram_id, master_name, services_json, schedule_json,
                    greeting_text, is_active, duration_minutes, card_number, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_telegram_id,
                    master_name,
                    json.dumps(_normalize_services(services), ensure_ascii=False),
                    json.dumps(schedule, ensure_ascii=False),
                    greeting_text,
                    1 if is_active else 0,
                    duration_minutes,
                    encrypted_card_number,
                    _utc_now_str(),
                    _utc_now_str(),
                ),
            )
            return cursor.lastrowid

    @staticmethod
    def get_all_bookings() -> List[Tuple]:
        """Return confirmed bookings for the admin utilities."""
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, full_name, phone_number, service, booking_date, booking_time, created_at
                FROM bookings
                ORDER BY booking_date, booking_time
                """
            )
            rows = cursor.fetchall()
            return [
                (
                    row["user_id"],
                    row["full_name"],
                    row["phone_number"],
                    row["service"],
                    row["booking_date"],
                    row["booking_time"],
                    row["created_at"],
                )
                for row in rows
            ]
