import os
import logging
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
import threading
import time
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler

# Состояния
NAME, PHONE, TG_ID, PARENT_NAME, PARENT_PHONE, PARENT_TG, LESSONS, DAYS, MEM_TG_ID, EXTEND_DAYS, GROUP_NAME, REQUEST_NAME, REQUEST_PHONE = range(13)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

admin_raw = os.getenv("ADMIN_CHAT_ID", "")
admin_clean = ''.join(c for c in admin_raw if c.isdigit() or c == ',')
ADMIN_IDS = [int(x) for x in admin_clean.split(',') if x.strip()]
BOT_TOKEN = os.getenv("BOT_TOKEN")

logger.info(f"👑 Загружены админы: {ADMIN_IDS}")

# ===== АНТИ-ЛАГ =====
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, format, *args): pass

def run_http_server():
    try:
        server = HTTPServer(('0.0.0.0', 8080), PingHandler)
        logger.info("🌐 HTTP сервер запущен")
        server.serve_forever()
    except Exception as e:
        logger.error(f"HTTP server error: {e}")

def ping_self():
    time.sleep(60)
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(('localhost', 8080))
            sock.send(b'GET /ping HTTP/1.0\r\n\r\n')
            sock.close()
        except:
            pass
        time.sleep(180)

threading.Thread(target=run_http_server, daemon=True).start()
threading.Thread(target=ping_self, daemon=True).start()

# ===== БАЗА =====
db_path = "/data/school.db" if os.path.exists("/data") else "school.db"
conn = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()
logger.info(f"📦 База данных: {db_path}")

cursor.execute('''CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE,
    name TEXT,
    phone TEXT,
    notifications INTEGER DEFAULT 1
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
    phone TEXT,
    notifications INTEGER DEFAULT 1
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS parent_child (
    parent_id INTEGER,
    student_id INTEGER,
    FOREIGN KEY(parent_id) REFERENCES parents(id) ON DELETE CASCADE,
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
    PRIMARY KEY(parent_id, student_id)
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS last_mark (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    student_id INTEGER,
    date TEXT,
    mark_type INTEGER,
    message_id INTEGER
)''')
conn.commit()

