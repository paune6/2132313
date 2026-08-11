import asyncio
import datetime
import logging
import random
import os
import sys
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, ChatTypeFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
import aiosqlite

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")

BOT_NAME = "💰 Dabloons"
CURRENCY_NAME = "Dabloons"
CURRENCY_SHORT = "DBL"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "wallet.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)
# ===================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ========== ХРАНИЛИЩЕ ДЛЯ ИГР ==========
active_games = {}

# ========== РАБОТА С БАЗОЙ (с обработкой ошибок) ==========
async def init_db():
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    balance INTEGER DEFAULT 1000,
                    level INTEGER DEFAULT 1,
                    last_daily DATE
                )
            """)
            await db.commit()
        logger.info(f"База данных инициализирована: {DB_NAME}")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")

async def get_user(user_id: int):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute(
                "SELECT user_id, username, balance, level, last_daily FROM users WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                return await cursor.fetchone()
    except Exception as e:
        logger.error(f"Ошибка get_user: {e}")
        return None

async def create_user(user_id: int, username: str):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO users (user_id, username, balance, last_daily) VALUES (?, ?, ?, ?)",
                (user_id, username or "NoName", 1000, None)
            )
            await db.commit()
        logger.info(f"Создан новый пользователь: {user_id} (@{username})")
    except Exception as e:
        logger.error(f"Ошибка create_user: {e}")

async def ensure_user_exists(user_id: int, username: str):
    user = await get_user(user_id)
    if not user:
        await create_user(user_id, username)
        user = await get_user(user_id)
    return user

async def update_balance(user_id: int, amount: int):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (amount, user_id)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка update_balance: {e}")

async def get_top_users(limit: int = 10):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute(
                "SELECT username, balance FROM users ORDER BY balance DESC LIMIT ?",
                (limit,)
            ) as cursor:
                return await cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка get_top_users: {e}")
        return []

async def get_daily_status(user_id: int):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute(
                "SELECT last_daily FROM users WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    return datetime.date.fromisoformat(row[0])
                return None
    except Exception as e:
        logger.error(f"Ошибка get_daily_status: {e}")
        return None

async def set_daily(user_id: int, date: datetime.date):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE users SET last_daily = ? WHERE user_id = ?",
                (date.isoformat(), user_id)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка set_daily: {e}")

async def get_user_by_username(username: str):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute(
                "SELECT user_id FROM users WHERE username = ?",
                (username,)
            ) as cursor:
                return await cursor.fetchone()
    except Exception as e:
        logger.error(f"Ошибка get_user_by_username: {e}")
        return None
# ===========================================

# ========== КЛАВИАТУРЫ ==========
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
         InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton(text="🏆 Топ", callback_data="top"),
         InlineKeyboardButton(text="🎁 Бонус", callback_data="daily")],
        [InlineKeyboardButton(text="⚔️ Дуэль", callback_data="duel"),
         InlineKeyboardButton(text="💣 Мины", callback_data="mines")],
        [InlineKeyboardButton(text="🃏 Джокер", callback_data="joker")]
    ])

def generate_mines_keyboard(user_id, game_data):
    total = game_data['total']
    opened = game_data['opened']
    mines = game_data['mines']
    buttons = []
    row = []
    for i in range(1, total + 1):
        if i in opened:
            if i in mines:
                label = "💣"
            else:
                label = "✅"
        else:
            label = str(i)
        row.append(InlineKeyboardButton(text=label, callback_data=f"mines_cell_{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="💰 Забрать выигрыш", callback_data="mines_cashout")])
    buttons.append([InlineKeyboardButton(text="❌ Выйти", callback_data="mines_quit")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
# ================================

# ========== ОБЩАЯ ЛОГИКА ЗАПУСКА МИН ==========
async def start_mines(message: Message, args: list):
    user = await ensure_user_exists(message.from_user.id, message.from_user.username)
    if not user:
        await message.answer("❌ Ошибка регистрации. Попробуйте позже.")
        return

    if len(args) == 0:
        await message.answer(
            "❗ Используй: `.мины (ставка) (количество мин)`\n"
            "Пример: `.мины 100 8`\n"
            "Если не указать количество мин, будет 8 (можно от 1 до 10).",
            parse_mode="Markdown"
        )
        return

    try:
        bet = int(args[0])
    except ValueError:
        await message.answer("❌ Ставка должна быть числом.")
        return

    if bet <= 0:
        await message.answer("❌ Ставка должна быть положительной.")
        return
    if user[2] < bet:
        await message.answer(f"❌ Недостаточно средств. У тебя {user[2]} {CURRENCY_SHORT}.")
        return

    if len(args) >= 2:
        try:
            mines_count = int(args[1])
        except ValueError:
            await message.answer("❌ Количество мин должно быть числом.")
            return
        if mines_count < 1 or mines_count > 10:
            await message.answer("❌ Количество мин должно быть от 1 до 10.")
            return
    else:
        mines_count = 8

    total_cells = 25
    all_positions = set(range(1, total_cells + 1))
    mine_positions = set(random.sample(list(all_positions), mines_count))

    game_data = {
        'mines': mine_positions,
        'opened': set(),
        'bet': bet,
        'multiplier': 1.0,
        'total': total_cells,
        'mine_count': mines_count,
        'safe_count': total_cells - mines_count,
        'user_id': message.from_user.id
    }
    active_games[message.from_user.id] = game_data

    await update_balance(user[0], -bet)

    await message.answer(
        f"💣 Игра началась!\n"
        f"Ставка: {bet} {CURRENCY_SHORT}\n"
        f"Мин: {mines_count}\n"
        f"Выбери ячейку (1-25). Если откроешь безопасную, множитель растёт.\n"
        f"Текущий множитель: x{game_data['multiplier']:.2f}\n"
        f"Потенциальный выигрыш: {int(bet * game_data['multiplier'])} {CURRENCY_SHORT}",
        reply_markup=generate_mines_keyboard(message.from_user.id, game_data)
    )
# ===============================================

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = await ensure_user_exists(message.from_user.id, message.from_user.username)
    if not user:
        await message.answer("❌ Ошибка регистрации. Попробуйте позже.")
        return
    await message.answer(
        f"🎉 Добро пожаловать в {BOT_NAME}!\n"
        f"Твой баланс: {user[2]} {CURRENCY_SHORT}.\n"
        "Используй меню или /help для списка команд.",
        reply_markup=main_menu()
    )

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    user = await ensure_user_exists(message.from_user.id, message.from_user.username)
    if not user:
        await message.answer("❌ Ошибка. Попробуйте позже.")
        return
    daily_status = "получен сегодня" if await get_daily_status(user[0]) == datetime.date.today() else "ещё не получен"
    text = (
        f"👤 <b>Твой профиль</b>\n"
        f"ID: {user[0]}\n"
        f"Имя: @{user[1] or 'нет'}\n"
        f"💰 Баланс: {user[2]} {CURRENCY_SHORT}\n"
        f"📈 Уровень: {user[3]}\n"
        f"📅 Бонус: {daily_status}"
    )
    await message.answer(text)

@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    user = await ensure_user_exists(message.from_user.id, message.from_user.username)
    if not user:
        await message.answer("❌ Ошибка. Попробуйте позже.")
        return
    await message.answer(f"💰 Твой баланс: <b>{user[2]}</b> {CURRENCY_SHORT}.")

@dp.message(Command("daily"))
async def cmd_daily(message: Message):
    user = await ensure_user_exists(message.from_user.id, message.from_user.username)
    if not user:
        await message.answer("❌ Ошибка. Попробуйте позже.")
        return

    today = datetime.date.today()
    last = await get_daily_status(user[0])
    if last == today:
        await message.answer("⏳ Ты уже получил бонус сегодня! Возвращайся завтра.")
        return

    bonus = 100 + (user[3] - 1) * 10
    await update_balance(user[0], bonus)
    await set_daily(user[0], today)
    await message.answer(f"🎁 Ты получил ежедневный бонус: {bonus} {CURRENCY_SHORT}!")

@dp.message(Command("send"))
async def cmd_send(message: Message):
    sender = await ensure_user_exists(message.from_user.id, message.from_user.username)
    if not sender:
        await message.answer("❌ Ошибка. Попробуйте позже.")
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "❗ Используй: `/send 100 @username`\nПример: `/send 50 @ivan`",
            parse_mode="Markdown"
        )
        return

    try:
        amount = int(args[1])
    except ValueError:
        await message.answer("❌ Сумма должна быть числом.")
        return

    if amount <= 0:
        await message.answer("❌ Сумма должна быть положительной.")
        return

    recipient_str = args[2].strip()
    if not recipient_str.startswith("@"):
        await message.answer("❌ Укажи получателя в формате @username")
        return

    username = recipient_str[1:]
    recipient = await get_user_by_username(username)
    if not recipient:
        await message.answer(f"❌ Пользователь @{username} не найден в базе.")
        return

    if sender[0] == recipient[0]:
        await message.answer("❌ Нельзя переводить самому себе.")
        return

    if sender[2] < amount:
        await message.answer(f"❌ Недостаточно средств. У тебя {sender[2]} {CURRENCY_SHORT}.")
        return

    await update_balance(sender[0], -amount)
    await update_balance(recipient[0], amount)

    await message.answer(
        f"✅ Перевод <b>{amount}</b> {CURRENCY_SHORT} пользователю @{username} выполнен!\n"
        f"Твой новый баланс: {sender[2] - amount} {CURRENCY_SHORT}."
    )

@dp.message(Command("top"))
async def cmd_top(message: Message):
    top = await get_top_users(10)
    if not top:
        await message.answer("Пока нет пользователей.")
        return
    text = "🏆 <b>Топ-10 богачей</b>\n\n"
    for i, (username, balance) in enumerate(top, 1):
        text += f"{i}. @{username or 'NoName'} — {balance} {CURRENCY_SHORT}\n"
    await message.answer(text)

@dp.message(Command("duel"))
async def cmd_duel(message: Message):
    user = await ensure_user_exists(message.from_user.id, message.from_user.username)
    if not user:
        await message.answer("❌ Ошибка. Попробуйте позже.")
        return

    args = message.text.split()
    if len(args) > 1:
        try:
            bet = int(args[1])
        except ValueError:
            await message.answer("❌ Ставка должна быть числом.")
            return
        if bet <= 0:
            await message.answer("❌ Ставка должна быть положительной.")
            return
        if user[2] < bet:
            await message.answer(f"❌ Недостаточно средств. У тебя {user[2]} {CURRENCY_SHORT}.")
            return
    else:
        bet = 50
        if user[2] < bet:
            await message.answer(
                f"❌ У тебя меньше {bet} {CURRENCY_SHORT}, измени ставку: `/duel (сумма)`",
                parse_mode="Markdown"
            )
            return

    win = random.choice([True, False])
    if win:
        await update_balance(user[0], bet)
        await message.answer(f"⚔️ Ты выиграл дуэль! +{bet} {CURRENCY_SHORT}. Новый баланс: {user[2] + bet}")
    else:
        await update_balance(user[0], -bet)
        await message.answer(f"💔 Ты проиграл дуэль. -{bet} {CURRENCY_SHORT}. Новый баланс: {user[2] - bet}")

@dp.message(Command("mines"))
async def cmd_mines(message: Message):
    args = message.text.split()[1:]
    await start_mines(message, args)

@dp.message(Command("joker"))
async def cmd_joker(message: Message):
    await play_joker(message)

# ========== ИГРА ДЖОКЕР ==========
async def play_joker(message: Message):
    user = await ensure_user_exists(message.from_user.id, message.from_user.username)
    if not user:
        await message.answer("❌ Ошибка. Попробуйте позже.")
        return

    cost = 50
    if user[2] < cost:
        await message.answer(f"❌ Недостаточно средств. Для джокера нужно {cost} {CURRENCY_SHORT}.")
        return

    await update_balance(user[0], -cost)

    # Вероятности: 20% проигрыш, 30% возврат, 30% x2, 20% x3
    results = [0, 0, 1, 1, 1, 2, 2, 2, 3, 3]
    multiplier = random.choice(results)

    if multiplier == 0:
        await message.answer(f"🃏 Джокер: ты проиграл! -{cost} {CURRENCY_SHORT}.\nОстаток: {user[2] - cost} {CURRENCY_SHORT}.")
    elif multiplier == 1:
        await update_balance(user[0], cost)
        await message.answer(f"🃏 Джокер: возврат ставки. Твой баланс не изменился: {user[2]} {CURRENCY_SHORT}.")
    else:
        win_amount = cost * multiplier
        await update_balance(user[0], win_amount)
        await message.answer(f"🃏 Джокер: множитель x{multiplier}! Ты выиграл {win_amount} {CURRENCY_SHORT}.\nНовый баланс: {user[2] - cost + win_amount} {CURRENCY_SHORT}.")

# ========== ОБРАБОТЧИК СООБЩЕНИЙ С ТОЧКОЙ (.команды) ==========
@dp.message(F.text.startswith('.'))
async def dot_command_handler(message: Message):
    text = message.text[1:].strip()
    if not text:
        return
    parts = text.split()
    command = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []

    if command in ("мина", "мины"):
        await start_mines(message, args)
    elif command in ("джокер", "джок"):
        await play_joker(message)
    # иначе игнорируем

# ========== КОРОТКИЕ КОМАНДЫ (только в личке) ==========
@dp.message(ChatTypeFilter(ChatType.PRIVATE), F.text)
async def short_commands(message: Message):
    text = message.text.lower().strip()
    if text in ("б", "баланс"):
        await cmd_balance(message)
    elif text in ("п", "профиль"):
        await cmd_profile(message)
    elif text in ("д", "дуэль"):
        await cmd_duel(message)  # вызовет дуэль со ставкой по умолчанию
    elif text in ("дж", "джокер"):
        await play_joker(message)
    elif text in ("т", "топ"):
        await cmd_top(message)
    elif text in ("е", "ежедневный", "бонус"):
        await cmd_daily(message)

# ========== HELP ==========
@dp.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        f"📖 <b>Доступные команды</b>\n"
        f"/start – главное меню\n"
        f"/profile – твой профиль\n"
        f"/balance – баланс\n"
        f"/daily – ежедневный бонус\n"
        f"/send 100 @username – перевод\n"
        f"/top – топ-10 богачей\n"
        f"/duel [ставка] – дуэль (по умолч. 50)\n"
        f"/mines (ставка) (мин) – Мины (по умолч. 8 мин, макс 10)\n"
        f"/joker – Джокер (стоит 50)\n"
        f"/help – эта справка\n"
        f"\n<b>Короткие команды (в личке):</b>\n"
        f"б / баланс – баланс\n"
        f"п / профиль – профиль\n"
        f"д / дуэль – дуэль (50 монет)\n"
        f"дж / джокер – Джокер\n"
        f"т / топ – топ\n"
        f"е / бонус – ежедневный бонус\n"
        f"\n<b>В группах используйте .команды:</b>\n"
        f".мины 100 8 – Мины\n"
        f".джокер – Джокер\n"
        f"\nВалюта: {CURRENCY_NAME} ({CURRENCY_SHORT})"
    )
    await message.answer(text)

# ========== CALLBACK-ОБРАБОТЧИКИ ==========
@dp.callback_query(F.data.startswith("mines_cell_"))
async def mines_cell_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in active_games:
        await callback.answer("Игра не найдена. Начни новую командой /mines или .мины.")
        return

    game = active_games[user_id]
    cell = int(callback.data.split("_")[-1])

    if cell in game['opened']:
        await callback.answer("Эта ячейка уже открыта.")
        return

    # Попали на мину
    if cell in game['mines']:
        game['opened'].add(cell)
        await callback.answer("💣 Ты попал на мину! Ты проиграл.")
        await callback.message.edit_text(
            f"💣 Ты попал на мину! Ставка {game['bet']} {CURRENCY_SHORT} сгорела.",
            reply_markup=generate_mines_keyboard(user_id, game)
        )
        del active_games[user_id]
        return

    # Безопасная ячейка
    game['opened'].add(cell)
    opened_safe = len([x for x in game['opened'] if x not in game['mines']])
    safe_total = game['safe_count']
    game['multiplier'] = 1.0 + (opened_safe / safe_total) * 1.0  # максимум 2.0

    # Все безопасные открыты → победа
    if opened_safe == safe_total:
        win_amount = int(game['bet'] * game['multiplier'])
        await update_balance(user_id, win_amount)
        await callback.answer(f"🎉 Ты открыл все безопасные ячейки! Выигрыш: {win_amount} {CURRENCY_SHORT}!")
        await callback.message.edit_text(
            f"🎉 Ты выиграл! {win_amount} {CURRENCY_SHORT} зачислено.\nМножитель: x{game['multiplier']:.2f}",
            reply_markup=generate_mines_keyboard(user_id, game)
        )
        del active_games[user_id]
        return

    current_win = int(game['bet'] * game['multiplier'])
    await callback.message.edit_text(
        f"💣 Игра продолжается.\n"
        f"Ставка: {game['bet']} {CURRENCY_SHORT}\n"
        f"Открыто безопасных: {opened_safe}/{safe_total}\n"
        f"Текущий множитель: x{game['multiplier']:.2f}\n"
        f"Потенциальный выигрыш: {current_win} {CURRENCY_SHORT}",
        reply_markup=generate_mines_keyboard(user_id, game)
    )
    await callback.answer()

@dp.callback_query(F.data == "mines_cashout")
async def mines_cashout(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in active_games:
        await callback.answer("Нет активной игры.")
        return

    game = active_games[user_id]
    win_amount = int(game['bet'] * game['multiplier'])
    await update_balance(user_id, win_amount)
    await callback.answer(f"💰 Ты забрал {win_amount} {CURRENCY_SHORT}!")
    await callback.message.edit_text(
        f"💰 Ты забрал выигрыш: {win_amount} {CURRENCY_SHORT}.\nМножитель: x{game['multiplier']:.2f}",
        reply_markup=generate_mines_keyboard(user_id, game)
    )
    del active_games[user_id]

@dp.callback_query(F.data == "mines_quit")
async def mines_quit(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in active_games:
        del active_games[user_id]
    await callback.answer("Игра завершена.")
    await callback.message.edit_text("❌ Игра завершена. Ставка потеряна.")

@dp.callback_query(F.data.in_(["profile", "balance", "daily", "top", "duel", "mines", "joker"]))
async def main_menu_callback(callback: CallbackQuery):
    await callback.answer()
    data = callback.data
    if data == "profile":
        await cmd_profile(callback.message)
    elif data == "balance":
        await cmd_balance(callback.message)
    elif data == "daily":
        await cmd_daily(callback.message)
    elif data == "top":
        await cmd_top(callback.message)
    elif data == "duel":
        await cmd_duel(callback.message)
    elif data == "mines":
        await callback.message.answer(
            "Используй команду `/mines (ставка) (мин)` или `.мины (ставка) (мин)`\n"
            "Пример: `/mines 100 8`\n"
            "Количество мин по умолчанию 8, макс 10.",
            parse_mode="Markdown"
        )
    elif data == "joker":
        await play_joker(callback.message)

# ========== УСТАНОВКА МЕНЮ КОМАНД ==========
async def set_commands():
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="profile", description="Мой профиль"),
        BotCommand(command="balance", description="Мой баланс"),
        BotCommand(command="daily", description="Ежедневный бонус"),
        BotCommand(command="send", description="Перевести монеты"),
        BotCommand(command="top", description="Топ-10 богачей"),
        BotCommand(command="duel", description="Дуэль (ставка по умолчанию 50)"),
        BotCommand(command="mines", description="Игра «Мины» (ставка, мин)"),
        BotCommand(command="joker", description="Игра «Джокер» (стоит 50)"),
        BotCommand(command="help", description="Помощь"),
    ]
    await bot.set_my_commands(commands)

# ========== ЗАПУСК ==========
async def main():
    await init_db()
    await set_commands()
    logger.info(f"🤖 Бот {BOT_NAME} запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
