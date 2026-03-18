import os
import asyncio
import logging
import random
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
import sqlite3
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Настройки из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
YOUR_TON_WALLET = os.getenv("YOUR_TON_WALLET")

# TON настройки
MIN_DEPOSIT_TON = 0.1
MIN_WITHDRAW_TON = 0.2
TON_PRICE_USD = 5.5

# Реферальный бонус (10%)
REFERRAL_BONUS_PERCENT = 10

# Проверка наличия обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения")
if not ADMIN_ID:
    raise ValueError("ADMIN_ID не установлен в переменных окружения")
if not YOUR_TON_WALLET:
    raise ValueError("YOUR_TON_WALLET не установлен в переменных окружения")

# Инициализация
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())
scheduler = AsyncIOScheduler()

# База данных
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, 
                  username TEXT,
                  wallet_address TEXT,
                  deposit_ton REAL DEFAULT 0,
                  balance_ton REAL DEFAULT 0,
                  last_profit DATE,
                  total_earned_ton REAL DEFAULT 0,
                  referrer_id INTEGER,
                  referral_bonus_ton REAL DEFAULT 0,
                  registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS referrals
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  referrer_id INTEGER,
                  referral_id INTEGER,
                  referral_deposit_ton REAL DEFAULT 0,
                  bonus_paid_ton REAL DEFAULT 0,
                  date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS pending_deposits
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  amount_ton REAL,
                  tx_hash TEXT UNIQUE,
                  status TEXT DEFAULT 'pending',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS withdraw_requests
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  amount_ton REAL,
                  wallet_address TEXT,
                  status TEXT DEFAULT 'pending',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.commit()
    conn.close()

# Клавиатура
def main_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("💰 Баланс"))
    keyboard.add(KeyboardButton("📥 Пополнить"), KeyboardButton("📤 Вывести"))
    keyboard.add(KeyboardButton("📊 Статистика"), KeyboardButton("👥 Рефералы"))
    return keyboard

# Функция для форматирования TON
def format_ton(amount):
    return f"{amount:.2f} TON (≈${amount * TON_PRICE_USD:.2f})"

# Команда старт с реферальной системой
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    
    # Проверяем реферальный код
    referrer_id = None
    args = message.get_args()
    if args and args.isdigit():
        referrer_id = int(args)
        # Не даем стать рефералом самого себя
        if referrer_id == user_id:
            referrer_id = None
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    # Проверяем, существует ли пользователь
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    existing_user = c.fetchone()
    
    if not existing_user:
        # Новый пользователь
        c.execute("""INSERT INTO users 
                     (user_id, username, deposit_ton, balance_ton, last_profit, referrer_id) 
                     VALUES (?, ?, 0, 0, date('now'), ?)""",
                  (user_id, username, referrer_id))
        
        # Если есть реферер, записываем в таблицу рефералов
        if referrer_id:
            c.execute("""INSERT INTO referrals (referrer_id, referral_id) 
                        VALUES (?, ?)""", (referrer_id, user_id))
            
            # Отправляем уведомление рефереру
            try:
                await bot.send_message(
                    referrer_id,
                    f"🎉 По вашей реферальной ссылке зарегистрировался новый пользователь @{username}!\n"
                    f"Когда он пополнит баланс, вы получите {REFERRAL_BONUS_PERCENT}% бонус!"
                )
            except:
                pass
    
    conn.commit()
    conn.close()
    
    # Генерируем реферальную ссылку для пользователя
    bot_username = (await bot.me).username
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    
    welcome_text = (
        "👋 Добро пожаловать в TON Investment Bot!\n\n"
        f"📈 Ежедневное начисление: 2-4%\n"
        f"💰 Минимальный депозит: {MIN_DEPOSIT_TON} TON (≈${MIN_DEPOSIT_TON * TON_PRICE_USD:.2f})\n"
        f"💸 Минимальный вывод: {MIN_WITHDRAW_TON} TON (≈${MIN_WITHDRAW_TON * TON_PRICE_USD:.2f})\n"
        f"🎁 Реферальный бонус: {REFERRAL_BONUS_PERCENT}% с каждого пополнения реферала\n\n"
        "🔷 Все операции в TON (The Open Network)\n\n"
        "👇 Используй кнопки ниже для управления."
    )
    
    await message.reply(welcome_text, reply_markup=main_keyboard())