# ===== УВЕДОМЛЕНИЯ =====
async def notify_student_and_parents(student_id, message, context):
    student = cursor.execute("SELECT telegram_id, name, notifications FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student: return
    if student[2] == 1:
        try: await context.bot.send_message(student[0], message)
        except: pass
    parents = cursor.execute("SELECT p.telegram_id, p.notifications FROM parents p JOIN parent_child pc ON p.id = pc.parent_id WHERE pc.student_id = ?", (student_id,)).fetchall()
    for parent in parents:
        if parent[1] == 1:
            try: await context.bot.send_message(parent[0], f"👪 {student[1]}: {message}")
            except: pass

async def notify_admin(student_id, new_balance, context):
    student = cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student: return
    student_name = student[0]
    for admin_id in ADMIN_IDS:
        try: await context.bot.send_message(admin_id, f"📊 {student_name}: осталось {new_balance} занятий")
        except: pass
    if new_balance == 1:
        await notify_student_and_parents(student_id, f"⚠️ У тебя последнее занятие!", context)
    elif new_balance == 0:
        await notify_student_and_parents(student_id, f"❌ Твои занятия закончились!", context)
    elif new_balance < 0:
        await notify_student_and_parents(student_id, f"⛔ У тебя долг: {abs(new_balance)} занятий", context)

# ===== ВСПОМОГАТЕЛЬНЫЕ =====
async def add_student_entry(update, context): return NAME
async def add_parent_entry(update, context): return PARENT_NAME
async def add_membership_entry(update, context): return LESSONS
async def add_group_entry(update, context): return GROUP_NAME
async def role_entry(update, context): return REQUEST_NAME

# ===== СТАРТ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
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
    parent = cursor.execute("SELECT id, name, notifications FROM parents WHERE telegram_id = ?", (uid,)).fetchone()
    if parent:
        children = cursor.execute("SELECT s.id, s.name FROM students s JOIN parent_child pc ON s.id = pc.student_id WHERE pc.parent_id = ?", (parent[0],)).fetchall()
        if children:
            kb = [[InlineKeyboardButton(f"👤 {child[1]}", callback_data=f"child_{child[0]}")] for child in children]
            notif_text = "🔔 Уведомления вкл" if parent[2] == 1 else "🔕 Уведомления выкл"
            kb.append([InlineKeyboardButton(notif_text, callback_data="toggle_parent_notifications")])
            await update.message.reply_text("👪 Ваши дети:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            kb = [[InlineKeyboardButton("🔔 Настройки уведомлений", callback_data="toggle_parent_notifications")]]
            await update.message.reply_text("👪 У вас нет привязанных учеников", reply_markup=InlineKeyboardMarkup(kb))
        return
    student = cursor.execute("SELECT id, name, notifications FROM students WHERE telegram_id = ?", (uid,)).fetchone()
    if student:
        kb = [
            [InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{student[0]}")],
            [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{student[0]}")],
        ]
        notif_text = "🔔 Уведомления вкл" if student[2] == 1 else "🔕 Уведомления выкл"
        kb.append([InlineKeyboardButton(notif_text, callback_data="toggle_student_notifications")])
        await update.message.reply_text(f"👋 {student[1]}", reply_markup=InlineKeyboardMarkup(kb))
        return
    kb = [[InlineKeyboardButton("👨‍🎓 Я ученик", callback_data="role_student")], [InlineKeyboardButton("👪 Я родитель", callback_data="role_parent")]]
    await update.message.reply_text("👋 Привет! Кто ты?", reply_markup=InlineKeyboardMarkup(kb))

# ===== КНОПКИ =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    uid = update.effective_user.id

    # --- Выбор роли ---
    if d == "role_student":
        context.user_data['request_role'] = 'student'
        await q.edit_message_text("✏️ Введи своё имя и фамилию:")
        return REQUEST_NAME
    if d == "role_parent":
        context.user_data['request_role'] = 'parent'
        await q.edit_message_text("✏️ Введи своё имя и фамилию:")
        return REQUEST_NAME

    # --- Для учеников/родителей ---
    if uid not in ADMIN_IDS:
        if d.startswith("balance_"):
            sid = int(d.split("_")[1])
            mem = cursor.execute("SELECT lessons_left, valid_until FROM memberships WHERE student_id = ? AND status = 'active' AND valid_until > date('now')", (sid,)).fetchone()
            text = f"📊 Осталось: {mem[0]}\n📅 Действует до: {mem[1]}" if mem else "📭 Нет активных абонементов"
            kb = [[InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_student_{sid}")]]
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
            return
        if d.startswith("attendance_"):
            sid = int(d.split("_")[1])
            rows = cursor.execute("SELECT date FROM attendance WHERE student_id = ? ORDER BY date DESC LIMIT 10", (sid,)).fetchall()
            text = "📅 Посещения:\n" + "\n".join([f"• {r[0]}" for r in rows]) if rows else "📅 Посещений нет"
            kb = [[InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_student_{sid}")]]
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
            return
        if d.startswith("child_"):
            sid = int(d.split("_")[1])
            name = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()[0]
            kb = [
                [InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{sid}")],
                [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{sid}")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_parent")]
            ]
            await q.edit_message_text(f"👤 {name}", reply_markup=InlineKeyboardMarkup(kb))
            return
        if d == "back_to_parent":
            parent = cursor.execute("SELECT id FROM parents WHERE telegram_id = ?", (uid,)).fetchone()
            if parent:
                children = cursor.execute("SELECT s.id, s.name FROM students s JOIN parent_child pc ON s.id = pc.student_id WHERE pc.parent_id = ?", (parent[0],)).fetchall()
                if children:
                    kb = [[InlineKeyboardButton(f"👤 {child[1]}", callback_data=f"child_{child[0]}")] for child in children]
                    await q.edit_message_text("👪 Ваши дети:", reply_markup=InlineKeyboardMarkup(kb))
            return
        if d.startswith("back_to_student_"):
            sid = int(d.split("_")[3])
            student = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
            kb = [[InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{sid}")], [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{sid}")]]
            await q.edit_message_text(f"👋 {student[0]}", reply_markup=InlineKeyboardMarkup(kb))
            return
        if d == "toggle_student_notifications":
            current = cursor.execute("SELECT notifications FROM students WHERE telegram_id = ?", (uid,)).fetchone()
            if current:
                new_val = 0 if current[0] == 1 else 1
                cursor.execute("UPDATE students SET notifications = ? WHERE telegram_id = ?", (new_val, uid))
                conn.commit()
                student = cursor.execute("SELECT id, name FROM students WHERE telegram_id = ?", (uid,)).fetchone()
                kb = [[InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{student[0]}")], [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{student[0]}")]]
                notif_text = "🔔 Уведомления вкл" if new_val == 1 else "🔕 Уведомления выкл"
                kb.append([InlineKeyboardButton(notif_text, callback_data="toggle_student_notifications")])
                await q.edit_message_text(f"👋 {student[1]}", reply_markup=InlineKeyboardMarkup(kb))
            return
        if d == "toggle_parent_notifications":
            current = cursor.execute("SELECT notifications FROM parents WHERE telegram_id = ?", (uid,)).fetchone()
            if current:
                new_val = 0 if current[0] == 1 else 1
                cursor.execute("UPDATE parents SET notifications = ? WHERE telegram_id = ?", (new_val, uid))
                conn.commit()
                parent = cursor.execute("SELECT id FROM parents WHERE telegram_id = ?", (uid,)).fetchone()
                children = cursor.execute("SELECT s.id, s.name FROM students s JOIN parent_child pc ON s.id = pc.student_id WHERE pc.parent_id = ?", (parent[0],)).fetchall()
                kb = [[InlineKeyboardButton(f"👤 {child[1]}", callback_data=f"child_{child[0]}")] for child in children] if children else []
                notif_text = "🔔 Уведомления вкл" if new_val == 1 else "🔕 Уведомления выкл"
                kb.append([InlineKeyboardButton(notif_text, callback_data="toggle_parent_notifications")])
                await q.edit_message_text("👪 Ваши дети:", reply_markup=InlineKeyboardMarkup(kb))
            return
        return

    # ===== АДМИН =====
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

    # --- ОТМЕТКИ ---
    elif d == "mark_group":
        groups = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
        if groups:
            kb = [[InlineKeyboardButton(f"📚 {g[1]}", callback_data=f"mark_group_{g[0]}")] for g in groups]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
            await q.edit_message_text("📚 Выберите группу:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q.edit_message_text("📚 Нет групп", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="start")]]))

    elif d.startswith("mark_group_"):
        gid = int(d.split("_")[2])
        await show_mark_group(q, context, gid)

    elif d.startswith("mark_student_"):
        parts = d.split("_")
        sid = int(parts[2])
        present = int(parts[3])
        gid = int(parts[4])

        student = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
        today = datetime.now().strftime("%Y-%m-%d")
        today_display = datetime.now().strftime("%d.%m.%Y")
        already_marked = cursor.execute("SELECT id, present FROM attendance WHERE student_id = ? AND date = ?", (sid, today)).fetchone()

        if present == 1:
            mem = cursor.execute("SELECT id, lessons_left FROM memberships WHERE student_id = ? AND status = 'active' AND valid_until > date('now') ORDER BY valid_until ASC LIMIT 1", (sid,)).fetchone()
            if mem:
                if already_marked:
                    await q.answer(f"⚠️ {student[0]} уже отмечен сегодня!", show_alert=True)
                else:
                    new_left = mem[1] - 1
                    cursor.execute("UPDATE memberships SET lessons_left = ? WHERE id = ?", (new_left, mem[0]))
                    cursor.execute("INSERT INTO attendance (student_id, date) VALUES (?, ?)", (sid, today))
                    conn.commit()

                    # Удаляем старую последнюю отметку этого админа
                    cursor.execute("DELETE FROM last_mark WHERE admin_id = ?", (uid,))
                    # Сохраняем новую
                    cursor.execute("INSERT INTO last_mark (admin_id, student_id, date, mark_type) VALUES (?, ?, ?, ?)", (uid, sid, today, 1))
                    conn.commit()

                    await notify_admin(sid, new_left, context)

                    # ✅ ОТДЕЛЬНОЕ СООБЩЕНИЕ С КНОПКОЙ ОТМЕНЫ
                    kb_undo = InlineKeyboardMarkup([[
                        InlineKeyboardButton("↩️ Отменить посещение", callback_data="undo_last_mark")
                    ]])
                    await context.bot.send_message(
                        uid,
                        f"✅ {student[0]} отмечена на занятии — осталось {new_left}",
                        reply_markup=kb_undo
                    )
            else:
                await q.answer(f"❌ У {student[0]} нет активного абонемента!", show_alert=True)
        else:
            if already_marked:
                if already_marked[1] == 1:
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Да, отметить пропуск", callback_data=f"force_absent_{sid}_{gid}"),
                        InlineKeyboardButton("❌ Нет", callback_data=f"mark_group_{gid}")
                    ]])
                    await q.edit_message_text(f"⚠️ {student[0]} уже отмечен как присутствовал сегодня.\nОтметить как пропуск? Это спишет занятие!", reply_markup=kb)
                    return
                else:
                    await q.answer(f"❌ {student[0]} уже отмечен как пропуск", show_alert=True)
            else:
                cursor.execute("INSERT INTO attendance (student_id, date, present) VALUES (?, ?, 0)", (sid, today))
                conn.commit()
                cursor.execute("DELETE FROM last_mark WHERE admin_id = ?", (uid,))
                cursor.execute("INSERT INTO last_mark (admin_id, student_id, date, mark_type) VALUES (?, ?, ?, ?)", (uid, sid, today, 0))
                conn.commit()
                kb_undo = InlineKeyboardMarkup([[
                    InlineKeyboardButton("↩️ Отменить посещение", callback_data="undo_last_mark")
                ]])
                await context.bot.send_message(uid, f"❌ {student[0]} отмечен как пропуск", reply_markup=kb_undo)

        await show_mark_group(q, context, gid)

    elif d.startswith("force_absent_"):
        parts = d.split("_")
        sid = int(parts[2])
        gid = int(parts[3])
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("DELETE FROM attendance WHERE student_id = ? AND date = ?", (sid, today))
        cursor.execute("INSERT INTO attendance (student_id, date, present) VALUES (?, ?, 0)", (sid, today))
        conn.commit()
        cursor.execute("DELETE FROM last_mark WHERE admin_id = ?", (uid,))
        cursor.execute("INSERT INTO last_mark (admin_id, student_id, date, mark_type) VALUES (?, ?, ?, ?)", (uid, sid, today, 0))
        conn.commit()
        student = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
        kb_undo = InlineKeyboardMarkup([[
            InlineKeyboardButton("↩️ Отменить посещение", callback_data="undo_last_mark")
        ]])
        await context.bot.send_message(uid, f"❌ {student[0]} отмечен как пропуск", reply_markup=kb_undo)
        await show_mark_group(q, context, gid)

    elif d == "undo_last_mark":
        last = cursor.execute("SELECT student_id, date, mark_type FROM last_mark WHERE admin_id = ?", (uid,)).fetchone()
        if not last:
            await q.answer("❌ Нет отметки для отмены", show_alert=True)
            return
        sid, date, mark_type = last
        cursor.execute("DELETE FROM attendance WHERE student_id = ? AND date = ?", (sid, date))
        if mark_type == 1:
            mem = cursor.execute("SELECT id, lessons_left FROM memberships WHERE student_id = ? AND status = 'active' AND valid_until > date('now') ORDER BY valid_until ASC LIMIT 1", (sid,)).fetchone()
            if mem:
                new_left = mem[1] + 1
                cursor.execute("UPDATE memberships SET lessons_left = ? WHERE id = ?", (new_left, mem[0]))
        cursor.execute("DELETE FROM last_mark WHERE admin_id = ?", (uid,))
        conn.commit()
        student = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
        await q.edit_message_text(f"↩️ Отметка для {student[0]} отменена")
        await q.answer("✅ Отметка отменена")

    elif d.startswith("mark_all_"):
        parts = d.split("_")
        present = int(parts[2])
        gid = int(parts[3])
        students = cursor.execute("SELECT s.id, s.name FROM students s JOIN student_group sg ON s.id = sg.student_id WHERE sg.group_id = ?", (gid,)).fetchall()
        today = datetime.now().strftime("%Y-%m-%d")
        success, failed, already = 0, 0, 0
        marked_list = []
        for s in students:
            sid = s[0]
            already_marked = cursor.execute("SELECT id, present FROM attendance WHERE student_id = ? AND date = ?", (sid, today)).fetchone()
            if already_marked:
                already += 1
                continue
            if present == 1:
                mem = cursor.execute("SELECT id, lessons_left FROM memberships WHERE student_id = ? AND status = 'active' AND valid_until > date('now') ORDER BY valid_until ASC LIMIT 1", (sid,)).fetchone()
                if mem:
                    new_left = mem[1] - 1
                    cursor.execute("UPDATE memberships SET lessons_left = ? WHERE id = ?", (new_left, mem[0]))
                    cursor.execute("INSERT INTO attendance (student_id, date) VALUES (?, ?)", (sid, today))
                    await notify_admin(sid, new_left, context)
                    success += 1
                    marked_list.append(f"✅ {s[1]}")
                else:
                    failed += 1
            else:
                cursor.execute("INSERT INTO attendance (student_id, date, present) VALUES (?, ?, 0)", (sid, today))
                success += 1
                marked_list.append(f"❌ {s[1]}")
        conn.commit()
        msg = f"✅ Отмечено: {success}"
        if failed > 0: msg += f"\n❌ Нет абонемента: {failed}"
        if already > 0: msg += f"\n⚠️ Уже отмечены: {already}"
        await q.answer(msg)
        if marked_list:
            await context.bot.send_message(uid, "Отмечены:\n" + "\n".join(marked_list))
        await show_mark_group(q, context, gid)

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
        await q.edit_message_text("📅 Выберите дату для удаления (ДД.ММ.ГГГГ):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="delete_menu")]]))

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

# ===== ПОКАЗ ГРУППЫ =====
async def show_mark_group(q, context, gid):
    group = cursor.execute("SELECT name FROM groups WHERE id = ?", (gid,)).fetchone()
    today = datetime.now().strftime("%Y-%m-%d")
    today_display = datetime.now().strftime("%d.%m.%Y")
    students = cursor.execute("SELECT s.id, s.name FROM students s JOIN student_group sg ON s.id = sg.student_id WHERE sg.group_id = ? ORDER BY s.name", (gid,)).fetchall()
    kb = []
    for s in students:
        marked_today = cursor.execute("SELECT present FROM attendance WHERE student_id = ? AND date = ?", (s[0], today)).fetchone()
        if marked_today:
            btn_text = f"✅ {s[1]}" if marked_today[0] == 1 else f"❌ {s[1]}"
        else:
            btn_text = s[1]
        kb.append([
            InlineKeyboardButton(f"{btn_text} ✅", callback_data=f"mark_student_{s[0]}_1_{gid}"),
            InlineKeyboardButton("❌", callback_data=f"mark_student_{s[0]}_0_{gid}")
        ])
    kb.append([InlineKeyboardButton("✅ Все", callback_data=f"mark_all_1_{gid}"), InlineKeyboardButton("❌ Все", callback_data=f"mark_all_0_{gid}")])
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="mark_group")])
    try:
        await q.edit_message_text(f"📋 {group[0]} на {today_display}", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Ошибка при обновлении сообщения: {e}")

# ===== ДИАЛОГИ =====
async def add_student_name(update, context):
    context.user_data['name'] = update.message.text
    await update.message.reply_text("📞 Введите телефон:")
    return PHONE

async def add_student_phone(update, context):
    context.user_data['phone'] = update.message.text
    await update.message.reply_text("🆔 Введите Telegram ID:")
    return TG_ID

async def add_student_id(update, context):
    try:
        tid = int(update.message.text)
        cursor.execute("INSERT INTO students (telegram_id, name, phone) VALUES (?, ?, ?)", (tid, context.user_data['name'], context.user_data['phone']))
        conn.commit()
        await update.message.reply_text("✅ Ученик добавлен")
    except:
        await update.message.reply_text("❌ Ошибка")
    context.user_data.clear()
    return ConversationHandler.END

async def add_parent_name(update, context):
    context.user_data['name'] = update.message.text
    await update.message.reply_text("📞 Введите телефон:")
    return PARENT_PHONE

async def add_parent_phone(update, context):
    context.user_data['phone'] = update.message.text
    await update.message.reply_text("🆔 Введите Telegram ID:")
    return PARENT_TG

async def add_parent_id(update, context):
    try:
        tid = int(update.message.text)
        cursor.execute("INSERT INTO parents (telegram_id, name, phone) VALUES (?, ?, ?)", (tid, context.user_data['name'], context.user_data['phone']))
        conn.commit()
        await update.message.reply_text("✅ Родитель добавлен")
    except:
        await update.message.reply_text("❌ Ошибка")
    context.user_data.clear()
    return ConversationHandler.END

async def add_membership_lessons(update, context):
    try:
        lessons = int(update.message.text)
        context.user_data['mem_lessons'] = lessons
        await update.message.reply_text("📅 Введите количество дней:")
        return DAYS
    except:
        await update.message.reply_text("❌ Введите число")
        return LESSONS

async def add_membership_days(update, context):
    try:
        days = int(update.message.text)
        context.user_data['mem_days'] = days
        await update.message.reply_text("🆔 Введите Telegram ID ученика:")
        return MEM_TG_ID
    except:
        await update.message.reply_text("❌ Введите число")
        return DAYS

async def add_membership_final(update, context):
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

async def add_group_name(update, context):
    name = update.message.text
    try:
        cursor.execute("INSERT INTO groups (name) VALUES (?)", (name,))
        conn.commit()
        await update.message.reply_text(f"✅ Группа '{name}' создана")
    except:
        await update.message.reply_text("❌ Ошибка")
    context.user_data.clear()
    return ConversationHandler.END

async def extend_days_input(update, context):
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

async def request_name(update, context):
    context.user_data['req_name'] = update.message.text
    await update.message.reply_text("📞 Теперь напиши свой телефон:")
    return REQUEST_PHONE

async def request_phone(update, context):
    uid = update.effective_user.id
    name = context.user_data.get('req_name')
    phone = update.message.text
    role = context.user_data.get('request_role', 'student')
    username = update.effective_user.username or "нет"
    role_text = "ученик" if role == "student" else "родитель"
    logger.info(f"📩 Заявка от {username} ({uid}): {name}, {phone}, роль: {role_text}")
    for admin_id in ADMIN_IDS:
        try:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Принять", callback_data=f"approve_{role}_{uid}_{name}_{phone}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{uid}")
            ]])
            await context.bot.send_message(admin_id, f"📩 Заявка от @{username}\nИмя: {name}\nТелефон: {phone}\nРоль: {role_text}\nID: {uid}", reply_markup=kb)
        except:
            pass
    await update.message.reply_text("✅ Заявка отправлена администратору. Как только тебя добавят, сможешь пользоваться ботом.")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update, context):
    await update.message.reply_text("❌ Отменено")
    context.user_data.clear()
    return ConversationHandler.END

# ===== ЗАПУСК =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(add_student_entry, pattern="^add_student$")], states={NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_name)], PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_phone)], TG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_id)]}, fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(add_parent_entry, pattern="^add_parent$")], states={PARENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_name)], PARENT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_phone)], PARENT_TG: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_id)]}, fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(add_membership_entry, pattern="^add_membership$")], states={LESSONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_membership_lessons)], DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_membership_days)], MEM_TG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_membership_final)]}, fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(add_group_entry, pattern="^add_group$")], states={GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_group_name)]}, fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(lambda u,c: EXTEND_DAYS, pattern="^extend_student_")], states={EXTEND_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_days_input)]}, fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(role_entry, pattern="^role_")], states={REQUEST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_name)], REQUEST_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_phone)]}, fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(CallbackQueryHandler(button_handler))
    logger.info("🚀 Бот с уведомлениями и кнопкой отмены запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
