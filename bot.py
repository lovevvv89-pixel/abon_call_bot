import os
import logging
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters

# Состояния для разговоров
NAME, PHONE, TG_ID, PARENT_NAME, PARENT_PHONE, PARENT_TG, LESSONS, DAYS, MEM_TG_ID, EXTEND_DAYS, GROUP_NAME = range(11)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ========== БЕЗОПАСНОЕ ЧТЕНИЕ ADMIN ID ==========
admin_chat_id_raw = os.getenv("ADMIN_CHAT_ID", "")
admin_chat_id_clean = ''.join(c for c in admin_chat_id_raw if c.isdigit() or c == ',')
ADMIN_IDS = [int(x) for x in admin_chat_id_clean.split(',') if x.strip()]
BOT_TOKEN = os.getenv("BOT_TOKEN")

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
    lessons_left INTEGER DEFAULT 0,
    valid_until TEXT,
    status TEXT DEFAULT 'active',
    purchase_date TEXT,
    activation_date TEXT,
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
async def notify_admin(student_id, new_balance, context):
    student = cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        return
    student_name = student[0]
    if new_balance == 1:
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, f"⚠️ У {student_name} последнее занятие!")
            except:
                pass
    elif new_balance == 0:
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, f"❌ У {student_name} закончились занятия!")
            except:
                pass
    elif new_balance < 0:
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, f"⛔ У {student_name} долг: {abs(new_balance)} занятий")
            except:
                pass

# ========== ПЛАНИРОВЩИК ==========
async def check_expiring_memberships(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().strftime("%Y-%m-%d")
    three_days_later = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    
    expiring_soon = cursor.execute('''
        SELECT m.id, s.id, s.name, s.telegram_id, p.telegram_id
        FROM memberships m
        JOIN students s ON m.student_id = s.id
        LEFT JOIN parent_child pc ON s.id = pc.student_id
        LEFT JOIN parents p ON pc.parent_id = p.id
        WHERE m.status = 'active' AND m.valid_until = ?
    ''', (three_days_later,)).fetchall()
    
    for mem in expiring_soon:
        _, student_id, student_name, student_tg, parent_tg = mem
        msg = f"⏰ Напоминание: через 3 дня заканчивается абонемент у {student_name}"
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, msg)
            except:
                pass
        if student_tg:
            try:
                await context.bot.send_message(student_tg, f"⏰ {student_name}, через 3 дня заканчивается абонемент")
            except:
                pass
        if parent_tg:
            try:
                await context.bot.send_message(parent_tg, f"⏰ У {student_name} через 3 дня заканчивается абонемент")
            except:
                pass
    
    expired_today = cursor.execute('''
        SELECT m.id, s.id, s.name, s.telegram_id, p.telegram_id
        FROM memberships m
        JOIN students s ON m.student_id = s.id
        LEFT JOIN parent_child pc ON s.id = pc.student_id
        LEFT JOIN parents p ON pc.parent_id = p.id
        WHERE m.status = 'active' AND m.valid_until = ?
    ''', (today,)).fetchall()
    
    for mem in expired_today:
        mem_id, student_id, student_name, student_tg, parent_tg = mem
        cursor.execute("UPDATE memberships SET status = 'expired' WHERE id = ?", (mem_id,))
        conn.commit()
        msg = f"❌ У {student_name} истёк срок абонемента"
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, msg)
            except:
                pass
        if student_tg:
            try:
                await context.bot.send_message(student_tg, f"❌ {student_name}, срок абонемента истёк")
            except:
                pass
        if parent_tg:
            try:
                await context.bot.send_message(parent_tg, f"❌ У {student_name} истёк срок абонемента")
            except:
                pass

