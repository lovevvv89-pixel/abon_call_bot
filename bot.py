import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
import sqlite3
from datetime import datetime, timedelta

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID"))

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

# ========== СТАРТ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("👥 Ученики", callback_data="admin_students")],
            [InlineKeyboardButton("📚 Группы", callback_data="admin_groups")],
            [InlineKeyboardButton("➕ Отметить посещение", callback_data="admin_mark")],
            [InlineKeyboardButton("👪 Родители", callback_data="admin_parents")],
            [InlineKeyboardButton("⏱️ Продлить", callback_data="admin_extend")],
            [InlineKeyboardButton("❌ Удалить ученика", callback_data="admin_delete")],
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
    elif data == "admin_students":
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
    elif data == "admin_delete":
        await q.edit_message_text("❌ Удалить ученика:\n`/delete_student TelegramID`", parse_mode="Markdown")

# ========== УЧЕНИКИ ==========
async def show_balance(user_id, q):
    cur = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (user_id,)).fetchone()
    if cur: await show_balance_by_id(cur[0], q)

async def show_balance_by_id(sid, q):
    rows = cursor.execute('''
        SELECT lessons_left, valid_until, status, purchase_date, activation_date
        FROM memberships WHERE student_id = ? AND status IN ('active','pending')
    ''', (sid,)).fetchall()
    msg = "📊 *Баланс:*\n" + "\n".join([f"{'⏳' if r[2]=='pending' else '✅'} Осталось: {r[0]}, до {r[2]}" for r in rows]) if rows else "Нет абонементов"
    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_to_children")]]))

async def show_attendance(user_id, q):
    sid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (user_id,)).fetchone()
    if sid:
        await show_attendance_by_id(sid[0], q)

async def show_attendance_by_id(sid, q):
    rows = cursor.execute("SELECT date FROM attendance WHERE student_id = ? ORDER BY date DESC LIMIT 15", (sid,)).fetchall()
    msg = "📅 *Посещения:*\n" + "\n".join([f"▫️ {r[0]}" for r in rows]) if rows else "Посещений нет"
    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_to_children")]]))

async def show_child_menu(cid, q):
    name = cursor.execute("SELECT name FROM students WHERE id = ?", (cid,)).fetchone()[0]
    kb = [
        [InlineKeyboardButton("📊 Баланс", callback_data=f"child_balance_{cid}")],
        [InlineKeyboardButton("📅 Посещения", callback_data=f"child_attendance_{cid}")],
        [InlineKeyboardButton("🔙", callback_data="back_to_children")]
    ]
    await q.edit_message_text(f"👤 {name}", reply_markup=InlineKeyboardMarkup(kb))

async def show_parent_children(pid, q):
    parent = cursor.execute("SELECT id FROM parents WHERE telegram_id = ?", (pid,)).fetchone()
    if not parent: return
    rows = cursor.execute('''
        SELECT students.id, students.name FROM students
        JOIN parent_child ON students.id = parent_child.student_id
        WHERE parent_child.parent_id = ?
    ''', (parent[0],)).fetchall()
    kb = [[InlineKeyboardButton(f"👤 {r[1]}", callback_data=f"child_{r[0]}")] for r in rows]
    await q.edit_message_text("👪 Ваши дети:", reply_markup=InlineKeyboardMarkup(kb))

# ========== АДМИН ==========
async def add_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        _, name, phone, tid = update.message.text.split()
        cursor.execute("INSERT OR IGNORE INTO students (telegram_id, name, phone) VALUES (?, ?, ?)", (int(tid), name, phone))
        conn.commit()
        await update.message.reply_text(f"✅ {name} добавлен")
    except: await update.message.reply_text("❌ /add_student Имя Телефон TelegramID")

async def delete_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        tid = int(context.args[0])
        cursor.execute("DELETE FROM students WHERE telegram_id = ?", (tid,))
        conn.commit()
        await update.message.reply_text(f"✅ Ученик {tid} удалён")
    except: await update.message.reply_text("❌ /delete_student TelegramID")

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        name = " ".join(context.args)
        cursor.execute("INSERT OR IGNORE INTO groups (name) VALUES (?)", (name,))
        conn.commit()
        await update.message.reply_text(f"✅ Группа «{name}» создана")
    except: await update.message.reply_text("❌ /add_group Название группы")

async def add_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        tid, group_name = int(context.args[0]), " ".join(context.args[1:])
        sid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (tid,)).fetchone()
        gid = cursor.execute("SELECT id FROM groups WHERE name = ?", (group_name,)).fetchone()
        if not sid or not gid: raise
        cursor.execute("INSERT OR IGNORE INTO student_group (student_id, group_id) VALUES (?, ?)", (sid[0], gid[0]))
        conn.commit()
        await update.message.reply_text(f"✅ Ученик добавлен в группу «{group_name}»")
    except: await update.message.reply_text("❌ /add_to_group TelegramID Название_группы")

async def add_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        tid, lessons, days = int(context.args[0]), int(context.args[1]), int(context.args[2])
        sid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (tid,)).fetchone()
        if not sid: raise
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute('''
            INSERT INTO memberships (student_id, lessons_total, lessons_left, purchase_date, status)
            VALUES (?, ?, ?, ?, 'pending')
        ''', (sid[0], lessons, lessons, today))
        conn.commit()
        await update.message.reply_text(f"✅ Абонемент (ожидание) на {lessons} занятий")
    except: await update.message.reply_text("❌ /add_membership TelegramID занятий дней")

