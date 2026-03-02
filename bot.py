import os
import logging
import ast
from dotenv import load_dotenv  # загрузка переменных окружения
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
import sqlite3
from datetime import datetime, timedelta

# Состояния для разговоров
NAME, PHONE, TG_ID, PARENT_NAME, PARENT_PHONE, PARENT_TG = range(6)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = ast.literal_eval(os.getenv("ADMIN_CHAT_ID"))

conn = sqlite3.connect("school.db", check_same_thread=False)
cursor = conn.cursor()

# ========== ТАБЛИЦЫ ==========
cursor.execute('''
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE,
    name TEXT,
    phone TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS student_group (
    student_id INTEGER,
    group_id INTEGER,
    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES groups (id) ON DELETE CASCADE,
    PRIMARY KEY (student_id, group_id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER,
    lessons_total INTEGER,
    lessons_left INTEGER,
    purchase_date TEXT,
    activation_date TEXT,
    valid_until TEXT,
    status TEXT DEFAULT 'pending',
    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER,
    date TEXT,
    membership_id INTEGER,
    present INTEGER DEFAULT 1,
    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
    FOREIGN KEY (membership_id) REFERENCES memberships (id) ON DELETE CASCADE
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
    FOREIGN KEY (parent_id) REFERENCES parents (id) ON DELETE CASCADE,
    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
    PRIMARY KEY (parent_id, student_id)
)
''')
conn.commit()

