import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
import sqlite3
from datetime import datetime, timedelta

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID"))

conn = sqlite3.connect("school.db", check_same_thread=False)
cursor = conn.cursor()

# Создаём таблицы
cursor.execute('''
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE,
    name TEXT,
    phone TEXT
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER,
    lessons_left INTEGER,
    valid_until TEXT,
    status TEXT DEFAULT 'active',
    FOREIGN KEY (student_id) REFERENCES students (id)
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER,
    date TEXT,
    membership_id INTEGER,
    FOREIGN KEY (student_id) REFERENCES students (id),
    FOREIGN KEY (membership_id) REFERENCES memberships (id)
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS parents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE,
    name TEXT,
    phone TEXT
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS parent_child (
    parent_id INTEGER,
    student_id INTEGER,
    FOREIGN KEY (parent_id) REFERENCES parents (id),
    FOREIGN KEY (student_id) REFERENCES students (id)
)
''')
conn.commit()

# ========== КНОПКИ И ОБЩИЕ ФУНКЦИИ ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Проверяем, кто это: ученик, родитель или админ
    cursor.execute("SELECT id, name FROM students WHERE telegram_id = ?", (user_id,))
    student = cursor.fetchone()

    cursor.execute("SELECT id, name FROM parents WHERE telegram_id = ?", (user_id,))
    parent = cursor.fetchone()

    keyboard = []

    if student:
        keyboard.append([InlineKeyboardButton("📊 Мой баланс", callback_data="balance")])
        keyboard.append([InlineKeyboardButton("📅 Мои посещения", callback_data="my_attendance")])
        msg = f"Привет, {student[1]}! 👋\nВыбери действие:"
    elif parent:
        # Показываем кнопки для каждого ребёнка
        cursor.execute('''
            SELECT students.id, students.name FROM students
            JOIN parent_child ON students.id = parent_child.student_id
            WHERE parent_child.parent_id = ?
        ''', (parent[0],))
        children = cursor.fetchall()
        for child in children:
            keyboard.append([InlineKeyboardButton(f"👤 {child[1]}", callback_data=f"child_{child[0]}")])
        msg = f"Здравствуйте, {parent[1]}!\nВыберите ребёнка:"
    elif user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👥 Ученики", callback_data="admin_students")])
        keyboard.append([InlineKeyboardButton("➕ Отметить посещение", callback_data="admin_mark")])
        keyboard.append([InlineKeyboardButton("👪 Родители", callback_data="admin_parents")])
        msg = "🔐 Панель администратора"
    else:
        msg = "👋 Привет! Ты пока не зарегистрирован в системе. Обратись к администратору."

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(msg, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "balance":
        await show_balance(user_id, query)
    elif data == "my_attendance":
        await show_attendance(user_id, query)
    elif data.startswith("child_"):
        child_id = int(data.split("_")[1])
        await show_child_menu(child_id, query)
    elif data == "back_to_children":
        await show_parent_children(user_id, query)
    elif data.startswith("child_balance_"):
        child_id = int(data.split("_")[2])
        await show_balance_by_id(child_id, query)
    elif data.startswith("child_attendance_"):
        child_id = int(data.split("_")[2])
        await show_attendance_by_id(child_id, query)

# ========== УЧЕНИКИ И РОДИТЕЛИ ==========

async def show_balance(user_id, query):
    cursor.execute("SELECT id, name FROM students WHERE telegram_id = ?", (user_id,))
    student = cursor.fetchone()
    if not student:
        await query.edit_message_text("Ты не ученик.")
        return
    await show_balance_by_id(student[0], query)

async def show_balance_by_id(student_id, query):
    cursor.execute('''
        SELECT lessons_left, valid_until FROM memberships
        WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
    ''', (student_id,))
    memberships = cursor.fetchall()
    if not memberships:
        msg = "Нет активных абонементов."
    else:
        msg = "📊 *Твой баланс:*\n"
        for i, m in enumerate(memberships, 1):
            msg += f"\n{i}. Осталось: {m[0]} занятий\n   Действует до: {m[1]}"
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_children")]]
    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_attendance(user_id, query):
    cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (user_id,))
    student = cursor.fetchone()
    if not student:
        await query.edit_message_text("Ты не ученик.")
        return
    await show_attendance_by_id(student[0], query)