# ========== СТАРТ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS:
        keyboard = [
            [InlineKeyboardButton("👥 Ученики", callback_data="admin_students")],
            [InlineKeyboardButton("📚 Группы", callback_data="admin_groups")],
            [InlineKeyboardButton("👪 Родители", callback_data="admin_parents")],
            [InlineKeyboardButton("➕ Добавить ученика", callback_data="add_student")],
            [InlineKeyboardButton("➕ Добавить родителя", callback_data="add_parent")],
            [InlineKeyboardButton("🎟 Абонемент", callback_data="add_membership")],
            [InlineKeyboardButton("➕ Создать группу", callback_data="add_group")],
            [InlineKeyboardButton("📚 Добавить в группу", callback_data="add_to_group")],
            [InlineKeyboardButton("🔗 Привязать родителя", callback_data="link_parent")],
            [InlineKeyboardButton("📋 Отметить группу", callback_data="mark_group")],
            [InlineKeyboardButton("⏱ Продлить", callback_data="extend_menu")],
        ]
        await update.message.reply_text("🔐 Админ-панель", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    parent = cursor.execute("SELECT id, name FROM parents WHERE telegram_id = ?", (user_id,)).fetchone()
    if parent:
        children = cursor.execute('''
            SELECT s.id, s.name FROM students s
            JOIN parent_child pc ON s.id = pc.student_id
            WHERE pc.parent_id = ?
        ''', (parent[0],)).fetchall()
        if children:
            kb = [[InlineKeyboardButton(f"👤 {ch[1]}", callback_data=f"child_{ch[0]}")] for ch in children]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
            await update.message.reply_text(f"👪 Ваши дети:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text("👪 У вас нет привязанных детей")
        return
    student = cursor.execute("SELECT id, name FROM students WHERE telegram_id = ?", (user_id,)).fetchone()
    if student:
        keyboard = [
            [InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{student[0]}")],
            [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{student[0]}")],
        ]
        await update.message.reply_text(f"👋 {student[1]}", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    await update.message.reply_text("👋 Ты не зарегистрирован")

# ========== КНОПКИ ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = update.effective_user.id
    
    if data.startswith("balance_"):
        student_id = int(data.split("_")[1])
        await show_balance(student_id, q)
    elif data.startswith("attendance_"):
        student_id = int(data.split("_")[1])
        await show_attendance(student_id, q)
    elif data.startswith("child_"):
        student_id = int(data.split("_")[1])
        await show_child_menu(student_id, q)
    elif data == "back_to_children":
        await show_parent_children(user_id, q)
    
    if user_id in ADMIN_IDS:
        if data == "admin_students":
            await show_all_students(q)
        elif data == "admin_groups":
            await show_groups_menu(q)
        elif data.startswith("group_"):
            await show_group_students(int(data.split("_")[1]), q)
        elif data == "admin_parents":
            await show_all_parents(q)
        elif data == "add_student":
            await q.edit_message_text("✏️ Введите имя ученика:")
            return NAME
        elif data == "add_parent":
            await q.edit_message_text("✏️ Введите имя родителя:")
            return PARENT_NAME
        elif data == "add_membership":
            await q.edit_message_text("🔢 Введите количество занятий:")
            return LESSONS
        elif data == "add_group":
            await q.edit_message_text("✏️ Введите название группы:")
            return GROUP_NAME
        elif data == "add_to_group":
            await show_students_for_group(q)
        elif data.startswith("select_student_"):
            student_id = int(data.split("_")[2])
            context.user_data['selected_student'] = student_id
            await show_groups_for_student(q, student_id)
        elif data.startswith("select_group_"):
            student_id = context.user_data.get('selected_student')
            group_id = int(data.split("_")[2])
            cursor.execute("INSERT OR IGNORE INTO student_group (student_id, group_id) VALUES (?, ?)", (student_id, group_id))
            conn.commit()
            await q.edit_message_text("✅ Добавлено", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="add_to_group")]]))
        elif data == "link_parent":
            await show_students_for_link(q)
        elif data.startswith("link_student_"):
            student_id = int(data.split("_")[2])
            context.user_data['link_student'] = student_id
            await show_parents_for_link(q)
        elif data.startswith("link_parent_"):
            parent_id = int(data.split("_")[2])
            student_id = context.user_data.get('link_student')
            cursor.execute("INSERT OR IGNORE INTO parent_child (parent_id, student_id) VALUES (?, ?)", (parent_id, student_id))
            conn.commit()
            await q.edit_message_text("✅ Привязано", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="link_parent")]]))
        elif data == "mark_group":
            await show_groups_for_mark(q)
        elif data.startswith("mark_group_"):
            group_id = int(data.split("_")[2])
            await show_students_for_mark(q, group_id, context)
        elif data.startswith("mark_student_"):
            parts = data.split("_")
            student_id = int(parts[2])
            present = int(parts[3])
            group_id = int(parts[4])
            await mark_student(q, student_id, present, group_id, context)
        elif data.startswith("delete_student_"):
            student_id = int(data.split("_")[2])
            await confirm_delete_student(q, student_id)
        elif data.startswith("confirm_delete_"):
            student_id = int(data.split("_")[2])
            await delete_student_final(q, student_id)
        elif data == "extend_menu":
            await show_students_for_extend(q)
        elif data.startswith("extend_student_"):
            student_id = int(data.split("_")[2])
            context.user_data['extend_student'] = student_id
            await q.edit_message_text("📅 Введите количество дней для продления:")
            return EXTEND_DAYS

