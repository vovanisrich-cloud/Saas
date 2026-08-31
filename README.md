# 🌸 Telegram Bot для бьюти-мастеров

Автоматизированный бот для записи клиентов на услуги красоты на украинском языке с календарем и системой управления слотами времени.

## 📋 Возможности

- ✅ Приветствие на украинском языке
- ✅ FSM (машина состояний) для управления диалогом
- ✅ Пошаговый сбор данных клиента:
  - Имя и фамилия
  - Номер телефона (через нативную клавиатуру)
  - Выбор услуги (манікюр, педикюр, комплекс)
  - 📅 **Выбор даты из календаря (ближайшие 7 дней)**
  - 🕐 **Выбор времени (10:00, 12:00, 14:00, 16:00)**
- ✅ Подтверждение данных перед отправкой
- ✅ **SQLite БД для управления занятыми слотами**
- ✅ **Автоматическое скрытие занятых слотов**
- ✅ MemoryStorage для временного хранения данных
- ✅ Интуитивный пользовательский интерфейс

## 🚀 Установка и запуск

### 1. Клонируйте репозиторий или создайте проект

```bash
cd d:\MicroSAAS
```

### 2. Активируйте виртуальную среду

**На Windows:**

```bash
.venv\Scripts\activate
```

**На Linux/Mac:**

```bash
source .venv/bin/activate
```

### 3. Установите зависимости

```bash
pip install -r requirements.txt
```

### 4. Настройте токен бота

Создайте файл `.env` на основе `.env.example`:

```bash
copy .env.example .env
```

Отредактируйте `.env` и добавьте ваш токен Telegram:

```
TELEGRAM_BOT_TOKEN=your_actual_bot_token_here
```

### Payments setup

To enable deposits via WayForPay, add these variables too:

```env
WAYFORPAY_MERCHANT_ACCOUNT=test_merch_n1
WAYFORPAY_SECRET_KEY=your_wayforpay_secret_key
WAYFORPAY_DOMAIN_NAME=your-domain-or-localhost
WAYFORPAY_SERVICE_URL=https://your-domain.com/payments/wayforpay/your-secret-path
PAYMENT_WEBHOOK_SECRET=your-secret-path
APP_HOST=0.0.0.0
APP_PORT=8080
```

The bot now works in two steps:

1. The client selects a time slot.
2. The bot sends `Для підтвердження запису внесіть передоплату 200 грн` and shows a payment button.
3. The slot is saved in the database only after WayForPay reports `Approved` via webhook or status check.

### Reservation TTL

`RESERVATION_TTL_MINUTES` controls how long a slot is held without payment. Default: `30`.

```env
RESERVATION_TTL_MINUTES=30
```

This value is used for:
- the temporary hold in `pending_payments` (`reservation_expires_at`);
- the WayForPay invoice timeout (`orderTimeout`).

After the TTL expires, the slot becomes available again automatically.

**Как получить токен:**

1. Откройте Telegram и найдите бота @BotFather
2. Напишите `/newbot`
3. Следуйте инструкциям
4. Скопируйте полученный токен в `.env`

### 5. Запустите бота

```bash
python main.py
```

## 📱 Использование

1. Откройте Telegram и найдите вашего бота
2. Нажмите `/start`
3. Нажмите кнопку "📅 Записатися на послугу"
4. Введите имя и фамилию
5. Поделитесь номером телефона
6. Выберите услугу из предложенных
7. **Выберите дату из календаря (ближайшие 7 дней)**
8. **Выберите свободное время (система скрывает занятые слоты)**
9. Подтвердите данные
10. Готово! Бот отправит подтверждение

## 🏗️ Структура проекта

```
MicroSAAS/
├── main.py              # Основной файл бота с FSM
├── database.py          # Работа с SQLite БД (управление слотами)
├── manage_bookings.py   # Утилита для просмотра и управления бронированиями
├── examples.py          # Примеры использования API database.py
├── requirements.txt     # Зависимости
├── .env                 # Конфигурация (не добавляйте в git)
├── .env.example         # Пример конфигурации
├── bookings.db          # SQLite БД с занятыми слотами (создается автоматически)
└── README.md           # Этот файл
```

## 🔧 Технологический стек

