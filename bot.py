import os
import logging
import sqlite3
from datetime import datetime, timedelta
import datetime as dt
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
import threading
import time
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler

# Состояния
(NAME, PHONE, TG_ID, PARENT_NAME, PARENT_PHONE, PARENT_TG, LESSONS, DAYS, 
 EXTEND_DAYS, GROUP_NAME, REQUEST_NAME, REQUEST_PHONE) = range(12)

# Добавляем состояния для выбора ученика
SELECT_STUDENT_FOR_MEMBERSHIP = 100
SELECT_STUDENT_FOR_EXTEND = 101
DELETE_ATTENDANCE_DATE = 102

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

# Создаём таблицы
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

cursor.execute('''CREATE TABLE IF NOT EXISTS last_mark (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    student_id INTEGER,
    date TEXT,
    mark_type INTEGER
)''')

# ===== НОВАЯ ТАБЛИЦА ДЛЯ ЗАЯВОК =====
cursor.execute('''CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    name TEXT,
    phone TEXT,
    role TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    delivered INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0
)''')
conn.commit()

# Добавляем колонку notifications, если её нет
try:
    cursor.execute("ALTER TABLE students ADD COLUMN notifications INTEGER DEFAULT 1")
    logger.info("✅ Добавлена колонка notifications в students")
except:
    pass

try:
    cursor.execute("ALTER TABLE parents ADD COLUMN notifications INTEGER DEFAULT 1")
    logger.info("✅ Добавлена колонка notifications в parents")
except:
    pass
conn.commit()