# ========== УВЕДОМЛЕНИЯ ==========
async def check_and_notify_admin(student_id, new_balance, context):
    """Проверяет баланс после отметки и уведомляет админов"""
    student = cursor.execute("SELECT name, telegram_id FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        return
    
    student_name = student[0]
    
    if new_balance == 1:
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"⚠️ *Последнее занятие*\nУ {student_name} осталось 1 занятие!",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
    
    elif new_balance <= 0:
        for admin_id in ADMIN_IDS:
            try:
                if new_balance == 0:
                    msg = f"❌ *Занятия закончились*\nУ {student_name} больше нет занятий!"
                else:
                    msg = f"⛔ *Долг*\nУ {student_name} долг: {abs(new_balance)} занятий"
                
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=msg,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")

# ========== СТАРТ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id in ADMIN_IDS:
        keyboard = [
            [InlineKeyboardButton("👥 Ученики", callback_data="admin_students")],
            [InlineKeyboardButton("📚 Группы", callback_data="admin_groups")],
            [InlineKeyboardButton("📋 Отметить группу", callback_data="mark_group")],
            [InlineKeyboardButton("➕ Отметить посещение", callback_data="admin_mark")],
            [InlineKeyboardButton("👪 Родители", callback_data="admin_parents")],
            [InlineKeyboardButton("⏱️ Продлить", callback_data="admin_extend")],
            [InlineKeyboardButton("➕ Добавить в группу", callback_data="admin_add_to_group")],
        ]
        await update.message.reply_text("🔐 Панель администратора", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    cursor.execute("SELECT id, name FROM parents WHERE telegram_id = ?", (user_id,))
    parent = cursor.fetchone()
    if parent:
        cursor.execute('''
            SELECT students.id, students.name FROM students
            JOIN parent_child ON students.id = parent_child.student_id
            WHERE parent_child.parent_id = ?
        ''', (parent[0],))
        children = cursor.fetchall()
        keyboard = [[InlineKeyboardButton(f"👤 {ch[1]}", callback_data=f"child_{ch[0]}")] for ch in children]
        await update.message.reply_text(f"👪 Здравствуйте, {parent[1]}!", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    cursor.execute("SELECT id, name FROM students WHERE telegram_id = ?", (user_id,))
    student = cursor.fetchone()
    if student:
        keyboard = [
            [InlineKeyboardButton("📊 Мой баланс", callback_data="balance")],
            [InlineKeyboardButton("📅 Посещения", callback_data="my_attendance")]
        ]
        await update.message.reply_text(f"👋 Привет, {student[1]}!", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    await update.message.reply_text("👋 Ты не зарегистрирован. Обратись к администратору.")

# ========== КНОПКИ ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = update.effective_user.id

    if data == "balance":
        await show_balance(user_id, q)
    elif data == "my_attendance":
        await show_attendance(user_id, q)
    elif data.startswith("child_"):
        await show_child_menu(int(data.split("_")[1]), q)
    elif data == "back_to_children":
        await show_parent_children(user_id, q)
    elif data.startswith("child_balance_"):
        await show_balance_by_id(int(data.split("_")[2]), q)
    elif data.startswith("child_attendance_"):
        await show_attendance_by_id(int(data.split("_")[2]), q)

    # Админские кнопки
    if user_id in ADMIN_IDS:
        if data == "admin_students":
            await show_all_students(q)
        elif data == "admin_groups":
            await show_groups_menu(q)
        elif data.startswith("group_"):
            await show_group_students(int(data.split("_")[1]), q)
        elif data == "admin_mark":
            await q.edit_message_text("📝 Отметить посещение:\n`/mark_visited TelegramID`", parse_mode="Markdown")
        elif data == "admin_parents":
            await show_all_parents(q)
        elif data == "admin_extend":
            await q.edit_message_text("📝 Продлить:\n`/extend TelegramID дни`", parse_mode="Markdown")
        elif data == "admin_add_to_group":
            await show_students_for_group(q)

        # НОВОЕ: отметка группы
        elif data == "mark_group":
            await show_groups_for_mark(q)
        elif data.startswith("mark_group_"):
            group_id = int(data.split("_")[2])
            await show_students_for_mark(q, group_id, context)
        elif data.startswith("mark_student_"):
            parts = data.split("_")
            student_id = int(parts[2])
            present = int(parts[3])  # 1 = пришёл, 0 = нет
            group_id = int(parts[4])
            await mark_student_attendance(q, student_id, present, group_id, context)
        elif data.startswith("mark_all_"):
            parts = data.split("_")
            present = int(parts[2])
            group_id = int(parts[3])
            await mark_all_students(q, present, group_id, context)
        elif data == "mark_done":
            await q.edit_message_text("✅ Отметка завершена", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В админку", callback_data="start")]]))

        # Добавление ученика
        elif data == "add_student_button":
            await q.edit_message_text("✏️ Введите имя ученика:")
            return NAME

        # Удаление ученика
        elif data == "delete_student_button":
            await show_students_for_delete(q)
        elif data.startswith("delete_student_"):
            student_id = int(data.split("_")[2])
            await confirm_delete_student(q, student_id)
        elif data.startswith("confirm_delete_"):
            student_id = int(data.split("_")[2])
            await confirm_delete(q, student_id)

        # Добавление родителя
        elif data == "add_parent_button":
            await q.edit_message_text("✏️ Введите имя родителя:")
            return PARENT_NAME

        # Привязка ребёнка
        elif data == "link_child_button":
            await show_parents_for_link(q)
        elif data.startswith("link_parent_"):
            parent_id = int(data.split("_")[2])
            context.user_data['link_parent_id'] = parent_id
            await show_students_for_link(q)
        elif data.startswith("link_student_"):
            student_id = int(data.split("_")[2])
            parent_id = context.user_data.get('link_parent_id')
            if parent_id:
                await link_child_to_parent(q, parent_id, student_id)

        # Кнопки для добавления в группу
        elif data.startswith("select_student_"):
            student_id = int(data.split("_")[2])
            context.user_data['selected_student'] = student_id
            await show_groups_for_student(q, student_id)
        elif data.startswith("select_group_"):
            group_id = int(data.split("_")[2])
            student_id = context.user_data.get('selected_student')
            if student_id:
                await add_student_to_group(q, student_id, group_id)
            else:
                await q.edit_message_text("❌ Ошибка. Начните заново.")

# ========== НОВЫЕ ФУНКЦИИ ДЛЯ ОТМЕТКИ ГРУППЫ ==========
async def show_groups_for_mark(q):
    """Показывает группы для выбора при отметке"""
    rows = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
    
    if not rows:
        await q.edit_message_text("📚 Группы пока не созданы.")
        return
    
    keyboard = []
    for group in rows:
        keyboard.append([InlineKeyboardButton(f"📚 {group[1]}", callback_data=f"mark_group_{group[0]}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
    await q.edit_message_text("Выберите группу для отметки:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_students_for_mark(q, group_id, context):
    """Показывает учеников группы с кнопками ✅ и ❌"""
    group = cursor.execute("SELECT name FROM groups WHERE id = ?", (group_id,)).fetchone()
    if not group:
        return
    
    # Сохраняем group_id в контексте
    context.user_data['mark_group_id'] = group_id
    
    rows = cursor.execute('''
        SELECT students.id, students.name 
        FROM students 
        JOIN student_group ON students.id = student_group.student_id
        WHERE student_group.group_id = ?
        ORDER BY students.name
    ''', (group_id,)).fetchall()
    
    if not rows:
        await q.edit_message_text(f"В группе {group[0]} нет учеников.")
        return
    
    keyboard = []
    for student in rows:
        keyboard.append([
            InlineKeyboardButton(f"{student[1]} ✅", callback_data=f"mark_student_{student[0]}_1_{group_id}"),
            InlineKeyboardButton("❌", callback_data=f"mark_student_{student[0]}_0_{group_id}")
        ])
    
    # Кнопки для массовой отметки
    keyboard.append([
        InlineKeyboardButton("✅ Все пришли", callback_data=f"mark_all_1_{group_id}"),
        InlineKeyboardButton("❌ Все пропустили", callback_data=f"mark_all_0_{group_id}")
    ])
    keyboard.append([InlineKeyboardButton("🔙 К группам", callback_data="mark_group")])
    
    today = datetime.now().strftime("%d.%m.%Y")
    await q.edit_message_text(
        f"📋 Отметка группы *{group[0]}* на {today}\n\nНажимай ✅ или ❌ для каждого ученика:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def mark_student_attendance(q, student_id, present, group_id, context):
    """Отмечает одного ученика (пришёл/не пришёл)"""
    student = cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        await q.answer("❌ Ученик не найден")
        return
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    if present == 1:
        # Пришёл - списываем занятие
        mem = cursor.execute('''
            SELECT id, lessons_left, status FROM memberships
            WHERE student_id = ? AND status IN ('active','pending')
            ORDER BY 
                CASE status
                    WHEN 'active' THEN 1
                    WHEN 'pending' THEN 2
                END,
                purchase_date ASC
            LIMIT 1
        ''', (student_id,)).fetchone()
        
        if mem:
            mem_id, lessons_left, status = mem
            new_left = lessons_left - 1
            
            if status == 'pending' and lessons_left > 0:
                valid_until = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
                cursor.execute('''
                    UPDATE memberships 
                    SET status='active', activation_date=?, valid_until=?, lessons_left=?
                    WHERE id=?
                ''', (today, valid_until, new_left, mem_id))
            else:
                cursor.execute("UPDATE memberships SET lessons_left=? WHERE id=?", (new_left, mem_id))
            
            cursor.execute("INSERT INTO attendance (student_id, date, membership_id, present) VALUES (?, ?, ?, 1)", 
                          (student_id, today, mem_id))
            conn.commit()
            
            await check_and_notify_admin(student_id, new_left, context)
            
            await q.answer(f"✅ {student[0]} отмечен")
        else:
            # Если нет абонемента, создаём долг
            cursor.execute('''
                INSERT INTO memberships (student_id, lessons_total, lessons_left, purchase_date, status)
                VALUES (?, 0, -1, ?, 'debt')
            ''', (student_id, today))
            mem_id = cursor.lastrowid
            cursor.execute("INSERT INTO attendance (student_id, date, membership_id, present) VALUES (?, ?, ?, 1)", 
                          (student_id, today, mem_id))
            conn.commit()
            await check_and_notify_admin(student_id, -1, context)
            await q.answer(f"⚠️ {student[0]} отмечен (долг)")
    else:
        # Не пришёл - просто записываем отсутствие
        cursor.execute('''
            INSERT INTO attendance (student_id, date, present) 
            VALUES (?, ?, 0)
        ''', (student_id, today))
        conn.commit()
        await q.answer(f"❌ {student[0]} пропустил")
    
    # Возвращаемся к списку группы
    await show_students_for_mark(q, group_id, context)

async def mark_all_students(q, present, group_id, context):
    """Отмечает всех учеников группы"""
    rows = cursor.execute('''
        SELECT students.id FROM students 
        JOIN student_group ON students.id = student_group.student_id
        WHERE student_group.group_id = ?
    ''', (group_id,)).fetchall()
    
    today = datetime.now().strftime("%Y-%m-%d")
    count = 0
    
    for (student_id,) in rows:
        if present == 1:
            # Пришли - списываем занятия
            mem = cursor.execute('''
                SELECT id, lessons_left, status FROM memberships
                WHERE student_id = ? AND status IN ('active','pending')
                ORDER BY 
                    CASE status
                        WHEN 'active' THEN 1
                        WHEN 'pending' THEN 2
                    END,
                    purchase_date ASC
                LIMIT 1
            ''', (student_id,)).fetchone()
            
            if mem:
                mem_id, lessons_left, status = mem
                new_left = lessons_left - 1
                
                if status == 'pending' and lessons_left > 0:
                    valid_until = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
                    cursor.execute('''
                        UPDATE memberships 
                        SET status='active', activation_date=?, valid_until=?, lessons_left=?
                        WHERE id=?
                    ''', (today, valid_until, new_left, mem_id))
                else:
                    cursor.execute("UPDATE memberships SET lessons_left=? WHERE id=?", (new_left, mem_id))
                
                cursor.execute("INSERT INTO attendance (student_id, date, membership_id, present) VALUES (?, ?, ?, 1)", 
                              (student_id, today, mem_id))
                await check_and_notify_admin(student_id, new_left, context)
            else:
                cursor.execute('''
                    INSERT INTO memberships (student_id, lessons_total, lessons_left, purchase_date, status)
                    VALUES (?, 0, -1, ?, 'debt')
                ''', (student_id, today))
                mem_id = cursor.lastrowid
                cursor.execute("INSERT INTO attendance (student_id, date, membership_id, present) VALUES (?, ?, ?, 1)", 
                              (student_id, today, mem_id))
                await check_and_notify_admin(student_id, -1, context)
        else:
            # Все пропустили
            cursor.execute('''
                INSERT INTO attendance (student_id, date, present) 
                VALUES (?, ?, 0)
            ''', (student_id, today))
        
        count += 1
    
    conn.commit()
    
    group = cursor.execute("SELECT name FROM groups WHERE id = ?", (group_id,)).fetchone()
    status_text = "пришли" if present == 1 else "пропустили"
    await q.edit_message_text(
        f"✅ {count} учеников отмечены как «{status_text}»",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К группам", callback_data="mark_group")]])
    )

# ========== ДОБАВЛЕНИЕ УЧЕНИКА ==========
async def add_student_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("✏️ Введите имя ученика:")
    return NAME

async def add_student_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_student_name'] = update.message.text
    await update.message.reply_text("📞 Введите телефон ученика:")
    return PHONE

async def add_student_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_student_phone'] = update.message.text
    await update.message.reply_text("🆔 Введите Telegram ID ученика (число):")
    return TG_ID

async def add_student_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        name = context.user_data['new_student_name']
        phone = context.user_data['new_student_phone']
        tg_id = int(update.message.text)
        
        cursor.execute(
            "INSERT INTO students (telegram_id, name, phone) VALUES (?, ?, ?)",
            (tg_id, name, phone)
        )
        conn.commit()
        
        await update.message.reply_text(f"✅ Ученик {name} добавлен!")
        await show_all_students(message=update.message)
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом. Попробуйте ещё раз.")
        return TG_ID
    except Exception as e:
        logger.error(f"Error adding student: {e}")
        await update.message.reply_text("❌ Ошибка при добавлении.")
    
    context.user_data.clear()
    return ConversationHandler.END

# ========== УДАЛЕНИЕ УЧЕНИКА ==========
async def show_students_for_delete(q):
    rows = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
    
    if not rows:
        await q.edit_message_text("👥 Учеников пока нет.")
        return
    
    keyboard = []
    for student in rows:
        keyboard.append([InlineKeyboardButton(f"❌ {student[1]}", callback_data=f"delete_student_{student[0]}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_students")])
    await q.edit_message_text("Выберите ученика для удаления:", reply_markup=InlineKeyboardMarkup(keyboard))

async def confirm_delete_student(q, student_id):
    student = cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        await q.edit_message_text("❌ Ученик не найден.")
        return
    
    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{student_id}")],
        [InlineKeyboardButton("❌ Нет, отмена", callback_data="admin_students")]
    ]
    await q.edit_message_text(
        f"Вы уверены, что хотите удалить ученика {student[0]}?\nВсе его данные будут удалены.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def confirm_delete(q, student_id):
    try:
        cursor.execute("DELETE FROM students WHERE id = ?", (student_id,))
        conn.commit()
        await q.edit_message_text("✅ Ученик удалён.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👥 К списку", callback_data="admin_students")]]))
    except Exception as e:
        logger.error(f"Error deleting student: {e}")
        await q.edit_message_text("❌ Ошибка при удалении.")

# ========== ДОБАВЛЕНИЕ РОДИТЕЛЯ ==========
async def add_parent_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("✏️ Введите имя родителя:")
    return PARENT_NAME

async def add_parent_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_parent_name'] = update.message.text
    await update.message.reply_text("📞 Введите телефон родителя:")
    return PARENT_PHONE

async def add_parent_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_parent_phone'] = update.message.text
    await update.message.reply_text("🆔 Введите Telegram ID родителя (число):")
    return PARENT_TG

async def add_parent_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        name = context.user_data['new_parent_name']
        phone = context.user_data['new_parent_phone']
        tg_id = int(update.message.text)
        
        cursor.execute(
            "INSERT INTO parents (telegram_id, name, phone) VALUES (?, ?, ?)",
            (tg_id, name, phone)
        )
        conn.commit()
        
        await update.message.reply_text(f"✅ Родитель {name} добавлен!")
        await show_all_parents(message=update.message)
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом. Попробуйте ещё раз.")
        return PARENT_TG
    except Exception as e:
        logger.error(f"Error adding parent: {e}")
        await update.message.reply_text("❌ Ошибка при добавлении.")
    
    context.user_data.clear()
    return ConversationHandler.END

# ========== ПРИВЯЗКА РЕБЁНКА ==========
async def show_parents_for_link(q):
    rows = cursor.execute("SELECT id, name FROM parents ORDER BY name").fetchall()
    
    if not rows:
        await q.edit_message_text("👪 Сначала добавьте родителя.")
        return
    
    keyboard = []
    for parent in rows:
        keyboard.append([InlineKeyboardButton(f"👪 {parent[1]}", callback_data=f"link_parent_{parent[0]}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_parents")])
    await q.edit_message_text("Выберите родителя:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_students_for_link(q):
    rows = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
    
    if not rows:
        await q.edit_message_text("👥 Сначала добавьте учеников.")
        return
    
    keyboard = []
    for student in rows:
        keyboard.append([InlineKeyboardButton(f"👤 {student[1]}", callback_data=f"link_student_{student[0]}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_parents")])
    await q.edit_message_text("Выберите ученика:", reply_markup=InlineKeyboardMarkup(keyboard))

async def link_child_to_parent(q, parent_id, student_id):
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO parent_child (parent_id, student_id) VALUES (?, ?)",
            (parent_id, student_id)
        )
        conn.commit()
        
        parent = cursor.execute("SELECT name FROM parents WHERE id = ?", (parent_id,)).fetchone()
        student = cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,)).fetchone()
        
        await q.edit_message_text(
            f"✅ {student[0]} привязан к родителю {parent[0]}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👪 К родителям", callback_data="admin_parents")]])
        )
    except Exception as e:
        logger.error(f"Error linking child: {e}")
        await q.edit_message_text("❌ Ошибка при привязке.")