- **aiogram 3.3.0** - Асинхронный фреймворк для Telegram API
- **Python 3.8+** - Язык программирования
- **SQLite** - Встроенная база данных для хранения слотов
- **MemoryStorage** - Встроенное хранилище FSM

## 📚 Состояния FSM

Бот использует следующие состояния:

1. `waiting_for_name` - Ожидание ввода имени
2. `waiting_for_phone` - Ожидание получения номера телефона
3. `waiting_for_service` - Ожидание выбора услуги
4. **`waiting_for_date` - Ожидание выбора даты**
5. **`waiting_for_time` - Ожидание выбора времени**
6. `confirmation` - Ожидание подтверждения данных

## 🎯 Расширение функциональности

### 📅 Управление слотами времени

**Изменить доступные слоты:**

Отредактируйте функцию `get_available_times()` в [database.py](database.py):

```python
def get_available_times(booking_date: str) -> List[str]:
    available_slots = ["10:00", "12:00", "14:00", "16:00", "18:00"]  # Добавьте нужные слоты
    # ...остальной код...
```

**Просмотр всех бронирований:**

```python
from database import BookingDatabase

bookings = BookingDatabase.get_all_bookings()
for booking in bookings:
    print(booking)  # (user_id, full_name, phone, service, date, time, created_at)
```

**Проверить доступность слота:**

```python
is_available = BookingDatabase.is_slot_available("2026-09-01", "10:00")
```

### Добавление новых услуг

Отредактируйте функцию `get_services_keyboard()`:

```python
def get_services_keyboard():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💅 Манікюр", callback_data="service_manicure")],
            [InlineKeyboardButton(text="🦶 Педикюр", callback_data="service_pedicure")],
            [InlineKeyboardButton(text="✨ Комплекс", callback_data="service_complex")],
            [InlineKeyboardButton(text="🎨 Нова послуга", callback_data="service_new")]  # Новая услуга
        ]
    )
    return keyboard
```

И добавьте в `process_service()`:

```python
services = {
    "service_manicure": "💅 Манікюр",
    "service_pedicure": "🦶 Педикюр",
    "service_complex": "✨ Комплекс",
    "service_new": "🎨 Нова послуга"  # Новая услуга
}
```

### Сохранение данных в базу

Для сохранения данных в БД замените MemoryStorage на другое хранилище:

```python
# Вместо MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

storage = RedisStorage.from_url("redis://localhost")
```

## ⚠️ Важные замечания

- **MemoryStorage** хранит данные только в памяти приложения (теряются при перезагрузке)
- **SQLite БД** (`bookings.db`) хранит все бронирования постоянно на диске
- Для продакшена используйте Redis вместо MemoryStorage для более надежного хранения сессий
- Убедитесь, что токен не попадает в систему контроля версий (добавьте `.env` в `.gitignore`)
- БД автоматически инициализируется при запуске бота

## � Примеры использования API

Для изучения полного API посмотрите файл [examples.py](examples.py):

```bash
python examples.py
```

Примеры включают:

- ✅ Проверку доступности слотов
- ✅ Получение расписания на день
- ✅ Фильтрацию бронирований по дате и услуге
- ✅ Статистику по услугам
- ✅ Предложение доступных дат

## �🛠️ Управление бронированиями

Используйте утилиту `manage_bookings.py` для просмотра и управления заказами:

**Просмотр всех бронирований:**

```bash
python manage_bookings.py all
```

**Просмотр бронирований на конкретную дату:**

```bash
python manage_bookings.py date 2026-09-01
```

**Проверить свободные слоты на дату:**

```bash
python manage_bookings.py check 2026-09-01
```

**Удалить бронирование:**

```bash
python manage_bookings.py delete 2026-09-01 10:00
```

## 🛠️ Резервное копирование БД

Для резервного копирования бронирований скопируйте файл `bookings.db`:

```powershell
copy bookings.db bookings_backup.db
```

Для восстановления:

```powershell
copy bookings_backup.db bookings.db
```

## 📞 Поддержка

Если у вас есть вопросы, добавьте логирование для отладки:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## 📄 Лицензия

Этот проект лицензирован под MIT License.

---

Успехов в развитии вашего сервиса красоты! 💕
