import os
import logging
import ast
from dotenv import load_dotenv  # ← ЭТОЙ СТРОКИ НЕ ХВАТАЕТ
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
import sqlite3
from datetime import datetime, timedelta

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

    if user_id in ADMIN_IDS:
        keyboard = [
            [InlineKeyboardButton("👥 Ученики", callback_data="admin_students")],
            [InlineKeyboardButton("📚 Группы", callback_data="admin_groups")],
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

# ========== НОВАЯ ФУНКЦИЯ: добавление в группу через кнопки ==========
async def show_students_for_group(q):
    """Показывает список учеников для выбора при добавлении в группу"""
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
    """Показывает список групп для выбора после выбора ученика"""
    rows = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
    
    if not rows:
        await q.edit_message_text("📚 Группы пока не созданы.")
        return
    
    keyboard = []
    for group in rows:
        # Проверяем, не состоит ли уже ученик в группе
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
    """Добавляет ученика в выбранную группу"""
    try:
        cursor.execute(
            "INSERT INTO student_group (student_id, group_id) VALUES (?, ?)",
            (student_id, group_id)
        )
        conn.commit()
        
        # Получаем имена для красивого ответа
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
            if status == 'pending':
                msg += f"⏳ *Ожидает активации*\n├ Куплен: {purchase}\n└ Осталось: {lessons}\n\n"
            else:
                msg += f"✅ *Активен*\n├ Действует до: {valid}\n├ Осталось: {lessons}\n├ Куплен: {purchase}\n└ Активирован: {activation}\n\n"
    
    kb = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_children")]]
    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def show_attendance(user_id, q):
    sid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (user_id,)).fetchone()
    if sid:
        await show_attendance_by_id(sid[0], q)
    else:
        await q.edit_message_text("Ты не ученик")

async def show_attendance_by_id(sid, q):
    rows = cursor.execute("SELECT date FROM attendance WHERE student_id = ? ORDER BY date DESC LIMIT 15", (sid,)).fetchall()
    
    if not rows:
        msg = "📅 Посещений пока нет"
    else:
        msg = "📅 *Последние посещения:*\n\n"
        for r in rows:
            msg += f"▫️ {r[0]}\n"
    
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
async def show_all_students(q):
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
    
    kb = [[InlineKeyboardButton("🔙 Назад", callback_data="start")]]
    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

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

async def show_all_parents(q):
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
    
    kb = [[InlineKeyboardButton("🔙 Назад", callback_data="start")]]
    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ========== АДМИН-КОМАНДЫ (оставлены для совместимости) ==========
async def add_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    try:
        _, name, phone, tid = update.message.text.split()
        cursor.execute("INSERT OR IGNORE INTO students (telegram_id, name, phone) VALUES (?, ?, ?)", (int(tid), name, phone))
        conn.commit()
        await update.message.reply_text(f"✅ Ученик {name} добавлен!")
    except Exception as e:
        logger.error(f"Error in add_student: {e}")
        await update.message.reply_text("❌ Ошибка. Формат: /add_student Имя Телефон TelegramID")

async def delete_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    try:
        tid = int(context.args[0])
        cursor.execute("DELETE FROM students WHERE telegram_id = ?", (tid,))
        conn.commit()
        await update.message.reply_text(f"✅ Ученик удалён")
    except Exception as e:
        logger.error(f"Error in delete_student: {e}")
        await update.message.reply_text("❌ Ошибка. Формат: /delete_student TelegramID")

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    try:
        name = " ".join(context.args)
        cursor.execute("INSERT OR IGNORE INTO groups (name) VALUES (?)", (name,))
        conn.commit()
        await update.message.reply_text(f"✅ Группа «{name}» создана")
    except Exception as e:
        logger.error(f"Error in add_group: {e}")
        await update.message.reply_text("❌ Ошибка. Формат: /add_group Название группы")