# ========== УЧЕНИКИ ==========
async def show_balance(student_id, q):
    mem = cursor.execute('''
        SELECT lessons_left, valid_until FROM memberships
        WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
    ''', (student_id,)).fetchone()
    if mem:
        left, valid = mem
        text = f"📊 Осталось: {left}\n📅 Действует до: {valid}"
    else:
        text = "📭 Нет активных абонементов"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_to_children")]]))

async def show_attendance(student_id, q):
    rows = cursor.execute('''
        SELECT date FROM attendance
        WHERE student_id = ?
        ORDER BY date DESC LIMIT 10
    ''', (student_id,)).fetchall()
    if rows:
        text = "📅 Посещения:\n" + "\n".join([f"▫️ {r[0]}" for r in rows])
    else:
        text = "📅 Посещений нет"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_to_children")]]))

async def show_child_menu(student_id, q):
    name = cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,)).fetchone()[0]
    kb = [
        [InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{student_id}")],
        [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{student_id}")],
        [InlineKeyboardButton("🔙", callback_data="back_to_children")]
    ]
    await q.edit_message_text(f"👤 {name}", reply_markup=InlineKeyboardMarkup(kb))

async def show_parent_children(pid, q):
    parent = cursor.execute("SELECT id FROM parents WHERE telegram_id = ?", (pid,)).fetchone()
    if not parent:
        return
    children = cursor.execute('''
        SELECT s.id, s.name FROM students s
        JOIN parent_child pc ON s.id = pc.student_id
        WHERE pc.parent_id = ?
    ''', (parent[0],)).fetchall()
    kb = [[InlineKeyboardButton(f"👤 {ch[1]}", callback_data=f"child_{ch[0]}")] for ch in children]
    kb.append([InlineKeyboardButton("🔙", callback_data="start")])
    await q.edit_message_text("👪 Дети:", reply_markup=InlineKeyboardMarkup(kb))

# ========== АДМИН-СПИСКИ ==========
async def show_all_students(q):
    rows = cursor.execute('''
        SELECT s.id, s.name, s.phone, s.telegram_id, g.name
        FROM students s
        LEFT JOIN student_group sg ON s.id = sg.student_id
        LEFT JOIN groups g ON sg.group_id = g.id
        ORDER BY s.name
    ''').fetchall()
    if not rows:
        text = "👥 Учеников нет"
    else:
        text = "👥 Список:\n"
        for r in rows:
            text += f"\n▫️ {r[1]} {r[2]} 🆔 {r[3]}" + (f" [{r[4]}]" if r[4] else "")
    kb = [
        [InlineKeyboardButton("➕ Добавить ученика", callback_data="add_student")],
        [InlineKeyboardButton("❌ Удалить ученика", callback_data="delete_student_start")],
        [InlineKeyboardButton("🔙", callback_data="start")]
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def show_groups_menu(q):
    rows = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
    if not rows:
        await q.edit_message_text("📚 Групп нет", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="start")]]))
        return
    kb = [[InlineKeyboardButton(f"📚 {r[1]}", callback_data=f"group_{r[0]}")] for r in rows]
    kb.append([InlineKeyboardButton("🔙", callback_data="start")])
    await q.edit_message_text("📚 Группы:", reply_markup=InlineKeyboardMarkup(kb))

async def show_group_students(group_id, q):
    group = cursor.execute("SELECT name FROM groups WHERE id = ?", (group_id,)).fetchone()
    rows = cursor.execute('''
        SELECT s.name, s.phone FROM students s
        JOIN student_group sg ON s.id = sg.student_id
        WHERE sg.group_id = ?
    ''', (group_id,)).fetchall()
    text = f"📚 {group[0]}\n"
    if rows:
        text += "\n".join([f"▫️ {r[0]} {r[1]}" for r in rows])
    else:
        text += "\nНет учеников"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="admin_groups")]]))