# ========== ДОБАВЛЕНИЕ В ГРУППУ ==========
async def show_students_for_group(q):
    rows = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
    
    if not rows:
        await q.edit_message_text("👥 Учеников пока нет.")
        return
    
    keyboard = []
    for student in rows:
        keyboard.append([InlineKeyboardButton(f"👤 {student[1]}", callback_data=f"select_student_{student[0]}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_groups")])
    await q.edit_message_text("Выберите ученика:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_groups_for_student(q, student_id):
    rows = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
    
    if not rows:
        await q.edit_message_text("📚 Группы пока не созданы.")
        return
    
    keyboard = []
    for group in rows:
        exists = cursor.execute('''
            SELECT 1 FROM student_group 
            WHERE student_id = ? AND group_id = ?
        ''', (student_id, group[0])).fetchone()
        
        if not exists:
            keyboard.append([InlineKeyboardButton(f"📚 {group[1]}", callback_data=f"select_group_{group[0]}")])
    
    if not keyboard:
        await q.edit_message_text("✅ Ученик уже во всех группах.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="admin_add_to_group")]]))
        return
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_add_to_group")])
    await q.edit_message_text("Выберите группу:", reply_markup=InlineKeyboardMarkup(keyboard))

async def add_student_to_group(q, student_id, group_id):
    try:
        cursor.execute(
            "INSERT INTO student_group (student_id, group_id) VALUES (?, ?)",
            (student_id, group_id)
        )
        conn.commit()
        
        student = cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,)).fetchone()
        group = cursor.execute("SELECT name FROM groups WHERE id = ?", (group_id,)).fetchone()
        
        await q.edit_message_text(
            f"✅ {student[0]} добавлен в группу «{group[0]}»",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Добавить ещё", callback_data="admin_add_to_group")]])
        )
    except Exception as e:
        logger.error(f"Error adding student to group: {e}")
        await q.edit_message_text("❌ Ошибка при добавлении.")