async def mark_visited(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        tid = int(context.args[0])
        sid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (tid,)).fetchone()
        if not sid: raise
        sid = sid[0]
        today = datetime.now().strftime("%Y-%m-%d")
        mem = cursor.execute('''
            SELECT id, lessons_left, status FROM memberships
            WHERE student_id = ? AND status IN ('active','pending')
            ORDER BY 
                CASE status WHEN 'active' THEN 1 ELSE 2 END,
                purchase_date ASC LIMIT 1
        ''', (sid,)).fetchone()
        if not mem: raise
        mid, left, status = mem
        if status == 'pending':
            valid = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
            cursor.execute('''
                UPDATE memberships SET status='active', activation_date=?, valid_until=?
                WHERE id=?
            ''', (today, valid, mid))
        new_left = left - 1
        if new_left == 0:
            cursor.execute("UPDATE memberships SET status='finished' WHERE id=?", (mid,))
        else:
            cursor.execute("UPDATE memberships SET lessons_left=? WHERE id=?", (new_left, mid))
        cursor.execute("INSERT INTO attendance (student_id, date, membership_id) VALUES (?, ?, ?)", (sid, today, mid))
        conn.commit()
        await update.message.reply_text(f"✅ Посещение отмечено, осталось {new_left}")
    except: await update.message.reply_text("❌ /mark_visited TelegramID")

async def extend_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        tid, days = int(context.args[0]), int(context.args[1])
        sid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (tid,)).fetchone()
        if not sid: raise
        mem = cursor.execute('''
            SELECT id, valid_until FROM memberships
            WHERE student_id = ? AND status='active' ORDER BY valid_until ASC LIMIT 1
        ''', (sid[0],)).fetchone()
        if not mem: raise
        new_date = (datetime.strptime(mem[1], "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute("UPDATE memberships SET valid_until=? WHERE id=?", (new_date, mem[0]))
        conn.commit()
        await update.message.reply_text(f"✅ Продлён до {new_date}")
    except: await update.message.reply_text("❌ /extend TelegramID дни")

async def add_parent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        _, name, phone, tid = update.message.text.split()
        cursor.execute("INSERT OR IGNORE INTO parents (telegram_id, name, phone) VALUES (?, ?, ?)", (int(tid), name, phone))
        conn.commit()
        await update.message.reply_text(f"✅ Родитель {name} добавлен")
    except: await update.message.reply_text("❌ /add_parent Имя Телефон TelegramID")

async def link_child(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        pt, ct = int(context.args[0]), int(context.args[1])
        pid = cursor.execute("SELECT id FROM parents WHERE telegram_id = ?", (pt,)).fetchone()
        cid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (ct,)).fetchone()
        if not pid or not cid: raise
        cursor.execute("INSERT OR IGNORE INTO parent_child (parent_id, student_id) VALUES (?, ?)", (pid[0], cid[0]))
        conn.commit()
        await update.message.reply_text("✅ Связь создана")
    except: await update.message.reply_text("❌ /link_child parentID childID")

async def show_all_students(q):
    rows = cursor.execute('''
        SELECT s.name, s.phone, s.telegram_id, g.name
        FROM students s
        LEFT JOIN student_group sg ON s.id = sg.student_id
        LEFT JOIN groups g ON sg.group_id = g.id
        ORDER BY s.name LIMIT 30
    ''').fetchall()
    msg = "👥 *Ученики:*\n"
    for r in rows:
        msg += f"▫️ {r[0]} {r[1]} `{r[2]}` {f'[{r[3]}]' if r[3] else ''}\n"
    await q.edit_message_text(msg, parse_mode="Markdown")

async def show_groups_menu(q):
    rows = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
    kb = [[InlineKeyboardButton(f"📚 {r[1]}", callback_data=f"group_{r[0]}")] for r in rows]
    kb.append([InlineKeyboardButton("➕ Новая группа", callback_data="admin_new_group")])
    await q.edit_message_text("📚 Группы:", reply_markup=InlineKeyboardMarkup(kb))

async def show_group_students(gid, q):
    name = cursor.execute("SELECT name FROM groups WHERE id=?", (gid,)).fetchone()[0]
    rows = cursor.execute('''
        SELECT students.name, students.phone, students.telegram_id
        FROM students JOIN student_group ON students.id = student_group.student_id
        WHERE student_group.group_id = ?
    ''', (gid,)).fetchall()
    msg = f"📚 *{name}*\n" + "\n".join([f"▫️ {r[0]} {r[1]} `{r[2]}`" for r in rows]) if rows else "В группе пока никого"
    await q.edit_message_text(msg, parse_mode="Markdown")

async def show_all_parents(q):
    rows = cursor.execute('''
        SELECT p.name, p.phone, p.telegram_id,
               (SELECT COUNT(*) FROM parent_child WHERE parent_id=p.id) as cnt
        FROM parents p ORDER BY p.name LIMIT 20
    ''').fetchall()
    msg = "👪 *Родители:*\n" + "\n".join([f"▫️ {r[0]} {r[1]} `{r[2]}` детей: {r[3]}" for r in rows]) if rows else "Нет родителей"
    await q.edit_message_text(msg, parse_mode="Markdown")

# ========== MAIN ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_student", add_student))
    app.add_handler(CommandHandler("delete_student", delete_student))
    app.add_handler(CommandHandler("add_group", add_group))
    app.add_handler(CommandHandler("add_to_group", add_to_group))
    app.add_handler(CommandHandler("add_membership", add_membership))
    app.add_handler(CommandHandler("mark_visited", mark_visited))
    app.add_handler(CommandHandler("extend", extend_days))
    app.add_handler(CommandHandler("add_parent", add_parent))
    app.add_handler(CommandHandler("link_child", link_child))
    app.add_handler(CallbackQueryHandler(button_handler))
    logger.info("🚀 Бот с группами и удалением запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