async def add_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    try:
        tid = int(context.args[0])
        group_name = " ".join(context.args[1:])
        
        sid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (tid,)).fetchone()
        gid = cursor.execute("SELECT id FROM groups WHERE name = ?", (group_name,)).fetchone()
        
        if not sid or not gid:
            await update.message.reply_text("❌ Ученик или группа не найдены")
            return
            
        cursor.execute("INSERT OR IGNORE INTO student_group (student_id, group_id) VALUES (?, ?)", (sid[0], gid[0]))
        conn.commit()
        await update.message.reply_text(f"✅ Ученик добавлен в группу «{group_name}»")
    except Exception as e:
        logger.error(f"Error in add_to_group: {e}")
        await update.message.reply_text("❌ Ошибка. Формат: /add_to_group TelegramID Название_группы")

async def add_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    try:
        tid = int(context.args[0])
        lessons = int(context.args[1])
        days = int(context.args[2])
        
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
    except Exception as e:
        logger.error(f"Error in add_membership: {e}")
        await update.message.reply_text("❌ Ошибка. Формат: /add_membership TelegramID занятий дней")

async def mark_visited(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа.")
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
            await update.message.reply_text("❌ Нет доступных абонементов")
            return
            
        mid, left, status = mem
        
        if status == 'pending':
            valid_until = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
            cursor.execute('''
                UPDATE memberships 
                SET status='active', activation_date=?, valid_until=?
                WHERE id=?
            ''', (today, valid_until, mid))
        
        new_left = left - 1
        if new_left == 0:
            cursor.execute("UPDATE memberships SET status='finished' WHERE id=?", (mid,))
        else:
            cursor.execute("UPDATE memberships SET lessons_left=? WHERE id=?", (new_left, mid))
        
        cursor.execute("INSERT INTO attendance (student_id, date, membership_id) VALUES (?, ?, ?)", (sid, today, mid))
        conn.commit()
        
        await update.message.reply_text(f"✅ Посещение отмечено! Осталось занятий: {new_left}")
    except Exception as e:
        logger.error(f"Error in mark_visited: {e}")
        await update.message.reply_text("❌ Ошибка. Формат: /mark_visited TelegramID")

async def extend_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    try:
        tid = int(context.args[0])
        days = int(context.args[1])
        
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
            await update.message.reply_text("❌ У ученика нет активных абонементов")
            return
            
        new_date = (datetime.strptime(mem[1], "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute("UPDATE memberships SET valid_until=? WHERE id=?", (new_date, mem[0]))
        conn.commit()
        
        await update.message.reply_text(f"✅ Абонемент продлён до {new_date}")
    except Exception as e:
        logger.error(f"Error in extend_days: {e}")
        await update.message.reply_text("❌ Ошибка. Формат: /extend TelegramID дни")

async def add_parent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    try:
        _, name, phone, tid = update.message.text.split()
        cursor.execute("INSERT OR IGNORE INTO parents (telegram_id, name, phone) VALUES (?, ?, ?)", (int(tid), name, phone))
        conn.commit()
        await update.message.reply_text(f"✅ Родитель {name} добавлен!")
    except Exception as e:
        logger.error(f"Error in add_parent: {e}")
        await update.message.reply_text("❌ Ошибка. Формат: /add_parent Имя Телефон TelegramID")

async def link_child(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    try:
        pt = int(context.args[0])
        ct = int(context.args[1])
        
        pid = cursor.execute("SELECT id FROM parents WHERE telegram_id = ?", (pt,)).fetchone()
        cid = cursor.execute("SELECT id FROM students WHERE telegram_id = ?", (ct,)).fetchone()
        
        if not pid or not cid:
            await update.message.reply_text("❌ Родитель или ученик не найдены")
            return
            
        cursor.execute("INSERT OR IGNORE INTO parent_child (parent_id, student_id) VALUES (?, ?)", (pid[0], cid[0]))
        conn.commit()
        await update.message.reply_text("✅ Связь создана!")
    except Exception as e:
        logger.error(f"Error in link_child: {e}")
        await update.message.reply_text("❌ Ошибка. Формат: /link_child TelegramID_родителя TelegramID_ребёнка")

# ========== ЗАПУСК ==========
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
    
    logger.info("🚀 Бот с кнопочным добавлением в группы запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