# ========== УЧЕНИКИ ==========
async def show_balance(user_id, q):
    sid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (user_id,)).fetchone()
    if sid:
        await show_balance_by_id(sid[0], q)
    else:
        await q.edit_message_text("Ты не ученик")

async def show_balance_by_id(sid, q):
    rows = cursor.execute('''
        SELECT lessons_left, valid_until, status, purchase_date, activation_date
        FROM memberships 
        WHERE student_id = ? AND status IN ('active','pending')
        ORDER BY 
            CASE status
                WHEN 'active' THEN 1
                WHEN 'pending' THEN 2
            END
    ''', (sid,)).fetchall()
    
    if not rows:
        msg = "📭 Нет абонементов"
    else:
        msg = "📊 *Твои абонементы:*\n\n"
        for r in rows:
            lessons, valid, status, purchase, activation = r
            if lessons < 0:
                balance_display = f"⛔ Долг: {abs(lessons)}"
            else:
                balance_display = f"Осталось: {lessons}"
            
            if status == 'pending':
                msg += f"⏳ *Ожидает активации*\n├ Куплен: {purchase}\n└ {balance_display}\n\n"
            else:
                msg += f"✅ *Активен*\n├ Действует до: {valid}\n├ {balance_display}\n├ Куплен: {purchase}\n└ Активирован: {activation}\n\n"
    
    kb = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_children")]]
    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def show_attendance(user_id, q):
    sid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (user_id,)).fetchone()
    if sid:
        await show_attendance_by_id(sid[0], q)
    else:
        await q.edit_message_text("Ты не ученик")

