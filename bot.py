import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request
import asyncio

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID"))
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
    lessons_left INTEGER,
    valid_until TEXT,
    status TEXT DEFAULT 'active',
    FOREIGN KEY (student_id) REFERENCES students (id)
)
''')
conn.commit()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cursor.execute("SELECT * FROM students WHERE telegram_id = ?", (user_id,))
    student = cursor.fetchone()
    if student:
        await update.message.reply_text(f"Привет, {student[2]}! Используй /balance чтобы узнать остаток занятий.")
    else:
        await update.message.reply_text("Привет! Ты ещё не зарегистрирован. Обратись к администратору.")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cursor.execute("SELECT id, name FROM students WHERE telegram_id = ?", (user_id,))
    student = cursor.fetchone()
    if not student:
        await update.message.reply_text("Ты не зарегистрирован в системе.")
        return
    student_id = student[0]
    cursor.execute('''
        SELECT lessons_left, valid_until FROM memberships 
        WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
    ''', (student_id,))
    memberships = cursor.fetchall()
    if not memberships:
        await update.message.reply_text("У тебя нет активных абонементов.")
        return
    msg = "📚 Твои абонементы:\n\n"
    for i, m in enumerate(memberships, 1):
        msg += f"{i}. Осталось занятий: {m[0]}\n   Действует до: {m[1]}\n\n"
    await update.message.reply_text(msg)

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
        await update.message.reply_text(f"Ученик {name} добавлен!")
    except:
        await update.message.reply_text("Ошибка. Формат: /add_student Имя Телефон TelegramID")

async def add_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    try:
        text = update.message.text.split()
        telegram_id = int(text[1])
        lessons = int(text[2])
        days = int(text[3])
        cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (telegram_id,))
        student = cursor.fetchone()
        if not student:
            await update.message.reply_text("Ученик не найден.")
            return
        student_id = student[0]
        valid_until = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute(
            "INSERT INTO memberships (student_id, lessons_left, valid_until) VALUES (?, ?, ?)",
            (student_id, lessons, valid_until)
        )
        conn.commit()
        await update.message.reply_text(f"Абонемент добавлен! Действует до {valid_until}")
    except:
        await update.message.reply_text("Ошибка. Формат: /add_membership TelegramID занятий дней")

async def use_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        conn.commit()
        await update.message.reply_text(f"Занятие списано. Осталось: {new_count}")
    except:
        await update.message.reply_text("Ошибка. Формат: /use_lesson TelegramID")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("add_student", add_student))
    app.add_handler(CommandHandler("add_membership", add_membership))
    app.add_handler(CommandHandler("use_lesson", use_lesson))
    
    print("Бот запущен в режиме polling...")
    app.run_polling()

if name == "__main__":
    main()
application = flask_app