async def show_all_parents(q):
    rows = cursor.execute('''
        SELECT p.name, p.phone, p.telegram_id, COUNT(pc.student_id)
        FROM parents p
        LEFT JOIN parent_child pc ON p.id = pc.parent_id
        GROUP BY p.id
    ''').fetchall()
    if not rows:
        text = "👪 Родителей нет"
    else:
        text = "👪 Родители:\n"
        for r in rows:
            text += f"\n▫️ {r[0]} {r[1]} 🆔 {r[2]} 👦 {r[3]}"
    kb = [
        [InlineKeyboardButton("➕ Добавить родителя", callback_data="add_parent")],
        [InlineKeyboardButton("🔙", callback_data="start")]
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

# ========== УДАЛЕНИЕ УЧЕНИКА ==========
async def show_students_for_delete(q):
    rows = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
    if not rows:
        await q.edit_message_text("👥 Нет учеников")
        return
    kb = [[InlineKeyboardButton(f"❌ {r[1]}", callback_data=f"delete_student_{r[0]}")] for r in rows]
    kb.append([InlineKeyboardButton("🔙", callback_data="admin_students")])
    await q.edit_message_text("Выбери ученика для удаления:", reply_markup=InlineKeyboardMarkup(kb))

async def confirm_delete_student(q, student_id):
    student = cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,)).fetchone()
    kb = [
        [InlineKeyboardButton("✅ Да", callback_data=f"confirm_delete_{student_id}")],
        [InlineKeyboardButton("❌ Нет", callback_data="admin_students")]
    ]
    await q.edit_message_text(f"Удалить {student[0]}?", reply_markup=InlineKeyboardMarkup(kb))

async def delete_student_final(q, student_id):
    cursor.execute("DELETE FROM students WHERE id = ?", (student_id,))
    conn.commit()
    await q.edit_message_text("✅ Ученик удалён", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="admin_students")]]))

# ========== ДОБАВЛЕНИЕ В ГРУППУ ==========
async def show_students_for_group(q):
    rows = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
    if not rows:
        await q.edit_message_text("👥 Нет учеников")
        return
    kb = [[InlineKeyboardButton(f"👤 {r[1]}", callback_data=f"select_student_{r[0]}")] for r in rows]
    kb.append([InlineKeyboardButton("🔙", callback_data="start")])
    await q.edit_message_text("Выбери ученика:", reply_markup=InlineKeyboardMarkup(kb))

async def show_groups_for_student(q, student_id):
    rows = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
    if not rows:
        await q.edit_message_text("📚 Нет групп")
        return
    kb = []
    for g in rows:
        exists = cursor.execute('''
            SELECT 1 FROM student_group WHERE student_id = ? AND group_id = ?
        ''', (student_id, g[0])).fetchone()
        if not exists:
            kb.append([InlineKeyboardButton(f"📚 {g[1]}", callback_data=f"select_group_{g[0]}")])
    kb.append([InlineKeyboardButton("🔙", callback_data="add_to_group")])
    await q.edit_message_text("Выбери группу:", reply_markup=InlineKeyboardMarkup(kb))

# ========== ПРИВЯЗКА РОДИТЕЛЯ ==========
async def show_students_for_link(q):
    rows = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
    if not rows:
        await q.edit_message_text("👥 Нет учеников")
        return
    kb = [[InlineKeyboardButton(f"👤 {r[1]}", callback_data=f"link_student_{r[0]}")] for r in rows]
    kb.append([InlineKeyboardButton("🔙", callback_data="start")])
    await q.edit_message_text("Выбери ученика:", reply_markup=InlineKeyboardMarkup(kb))

async def show_parents_for_link(q):
    rows = cursor.execute("SELECT id, name FROM parents ORDER BY name").fetchall()
    if not rows:
        await q.edit_message_text("👪 Нет родителей")
        return
    kb = [[InlineKeyboardButton(f"👪 {r[1]}", callback_data=f"link_parent_{r[0]}")] for r in rows]
    kb.append([InlineKeyboardButton("🔙", callback_data="link_parent")])
    await q.edit_message_text("Выбери родителя:", reply_markup=InlineKeyboardMarkup(kb))

# ========== ОТМЕТКА ГРУППЫ ==========
async def show_groups_for_mark(q):
    rows = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
    if not rows:
        await q.edit_message_text("📚 Нет групп")
        return
    kb = [[InlineKeyboardButton(f"📚 {r[1]}", callback_data=f"mark_group_{r[0]}")] for r in rows]
    kb.append([InlineKeyboardButton("🔙", callback_data="start")])
    await q.edit_message_text("Выбери группу для отметки:", reply_markup=InlineKeyboardMarkup(kb))