async def show_attendance_by_id(sid, q):
    rows = cursor.execute('''
        SELECT date, present FROM attendance 
        WHERE student_id = ? 
        ORDER BY date DESC 
        LIMIT 15
    ''', (sid,)).fetchall()
    
    if not rows:
        msg = "📅 Посещений пока нет"
    else:
        msg = "📅 *Последние посещения:*\n\n"
        for r in rows:
            status = "✅" if r[1] == 1 else "❌"
            msg += f"{status} {r[0]}\n"
    
    kb = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_children")]]
    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def show_child_menu(cid, q):
    name = cursor.execute("SELECT name FROM students WHERE id = ?", (cid,)).fetchone()[0]
    kb = [
        [InlineKeyboardButton("📊 Баланс", callback_data=f"child_balance_{cid}")],
        [InlineKeyboardButton("📅 Посещения", callback_data=f"child_attendance_{cid}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_children")]
    ]
    await q.edit_message_text(f"👤 {name}\nВыберите действие:", reply_markup=InlineKeyboardMarkup(kb))

async def show_parent_children(pid, q):
    parent = cursor.execute("SELECT id, name FROM parents WHERE telegram_id = ?", (pid,)).fetchone()
    if not parent:
        return
    
    rows = cursor.execute('''
        SELECT students.id, students.name FROM students
        JOIN parent_child ON students.id = parent_child.student_id
        WHERE parent_child.parent_id = ?
    ''', (parent[0],)).fetchall()
    
    if not rows:
        await q.edit_message_text("👪 У вас пока нет привязанных детей.")
        return
    
    kb = [[InlineKeyboardButton(f"👤 {r[1]}", callback_data=f"child_{r[0]}")] for r in rows]
    await q.edit_message_text(f"👪 Ваши дети:", reply_markup=InlineKeyboardMarkup(kb))