# ===== УВЕДОМЛЕНИЯ =====
async def notify_student_and_parents(student_id, new_balance, context):
    student = cursor.execute("SELECT telegram_id, name, notifications FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student: 
        return
    
    student_name = student[1]
    
    if new_balance == 1:
        message = f"⚠️ У тебя осталось **последнее занятие**! Не забудь продлить абонемент."
    elif new_balance == 0:
        message = f"❌ Твои занятия закончились!\n\nПросьба оплатить абонемент перед следующим занятием."
    elif new_balance < 0:
        message = f"⛔ У тебя задолженность: **{abs(new_balance)} занятий**.\n\nПросьба оплатить абонемент."
    else:
        return
    
    if student[2] == 1:
        try:
            await context.bot.send_message(student[0], message, parse_mode="Markdown")
            logger.info(f"📨 Уведомление отправлено ученику {student_name}")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки ученику {student_name}: {e}")
    
    parents = cursor.execute("""
        SELECT p.telegram_id, p.notifications FROM parents p
        JOIN parent_child pc ON p.id = pc.parent_id
        WHERE pc.student_id = ?
    """, (student_id,)).fetchall()
    
    for parent in parents:
        if parent[1] == 1:
            try:
                await context.bot.send_message(
                    parent[0], 
                    f"👪 **{student_name}**: {message}", 
                    parse_mode="Markdown"
                )
                logger.info(f"📨 Уведомление отправлено родителю")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки родителю: {e}")

async def notify_admin(student_id, new_balance, context):
    student = cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student: 
        return
    student_name = student[0]
    
    # Админу всегда
    for admin_id in ADMIN_IDS:
        try:
            if new_balance < 0:
                await context.bot.send_message(admin_id, f"⛔ {student_name}: долг {abs(new_balance)} занятий")
            elif new_balance == 0:
                await context.bot.send_message(admin_id, f"❌ {student_name}: занятия закончились!")
            else:
                await context.bot.send_message(admin_id, f"📊 {student_name}: осталось {new_balance} занятий")
        except:
            pass
    
    # Ученику только при 0 или минусе
    if new_balance <= 0:
        await notify_student_and_parents(student_id, new_balance, context)

# ===== УВЕДОМЛЕНИЕ ОБ ИСТЕЧЕНИИ АБОНЕМЕНТА =====
async def check_expiring_memberships(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет абонементы, которые истекают через 5 дней, и отправляет уведомления"""
    today = datetime.now().date()
    warning_date = (today + timedelta(days=5)).strftime("%Y-%m-%d")
    
    expiring = cursor.execute("""
        SELECT m.id, m.student_id, s.name, s.telegram_id, s.notifications, m.valid_until 
        FROM memberships m
        JOIN students s ON m.student_id = s.id
        WHERE m.status = 'active' AND m.valid_until = ?
    """, (warning_date,)).fetchall()
    
    for mem in expiring:
        mem_id, student_id, student_name, tg_id, notif, valid_until = mem
        
        student_msg = (
            f"⚠️ Твой абонемент закончится через 5 дней (до {valid_until}).\n"
            f"Обратись к администратору для продления."
        )
        
        if notif == 1 and tg_id:
            try:
                await context.bot.send_message(tg_id, student_msg)
                logger.info(f"📨 Уведомление об истечении отправлено ученику {student_name}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки ученику {student_name}: {e}")
        
        admin_msg = f"⚠️ Ученик {student_name}: абонемент истекает через 5 дней (до {valid_until})"
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, admin_msg)
            except:
                pass

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
async def add_student_entry(update, context): return NAME
async def add_parent_entry(update, context): return PARENT_NAME
async def add_group_entry(update, context): return GROUP_NAME
async def role_entry(update, context): return REQUEST_NAME
async def membership_lessons_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return LESSONS
async def delete_attendance_entry(update, context): return DELETE_ATTENDANCE_DATE

# ===== ФУНКЦИЯ ГАРАНТИРОВАННОЙ ОТПРАВКИ =====
async def send_request_to_admins(context, request_id, user_id, username, name, phone, role):
    """Отправляет заявку всем админам с гарантией доставки"""
    role_text = "ученик" if role == "student" else "родитель"
    sent_count = 0
    now = datetime.now().strftime("%H:%M:%S")
    
    for admin_id in ADMIN_IDS:
        try:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Принять", callback_data=f"approve_{role}_{user_id}_{name}_{phone}_{request_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{user_id}_{request_id}")
            ]])
            
            await context.bot.send_message(
                admin_id,
                f"📩 Заявка #{request_id} от @{username}\n"
                f"Имя: {name}\n"
                f"Телефон: {phone}\n"
                f"Роль: {role_text}\n"
                f"Время: {now}",
                reply_markup=kb
            )
            sent_count += 1
            logger.info(f"✅ Заявка #{request_id} отправлена админу {admin_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки заявки #{request_id} админу {admin_id}: {e}")
    
    # Обновляем статус в БД
    cursor.execute("UPDATE requests SET delivered = 1, attempts = attempts + 1 WHERE id = ?", (request_id,))
    conn.commit()
    
    return sent_count

# ===== ПРОСМОТР ВСЕХ ЗАЯВОК =====
async def all_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все необработанные заявки"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Нет доступа")
        return
    
    requests = cursor.execute("""
        SELECT id, username, name, phone, role, created_at, attempts
        FROM requests 
        WHERE status = 'pending' 
        ORDER BY created_at DESC
    """).fetchall()
    
    if not requests:
        await update.message.reply_text("📭 Нет ожидающих заявок")
        return
    
    for req in requests[:5]:  # Показываем последние 5
        text = f"📩 Заявка #{req[0]}\n"
        text += f"👤 {req[2]} (@{req[1]})\n"
        text += f"📞 {req[3]}\n"
        text += f"🎭 Роль: {req[4]}\n"
        text += f"⏱ Время: {req[5]}\n"
        text += f"🔄 Попыток: {req[6]}"
        
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Принять", callback_data=f"approve_{req[4]}_{req[0]}_{req[2]}_{req[3]}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{req[0]}")
        ]])
        
        await update.message.reply_text(text, reply_markup=kb)
    
    if len(requests) > 5:
        await update.message.reply_text(f"📊 Всего ожидающих заявок: {len(requests)}")

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
            [InlineKeyboardButton("❄️ Заморозка", callback_data="freeze_menu")],
            [InlineKeyboardButton("📋 Заявки", callback_data="admin_requests")],
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

    # Отладка
    logger.info(f"📩 Получен callback: {d} от пользователя {uid}")

    if d == "role_student":
        context.user_data['request_role'] = 'student'
        await q.edit_message_text("✏️ Введи своё имя и фамилию:")
        return REQUEST_NAME
    if d == "role_parent":
        context.user_data['request_role'] = 'parent'
        await q.edit_message_text("✏️ Введи своё имя и фамилию:")
        return REQUEST_NAME

    if uid not in ADMIN_IDS:
        if d.startswith("balance_"):
            sid = int(d.split("_")[1])
            mem = cursor.execute("SELECT lessons_left, valid_until FROM memberships WHERE student_id = ? AND status = 'active' AND valid_until > date('now')", (sid,)).fetchone()
            if mem:
                text = f"📊 Осталось: {mem[0]}\n📅 Действует до: {mem[1]}"
            else:
                text = "📊 Осталось: 0\n📅 Нет активного абонемента"
            kb = [[InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_student_{sid}")]]
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
            return
        elif d.startswith("attendance_"):
            sid = int(d.split("_")[1])
            student = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
            
            months = cursor.execute("""
                SELECT DISTINCT strftime('%Y-%m', date) as month 
                FROM attendance 
                WHERE student_id = ? 
                ORDER BY month DESC
            """, (sid,)).fetchall()
            
            if months:
                kb = []
                for month in months:
                    year, month_num = month[0].split('-')
                    month_names = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                                  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
                    month_name = month_names[int(month_num)-1]
                    btn_text = f"📅 {month_name} {year}"
                    kb.append([InlineKeyboardButton(btn_text, callback_data=f"attendance_month_{sid}_{month[0]}")])
                
                kb.append([InlineKeyboardButton("📋 Все посещения", callback_data=f"attendance_all_{sid}")])
                kb.append([InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_student_{sid}")])
                
                await q.edit_message_text(f"👤 {student[0]}\nВыберите месяц:", reply_markup=InlineKeyboardMarkup(kb))
            else:
                await q.edit_message_text("📭 Нет посещений", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_student_{sid}")]]))
            return
        elif d.startswith("attendance_month_"):
            parts = d.split("_")
            sid = int(parts[2])
            month = parts[3]
            
            year, month_num = month.split('-')
            month_names = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                          "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
            month_name = month_names[int(month_num)-1]
            
            rows = cursor.execute("""
                SELECT date FROM attendance 
                WHERE student_id = ? AND strftime('%Y-%m', date) = ?
                ORDER BY date DESC
            """, (sid, month)).fetchall()
            
            if rows:
                text = f"📅 {month_name} {year}\n\n"
                for r in rows:
                    date_obj = datetime.strptime(r[0], "%Y-%m-%d")
                    date_display = date_obj.strftime("%d.%m.%Y")
                    text += f"• {date_display}\n"
                
                kb = [[InlineKeyboardButton("🔙 Назад", callback_data=f"attendance_{sid}")]]
                await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
            else:
                await q.edit_message_text(f"📭 В {month_name} {year} посещений нет", 
                                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=f"attendance_{sid}")]]))
            return
        elif d.startswith("attendance_all_"):
            sid = int(d.split("_")[2])
            student = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
            
            rows = cursor.execute("""
                SELECT date FROM attendance 
                WHERE student_id = ? 
                ORDER BY date DESC
                LIMIT 50
            """, (sid,)).fetchall()
            
            if rows:
                text = f"📋 Все посещения {student[0]}\n\n"
                current_month = ""
                for r in rows:
                    date_obj = datetime.strptime(r[0], "%Y-%m-%d")
                    month_key = date_obj.strftime("%Y-%m")
                    date_display = date_obj.strftime("%d.%m.%Y")
                    
                    if month_key != current_month:
                        current_month = month_key
                        year, month_num = month_key.split('-')
                        month_name = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                                     "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"][int(month_num)-1]
                        text += f"\n📅 {month_name} {year}\n"
                    
                    text += f"  • {date_display}\n"
                
                kb = [[InlineKeyboardButton("🔙 Назад", callback_data=f"attendance_{sid}")]]
                await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
            else:
                await q.edit_message_text("📭 Нет посещений", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_student_{sid}")]]))
            return
        elif d.startswith("child_"):
            sid = int(d.split("_")[1])
            name = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()[0]
            kb = [
                [InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{sid}")],
                [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{sid}")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_parent")]
            ]
            await q.edit_message_text(f"👤 {name}", reply_markup=InlineKeyboardMarkup(kb))
            return
        elif d == "back_to_parent":
            parent = cursor.execute("SELECT id FROM parents WHERE telegram_id = ?", (uid,)).fetchone()
            if parent:
                children = cursor.execute("SELECT s.id, s.name FROM students s JOIN parent_child pc ON s.id = pc.student_id WHERE pc.parent_id = ?", (parent[0],)).fetchall()
                if children:
                    kb = [[InlineKeyboardButton(f"👤 {child[1]}", callback_data=f"child_{child[0]}")] for child in children]
                    await q.edit_message_text("👪 Ваши дети:", reply_markup=InlineKeyboardMarkup(kb))
            return
        elif d.startswith("back_to_student_"):
            sid = int(d.split("_")[3])
            student = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
            kb = [
                [InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{sid}")],
                [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{sid}")],
            ]
            await q.edit_message_text(f"👋 {student[0]}", reply_markup=InlineKeyboardMarkup(kb))
            return
        elif d == "toggle_student_notifications":
            current = cursor.execute("SELECT notifications FROM students WHERE telegram_id = ?", (uid,)).fetchone()
            if current:
                new_val = 0 if current[0] == 1 else 1
                cursor.execute("UPDATE students SET notifications = ? WHERE telegram_id = ?", (new_val, uid))
                conn.commit()
                student = cursor.execute("SELECT id, name FROM students WHERE telegram_id = ?", (uid,)).fetchone()
                kb = [
                    [InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{student[0]}")],
                    [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{student[0]}")],
                ]
                notif_text = "🔔 Уведомления вкл" if new_val == 1 else "🔕 Уведомления выкл"
                kb.append([InlineKeyboardButton(notif_text, callback_data="toggle_student_notifications")])
                await q.edit_message_text(f"👋 {student[1]}", reply_markup=InlineKeyboardMarkup(kb))
            return
        elif d == "toggle_parent_notifications":
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

    # АДМИН
    if d == "start":
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
            [InlineKeyboardButton("❄️ Заморозка", callback_data="freeze_menu")],
            [InlineKeyboardButton("📋 Заявки", callback_data="admin_requests")],
        ]
        await q.edit_message_text("🔐 Админ-панель", reply_markup=InlineKeyboardMarkup(kb))
        return

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

    elif d == "admin_parents":
        rows = cursor.execute("SELECT p.name, p.phone, p.telegram_id, COUNT(pc.student_id) FROM parents p LEFT JOIN parent_child pc ON p.id = pc.parent_id GROUP BY p.id").fetchall()
        txt = "👪 Родители:\n" + "\n".join([f"• {r[0]} {r[1]} 🆔 {r[2]} 👦 {r[3]}" for r in rows]) if rows else "👪 Нет родителей"
        kb = [[InlineKeyboardButton("➕ Родитель", callback_data="add_parent")], [InlineKeyboardButton("🔙 Назад", callback_data="start")]]
        await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

    elif d == "admin_requests":
        requests = cursor.execute("""
            SELECT id, username, name, phone, role, created_at, attempts
            FROM requests 
            WHERE status = 'pending' 
            ORDER BY created_at DESC
            LIMIT 5
        """).fetchall()
        
        if requests:
            txt = "📋 **Новые заявки:**\n\n"
            for req in requests:
                txt += f"#{req[0]} от @{req[1]}\n"
                txt += f"👤 {req[2]} ({req[4]})\n"
                txt += f"📞 {req[3]}\n"
                txt += f"⏱ {req[5]}\n"
                txt += f"🔄 попыток: {req[6]}\n\n"
            kb = [[InlineKeyboardButton("🔄 Обновить", callback_data="admin_requests")],
                   [InlineKeyboardButton("🔙 Назад", callback_data="start")]]
        else:
            txt = "📭 Нет новых заявок"
            kb = [[InlineKeyboardButton("🔙 Назад", callback_data="start")]]
        
        await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif d == "add_student":
        await q.edit_message_text("✏️ Введите имя ученика:")
        return NAME

    elif d == "add_parent":
        await q.edit_message_text("✏️ Введите имя родителя:")
        return PARENT_NAME

    elif d == "add_membership":
        students = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
        if students:
            kb = [[InlineKeyboardButton(f"👤 {s[1]}", callback_data=f"select_student_membership_{s[0]}")] for s in students]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
            await q.edit_message_text("👤 Выберите ученика для абонемента:", reply_markup=InlineKeyboardMarkup(kb))
            return
        else:
            await q.edit_message_text("👥 Сначала добавьте учеников", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="start")]]))
            return

    elif d.startswith("select_student_membership_"):
        sid = int(d.split("_")[3])
        context.user_data['membership_student'] = sid
        await q.edit_message_text("🔢 Введите количество занятий:")
        return LESSONS

    elif d == "add_group":
        await
