"""
Примеры использования database.py API

Этот файл содержит примеры кода для работы с бронированиями
"""

from database import BookingDatabase
from datetime import datetime, timedelta

# Инициализируем БД (это происходит автоматически в main.py)
BookingDatabase.init_db()

# ===== ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ =====

# 1. Проверить, доступен ли конкретный слот
print("1️⃣ Проверка доступности слота:")
date_str = "2026-09-01"
time_str = "10:00"
is_available = BookingDatabase.is_slot_available(date_str, time_str)
print(f"   Слот {date_str} в {time_str} доступен: {is_available}\n")


# 2. Получить список доступных слотов на дату
print("2️⃣ Получить доступные слоты на дату:")
available_times = BookingDatabase.get_available_times(date_str)
print(f"   Доступные слоты на {date_str}: {available_times}\n")


# 3. Сохранить новое бронирование
print("3️⃣ Сохранить новое бронирование:")
user_id = 123456789
full_name = "Ольга Петренко"
phone_number = "+380671234567"
service = "💅 Манікюр"
booking_date = "2026-09-05"
booking_time = "14:00"

success = BookingDatabase.save_booking(
    user_id=user_id,
    full_name=full_name,
    phone_number=phone_number,
    service=service,
    booking_date=booking_date,
    booking_time=booking_time
)

if success:
    print(f"   ✅ Бронирование сохранено!")
    print(f"      {full_name} - {service}")
    print(f"      {booking_date} в {booking_time}\n")
else:
    print(f"   ❌ Ошибка: слот уже занят\n")


# 4. Получить все бронирования
print("4️⃣ Получить все бронирования:")
all_bookings = BookingDatabase.get_all_bookings()
print(f"   Всего бронирований: {len(all_bookings)}")
for booking in all_bookings[:3]:  # Показываем первые 3
    user_id, full_name, phone, service, date, time, created_at = booking
    print(f"   • {full_name} ({service}) - {date} {time}")
print()


# 5. Фильтрация бронирований по дате
print("5️⃣ Фильтрация по дате:")
target_date = "2026-09-05"
filtered = [b for b in all_bookings if b[4] == target_date]
print(f"   Бронирований на {target_date}: {len(filtered)}")
for booking in filtered:
    user_id, full_name, phone, service, date, time, created_at = booking
    print(f"   • {time}: {full_name} - {service}")
print()


# 6. Фильтрация по услуге
print("6️⃣ Фильтрация по услуге:")
service_filter = "💅 Манікюр"
filtered = [b for b in all_bookings if b[3] == service_filter]
print(f"   Бронирований услуги '{service_filter}': {len(filtered)}")
for booking in filtered[:3]:  # Показываем первые 3
    user_id, full_name, phone, service, date, time, created_at = booking
    print(f"   • {date} {time}: {full_name}")
print()


# 7. Получить расписание на день
print("7️⃣ Расписание на день:")
schedule_date = "2026-09-05"
day_bookings = [b for b in all_bookings if b[4] == schedule_date]
time_slots = ["10:00", "12:00", "14:00", "16:00"]

print(f"   Расписание на {schedule_date}:")
for slot in time_slots:
    booking = next((b for b in day_bookings if b[5] == slot), None)
    if booking:
        user_id, full_name, phone, service, date, time, created_at = booking
        print(f"   🔴 {slot}: {full_name} - {service}")
    else:
        print(f"   🟢 {slot}: [Свободно]")
print()


# 8. Статистика использования услуг
print("8️⃣ Статистика по услугам:")
services_count = {}
for booking in all_bookings:
    service = booking[3]
    services_count[service] = services_count.get(service, 0) + 1

for service, count in services_count.items():
    print(f"   {service}: {count} бронирований")
print()


# 9. Предложение доступных дат
print("9️⃣ Доступные даты на ближайшие 7 дней:")
from datetime import datetime, timedelta
today = datetime.now()
for i in range(7):
    date_obj = today + timedelta(days=i)
    date_str = date_obj.strftime("%Y-%m-%d")
    available_times = BookingDatabase.get_available_times(date_str)
    is_full = len(available_times) == 0
    status = "❌ Полностью занято" if is_full else f"✅ {len(available_times)} слотов"
    date_display = date_obj.strftime("%a, %d.%m")
    print(f"   {date_display}: {status}")
print()


print("=" * 50)
print("ℹ️  Для использования этого кода в своих скриптах:")
print("   from database import BookingDatabase")
print("=" * 50)