# ========== АДМИН-СПИСКИ ==========
async def show_all_students(q=None, message=None):
    rows = cursor.execute('''
        SELECT s.name, s.phone, s.telegram_id, g.name
        FROM students s
        LEFT JOIN student_group sg ON s.id = sg.student_id
        LEFT JOIN groups g ON sg.group_id = g.id
        ORDER BY s.name
        LIMIT 30
    ''').fetchall()
    
    if not rows:
        msg = "👥 Учеников пока нет"
    else:
        msg = "👥 *Список учеников:*\n\n"
        for r in rows:
            name, phone, tg, group = r
            group_text = f" 📚 {group}" if group else ""
            msg += f"▫️ *{name}* 📞 {phone} 🆔 `{tg}`{group_text}\n"
    
    keyboard = [
        [InlineKeyboardButton("➕ Добавить ученика", callback_data="add_student_button")],
        [InlineKeyboardButton("❌ Удалить ученика", callback_data="delete_student_button")],
        [InlineKeyboardButton("🔙 Назад", callback_data="start")]
    ]
    
    if q:
        await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif message:
        await message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_all_parents(q=None, message=None):
    rows = cursor.execute('''
        SELECT p.name, p.phone, p.telegram_id,
               COUNT(pc.student_id) as children_count
        FROM parents p
        LEFT JOIN parent_child pc ON p.id = pc.parent_id
        GROUP BY p.id
        ORDER BY p.name
        LIMIT 20
    ''').fetchall()
    
    if not rows:
        msg = "👪 Родителей пока нет"
    else:
        msg = "👪 *Список родителей:*\n\n"
        for r in rows:
            name, phone, tg, cnt = r
            msg += f"▫️ *{name}* 📞 {phone} 🆔 `{tg}` 👦 Детей: {cnt}\n"
    
    keyboard = [
        [InlineKeyboardButton("➕ Добавить родителя", callback_data="add_parent_button")],
        [InlineKeyboardButton("🔗 Привязать ребёнка", callback_data="link_child_button")],
        [InlineKeyboardButton("🔙 Назад", callback_data="start")]
    ]
    
    if q:
        await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif message:
        await message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_groups_menu(q):
    rows = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
    
    if not rows:
        await q.edit_message_text("📚 Группы пока не созданы.\nИспользуй /add_group Название")
        return
    
    kb = [[InlineKeyboardButton(f"📚 {r[1]}", callback_data=f"group_{r[0]}")] for r in rows]
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
    await q.edit_message_text("📚 Выберите группу:", reply_markup=InlineKeyboardMarkup(kb))