async def show_students_for_mark(q, group_id, context):
    group = cursor.execute("SELECT name FROM groups WHERE id = ?", (group_id,)).fetchone()
    students = cursor.execute('''
        SELECT s.id, s.name FROM students s
        JOIN student_group sg ON s.id = sg.student_id
        WHERE sg.group_id = ?
    ''', (group_id,)).fetchall()
    if not students:
        await q.edit_message_text(f"📚 {group[0]}\nНет учеников")
        return
    context.user_data['mark_group_id'] = group_id
    kb = []
    for s in students:
        kb.append([
            InlineKeyboardButton(f"{s[1]} ✅", callback_data=f"mark_student_{s[0]}_1_{group_id}"),
            InlineKeyboardButton("❌", callback_data=f"mark_student_{s[0]}_0_{group_id}")
        ])
    kb.append([InlineKeyboardButton("✅ Все", callback_data=f"mark_all_1_{group_id}"),
               InlineKeyboardButton("❌ Все", callback_data=f"mark_all_0_{group_id}")])
    kb.append([InlineKeyboardButton("🔙", callback_data="mark_group")])
    today = datetime.now().strftime("%d.%m.%Y")
    await q.edit_message_text(f"📋 {group[0]} на {today}", reply_markup=InlineKeyboardMarkup(kb))

async def mark_student(q, student_id, present, group_id, context):
    student = cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,)).fetchone()
    today = datetime.now().strftime("%Y-%m-%d")
    if present == 1:
        mem = cursor.execute('''
            SELECT id, lessons_left FROM memberships
            WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
            ORDER BY valid_until ASC LIMIT 1
        ''', (student_id,)).fetchone()
        if mem:
            mem_id, left = mem
            new_left = left - 1
            cursor.execute("UPDATE memberships SET lessons_left = ? WHERE id = ?", (new_left, mem_id))
            cursor.execute("INSERT INTO attendance (student_id, date) VALUES (?, ?)", (student_id, today))
            conn.commit()
            await notify_admin(student_id, new_left, context)
        else:
            cursor.execute("INSERT INTO attendance (student_id, date) VALUES (?, ?)", (student_id, today))
            conn.commit()
    else:
        cursor.execute("INSERT INTO attendance (student_id, date, present) VALUES (?, ?, 0)", (student_id, today))
        conn.commit()
    await q.answer(f"{'✅' if present else '❌'} {student[0]}")
    await show_students_for_mark(q, group_id, context)

# ========== ПРОДЛЕНИЕ ==========
async def show_students_for_extend(q):
    rows = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
    if not rows:
        await q.edit_message_text("👥 Нет учеников")
        return
    kb = [[InlineKeyboardButton(f"👤 {r[1]}", callback_data=f"extend_student_{r[0]}")] for r in rows]
    kb.append([InlineKeyboardButton("🔙", callback_data="start")])
    await q.edit_message_text("Выбери ученика для продления:", reply_markup=InlineKeyboardMarkup(kb))

# ========== ДОБАВЛЕНИЕ УЧЕНИКА ==========
async def add_student_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_student_name'] = update.message.text
    await update.message.reply_text("📞 Телефон:")
    return PHONE

async def add_student_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_student_phone'] = update.message.text
    await update.message.reply_text("🆔 Telegram ID:")
    return TG_ID

async def add_student_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tg_id = int(update.message.text)
        name = context.user_data['new_student_name']
        phone = context.user_data['new_student_phone']
        cursor.execute("INSERT INTO students (telegram_id, name, phone) VALUES (?, ?, ?)", (tg_id, name, phone))
        conn.commit()
        await update.message.reply_text("✅ Ученик добавлен")
    except:
        await update.message.reply_text("❌ Ошибка")
    context.user_data.clear()
    return ConversationHandler.END

# ========== ДОБАВЛЕНИЕ РОДИТЕЛЯ ==========
async def add_parent_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_parent_name'] = update.message.text
    await update.message.reply_text("📞 Телефон:")
    return PARENT_PHONE

async def add_parent_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_parent_phone'] = update.message.text
    await update.message.reply_text("🆔 Telegram ID:")
    return PARENT_TG