async def show_attendance_by_id(student_id, query):
    cursor.execute('''
        SELECT date FROM attendance
        WHERE student_id = ?
        ORDER BY date DESC
        LIMIT 10
    ''', (student_id,))
    visits = cursor.fetchall()
    if not visits:
        msg = "📅 Посещений пока нет."
    else:
        msg = "📅 *Последние посещения:*\n"
        for v in visits:
            msg += f"\n• {v[0]}"
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_children")]]
    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_parent_children(parent_id, query):
    cursor.execute("SELECT name FROM parents WHERE telegram_id = ?", (parent_id,))
    parent = cursor.fetchone()
    cursor.execute('''
        SELECT students.id, students.name FROM students
        JOIN parent_child ON students.id = parent_child.student_id
        WHERE parent_child.parent_id = ?
    ''', (parent[0],))
    children = cursor.fetchall()
    keyboard = []
    for child in children:
        keyboard.append([InlineKeyboardButton(f"👤 {child[1]}", callback_data=f"child_{child[0]}")])
    await query.edit_message_text("Выберите ребёнка:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_child_menu(child_id, query):
    cursor.execute("SELECT name FROM students WHERE id = ?", (child_id,))
    child = cursor.fetchone()
    keyboard = [
        [InlineKeyboardButton("📊 Баланс", callback_data=f"child_balance_{child_id}")],
        [InlineKeyboardButton("📅 Посещения", callback_data=f"child_attendance_{child_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_children")]
    ]
    await query.edit_message_text(f"👤 {child[0]}\nВыберите действие:", reply_markup=InlineKeyboardMarkup(keyboard))

# ========== АДМИН-КОМАНДЫ ==========

async def add_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    try:
        text = update.message.text.split()
        name = text[1]
        phone = text[2]
        telegram_id = int(text[3])
        cursor.execute(
            "INSERT OR IGNORE INTO students (telegram_id, name, phone) VALUES (?, ?, ?)",
            (telegram_id, name, phone)
        )
        conn.commit()
        await update.message.reply_text(f"✅ Ученик {name} добавлен!")
    except:
        await update.message.reply_text("Ошибка. Формат: /add_student Имя Телефон TelegramID")

async def add_parent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    try:
        text = update.message.text.split()
        name = text[1]
        phone = text[2]
        telegram_id = int(text[3])
        cursor.execute(
            "INSERT OR IGNORE INTO parents (telegram_id, name, phone) VALUES (?, ?, ?)",
            (telegram_id, name, phone)
        )
        conn.commit()
        await update.message.reply_text(f"✅ Родитель {name} добавлен!")
    except:
        await update.message.reply_text("Ошибка. Формат: /add_parent Имя Телефон TelegramID")

async def link_child(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    try:
        text = update.message.text.split()
        parent_tg = int(text[1])
        child_tg = int(text[2])
        cursor.execute("SELECT id FROM parents WHERE telegram_id = ?", (parent_tg,))
        parent = cursor.fetchone()
        cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (child_tg,))
        child = cursor.fetchone()
        if not parent or not child:
            await update.message.reply_text("Родитель или ученик не найдены.")
            return
        cursor.execute(
            "INSERT INTO parent_child (parent_id, student_id) VALUES (?, ?)",
            (parent[0], child[0])
        )
        conn.commit()
        await update.message.reply_text("✅ Связь создана!")
    except:
        await update.message.reply_text("Ошибка. Формат: /link_child TelegramID_родителя TelegramID_ребёнка")

async def mark_visited(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    try:
        telegram_id = int(update.message.text.split()[1])
        cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (telegram_id,))
        student = cursor.fetchone()
        if not student:
            await update.message.reply_text("Ученик не найден.")
            return
        student_id = student[0]
        cursor.execute('''
            SELECT id, lessons_left FROM memberships
            WHERE student_id = ? AND status = 'active' AND lessons_left > 0
            AND valid_until > date('now')
            ORDER BY valid_until ASC
            LIMIT 1
        ''', (student_id,))
        membership = cursor.fetchone()
        if not membership:
            await update.message.reply_text("Нет активных абонементов с занятиями.")
            return
        membership_id = membership[0]
        new_count = membership[1] - 1
        if new_count == 0:
            cursor.execute("UPDATE memberships SET status = 'finished' WHERE id = ?", (membership_id,))
        else:
            cursor.execute("UPDATE memberships SET lessons_left = ? WHERE id = ?", (new_count, membership_id))
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            "INSERT INTO attendance (student_id, date, membership_id) VALUES (?, ?, ?)",
            (student_id, today, membership_id)
        )
        conn.commit()
        await update.message.reply_text(f"✅ Посещение отмечено! Осталось занятий: {new_count}")
    except:
        await update.message.reply_text("Ошибка. Формат: /mark_visited TelegramID")

# ========== НОВЫЕ КОМАНДЫ ДЛЯ ПРОДЛЕНИЯ ==========

async def extend_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Продлевает абонемент ученика на указанное количество дней"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    try:
        # Формат: /extend TelegramID количество_дней
        telegram_id = int(context.args[0])
        days_to_add = int(context.args[1])

        cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (telegram_id,))
        student = cursor.fetchone()
        if not student:
            await update.message.reply_text("Ученик не найден.")
            return
        student_id = student[0]

        # Находим активный абонемент ученика
        cursor.execute('''
            SELECT id, valid_until FROM memberships
            WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
            ORDER BY valid_until ASC
            LIMIT 1
        ''', (student_id,))
        membership = cursor.fetchone()

        if not membership:
            await update.message.reply_text("У ученика нет активных абонементов.")
            return

        membership_id, old_valid_until = membership
        
        # Рассчитываем новую дату
        old_date = datetime.strptime(old_valid_until, "%Y-%m-%d")
        new_valid_until = (old_date + timedelta(days=days_to_add)).strftime("%Y-%m-%d")

        # Обновляем базу данных
        cursor.execute(
            "UPDATE memberships SET valid_until = ? WHERE id = ?",
            (new_valid_until, membership_id)
        )
        conn.commit()

        await update.message.reply_text(
            f"✅ Абонемент продлён на {days_to_add} дней!\n"
            f"   Новая дата окончания: {new_valid_until}"
        )
    except (IndexError, ValueError):
        await update.message.reply_text("Ошибка. Формат: /extend TelegramID дни")
    except Exception as e:
        logger.error(f"Error in extend_days: {e}")
        await update.message.reply_text("Произошла ошибка.")

# ========== ЗАПУСК ==========

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Основные команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_student", add_student))
    app.add_handler(CommandHandler("add_parent", add_parent))
    app.add_handler(CommandHandler("link_child", link_child))
    app.add_handler(CommandHandler("mark_visited", mark_visited))
    
    # Новая команда для продления
    app.add_handler(CommandHandler("extend", extend_days))
    
    # Обработчик кнопок
    app.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("🚀 Бот запущен и готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    main()
