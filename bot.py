import os
import logging
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters

# Состояния
NAME, PHONE, TG_ID, PARENT_NAME, PARENT_PHONE, PARENT_TG, LESSONS, DAYS, MEM_TG_ID, EXTEND_DAYS, GROUP_NAME, REQUEST_NAME, REQUEST_PHONE = range(13)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

admin_raw = os.getenv("ADMIN_CHAT_ID", "")
admin_clean = ''.join(c for c in admin_raw if c.isdigit() or c == ',')
ADMIN_IDS = [int(x) for x in admin_clean.split(',') if x.strip()]
BOT_TOKEN = os.getenv("BOT_TOKEN")

conn = sqlite3.connect("school.db", check_same_thread=False)
cursor = conn.cursor()

# ===== ТАБЛИЦЫ =====
cursor.execute('''CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE,
    name TEXT,
    phone TEXT
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS student_group (
    student_id INTEGER,
    group_id INTEGER,
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY(student_id, group_id)
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER,
    lessons_left INTEGER DEFAULT 0,
    valid_until TEXT,
    status TEXT DEFAULT 'active',
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER,
    date TEXT,
    present INTEGER DEFAULT 1,
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS parents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE,
    name TEXT,
    phone TEXT
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS parent_child (
    parent_id INTEGER,
    student_id INTEGER,
    FOREIGN KEY(parent_id) REFERENCES parents(id) ON DELETE CASCADE,
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
    PRIMARY KEY(parent_id, student_id)
)''')
conn.commit()

# ===== УВЕДОМЛЕНИЯ =====
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
                await context.bot.send_message(admin_id, f"❌ У {student_name} занятия закончились!")
            except:
                pass
    elif new_balance < 0:
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, f"⛔ У {student_name} долг: {abs(new_balance)}")
            except:
                pass

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
async def add_student_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return NAME

async def add_parent_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return PARENT_NAME

async def add_membership_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return LESSONS

async def add_group_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return GROUP_NAME