async def add_parent_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tg_id = int(update.message.text)
        name = context.user_data['new_parent_name']
        phone = context.user_data['new_parent_phone']
        cursor.execute("INSERT INTO parents (telegram_id, name, phone) VALUES (?, ?, ?)", (tg_id, name, phone))
        conn.commit()
        await update.message.reply_text("✅ Родитель добавлен")
    except:
        await update.message.reply_text("❌ Ошибка")
    context.user_data.clear()
    return ConversationHandler.END

# ========== ДОБАВЛЕНИЕ АБОНЕМЕНТА ==========
async def add_membership_lessons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lessons = int(update.message.text)
        context.user_data['mem_lessons'] = lessons
        await update.message.reply_text("📅 Введите количество дней:")
        return DAYS
    except:
        await update.message.reply_text("❌ Введите число")
        return LESSONS

async def add_membership_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text)
        context.user_data['mem_days'] = days
        today = datetime.now().strftime("%Y-%m-%d")
        context.user_data['purchase_date'] = today
        await update.message.reply_text("🆔 Введите Telegram ID ученика:")
        return MEM_TG_ID
    except:
        await update.message.reply_text("❌ Введите число")
        return DAYS

async def add_membership_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tg_id = int(update.message.text)
        student = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (tg_id,)).fetchone()
        if not student:
            await update.message.reply_text("❌ Ученик не найден")
            return ConversationHandler.END
        lessons = context.user_data.get('mem_lessons')
        days = context.user_data.get('mem_days')
        purchase_date = context.user_data.get('purchase_date')
        valid_until = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute('''
            INSERT INTO memberships (student_id, lessons_left, valid_until, status, purchase_date)
            VALUES (?, ?, ?, 'active', ?)
        ''', (student[0], lessons, valid_until, purchase_date))
        conn.commit()
        await update.message.reply_text("✅ Абонемент добавлен")
    except Exception as e:
        logger.error(f"Ошибка добавления абонемента: {e}")
        await update.message.reply_text("❌ Ошибка")
    context.user_data.clear()
    return ConversationHandler.END

# ========== ДОБАВЛЕНИЕ ГРУППЫ ==========
async def add_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_name = update.message.text
    try:
        cursor.execute("INSERT INTO groups (name) VALUES (?)", (group_name,))
        conn.commit()
        await update.message.reply_text(f"✅ Группа «{group_name}» создана")
    except:
        await update.message.reply_text("❌ Ошибка: группа уже существует или другое")
    context.user_data.clear()
    return ConversationHandler.END

# ========== ПРОДЛЕНИЕ (диалог) ==========
async def extend_days_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text)
        student_id = context.user_data.get('extend_student')
        mem = cursor.execute('''
            SELECT id, valid_until FROM memberships
            WHERE student_id = ? AND status = 'active'
            ORDER BY valid_until ASC LIMIT 1
        ''', (student_id,)).fetchone()
        if mem:
            new_date = (datetime.strptime(mem[1], "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
            cursor.execute("UPDATE memberships SET valid_until = ? WHERE id = ?", (new_date, mem[0]))
            conn.commit()
            await update.message.reply_text(f"✅ Продлён до {new_date}")
        else:
            await update.message.reply_text("❌ Нет активных абонементов")
    except:
        await update.message.reply_text("❌ Введите число")
    context.user_data.clear()
    return ConversationHandler.END

# ========== ОТМЕНА ==========
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено")
    context.user_data.clear()
    return ConversationHandler.END

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    
    student_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(lambda u,c: NAME, pattern="^add_student$")],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_phone)],
            TG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(student_conv)
    
    parent_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(lambda u,c: PARENT_NAME, pattern="^add_parent$")],
        states={
            PARENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_name)],
            PARENT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_phone)],
            PARENT_TG: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(parent_conv)
    
    mem_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(lambda u,c: LESSONS, pattern="^add_membership$")],
        states={
            LESSONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_membership_lessons)],
            DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_membership_days)],
            MEM_TG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_membership_final)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(mem_conv)
    
    group_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(lambda u,c: GROUP_NAME, pattern="^add_group$")],
        states={
            GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_group_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(group_conv)
    
    extend_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(lambda u,c: EXTEND_DAYS, pattern="^extend_student_")],
        states={
            EXTEND_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_days_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(extend_conv)
    
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Планировщик
    job_queue = app.job_queue
    job_queue.run_daily(check_expiring_memberships, time=datetime.strptime("10:00", "%H:%M").time())
    
    logger.info("🚀 Бот с группами и диалогами запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
