import os
import logging
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters

# Состояния
NAME, PHONE, TG_ID, PARENT_NAME, PARENT_PHONE, PARENT_TG, LESSONS, DAYS, MEM_TG_ID, EXTEND_DAYS, GROUP_NAME = range(11)

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

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ENTRY POINTS =====
async def add_student_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return NAME

async def add_parent_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return PARENT_NAME

async def add_membership_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return LESSONS

async def add_group_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return GROUP_NAME

# ===== КОМАНДЫ =====
async def delete_student_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалить ученика по Telegram ID"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        tg_id = int(context.args[0])
        cursor.execute("DELETE FROM students WHERE telegram_id = ?", (tg_id,))
        conn.commit()
        await update.message.reply_text(f"✅ Ученик с ID {tg_id} удалён")
    except:
        await update.message.reply_text("❌ Ошибка. Формат: /delete_student TelegramID")

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
        ]
        await update.message.reply_text("🔐 Админ-панель", reply_markup=InlineKeyboardMarkup(kb))
        return
    p = cursor.execute("SELECT id, name FROM parents WHERE telegram_id = ?", (uid,)).fetchone()
    if p:
        ch = cursor.execute("SELECT s.id, s.name FROM students s JOIN parent_child pc ON s.id = pc.student_id WHERE pc.parent_id = ?", (p[0],)).fetchall()
        if ch:
            kb = [[InlineKeyboardButton(f"👤 {c[1]}", callback_data=f"child_{c[0]}")] for c in ch]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
            await update.message.reply_text("👪 Ваши дети:", reply_markup=InlineKeyboardMarkup(kb))
        return
    s = cursor.execute("SELECT id, name FROM students WHERE telegram_id = ?", (uid,)).fetchone()
    if s:
        kb = [
            [InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{s[0]}")],
            [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{s[0]}")],
        ]
        await update.message.reply_text(f"👋 {s[1]}", reply_markup=InlineKeyboardMarkup(kb))
        return
    await update.message.reply_text("👋 Вы не зарегистрированы")

# ===== КНОПКИ =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    uid = update.effective_user.id

    if d.startswith("balance_"):
        sid = int(d.split("_")[1])
        mem = cursor.execute("SELECT lessons_left, valid_until FROM memberships WHERE student_id = ? AND status = 'active' AND valid_until > date('now')", (sid,)).fetchone()
        if mem:
            await q.edit_message_text(f"📊 Осталось: {mem[0]}\n📅 Действует до: {mem[1]}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_children")]]))
        else:
            await q.edit_message_text("📭 Нет активных абонементов", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_children")]]))
    elif d.startswith("attendance_"):
        sid = int(d.split("_")[1])
        rows = cursor.execute("SELECT date FROM attendance WHERE student_id = ? ORDER BY date DESC LIMIT 10", (sid,)).fetchall()
        if rows:
            txt = "📅 Посещения:\n" + "\n".join([f"• {r[0]}" for r in rows])
        else:
            txt = "📅 Посещений нет"
        await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_children")]]))
    elif d.startswith("child_"):
        sid = int(d.split("_")[1])
        name = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()[0]
        kb = [
            [InlineKeyboardButton("📊 Баланс", callback_data=f"balance_{sid}")],
            [InlineKeyboardButton("📅 Посещения", callback_data=f"attendance_{sid}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_children")]
        ]
        await q.edit_message_text(f"👤 {name}", reply_markup=InlineKeyboardMarkup(kb))
    elif d == "back_to_children":
        p = cursor.execute("SELECT id FROM parents WHERE telegram_id = ?", (uid,)).fetchone()
        if p:
            ch = cursor.execute("SELECT s.id, s.name FROM students s JOIN parent_child pc ON s.id = pc.student_id WHERE pc.parent_id = ?", (p[0],)).fetchall()
            if ch:
                kb = [[InlineKeyboardButton(f"👤 {c[1]}", callback_data=f"child_{c[0]}")] for c in ch]
                kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
                await q.edit_message_text("👪 Ваши дети:", reply_markup=InlineKeyboardMarkup(kb))

    if uid in ADMIN_IDS:
        if d == "admin_students":
            rows = cursor.execute("SELECT s.name, s.phone, s.telegram_id, g.name FROM students s LEFT JOIN student_group sg ON s.id = sg.student_id LEFT JOIN groups g ON sg.group_id = g.id ORDER BY s.name").fetchall()
            if rows:
                txt = "👥 Список учеников:\n" + "\n".join([f"• {r[0]} {r[1]} 🆔 {r[2]}" + (f" [{r[3]}]" if r[3] else "") for r in rows])
            else:
                txt = "👥 Нет учеников"
            kb = [
                [InlineKeyboardButton("➕ Ученик", callback_data="add_student")],
                [InlineKeyboardButton("🔙 Назад", callback_data="start")]
            ]
            await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        elif d == "admin_groups":
            rows = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
            if rows:
                kb = [[InlineKeyboardButton(f"📚 {r[1]}", callback_data=f"group_{r[0]}")] for r in rows]
                kb.append([InlineKeyboardButton("➕ Группа", callback_data="add_group")])
                kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
                await q.edit_message_text("📚 Группы:", reply_markup=InlineKeyboardMarkup(kb))
            else:
                await q.edit_message_text("📚 Нет групп", reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Группа", callback_data="add_group")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="start")]
                ]))
        elif d.startswith("group_"):
            gid = int(d.split("_")[1])
            group = cursor.execute("SELECT name FROM groups WHERE id = ?", (gid,)).fetchone()
            rows = cursor.execute("SELECT s.id, s.name, s.phone FROM students s JOIN student_group sg ON s.id = sg.student_id WHERE sg.group_id = ? ORDER BY s.name", (gid,)).fetchall()
            if rows:
                txt = f"📚 *{group[0]}*\n\n"
                for i, r in enumerate(rows, 1):
                    mem = cursor.execute("SELECT lessons_left, valid_until FROM memberships WHERE student_id = ? AND status = 'active' AND valid_until > date('now') ORDER BY valid_until ASC LIMIT 1", (r[0],)).fetchone()
                    if mem:
                        txt += f"{i}. {r[1]} — {mem[0]} занятий (до {mem[1]})\n"
                    else:
                        txt += f"{i}. {r[1]} — ❌ нет абонемента\n"
            else:
                txt = f"📚 {group[0]}: нет учеников"
            await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_groups")]]))
        elif d == "admin_parents":
            rows = cursor.execute("SELECT p.name, p.phone, p.telegram_id, COUNT(pc.student_id) FROM parents p LEFT JOIN parent_child pc ON p.id = pc.parent_id GROUP BY p.id").fetchall()
            if rows:
                txt = "👪 Родители:\n" + "\n".join([f"• {r[0]} {r[1]} 🆔 {r[2]} 👦 {r[3]}" for r in rows])
            else:
                txt = "👪 Нет родителей"
            kb = [
                [InlineKeyboardButton("➕ Родитель", callback_data="add_parent")],
                [InlineKeyboardButton("🔙 Назад", callback_data="start")]
            ]
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
        elif d == "mark_group":
            groups = cursor.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
            if groups:
                kb = [[InlineKeyboardButton(f"📚 {g[1]}", callback_data=f"mark_group_{g[0]}")] for g in groups]
                kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
                await q.edit_message_text("📚 Выберите группу для отметки:", reply_markup=InlineKeyboardMarkup(kb))
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
        elif d.startswith("mark_student_"):
            parts = d.split("_")
            sid = int(parts[2])
            present = int(parts[3])
            gid = int(parts[4])

            student = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
            today = datetime.now().strftime("%Y-%m-%d")
            today_display = datetime.now().strftime("%d.%m.%Y")

            # Проверяем, был ли уже отмечен сегодня
            already_marked = cursor.execute(
                "SELECT id, present FROM attendance WHERE student_id = ? AND date = ?", 
                (sid, today)
            ).fetchone()

            if present == 1:
                mem = cursor.execute("""
                    SELECT id, lessons_left FROM memberships 
                    WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
                    ORDER BY valid_until ASC LIMIT 1
                """, (sid,)).fetchone()

                if mem:
                    if already_marked:
                        # Если уже отмечен, показываем предупреждение
                        await q.answer(f"⚠️ {student[0]} уже отмечен сегодня!", show_alert=True)
                    else:
                        new_left = mem[1] - 1
                        cursor.execute("UPDATE memberships SET lessons_left = ? WHERE id = ?", (new_left, mem[0]))
                        cursor.execute("INSERT INTO attendance (student_id, date) VALUES (?, ?)", (sid, today))
                        conn.commit()

                        # Уведомления
                        if new_left == 1:
                            for admin_id in ADMIN_IDS:
                                try:
                                    await context.bot.send_message(admin_id, f"⚠️ У {student[0]} последнее занятие!")
                                except:
                                    pass
                        elif new_left == 0:
                            for admin_id in ADMIN_IDS:
                                try:
                                    await context.bot.send_message(admin_id, f"❌ У {student[0]} занятия закончились!")
                                except:
                                    pass
                        elif new_left < 0:
                            for admin_id in ADMIN_IDS:
                                try:
                                    await context.bot.send_message(admin_id, f"⛔ У {student[0]} долг: {abs(new_left)}")
                                except:
                                    pass

                        await q.answer(f"✅ {student[0]} отмечен! Осталось: {new_left}")
                else:
                    await q.answer(f"❌ У {student[0]} нет активного абонемента!", show_alert=True)
            else:
                if already_marked:
                    # Если уже отмечен как присутствовал, спрашиваем подтверждение на пропуск
                    if already_marked[1] == 1:
                        kb = InlineKeyboardMarkup([[
                            InlineKeyboardButton("✅ Да, отметить пропуск", callback_data=f"force_absent_{sid}_{gid}"),
                            InlineKeyboardButton("❌ Нет", callback_data=f"mark_group_{gid}")
                        ]])
                        await q.edit_message_text(
                            f"⚠️ {student[0]} уже отмечен как присутствовал сегодня.\n"
                            f"Отметить как пропуск? Это спишет занятие!",
                            reply_markup=kb
                        )
                        return
                    else:
                        await q.answer(f"❌ {student[0]} уже отмечен как пропуск", show_alert=True)
                else:
                    cursor.execute("INSERT INTO attendance (student_id, date, present) VALUES (?, ?, 0)", (sid, today,))
                    conn.commit()
                    await q.answer(f"❌ {student[0]} отмечен как пропуск")

            # Обновляем список группы с подсветкой отмеченных
            group = cursor.execute("SELECT name FROM groups WHERE id = ?", (gid,)).fetchone()
            students = cursor.execute("""
                SELECT s.id, s.name FROM students s 
                JOIN student_group sg ON s.id = sg.student_id 
                WHERE sg.group_id = ? 
                ORDER BY s.name
            """, (gid,)).fetchall()
            
            kb = []
            for s in students:
                # Проверяем, отмечен ли сегодня
                marked_today = cursor.execute(
                    "SELECT present FROM attendance WHERE student_id = ? AND date = ?", 
                    (s[0], today)
                ).fetchone()
                
                if marked_today:
                    if marked_today[0] == 1:
                        # Был ✅
                        btn_text = f"{s[1]} ✅✅"
                    else:
                        # Был ❌
                        btn_text = f"{s[1]} ❌❌"
                else:
                    btn_text = s[1]
                
                kb.append([
                    InlineKeyboardButton(f"{btn_text} ✅", callback_data=f"mark_student_{s[0]}_1_{gid}"),
                    InlineKeyboardButton("❌", callback_data=f"mark_student_{s[0]}_0_{gid}")
                ])
            
            # Добавляем кнопки управления
            kb.append([
                InlineKeyboardButton("✅ Все", callback_data=f"mark_all_1_{gid}"),
                InlineKeyboardButton("❌ Все", callback_data=f"mark_all_0_{gid}")
            ])
            kb.append([InlineKeyboardButton("📋 Журнал сегодня", callback_data=f"today_log_{gid}")])
            kb.append([InlineKeyboardButton("↩️ Исправить", callback_data=f"fix_today_{gid}")])
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="mark_group")])
            
            await q.edit_message_text(f"📋 {group[0]} на {today_display}", reply_markup=InlineKeyboardMarkup(kb))

        elif d.startswith("force_absent_"):
            parts = d.split("_")
            sid = int(parts[2])
            gid = int(parts[3])
            
            today = datetime.now().strftime("%Y-%m-%d")
            
            # Удаляем старую отметку и ставим пропуск
            cursor.execute("DELETE FROM attendance WHERE student_id = ? AND date = ?", (sid, today))
            cursor.execute("INSERT INTO attendance (student_id, date, present) VALUES (?, ?, 0)", (sid, today))
            conn.commit()
            
            student = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
            await q.answer(f"❌ {student[0]} теперь отмечен как пропуск")
            
            # Возвращаемся к группе
            await show_mark_group(q, context, gid)

        elif d.startswith("today_log_"):
            gid = int(d.split("_")[2])
            today = datetime.now().strftime("%Y-%m-%d")
            today_display = datetime.now().strftime("%d.%m.%Y")
            
            group = cursor.execute("SELECT name FROM groups WHERE id = ?", (gid,)).fetchone()
            marked = cursor.execute("""
                SELECT s.name, a.present 
                FROM attendance a
                JOIN students s ON a.student_id = s.id
                JOIN student_group sg ON s.id = sg.student_id
                WHERE sg.group_id = ? AND a.date = ?
                ORDER BY a.present DESC, s.name
            """, (gid, today)).fetchall()
            
            if marked:
                txt = f"📋 Журнал {group[0]} на {today_display}:\n\n"
                present = [f"✅ {m[0]}" for m in marked if m[1] == 1]
                absent = [f"❌ {m[0]}" for m in marked if m[1] == 0]
                
                if present:
                    txt += "**Присутствовали:**\n" + "\n".join(present) + "\n\n"
                if absent:
                    txt += "**Отсутствовали:**\n" + "\n".join(absent)
            else:
                txt = f"📭 На {today_display} отметок нет"
            
            kb = [[InlineKeyboardButton("🔙 Назад", callback_data=f"mark_group_{gid}")]]
            await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

        elif d.startswith("fix_today_"):
            gid = int(d.split("_")[2])
            today = datetime.now().strftime("%Y-%m-%d")
            
            # Показываем список для исправления
            group = cursor.execute("SELECT name FROM groups WHERE id = ?", (gid,)).fetchone()
            students = cursor.execute("""
                SELECT s.id, s.name, a.present 
                FROM students s 
                JOIN student_group sg ON s.id = sg.student_id 
                LEFT JOIN attendance a ON s.id = a.student_id AND a.date = ?
                WHERE sg.group_id = ?
                ORDER BY s.name
            """, (today, gid)).fetchall()
            
            kb = []
            for s in students:
                status = ""
                if s[2] == 1:
                    status = " ✅"
                elif s[2] == 0:
                    status = " ❌"
                    
                kb.append([
                    InlineKeyboardButton(f"{s[1]}{status}", callback_data=f"fix_student_{s[0]}_{gid}")
                ])
            
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data=f"mark_group_{gid}")])
            await q.edit_message_text(
                f"📝 Исправление отметок для {group[0]}\nВыберите ученика:",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        elif d.startswith("fix_student_"):
            parts = d.split("_")
            sid = int(parts[2])
            gid = int(parts[3])
            
            today = datetime.now().strftime("%Y-%m-%d")
            current = cursor.execute(
                "SELECT present FROM attendance WHERE student_id = ? AND date = ?", 
                (sid, today)
            ).fetchone()
            
            student = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
            
            kb = [
                [InlineKeyboardButton("✅ Присутствовал", callback_data=f"set_present_{sid}_{gid}")],
                [InlineKeyboardButton("❌ Отсутствовал", callback_data=f"set_absent_{sid}_{gid}")],
                [InlineKeyboardButton("🗑️ Удалить отметку", callback_data=f"clear_mark_{sid}_{gid}")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"fix_today_{gid}")]
            ]
            
            status_text = ""
            if current:
                status_text = f"\n\nСейчас: {'✅' if current[0] == 1 else '❌'}"
            
            await q.edit_message_text(
                f"📝 {student[0]}{status_text}\nВыберите действие:",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        elif d.startswith("set_present_"):
            parts = d.split("_")
            sid = int(parts[2])
            gid = int(parts[3])
            
            today = datetime.now().strftime("%Y-%m-%d")
            
            # Проверяем, есть ли уже отметка
            current = cursor.execute(
                "SELECT present FROM attendance WHERE student_id = ? AND date = ?", 
                (sid, today)
            ).fetchone()
            
            if current:
                if current[0] == 0:
                    # Меняем пропуск на присутствие
                    cursor.execute("UPDATE attendance SET present = 1 WHERE student_id = ? AND date = ?", (sid, today))
                    # Возвращаем занятие
                    cursor.execute("""
                        UPDATE memberships SET lessons_left = lessons_left + 1 
                        WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
                    """, (sid,))
                    await q.answer("✅ Отметка исправлена на присутствие")
                else:
                    await q.answer("ℹ️ Уже отмечен как присутствие")
            else:
                # Новой отметки нет, надо списать занятие
                mem = cursor.execute("""
                    SELECT id, lessons_left FROM memberships 
                    WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
                    LIMIT 1
                """, (sid,)).fetchone()
                
                if mem:
                    new_left = mem[1] - 1
                    cursor.execute("UPDATE memberships SET lessons_left = ? WHERE id = ?", (new_left, mem[0]))
                    cursor.execute("INSERT INTO attendance (student_id, date) VALUES (?, ?)", (sid, today))
                    await q.answer(f"✅ Добавлено присутствие, осталось: {new_left}")
                else:
                    await q.answer("❌ Нет активного абонемента!", show_alert=True)
            
            conn.commit()
            await show_mark_group(q, context, gid)

        elif d.startswith("set_absent_"):
            parts = d.split("_")
            sid = int(parts[2])
            gid = int(parts[3])
            
            today = datetime.now().strftime("%Y-%m-%d")
            
            # Проверяем, есть ли уже отметка
            current = cursor.execute(
                "SELECT present FROM attendance WHERE student_id = ? AND date = ?", 
                (sid, today)
            ).fetchone()
            
            if current:
                if current[0] == 1:
                    # Меняем присутствие на пропуск, возвращаем занятие
                    cursor.execute("UPDATE attendance SET present = 0 WHERE student_id = ? AND date = ?", (sid, today))
                    cursor.execute("""
                        UPDATE memberships SET lessons_left = lessons_left + 1 
                        WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
                    """, (sid,))
                    await q.answer("✅ Отметка исправлена на пропуск")
                else:
                    await q.answer("ℹ️ Уже отмечен как пропуск")
            else:
                # Просто ставим пропуск
                cursor.execute("INSERT INTO attendance (student_id, date, present) VALUES (?, ?, 0)", (sid, today))
                await q.answer("❌ Добавлен пропуск")
            
            conn.commit()
            await show_mark_group(q, context, gid)

        elif d.startswith("clear_mark_"):
            parts = d.split("_")
            sid = int(parts[2])
            gid = int(parts[3])
            
            today = datetime.now().strftime("%Y-%m-%d")
            
            # Проверяем, была ли отметка присутствия
            was_present = cursor.execute(
                "SELECT present FROM attendance WHERE student_id = ? AND date = ?", 
                (sid, today)
            ).fetchone()
            
            if was_present and was_present[0] == 1:
                # Если был отмечен как присутствие, возвращаем занятие
                cursor.execute("""
                    UPDATE memberships SET lessons_left = lessons_left + 1 
                    WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
                """, (sid,))
            
            cursor.execute("DELETE FROM attendance WHERE student_id = ? AND date = ?", (sid, today))
            conn.commit()
            
            student = cursor.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
            await q.answer(f"🗑️ Отметка для {student[0]} удалена")
            
            await show_mark_group(q, context, gid)

        elif d.startswith("mark_all_"):
            parts = d.split("_")
            present = int(parts[2])
            gid = int(parts[3])

            students = cursor.execute("""
                SELECT s.id, s.name FROM students s 
                JOIN student_group sg ON s.id = sg.student_id 
                WHERE sg.group_id = ?
            """, (gid,)).fetchall()
            
            today = datetime.now().strftime("%Y-%m-%d")
            success = 0
            failed = 0
            already = 0

            for s in students:
                sid = s[0]
                
                # Проверяем, был ли уже отмечен
                already_marked = cursor.execute(
                    "SELECT id, present FROM attendance WHERE student_id = ? AND date = ?", 
                    (sid, today)
                ).fetchone()
                
                if already_marked:
                    already += 1
                    continue
                    
                if present == 1:
                    mem = cursor.execute("""
                        SELECT id, lessons_left FROM memberships 
                        WHERE student_id = ? AND status = 'active' AND valid_until > date('now')
                        ORDER BY valid_until ASC LIMIT 1
                    """, (sid,)).fetchone()

                    if mem:
                        new_left = mem[1] - 1
                        cursor.execute("UPDATE memberships SET lessons_left = ? WHERE id = ?", (new_left, mem[0]))
                        cursor.execute("INSERT INTO attendance (student_id, date) VALUES (?, ?)", (sid, today))

                        if new_left == 1:
                            for admin_id in ADMIN_IDS:
                                try:
                                    await context.bot.send_message(admin_id, f"⚠️ У {s[1]} последнее занятие!")
                                except:
                                    pass
                        elif new_left == 0:
                            for admin_id in ADMIN_IDS:
                                try:
                                    await context.bot.send_message(admin_id, f"❌ У {s[1]} занятия закончились!")
                                except:
                                    pass
                        elif new_left < 0:
                            for admin_id in ADMIN_IDS:
                                try:
                                    await context.bot.send_message(admin_id, f"⛔ У {s[1]} долг: {abs(new_left)}")
                                except:
                                    pass
                        success += 1
                    else:
                        failed += 1
                else:
                    cursor.execute("INSERT INTO attendance (student_id, date, present) VALUES (?, ?, 0)", (sid, today))
                    success += 1

            conn.commit()

            msg = f"✅ Отмечено: {success}"
            if failed > 0:
                msg += f"\n❌ Нет абонемента: {failed}"
            if already > 0:
                msg += f"\n⚠️ Уже отмечены: {already}"
            await q.answer(msg)

            await show_mark_group(q, context, gid)

# Добавь вспомогательную функцию после button_handler:
async def show_mark_group(q, context, gid):
    """Показывает группу для отметки"""
    group = cursor.execute("SELECT name FROM groups WHERE id = ?", (gid,)).fetchone()
    today = datetime.now().strftime("%Y-%m-%d")
    today_display = datetime.now().strftime("%d.%m.%Y")
    
    students = cursor.execute("""
        SELECT s.id, s.name FROM students s 
        JOIN student_group sg ON s.id = sg.student_id 
        WHERE sg.group_id = ? 
        ORDER BY s.name
    """, (gid,)).fetchall()
    
    kb = []
    for s in students:
        # Проверяем, отмечен ли сегодня
        marked_today = cursor.execute(
            "SELECT present FROM attendance WHERE student_id = ? AND date = ?", 
            (s[0], today)
        ).fetchone()
        
        if marked_today:
            if marked_today[0] == 1:
                btn_text = f"{s[1]} ✅✅"
            else:
                btn_text = f"{s[1]} ❌❌"
        else:
            btn_text = s[1]
        
        kb.append([
            InlineKeyboardButton(f"{btn_text} ✅", callback_data=f"mark_student_{s[0]}_1_{gid}"),
            InlineKeyboardButton("❌", callback_data=f"mark_student_{s[0]}_0_{gid}")
        ])
    
    kb.append([
        InlineKeyboardButton("✅ Все", callback_data=f"mark_all_1_{gid}"),
        InlineKeyboardButton("❌ Все", callback_data=f"mark_all_0_{gid}")
    ])
    kb.append([InlineKeyboardButton("📋 Журнал сегодня", callback_data=f"today_log_{gid}")])
    kb.append([InlineKeyboardButton("↩️ Исправить", callback_data=f"fix_today_{gid}")])
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="mark_group")])
    
    await q.edit_message_text(f"📋 {group[0]} на {today_display}", reply_markup=InlineKeyboardMarkup(kb))