async def show_group_students(gid, q):
    group = cursor.execute("SELECT name FROM groups WHERE id=?", (gid,)).fetchone()
    if not group:
        return
    
    rows = cursor.execute('''
        SELECT students.name, students.phone, students.telegram_id
        FROM students 
        JOIN student_group ON students.id = student_group.student_id
        WHERE student_group.group_id = ?
    ''', (gid,)).fetchall()
    
    if not rows:
        msg = f"📚 *{group[0]}*\n\nВ группе пока нет учеников"
    else:
        msg = f"📚 *{group[0]}*\n\n"
        for r in rows:
            msg += f"▫️ {r[0]} 📞 {r[1]} 🆔 `{r[2]}`\n"
    
    kb = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_groups")]]
    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ========== АДМИН-КОМАНДЫ ==========
async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        name = " ".join(context.args)
        cursor.execute("INSERT OR IGNORE INTO groups (name) VALUES (?)", (name,))
        conn.commit()
        await update.message.reply_text(f"✅ Группа «{name}» создана")
    except:
        await update.message.reply_text("❌ /add_group Название")

async def add_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        tid, lessons, days = int(context.args[0]), int(context.args[1]), int(context.args[2])
        sid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (tid,)).fetchone()
        if not sid:
            await update.message.reply_text("❌ Ученик не найден")
            return
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute('''
            INSERT INTO memberships (student_id, lessons_total, lessons_left, purchase_date, status)
            VALUES (?, ?, ?, ?, 'pending')
        ''', (sid[0], lessons, lessons, today))
        conn.commit()
        await update.message.reply_text(f"✅ Абонемент добавлен (ожидание), {lessons} занятий")
    except:
        await update.message.reply_text("❌ /add_membership TelegramID занятий дней")

