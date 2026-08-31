import sqlite3
import json
from datetime import datetime, timezone
from uuid import uuid4
from typing import List, Optional, Tuple

DATABASE_FILE = "bookings.db"
ACTIVE_PAYMENT_STATUSES = ("creating", "pending_payment", "processing")


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class BookingDatabase:
    """SQLite layer for confirmed bookings and pending payment reservations."""

    @staticmethod
    def _connect() -> sqlite3.Connection:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
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
                    master_telegram_id INTEGER,
                    full_name TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    service TEXT NOT NULL,
                    booking_date TEXT NOT NULL,
                    booking_time TEXT NOT NULL,
                    payment_invoice_id TEXT,
                    payment_provider TEXT DEFAULT 'wayforpay',
                    payment_amount INTEGER DEFAULT 200,
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
                    "status": "TEXT",
                    "expires_at": "TEXT",
                    "booking_id": "INTEGER",
                    "created_at": "TEXT",
                    "updated_at": "TEXT",
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
                    telegram_id INTEGER PRIMARY KEY,
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

            BookingDatabase._ensure_columns(
                cursor,
                "masters",
                {
                    "duration_minutes": "INTEGER DEFAULT 60",
                },
            )

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_masters_active
                ON masters (is_active)
                """
            )

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

        logger = __import__("logging").getLogger(__name__)
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
            )
            SELECT
                id, user_id, COALESCE(master_telegram_id, 0), full_name, phone_number, service,
                booking_date, booking_time, payment_invoice_id, payment_provider,
                payment_amount, payment_confirmed_at, created_at
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

        logger = __import__("logging").getLogger(__name__)
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
        master_telegram_id: Optional[int] = None,
    ) -> Optional[str]:
        """Create a temporary hold for a slot before the payment is created."""
        if not request_id:
            request_id = f"wp_{uuid4().hex[:24]}"
        if not expires_at:
            expires_at = _utc_now_str()

        try:
            with BookingDatabase._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO pending_payments (
                        request_id, user_id, master_telegram_id, full_name, phone_number, service,
                        booking_date, booking_time, payment_provider, amount,
                        status, expires_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'creating', ?, ?, ?)
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
                        expires_at,
                        _utc_now_str(),
                        _utc_now_str(),
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
            return dict(row) if row else None

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
            return dict(row) if row else None

    @staticmethod
    def mark_payment_status(invoice_id: str, status: str) -> bool:
        """Update payment status without finalizing the booking."""
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

    @staticmethod
    def update_pending_status_by_request(request_id: str, status: str) -> bool:
        """Update the status of a reservation before the invoice exists or when it fails."""
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

    @staticmethod
    def delete_pending_by_request(request_id: str) -> bool:
        """Remove a temporary hold entirely."""
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

    @staticmethod
    def finalize_booking_from_payment(invoice_id: str) -> Optional[int]:
        """Move a paid pending reservation into the confirmed bookings table."""
        pending = BookingDatabase.get_pending_payment_by_invoice(invoice_id)
        if not pending:
            return None

        if pending["status"] == "paid":
            return None

        booking_payload = (
            pending["user_id"],
            pending.get("master_telegram_id") or 0,
            pending["full_name"],
            pending["phone_number"],
            pending["service"],
            pending["booking_date"],
            pending["booking_time"],
            pending["payment_invoice_id"],
            pending["payment_provider"],
            pending["amount"],
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
                        payment_provider, payment_amount, payment_confirmed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    booking_payload,
                )
                booking_id = cursor.lastrowid

                cursor.execute(
                    """
                    UPDATE pending_payments
                    SET status = 'paid',
                        booking_id = ?,
                        updated_at = ?
                    WHERE payment_invoice_id = ?
                    """,
                    (booking_id, _utc_now_str(), invoice_id),
                )

                conn.commit()
            return booking_id
        except sqlite3.IntegrityError:
            with BookingDatabase._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE pending_payments
                    SET status = 'conflict',
                        updated_at = ?
                    WHERE payment_invoice_id = ?
                    """,
                    (_utc_now_str(), invoice_id),
                )
            return None
        except sqlite3.Error:
            return None

    @staticmethod
    def cancel_booking(booking_id: int, master_telegram_id: int) -> Optional[dict]:
        """Delete a booking only if it belongs to the given master."""
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, user_id, full_name, phone_number, service, booking_date, booking_time
                FROM bookings
                WHERE id = ? AND master_telegram_id = ?
                """,
                (booking_id, master_telegram_id),
            )
            row = cursor.fetchone()
            if not row:
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
                        payment_provider, payment_amount, payment_confirmed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        payment_confirmed_at,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

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
    def get_master_profile(master_telegram_id: int) -> Optional[dict]:
        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT *
                FROM masters
                WHERE telegram_id = ? AND is_active = 1
                """,
                (master_telegram_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            profile = dict(row)
            profile["services"] = json.loads(profile.get("services_json") or "[]")
            profile["schedule"] = json.loads(profile.get("schedule_json") or "[]")
            return profile

    @staticmethod
    def upsert_master_profile(
        master_telegram_id: int,
        master_name: str,
        services: list[str],
        schedule: list[str],
        greeting_text: Optional[str] = None,
        is_active: bool = True,
        duration_minutes: int = 60,
    ) -> bool:
        payload = (
            master_telegram_id,
            master_name,
            json.dumps(services, ensure_ascii=False),
            json.dumps(schedule, ensure_ascii=False),
            greeting_text,
            1 if is_active else 0,
            duration_minutes,
            _utc_now_str(),
            _utc_now_str(),
        )

        with BookingDatabase._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO masters (
                    telegram_id, master_name, services_json, schedule_json,
                    greeting_text, is_active, duration_minutes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    master_name = excluded.master_name,
                    services_json = excluded.services_json,
                    schedule_json = excluded.schedule_json,
                    greeting_text = excluded.greeting_text,
                    is_active = excluded.is_active,
                    duration_minutes = excluded.duration_minutes,
                    updated_at = excluded.updated_at
                """,
                payload,
            )
        return True

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