def main():
            students = cursor.execute("SELECT id, name FROM students ORDER BY name").fetchall()
            if students:
                kb = [[InlineKeyboardButton(f"👤 {s[1]}", callback_data=f"extend_student_{s[0]}")] for s in students]
                kb.append([InlineKeyboardButton("🔙 Назад", callback_data="start")])
                await q.edit_message_text("👤 Выберите ученика для продления:", reply_markup=InlineKeyboardMarkup(kb))
            else:
                await q.edit_message_text("👥 Нет учеников")
    elif d.startswith("extend_student_"):
            sid = int(d.split("_")[2])
            context.user_data['extend_student'] = sid
            await q.edit_message_text("📅 Введите количество дней для продления:")
            return EXTEND_DAYS

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
        cursor.execute("INSERT INTO students (telegram_id, name, phone) VALUES (?, ?, ?)",
                      (tid, context.user_data['name'], context.user_data['phone']))
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
        cursor.execute("INSERT INTO parents (telegram_id, name, phone) VALUES (?, ?, ?)",
                      (tid, context.user_data['name'], context.user_data['phone']))
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

        cursor.execute('''
            INSERT INTO memberships (student_id, lessons_left, valid_until, status) 
            VALUES (?, ?, ?, 'active')
        ''', (student[0], lessons, valid_until))
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

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено")
    context.user_data.clear()
    return ConversationHandler.END

# ===== ЗАПУСК =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("delete_student", delete_student_cmd))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(add_student_entry, pattern="^add_student$")],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_phone)],
            TG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_student_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(add_parent_entry, pattern="^add_parent$")],
        states={
            PARENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_name)],
            PARENT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_phone)],
            PARENT_TG: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_parent_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(add_membership_entry, pattern="^add_membership$")],
        states={
            LESSONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_membership_lessons)],
            DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_membership_days)],
            MEM_TG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_membership_final)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(add_group_entry, pattern="^add_group$")],
        states={
            GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_group_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(lambda u,c: EXTEND_DAYS, pattern="^extend_student_")],
        states={
            EXTEND_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_days_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("🚀 Бот с удалением ученика и сводкой по группам запущен")
    app.run_polling()
    # ===== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ОТМЕТОК =====


# ===== ЗАПУСК =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    # ... весь твой код в main() ...
    app.run_polling()

if __name__ == "__main__":
    main()