async def mark_visited(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старая команда для одиночной отметки (оставлена для совместимости)"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        tid = int(context.args[0])
        sid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (tid,)).fetchone()
        if not sid:
            await update.message.reply_text("❌ Ученик не найден")
            return
        sid = sid[0]
        today = datetime.now().strftime("%Y-%m-%d")
        
        mem = cursor.execute('''
            SELECT id, lessons_left, status FROM memberships
            WHERE student_id = ? AND status IN ('active','pending')
            ORDER BY 
                CASE status
                    WHEN 'active' THEN 1
                    WHEN 'pending' THEN 2
                END,
                purchase_date ASC
            LIMIT 1
        ''', (sid,)).fetchone()
        
        if not mem:
            cursor.execute('''
                INSERT INTO memberships (student_id, lessons_total, lessons_left, purchase_date, status)
                VALUES (?, 0, -1, ?, 'debt')
            ''', (sid, today))
            mem_id = cursor.lastrowid
            new_left = -1
        else:
            mem_id, lessons_left, status = mem
            new_left = lessons_left - 1
            
            if status == 'pending' and lessons_left > 0:
                valid_until = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
                cursor.execute('''
                    UPDATE memberships 
                    SET status='active', activation_date=?, valid_until=?, lessons_left=?
                    WHERE id=?
                ''', (today, valid_until, new_left, mem_id))
            else:
                cursor.execute("UPDATE memberships SET lessons_left=? WHERE id=?", (new_left, mem_id))
        
        cursor.execute("INSERT INTO attendance (student_id, date, membership_id, present) VALUES (?, ?, ?, 1)", 
                      (sid, today, mem_id))
        conn.commit()
        
        await check_and_notify_admin(sid, new_left, context)
        
        if new_left < 0:
            await update.message.reply_text(f"⛔ Посещение отмечено. Долг: {abs(new_left)}")
        elif new_left == 0:
            await update.message.reply_text(f"⚠️ Посещение отмечено. Занятия закончились!")
        else:
            await update.message.reply_text(f"✅ Посещение отмечено! Осталось: {new_left}")
    except:
        await update.message.reply_text("❌ /mark_visited TelegramID")

async def extend_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        tid, days = int(context.args[0]), int(context.args[1])
        sid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (tid,)).fetchone()
        if not sid:
            await update.message.reply_text("❌ Ученик не найден")
            return
        mem = cursor.execute('''
            SELECT id, valid_until FROM memberships
            WHERE student_id = ? AND status='active'
            ORDER BY valid_until ASC
            LIMIT 1
        ''', (sid[0],)).fetchone()
        if not mem:
            await update.message.reply_text("❌ Нет активных абонементов")
            return
        new_date = (datetime.strptime(mem[1], "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute("UPDATE memberships SET valid_until=? WHERE id=?", (new_date, mem[0]))
        conn.commit()
        await update.message.reply_text(f"✅ Продлён до {new_date}")
    except:
        await update.message.reply_text("❌ /extend TelegramID дни")

# ========== ОТМЕНА ==========
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Разговоры
    student_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_student_start, pattern="^add_student_button$")],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_phone)],
            TG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(student_conv)
    
    parent_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_parent_start, pattern="^add_parent_button$")],
        states={
            PARENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_name)],
            PARENT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_phone)],
            PARENT_TG: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(parent_conv)
    
    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_group", add_group))
    app.add_handler(CommandHandler("add_membership", add_membership))
    app.add_handler(CommandHandler("mark_visited", mark_visited))
    app.add_handler(CommandHandler("extend", extend_days))
    
    # Кнопки
    app.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("🚀 Бот с отметкой групп и полным функционалом запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
