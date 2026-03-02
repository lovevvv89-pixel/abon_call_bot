import os
import logging
import sqlite3
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Безопасное чтение ADMIN_IDS
admin_raw = os.getenv("ADMIN_CHAT_ID", "")
admin_clean = re.sub(r'[^\d,]', '', admin_raw)
ADMIN_IDS = [int(x) for x in admin_clean.split(',') if x]

conn = sqlite3.connect("school.db", check_same_thread=False)
cursor = conn.cursor()

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
    lessons_left INTEGER DEFAULT 0,
    valid_until TEXT,
    status TEXT DEFAULT 'active',
    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER,
    date TEXT,
    present INTEGER DEFAULT 1,
    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE
)
''')
conn.commit()

# ========== СТАРТ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS:
        await update.message.reply_text(
            "🔐 Админ-панель\n\n"
            "/students — список учеников\n"
            "/add_student Имя Телефон ID — добавить ученика\n"
            "/delete_student ID — удалить ученика\n"
            "/add_membership ID занятий дней — добавить абонемент\n"
            "/mark_visited ID — отметить посещение"
        )
        return
    student = cursor.execute("SELECT id, name FROM students WHERE telegram_id = ?", (user_id,)).fetchone()
    if student:
        keyboard = [
            [InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{student[0]}")],
            [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{student[0]}")],
        ]
        await update.message.reply_text(f"👋 {student[1]}", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("👋 Ты не зарегистрирован")

# ========== КНОПКИ ДЛЯ УЧЕНИКОВ ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data.startswith("balance_"):
        student_id = int(data.split("_")[1])
        mem = cursor.execute('''
            SELECT lessons_left, valid_until FROM memberships
            WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
        ''', (student_id,)).fetchone()
        if mem:
            text = f"📊 Осталось: {mem[0]}\n📅 Действует до: {mem[1]}"
        else:
            text = "📭 Нет активных абонементов"
        await q.edit_message_text(text)
    elif data.startswith("attendance_"):
        student_id = int(data.split("_")[1])
        rows = cursor.execute('''
            SELECT date FROM attendance
            WHERE student_id = ?
            ORDER BY date DESC LIMIT 10
        ''', (student_id,)).fetchall()
        if rows:
            text = "📅 Посещения:\n" + "\n".join([f"▫️ {r[0]}" for r in rows])
        else:
            text = "📅 Посещений нет"
        await q.edit_message_text(text)

# ========== АДМИН-КОМАНДЫ ==========
async def students(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    rows = cursor.execute("SELECT name, phone, telegram_id FROM students").fetchall()
    if not rows:
        await update.message.reply_text("👥 Учеников нет")
        return
    text = "👥 Ученики:\n" + "\n".join([f"▫️ {r[0]} {r[1]} 🆔 {r[2]}" for r in rows])
    await update.message.reply_text(text)

async def add_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        _, name, phone, tg_id = update.message.text.split()
        cursor.execute("INSERT INTO students (telegram_id, name, phone) VALUES (?, ?, ?)",
                      (int(tg_id), name, phone))
        conn.commit()
        await update.message.reply_text(f"✅ Ученик {name} добавлен")
    except:
        await update.message.reply_text("❌ Ошибка. Формат: /add_student Имя Телефон ID")

async def delete_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        tg_id = int(context.args[0])
        cursor.execute("DELETE FROM students WHERE telegram_id = ?", (tg_id,))
        conn.commit()
        await update.message.reply_text("✅ Ученик удалён")
    except:
        await update.message.reply_text("❌ Ошибка. Формат: /delete_student ID")

async def add_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        tg_id = int(context.args[0])
        lessons = int(context.args[1])
        days = int(context.args[2])
        student = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (tg_id,)).fetchone()
        if not student:
            await update.message.reply_text("❌ Ученик не найден")
            return
        valid_until = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute('''
            INSERT INTO memberships (student_id, lessons_left, valid_until, status)
            VALUES (?, ?, ?, 'active')
        ''', (student[0], lessons, valid_until))
        conn.commit()
        await update.message.reply_text(f"✅ Абонемент добавлен на {lessons} занятий до {valid_until}")
    except:
        await update.message.reply_text("❌ Ошибка. Формат: /add_membership ID занятий дней")

async def mark_visited(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        tg_id = int(context.args[0])
        student = cursor.execute("SELECT id, name FROM students WHERE telegram_id = ?", (tg_id,)).fetchone()
        if not student:
            await update.message.reply_text("❌ Ученик не найден")
            return
        today = datetime.now().strftime("%Y-%m-%d")
        mem = cursor.execute('''
            SELECT id, lessons_left FROM memberships
            WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
            ORDER BY valid_until ASC LIMIT 1
        ''', (student[0],)).fetchone()
        if mem:
            new_left = mem[1] - 1
            cursor.execute("UPDATE memberships SET lessons_left = ? WHERE id = ?", (new_left, mem[0]))
            cursor.execute("INSERT INTO attendance (student_id, date) VALUES (?, ?)", (student[0], today))
            conn.commit()
            await update.message.reply_text(f"✅ Отмечено. Осталось: {new_left}")
        else:
            cursor.execute("INSERT INTO attendance (student_id, date) VALUES (?, ?)", (student[0], today))
            conn.commit()
            await update.message.reply_text("✅ Отмечено (без абонемента)")
    except:
        await update.message.reply_text("❌ Ошибка. Формат: /mark_visited ID")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("students", students))
    app.add_handler(CommandHandler("add_student", add_student))
    app.add_handler(CommandHandler("delete_student", delete_student))
    app.add_handler(CommandHandler("add_membership", add_membership))
    app.add_handler(CommandHandler("mark_visited", mark_visited))
    app.add_handler(CallbackQueryHandler(button_handler))
    logger.info("🚀 Бот запущен (кнопки только для учеников)")
    app.run_polling()

if __name__ == "__main__":
    main()
