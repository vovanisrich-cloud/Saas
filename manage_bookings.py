#!/usr/bin/env python3
"""
Utility for viewing and deleting bookings.
"""

import argparse
import sqlite3

from tabulate import tabulate

from database import BookingDatabase, DATABASE_FILE


def view_all_bookings():
    bookings = BookingDatabase.get_all_bookings()
    if not bookings:
        print("❌ No bookings found")
        return

    headers = ["ID", "Name", "Phone", "Service", "Date", "Time", "Created"]
    data = []
    for i, booking in enumerate(bookings, 1):
        user_id, full_name, phone_number, service, booking_date, booking_time, created_at = booking
        data.append([i, full_name, phone_number, service, booking_date, booking_time, created_at[:10]])

    print("\n📋 ALL BOOKINGS\n")
    print(tabulate(data, headers=headers, tablefmt="grid"))
    print(f"\n✅ Total bookings: {len(bookings)}\n")


def view_by_date(date_str: str):
    bookings = BookingDatabase.get_all_bookings()
    filtered = [b for b in bookings if b[4] == date_str]

    if not filtered:
        print(f"❌ No bookings for {date_str}")
        return

    headers = ["Name", "Phone", "Service", "Time"]
    data = [[b[1], b[2], b[3], b[5]] for b in filtered]
    print(f"\n📅 BOOKINGS FOR {date_str}\n")
    print(tabulate(data, headers=headers, tablefmt="grid"))
    print(f"\n✅ Total for date: {len(filtered)}\n")


def delete_booking(booking_date: str, booking_time: str):
    """Delete a confirmed booking and any matching pending hold for the same slot."""
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM bookings WHERE booking_date = ? AND booking_time = ?",
            (booking_date, booking_time),
        )
        bookings_deleted = cursor.rowcount

        cursor.execute(
            "DELETE FROM pending_payments WHERE booking_date = ? AND booking_time = ?",
            (booking_date, booking_time),
        )
        pending_deleted = cursor.rowcount

        conn.commit()

    if bookings_deleted or pending_deleted:
        print(
            f"✅ Removed slot {booking_date} {booking_time} "
            f"(bookings: {bookings_deleted}, pending: {pending_deleted})"
        )
    else:
        print("❌ Booking not found")


def check_availability(booking_date: str):
    available = BookingDatabase.get_available_times(booking_date)

    print(f"\n✅ AVAILABLE SLOTS FOR {booking_date}\n")
    if not available:
        print("❌ No available slots")
    else:
        for slot in available:
            print(f"  🕒 {slot}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Manage bookings")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    subparsers.add_parser("all", help="Show all bookings")

    view_date_parser = subparsers.add_parser("date", help="Show bookings for a date")
    view_date_parser.add_argument("date", help="Date (YYYY-MM-DD)")

    delete_parser = subparsers.add_parser("delete", help="Delete a booking")
    delete_parser.add_argument("date", help="Date (YYYY-MM-DD)")
    delete_parser.add_argument("time", help="Time (HH:MM)")

    check_parser = subparsers.add_parser("check", help="Check free slots")
    check_parser.add_argument("date", help="Date (YYYY-MM-DD)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    if args.command == "all":
        view_all_bookings()
    elif args.command == "date":
        view_by_date(args.date)
    elif args.command == "delete":
        delete_booking(args.date, args.time)
    elif args.command == "check":
        check_availability(args.date)


if __name__ == "__main__":
    main()