# ===== СТАРТ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # Админ
    if uid in ADMIN_IDS:
        kb = [
            [InlineKeyboardButton("👥 Ученики", callback_data="admin_students")],
            [InlineKeyboardButton("📚 Группы", callback_data="admin_groups")],
            [InlineKeyboardButton("👪 Родители", callback_data="admin_parents")],
            [InlineKeyboardButton("➕ Ученик", callback_data="add_student")],
            [InlineKeyboardButton("➕ Родитель", callback_data="add_parent")],
            [InlineKeyboardButton("🎟 Абонемент", callback_data="add_membership")],
            [InlineKeyboardButton("➕ Группа", callback_data="add_group")],
            [InlineKeyboardButton("📚 В группу", callback_data="add_to_group")],
            [InlineKeyboardButton("🔗 Привязать", callback_data="link_parent")],
            [InlineKeyboardButton("📋 Отметка", callback_data="mark_group")],
            [InlineKeyboardButton("⏱ Продлить", callback_data="extend_menu")],
            [InlineKeyboardButton("🗑 Удаление", callback_data="delete_menu")],
        ]
        await update.message.reply_text("🔐 Админ-панель", reply_markup=InlineKeyboardMarkup(kb))
        return

    # Родитель
    parent = cursor.execute("SELECT id, name FROM parents WHERE telegram_id = ?", (uid,)).fetchone()
    if parent:
        children = cursor.execute("""
            SELECT s.id, s.name FROM students s 
            JOIN parent_child pc ON s.id = pc.student_id 
            WHERE pc.parent_id = ?
        """, (parent[0],)).fetchall()
        
        if children:
            kb = [[InlineKeyboardButton(f"👤 {child[1]}", callback_data=f"child_{child[0]}")] for child in children]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
            await update.message.reply_text("👪 Ваши дети:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text("👪 У вас нет привязанных учеников")
        return

    # Ученик
    student = cursor.execute("SELECT id, name FROM students WHERE telegram_id = ?", (uid,)).fetchone()
    if student:
        kb = [
            [InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{student[0]}")],
            [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{student[0]}")],
        ]
        await update.message.reply_text(f"👋 {student[1]}", reply_markup=InlineKeyboardMarkup(kb))
        return

    # Новый пользователь
    kb = [
        [InlineKeyboardButton("👨‍🎓 Я ученик", callback_data="role_student")],
        [InlineKeyboardButton("👪 Я родитель", callback_data="role_parent")],
    ]
    await update.message.reply_text(
        "👋 Привет! Кто ты?",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ===== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ РЕГИСТРАЦИИ =====
async def role_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return REQUEST_NAME

# ===== КНОПКИ =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    uid = update.effective_user.id

    # ---- Для новых пользователей (выбор роли) ----
    if d == "role_student":
        context.user_data['request_role'] = 'student'
        await q.edit_message_text("✏️ Введи своё имя и фамилию:")
        return REQUEST_NAME

    if d == "role_parent":
        context.user_data['request_role'] = 'parent'
        await q.edit_message_text("✏️ Введи своё имя и фамилию:")
        return REQUEST_NAME

    # ---- Для не-админов (только свои данные) ----
    if uid not in ADMIN_IDS:
        if d.startswith("balance_"):
            sid = int(d.split("_")[1])
            mem = cursor.execute("SELECT lessons_left, valid_until FROM memberships WHERE student_id = ? AND status = 'active' AND valid_until > date('now')", (sid,)).fetchone()
            if mem:
                await q.edit_message_text(f"📊 Осталось: {mem[0]}\n📅 Действует до: {mem[1]}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="start")]]))
            else:
                await q.edit_message_text("📭 Нет активных абонементов", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="start")]]))
        elif d.startswith("attendance_"):
            sid = int(d.split("_")[1])
            rows = cursor.execute("SELECT date FROM attendance WHERE student_id = ? ORDER BY date DESC LIMIT 10", (sid,)).fetchall()
            if rows:
                txt = "📅 Посещения:\n" + "\n".join([f"• {r[0]}" for r in rows])
            else:
                txt = "📅 Посещений нет"
            await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="start")]]))
        elif d.startswith("child_"):
            sid = int(d.split("_")[1])
            name = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()[0]
            kb = [
                [InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{sid}")],
                [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{sid}")],
                [InlineKeyboardButton("🔙 Назад", callback_data="start")]
            ]
            await q.edit_message_text(f"👤 {name}", reply_markup=InlineKeyboardMarkup(kb))
        return

    # ====== ВСЁ, ЧТО НИЖЕ — ТОЛЬКО ДЛЯ АДМИНА ======

    # --- Просмотр списков ---
    if d == "admin_students":
        rows = cursor.execute("SELECT s.name, s.phone, s.telegram_id, g.name FROM students s LEFT JOIN student_group sg ON s.id = sg.student_id LEFT JOIN groups g ON sg.group_id = g.id ORDER BY s.name").fetchall()
        txt = "👥 Список учеников:\n" + "\n".join([f"• {r[0]} {r[1]} 🆔 {r[2]}" + (f" [{r[3]}]" if r[3] else "") for r in rows]) if rows else "👥 Нет учеников"
        kb = [[InlineKeyboardButton("➕ Ученик", callback_data="add_student")], [InlineKeyboardButton("🔙 Назад", callback_data="start")]]
        await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

    elif d == "admin_groups":
        rows = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
        if rows:
            kb = [[InlineKeyboardButton(f"📚 {r[1]}", callback_data=f"group_{r[0]}")] for r in rows]
            kb.append([InlineKeyboardButton("➕ Группа", callback_data="add_group")])
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
            await q.edit_message_text("📚 Группы:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text("📚 Нет групп", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Группа", callback_data="add_group")], [InlineKeyboardButton("🔙 Назад", callback_data="start")]]))

    elif d.startswith("group_"):
        gid = int(d.split("_")[1])
        group = cursor.execute("SELECT name FROM groups WHERE id = ?", (gid,)).fetchone()
        rows = cursor.execute("SELECT s.id, s.name FROM students s JOIN student_group sg ON s.id = sg.student_id WHERE sg.group_id = ? ORDER BY s.name", (gid,)).fetchall()
        if rows:
            txt = f"📚 *{group[0]}*\n\n"
            for r in rows:
                mem = cursor.execute("SELECT lessons_left, valid_until FROM memberships WHERE student_id = ? AND status = 'active' AND valid_until > date('now') LIMIT 1", (r[0],)).fetchone()
                txt += f"• {r[1]} — {mem[0] if mem else '❌ нет абонемента'}\n"
        else:
            txt = f"📚 {group[0]}: нет учеников"
        await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_groups")]]))

    elif d == "admin_parents":
        rows = cursor.execute("SELECT p.name, p.phone, p.telegram_id, COUNT(pc.student_id) FROM parents p LEFT JOIN parent_child pc ON p.id = pc.parent_id GROUP BY p.id").fetchall()
        txt = "👪 Родители:\n" + "\n".join([f"• {r[0]} {r[1]} 🆔 {r[2]} 👦 {r[3]}" for r in rows]) if rows else "👪 Нет родителей"
        kb = [[InlineKeyboardButton("➕ Родитель", callback_data="add_parent")], [InlineKeyboardButton("🔙 Назад", callback_data="start")]]
        await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

    # --- Добавление вручную ---
    elif d == "add_student":
        await q.edit_message_text("✏️ Введите имя ученика:")
        return NAME
    elif d == "add_parent":
        await q.edit_message_text("✏️ Введите имя родителя:")
        return PARENT_NAME
    elif d == "add_membership":
        await q.edit_message_text("🔢 Введите количество занятий:")
        return LESSONS
    elif d == "add_group":
        await q.edit_message_text("✏️ Введите название группы:")
        return GROUP_NAME

    # --- Добавление в группу ---
    elif d == "add_to_group":
        students = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
        if students:
            kb = [[InlineKeyboardButton(f"👤 {s[1]}", callback_data=f"select_student_{s[0]}")] for s in students]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
            await q.edit_message_text("👤 Выберите ученика:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text("👥 Нет учеников")

    elif d.startswith("select_student_"):
        sid = int(d.split("_")[2])
        context.user_data['selected_student'] = sid
        groups = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
        if groups:
            kb = []
            for g in groups:
                exists = cursor.execute("SELECT 1 FROM student_group WHERE student_id = ? AND group_id = ?", (sid, g[0])).fetchone()
                if not exists:
                    kb.append([InlineKeyboardButton(f"📚 {g[1]}", callback_data=f"select_group_{g[0]}")])
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="add_to_group")])
            await q.edit_message_text("📚 Выберите группу:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text("📚 Нет групп")

    elif d.startswith("select_group_"):
        gid = int(d.split("_")[2])
        sid = context.user_data.get('selected_student')
        cursor.execute("INSERT OR IGNORE INTO student_group (student_id, group_id) VALUES (?, ?)", (sid, gid))
        conn.commit()
        await q.edit_message_text("✅ Добавлено", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="add_to_group")]]))

    # --- Привязка родителя ---
    elif d == "link_parent":
        students = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
        if students:
            kb = [[InlineKeyboardButton(f"👤 {s[1]}", callback_data=f"link_student_{s[0]}")] for s in students]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
            await q.edit_message_text("👤 Выберите ученика:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text("👥 Нет учеников")

    elif d.startswith("link_student_"):
        sid = int(d.split("_")[2])
        context.user_data['link_student'] = sid
        parents = cursor.execute("SELECT id, name FROM parents ORDER BY name").fetchall()
        if parents:
            kb = [[InlineKeyboardButton(f"👪 {p[1]}", callback_data=f"link_parent_{p[0]}")] for p in parents]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="link_parent")])
            await q.edit_message_text("👪 Выберите родителя:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text("👪 Нет родителей")

    elif d.startswith("link_parent_"):
        pid = int(d.split("_")[2])
        sid = context.user_data.get('link_student')
        cursor.execute("INSERT OR IGNORE INTO parent_child (parent_id, student_id) VALUES (?, ?)", (pid, sid))
        conn.commit()
        await q.edit_message_text("✅ Привязано", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="link_parent")]]))

    # --- Отметки ---
    elif d == "mark_group":
        groups = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
        if groups:
            kb = [[InlineKeyboardButton(f"📚 {g[1]}", callback_data=f"mark_group_{g[0]}")] for g in groups]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
            await q.edit_message_text("📚 Выберите группу:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text("📚 Нет групп")

    elif d.startswith("mark_group_"):
        gid = int(d.split("_")[2])
        group = cursor.execute("SELECT name FROM groups WHERE id = ?", (gid,)).fetchone()
        students = cursor.execute("SELECT s.id, s.name FROM students s JOIN student_group sg ON s.id = sg.student_id WHERE sg.group_id = ?", (gid,)).fetchall()
        if students:
            kb = []
            for s in students:
                kb.append([
                    InlineKeyboardButton(f"{s[1]} ✅", callback_data=f"mark_student_{s[0]}_1_{gid}"),
                    InlineKeyboardButton("❌", callback_data=f"mark_student_{s[0]}_0_{gid}")
                ])
            kb.append([InlineKeyboardButton("✅ Все", callback_data=f"mark_all_1_{gid}"),
                       InlineKeyboardButton("❌ Все", callback_data=f"mark_all_0_{gid}")])
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="mark_group")])
            today = datetime.now().strftime("%d.%m.%Y")
            await q.edit_message_text(f"📋 {group[0]} на {today}", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text(f"📚 {group[0]}: нет учеников")

    # --- Продление ---
    elif d == "extend_menu":
        students = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
        if students:
            kb = [[InlineKeyboardButton(f"👤 {s[1]}", callback_data=f"extend_student_{s[0]}")] for s in students]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
            await q.edit_message_text("👤 Выберите ученика:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text("👥 Нет учеников")

    elif d.startswith("extend_student_"):
        sid = int(d.split("_")[2])
        context.user_data['extend_student'] = sid
        await q.edit_message_text("📅 Введите количество дней:")
        return EXTEND_DAYS

    # --- Удаление ---
    elif d == "delete_menu":
        kb = [
            [InlineKeyboardButton("👤 Ученика", callback_data="delete_student_menu")],
            [InlineKeyboardButton("🎟 Абонемент", callback_data="delete_membership_menu")],
            [InlineKeyboardButton("📚 Группу", callback_data="delete_group_menu")],
            [InlineKeyboardButton("👪 Родителя", callback_data="delete_parent_menu")],
            [InlineKeyboardButton("📅 Посещение", callback_data="delete_attendance_menu")],
            [InlineKeyboardButton("🔙 Назад", callback_data="start")],
        ]
        await q.edit_message_text("🗑 Что удаляем?", reply_markup=InlineKeyboardMarkup(kb))

    elif d == "delete_student_menu":
        students = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
        if students:
            kb = [[InlineKeyboardButton(f"👤 {s[1]}", callback_data=f"delete_student_{s[0]}")] for s in students]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")])
            await q.edit_message_text("Выбери ученика:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text("👥 Нет учеников", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")]]))

    elif d.startswith("delete_student_"):
        sid = int(d.split("_")[2])
        student = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_student_{sid}"),
            InlineKeyboardButton("❌ Нет", callback_data="delete_student_menu")
        ]])
        await q.edit_message_text(f"Точно удалить ученика {student[0]}?", reply_markup=kb)

    elif d.startswith("confirm_delete_student_"):
        sid = int(d.split("_")[3])
        cursor.execute("DELETE FROM students WHERE id = ?", (sid,))
        conn.commit()
        await q.edit_message_text("✅ Ученик удалён", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")]]))

    elif d == "delete_membership_menu":
        memberships = cursor.execute("""
            SELECT m.id, s.name, m.lessons_left, m.valid_until 
            FROM memberships m
            JOIN students s ON m.student_id = s.id
            WHERE m.status = 'active'
            ORDER BY s.name
        """).fetchall()
        if memberships:
            kb = []
            for m in memberships:
                btn_text = f"{m[1]} — {m[2]} занятий, до {m[3]}"
                kb.append([InlineKeyboardButton(btn_text, callback_data=f"delete_membership_{m[0]}")])
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")])
            await q.edit_message_text("Выбери абонемент:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text("🎟 Нет активных абонементов", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")]]))

    elif d.startswith("delete_membership_"):
        mid = int(d.split("_")[2])
        cursor.execute("DELETE FROM memberships WHERE id = ?", (mid,))
        conn.commit()
        await q.edit_message_text("✅ Абонемент удалён", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")]]))

    elif d == "delete_group_menu":
        groups = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
        if groups:
            kb = [[InlineKeyboardButton(f"📚 {g[1]}", callback_data=f"delete_group_{g[0]}")] for g in groups]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")])
            await q.edit_message_text("Выбери группу:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text("📚 Нет групп", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")]]))

    elif d.startswith("delete_group_"):
        gid = int(d.split("_")[2])
        cursor.execute("DELETE FROM groups WHERE id = ?", (gid,))
        conn.commit()
        await q.edit_message_text("✅ Группа удалена", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")]]))

    elif d == "delete_parent_menu":
        parents = cursor.execute("SELECT id, name FROM parents ORDER BY name").fetchall()
        if parents:
            kb = [[InlineKeyboardButton(f"👪 {p[1]}", callback_data=f"delete_parent_{p[0]}")] for p in parents]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")])
            await q.edit_message_text("Выбери родителя:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text("👪 Нет родителей", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")]]))

    elif d.startswith("delete_parent_"):
        pid = int(d.split("_")[2])
        cursor.execute("DELETE FROM parents WHERE id = ?", (pid,))
        conn.commit()
        await q.edit_message_text("✅ Родитель удалён", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")]]))

    elif d == "delete_attendance_menu":
        await q.edit_message_text("📅 Эта функция в разработке, пока можно удалить через исправление отметок", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")]]))

    # --- Одобрение заявок ---
    elif d.startswith("approve_student_"):
        parts = d.split("_")
        uid = int(parts[2])
        name = parts[3]
        phone = parts[4]
        try:
            cursor.execute("INSERT INTO students (telegram_id, name, phone) VALUES (?, ?, ?)", (uid, name, phone))
            conn.commit()
            await q.edit_message_text(f"✅ Ученик {name} добавлен")
            await context.bot.send_message(uid, "✅ Администратор добавил тебя как **ученика**!\nНапиши /start")
        except Exception as e:
            await q.edit_message_text(f"❌ Ошибка: {e}")

    elif d.startswith("approve_parent_"):
        parts = d.split("_")
        uid = int(parts[2])
        name = parts[3]
        phone = parts[4]
        try:
            cursor.execute("INSERT INTO parents (telegram_id, name, phone) VALUES (?, ?, ?)", (uid, name, phone))
            conn.commit()
            await q.edit_message_text(f"✅ Родитель {name} добавлен")
            await context.bot.send_message(uid, "✅ Администратор добавил тебя как **родителя**!\nНапиши /start")
        except Exception as e:
            await q.edit_message_text(f"❌ Ошибка: {e}")

    elif d.startswith("reject_"):
        uid = int(d.split("_")[1])
        await q.edit_message_text("❌ Заявка отклонена")
        await context.bot.send_message(uid, "❌ К сожалению, твоя заявка отклонена администратором.")

# ===== ДИАЛОГИ =====
async def add_student_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text
    await update.message.reply_text("📞 Введите телефон:")
    return PHONE

async def add_student_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['phone'] = update.message.text
    await update.message.reply_text("🆔 Введите Telegram ID:")
    return TG_ID

async def add_student_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(update.message.text)
        cursor.execute("INSERT INTO students (telegram_id, name, phone) VALUES (?, ?, ?)", (tid, context.user_data['name'], context.user_data['phone']))
        conn.commit()
        await update.message.reply_text("✅ Ученик добавлен")
    except:
        await update.message.reply_text("❌ Ошибка")
    context.user_data.clear()
    return ConversationHandler.END

async def add_parent_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text
    await update.message.reply_text("📞 Введите телефон:")
    return PARENT_PHONE

async def add_parent_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['phone'] = update.message.text
    await update.message.reply_text("🆔 Введите Telegram ID:")
    return PARENT_TG

async def add_parent_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(update.message.text)
        cursor.execute("INSERT INTO parents (telegram_id, name, phone) VALUES (?, ?, ?)", (tid, context.user_data['name'], context.user_data['phone']))
        conn.commit()
        await update.message.reply_text("✅ Родитель добавлен")
    except:
        await update.message.reply_text("❌ Ошибка")
    context.user_data.clear()
    return ConversationHandler.END

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
        await update.message.reply_text("🆔 Введите Telegram ID ученика:")
        return MEM_TG_ID
    except:
        await update.message.reply_text("❌ Введите число")
        return DAYS

async def add_membership_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tg_id = int(update.message.text)
        student = cursor.execute("SELECT id, name FROM students WHERE telegram_id = ?", (tg_id,)).fetchone()
        if not student:
            await update.message.reply_text("❌ Ученик не найден")
            return ConversationHandler.END
        lessons = context.user_data.get('mem_lessons')
        days = context.user_data.get('mem_days')
        valid_until = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute("INSERT INTO memberships (student_id, lessons_left, valid_until, status) VALUES (?, ?, ?, 'active')", (student[0], lessons, valid_until))
        conn.commit()
        await update.message.reply_text(f"✅ Абонемент добавлен для {student[1]}")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка")
    context.user_data.clear()
    return ConversationHandler.END

async def add_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text
    try:
        cursor.execute("INSERT INTO groups (name) VALUES (?)", (name,))
        conn.commit()
        await update.message.reply_text(f"✅ Группа '{name}' создана")
    except:
        await update.message.reply_text("❌ Ошибка")
    context.user_data.clear()
    return ConversationHandler.END

async def extend_days_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text)
        sid = context.user_data.get('extend_student')
        mem = cursor.execute("SELECT id, valid_until FROM memberships WHERE student_id = ? AND status = 'active' ORDER BY valid_until ASC LIMIT 1", (sid,)).fetchone()
        if mem:
            new = (datetime.strptime(mem[1], "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
            cursor.execute("UPDATE memberships SET valid_until = ? WHERE id = ?", (new, mem[0]))
            conn.commit()
            await update.message.reply_text(f"✅ Продлён до {new}")
        else:
            await update.message.reply_text("❌ Нет активных абонементов")
    except:
        await update.message.reply_text("❌ Ошибка")
    context.user_data.clear()
    return ConversationHandler.END

async def request_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['req_name'] = update.message.text
    await update.message.reply_text("📞 Теперь напиши свой телефон:")
    return REQUEST_PHONE

async def request_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = context.user_data.get('req_name')
    phone = update.message.text
    role = context.user_data.get('request_role', 'student')
    username = update.effective_user.username or "нет"
    role_text = "ученик" if role == "student" else "родитель"

    for admin_id in ADMIN_IDS:
        try:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Принять", callback_data=f"approve_{role}_{uid}_{name}_{phone}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{uid}")
            ]])
            await context.bot.send_message(
                admin_id,
                f"📩 Заявка от @{username}\n"
                f"Имя: {name}\n"
                f"Телефон: {phone}\n"
                f"Роль: {role_text}\n"
                f"ID: `{uid}`",
                reply_markup=kb,
                parse_mode="Markdown"
            )
        except:
            pass

    await update.message.reply_text(
        "✅ Заявка отправлена администратору. Как только тебя добавят, сможешь пользоваться ботом."
    )
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено")
    context.user_data.clear()
    return ConversationHandler.END

# ===== ЗАПУСК =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # Ручное добавление ученика
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(add_student_entry, pattern="^add_student$")],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_phone)],
            TG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # Ручное добавление родителя
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(add_parent_entry, pattern="^add_parent$")],
        states={
            PARENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_name)],
            PARENT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_phone)],
            PARENT_TG: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # Добавление абонемента
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(add_membership_entry, pattern="^add_membership$")],
        states={
            LESSONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_membership_lessons)],
            DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_membership_days)],
            MEM_TG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_membership_final)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # Добавление группы
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(add_group_entry, pattern="^add_group$")],
        states={
            GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_group_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # Продление
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(lambda u,c: EXTEND_DAYS, pattern="^extend_student_")],
        states={
            EXTEND_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_days_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # Заявки на регистрацию - ИСПРАВЛЕНО
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(role_entry, pattern="^role_")],
        states={
            REQUEST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_name)],
            REQUEST_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("🚀 Бот с регистрацией и удалением запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