# Проверка баланса
@dp.message_handler(lambda message: message.text == "💰 Баланс")
async def show_balance(message: types.Message):
    user_id = message.from_user.id
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("""SELECT deposit_ton, balance_ton, total_earned_ton, referral_bonus_ton 
                 FROM users WHERE user_id = ?""", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result:
        deposit, balance, total_earned, referral_bonus = result
        total = deposit + balance
        
        await message.reply(
            f"📊 Ваш баланс в TON:\n\n"
            f"💰 Вклад: {format_ton(deposit)}\n"
            f"💵 Доступно: {format_ton(balance)}\n"
            f"📈 Общая сумма: {format_ton(total)}\n"
            f"🏆 Всего заработано: {format_ton(total_earned)}\n"
            f"🎁 Реферальные бонусы: {format_ton(referral_bonus)}\n\n"
            f"Курс TON: ${TON_PRICE_USD:.2f}"
        )

# ПОПОЛНЕНИЕ
@dp.message_handler(lambda message: message.text == "📥 Пополнить")
async def deposit(message: types.Message):
    user_id = message.from_user.id
    
    # Генерируем уникальный комментарий для транзакции
    comment = f"deposit_{user_id}_{random.randint(1000, 9999)}"
    
    # Сохраняем комментарий в базу
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("UPDATE users SET wallet_address = ? WHERE user_id = ?", (comment, user_id))
    conn.commit()
    conn.close()
    
    # Создаем кнопки
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("🔷 Копировать адрес", callback_data="copy_address"),
        InlineKeyboardButton("📱 Как пополнить?", callback_data="how_to_deposit")
    )
    
    await message.reply(
        f"📥 Пополнение баланса в TON\n\n"
        f"➡️ **Отправьте TON (минимум {MIN_DEPOSIT_TON} TON) на этот адрес:**\n"
        f"`{YOUR_TON_WALLET}`\n\n"
        f"📝 **Обязательно укажите комментарий:**\n"
        f"`{comment}`\n\n"
        f"⏳ После отправки ожидайте подтверждения (обычно 1-2 минуты)\n"
        f"💰 Минимальная сумма: {MIN_DEPOSIT_TON} TON (≈${MIN_DEPOSIT_TON * TON_PRICE_USD:.2f})",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

# Обработчик для копирования адреса
@dp.callback_query_handler(lambda c: c.data == "copy_address")
async def copy_address(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(
        callback_query.id,
        text="Адрес скопирован в буфер обмена",
        show_alert=False
    )
    
    await bot.send_message(
        callback_query.from_user.id,
        f"`{YOUR_TON_WALLET}`",
        parse_mode="Markdown"
    )

# Инструкция по пополнению
@dp.callback_query_handler(lambda c: c.data == "how_to_deposit")
async def how_to_deposit(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(
        callback_query.from_user.id,
        "📱 **Как пополнить через TON кошелек:**\n\n"
        "1. Откройте @wallet (официальный кошелек в Telegram)\n"
        "2. Нажмите «Перевести»\n"
        "3. Вставьте адрес:\n"
        f"`{YOUR_TON_WALLET}`\n"
        f"4. Введите сумму (минимум {MIN_DEPOSIT_TON} TON)\n"
        "5. **ВАЖНО:** В поле комментарий укажите код из сообщения выше\n"
        "6. Подтвердите перевод\n\n"
        "✅ Средства зачислятся автоматически после проверки",
        parse_mode="Markdown"
    )

# Вывод средств
@dp.message_handler(lambda message: message.text == "📤 Вывести")
async def withdraw(message: types.Message):
    await message.reply(
        f"📤 Вывод TON\n\n"
        f"Отправьте сумму и адрес кошелька в формате:\n"
        f"`сумма адрес`\n\n"
        f"Пример: `0.2 EQD4fp5zKjVLXw7k7R9qXq8kR9qXq8kR9qXq8`\n\n"
        f"Минимальная сумма: {MIN_WITHDRAW_TON} TON (≈${MIN_WITHDRAW_TON * TON_PRICE_USD:.2f})\n"
        f"Комиссия сети: 0.005 TON",
        parse_mode="Markdown"
    )

# РЕФЕРАЛЫ - просмотр информации
@dp.message_handler(lambda message: message.text == "👥 Рефералы")
async def referrals(message: types.Message):
    user_id = message.from_user.id
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    # Получаем количество рефералов
    c.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,))
    referrals_count = c.fetchone()[0]
    
    # Получаем сумму депозитов рефералов
    c.execute("SELECT SUM(referral_deposit_ton) FROM referrals WHERE referrer_id = ?", (user_id,))
    total_ref_deposit = c.fetchone()[0] or 0
    
    # Получаем сумму выплаченных бонусов
    c.execute("SELECT SUM(bonus_paid_ton) FROM referrals WHERE referrer_id = ?", (user_id,))
    total_bonus_paid = c.fetchone()[0] or 0
    
    # Получаем текущий реферальный бонус на балансе
    c.execute("SELECT referral_bonus_ton FROM users WHERE user_id = ?", (user_id,))
    current_bonus = c.fetchone()[0] or 0
    
    conn.close()
    
    # Генерируем реферальную ссылку
    bot_username = (await bot.me).username
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    
    await message.reply(
        f"👥 **Реферальная программа**\n\n"
        f"🔗 **Ваша ссылка:**\n`{referral_link}`\n\n"
        f"📊 **Статистика:**\n"
        f"• Приглашено друзей: {referrals_count}\n"
        f"• Общий депозит рефералов: {format_ton(total_ref_deposit)}\n"
        f"• Получено бонусов всего: {format_ton(total_bonus_paid)}\n"
        f"• Доступно бонусов: {format_ton(current_bonus)}\n\n"
        f"🎁 **Бонус:** {REFERRAL_BONUS_PERCENT}% от каждого пополнения реферала\n\n"
        f"💡 Бонусы начисляются автоматически при пополнении реферала и доступны для вывода!",
        parse_mode="Markdown"
    )

# Статистика
@dp.message_handler(lambda message: message.text == "📊 Статистика")
async def stats(message: types.Message):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("""SELECT COUNT(*), 
                        SUM(deposit_ton), 
                        SUM(balance_ton), 
                        SUM(total_earned_ton),
                        SUM(referral_bonus_ton)
                 FROM users""")
    result = c.fetchone()
    total_users, total_deposit, total_balance, total_earned, total_bonuses = result
    conn.close()
    
    await message.reply(
        f"📊 **Статистика бота:**\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"💰 Всего вкладов: {format_ton(total_deposit or 0)}\n"
        f"💵 Доступно средств: {format_ton(total_balance or 0)}\n"
        f"🏆 Всего заработано: {format_ton(total_earned or 0)}\n"
        f"🎁 Всего бонусов: {format_ton(total_bonuses or 0)}",
        parse_mode="Markdown"
    )

# Функция для обработки пополнения (вызывается админом вручную)
async def process_deposit(user_id, amount):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    # Обновляем баланс пользователя
    c.execute("UPDATE users SET deposit_ton = deposit_ton + ?, balance_ton = balance_ton + ? WHERE user_id = ?",
              (amount, amount, user_id))
    
    # Проверяем, есть ли у пользователя реферер
    c.execute("SELECT referrer_id FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    
    if result and result[0]:
        referrer_id = result[0]
        
        # Рассчитываем бонус (10%)
        bonus_amount = amount * (REFERRAL_BONUS_PERCENT / 100)
        
        # Начисляем бонус рефереру
        c.execute("UPDATE users SET referral_bonus_ton = referral_bonus_ton + ?, balance_ton = balance_ton + ? WHERE user_id = ?",
                  (bonus_amount, bonus_amount, referrer_id))
        
        # Обновляем информацию в таблице рефералов
        c.execute("""UPDATE referrals 
                    SET referral_deposit_ton = referral_deposit_ton + ?,
                        bonus_paid_ton = bonus_paid_ton + ?
                    WHERE referrer_id = ? AND referral_id = ?""",
                  (amount, bonus_amount, referrer_id, user_id))
        
        # Отправляем уведомление рефереру
        try:
            await bot.send_message(
                referrer_id,
                f"🎉 Ваш реферал пополнил баланс на {amount} TON!\n"
                f"💰 Вы получили бонус {bonus_amount:.2f} TON ({REFERRAL_BONUS_PERCENT}%)"
            )
        except:
            pass
    
    conn.commit()
    conn.close()
    
    # Уведомляем пользователя
    await bot.send_message(
        user_id,
        f"✅ Ваш счет пополнен на {amount} TON!\n"
        f"💰 Текущий баланс: {format_ton(amount)}"
    )

# Админ: ручное зачисление TON (с обработкой рефералов)
@dp.message_handler(commands=['add_ton'])
async def add_ton(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        _, user_id, amount = message.text.split()
        user_id = int(user_id)
        amount = float(amount)
        
        await process_deposit(user_id, amount)
        
        await message.reply(f"✅ Зачислено {amount} TON пользователю {user_id} (бонусы рефереру начислены)")
        
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}. Используйте: /add_ton user_id сумма")

# Вывод средств
@dp.message_handler()
async def handle_withdraw_request(message: types.Message):
    try:
        parts = message.text.split()
        if len(parts) >= 2:
            amount = float(parts[0].replace(',', '.'))
            wallet = parts[1]
            
            user_id = message.from_user.id
            
            if amount < MIN_WITHDRAW_TON:
                await message.reply(
                    f"❌ Минимальная сумма вывода: {MIN_WITHDRAW_TON} TON "
                    f"(≈${MIN_WITHDRAW_TON * TON_PRICE_USD:.2f})"
                )
                return
            
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute("SELECT balance_ton FROM users WHERE user_id = ?", (user_id,))
            balance = c.fetchone()[0]
            
            if balance < amount:
                await message.reply(f"❌ Недостаточно средств. Доступно: {format_ton(balance)}")
                conn.close()
                return
            
            c.execute("""INSERT INTO withdraw_requests 
                        (user_id, amount_ton, wallet_address, status) 
                        VALUES (?, ?, ?, 'pending')""",
                     (user_id, amount, wallet))
            
            request_id = c.lastrowid
            
            await bot.send_message(
                ADMIN_ID,
                f"💸 Запрос на вывод #{request_id}\n\n"
                f"Пользователь: @{message.from_user.username} (ID: {user_id})\n"
                f"Сумма: {amount} TON\n"
                f"Кошелек: {wallet}\n\n"
                f"Подтвердить: /approve_withdraw {request_id}\n"
                f"Отклонить: /reject_withdraw {request_id}"
            )
            
            conn.commit()
            conn.close()
            
            await message.reply(
                f"✅ Заявка на вывод {amount} TON создана\n"
                f"Ожидайте подтверждения (обычно до 24 часов)"
            )
            
    except ValueError:
        pass

# Админ: подтверждение вывода
@dp.message_handler(commands=['approve_withdraw'])
async def approve_withdraw(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        request_id = int(message.text.split()[1])
        
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        
        c.execute("""SELECT user_id, amount_ton FROM withdraw_requests 
                    WHERE id = ? AND status = 'pending'""", (request_id,))
        request = c.fetchone()
        
        if request:
            user_id, amount = request
            
            c.execute("SELECT balance_ton FROM users WHERE user_id = ?", (user_id,))
            balance = c.fetchone()[0]
            
            if balance >= amount:
                c.execute("UPDATE users SET balance_ton = balance_ton - ? WHERE user_id = ?",
                         (amount, user_id))
                c.execute("UPDATE withdraw_requests SET status = 'approved' WHERE id = ?",
                         (request_id,))
                conn.commit()
                
                await bot.send_message(
                    user_id,
                    f"✅ Ваш вывод {amount} TON подтвержден!"
                )
                await message.reply(f"✅ Вывод #{request_id} подтвержден")
            else:
                await message.reply(f"❌ Недостаточно средств")
        else:
            await message.reply(f"❌ Заявка не найдена")
        
        conn.close()
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

# Админ: отклонение вывода
@dp.message_handler(commands=['reject_withdraw'])
async def reject_withdraw(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        request_id = int(message.text.split()[1])
        
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("UPDATE withdraw_requests SET status = 'rejected' WHERE id = ?", (request_id,))
        c.execute("SELECT user_id, amount_ton FROM withdraw_requests WHERE id = ?", (request_id,))
        user_id, amount = c.fetchone()
        conn.commit()
        conn.close()
        
        await bot.send_message(
            user_id,
            f"❌ Ваш вывод {amount} TON отклонен"
        )
        await message.reply(f"✅ Вывод #{request_id} отклонен")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

# Ежедневное начисление процентов
async def daily_profit():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT user_id, deposit_ton, balance_ton FROM users")
    users = c.fetchall()
    
    for user_id, deposit, balance in users:
        if deposit > 0:
            profit_percent = random.uniform(2.0, 4.0)
            profit = deposit * (profit_percent / 100)
            new_balance = balance + profit
            c.execute("""UPDATE users 
                        SET balance_ton = ?, 
                            last_profit = date('now'),
                            total_earned_ton = total_earned_ton + ?
                        WHERE user_id = ?""",
                      (new_balance, profit, user_id))
            
            try:
                await bot.send_message(
                    user_id,
                    f"📈 Ежедневное начисление!\n\n"
                    f"Ставка: {profit_percent:.1f}%\n"
                    f"Начислено: {profit:.2f} TON\n"
                    f"Текущий баланс: {new_balance:.2f} TON"
                )
            except:
                pass
    
    conn.commit()
    conn.close()
    await bot.send_message(ADMIN_ID, "✅ Ежедневное начисление выполнено")

# Команда для ручного запуска начисления
@dp.message_handler(commands=['run_profit'])
async def run_profit(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await daily_profit()
        await message.reply("✅ Начисление запущено вручную")

# Команда для установки курса TON
@dp.message_handler(commands=['set_ton_price'])
async def set_ton_price(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        global TON_PRICE_USD
        new_price = float(message.text.split()[1])
        TON_PRICE_USD = new_price
        await message.reply(f"✅ Курс TON установлен: ${TON_PRICE_USD}")
    except:
        await message.reply("❌ Использование: /set_ton_price 5.5")

# Запуск
if __name__ == '__main__':
    init_db()
    scheduler.add_job(daily_profit, 'cron', hour=0, minute=0)
    scheduler.start()
    
    print("🚀 TON Investment Bot запущен...")
    print(f"Минимальный депозит: {MIN_DEPOSIT_TON} TON")
    print(f"Минимальный вывод: {MIN_WITHDRAW_TON} TON")
    print(f"Ежедневный процент: 2-4%")
    print(f"Реферальный бонус: {REFERRAL_BONUS_PERCENT}%")
    executor.start_polling(dp, skip_updates=True)
