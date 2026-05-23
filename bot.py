import os
import sqlite3
import requests
import math
import json
from datetime import datetime, date
from calendar import monthrange
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

DB_PATH = os.environ.get("DB_PATH", "budget.db")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
EXCHANGE_API_KEY = os.environ.get("EXCHANGE_API_KEY", "")
USER1 = os.environ.get("USER1", "tim")
USER2 = os.environ.get("USER2", "masha")

CATEGORIES = [
    "🛒 Продукты", "🍽 Рестораны", "🛵 Доставка", "☕️ Кофик/Сигареты",
    "💪 Спорт", "🏥 Страховка", "🏠 Дом",
    "🧴 Бытовая химия", "🐩 Чуи",
    "👗 Одежда", "📦 Онлайн-покупки", "🎁 Подарки", "📝 Другое",
]

WALLETS_DEFAULT = [
    ("нз_брат",   "НЗ у брата",       "$", 6000.0),
    ("крипто_хол","Крипто холодный",  "$", 3000.0),
    ("крипто_теп","Крипто тёплый",    "$", 1500.0),
    ("оборот",    "В обороте",        "$", 4000.0),
    ("нал_usd",   "Наличка $",        "$", 0.0),
    ("нал_eur",   "Наличка €",        "€", 0.0),
]

FIXED_EXPENSES = [
    ("Аренда квартиры", 1280.0),
    ("Интернет",          26.0),
    ("Свет",             100.0),
    ("Вода",              30.0),
    ("Спортзал",          35.0),
]
FIXED_TOTAL = sum(a for _, a in FIXED_EXPENSES)
INCOME_USD  = 3700.0

GOOGLE_SHEET_ID  = os.environ.get("GOOGLE_SHEET_ID", "1pke7JzLELpxgvkcRwHCUcYRWKvP4YFhyjqSSv5eE6WU")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS", "")

# ── States ────────────────────────────────────────────────────
(
    MAIN_MENU,
    ADD_CATEGORY, ADD_AMOUNT, ADD_DESC,
    # Долги — новая расширенная цепочка
    DEBT_TYPE,       # между нами / с чужими
    DEBT_DIRECTION,  # мне должны / я должен
    DEBT_WHO,        # имя
    DEBT_AMOUNT_S,
    DEBT_CURRENCY,
    DEBT_DESC_S,
    # Кошелёк
    WALLET_CHOOSE, WALLET_OP, WALLET_AMOUNT_S,
) = range(13)

# ── Google Sheets ──────────────────────────────────────────────
def get_sheet():
    if not GSPREAD_AVAILABLE:
        return None
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds_dict = {
            "type": "service_account",
            "project_id": "mystic-song-468921-r7",
            "private_key_id": "6e82a413d8181dd1ae45bfd60839c3227ef24785",
            "private_key": (
                "-----BEGIN PRIVATE KEY-----\n"
                "MIIEvwIBADANBgkqhkiG9w0BAQEFAASCBKkwggSlAgEAAoIBAQDAHRztikWqJvoq\n"
                "bAFpsy9E5RXWtNJiAzzkB4IMU4hC0C1G53atWkYV4dyPOY8+8R9aSFuL3AgmfTZs\n"
                "TTVEUIbuk+KC3Zh9slWL/2JGebpwP2XwdXXzFKS8aObg6mBjzkQug3HeuNelV2Zo\n"
                "adFqgDrs5KVHTnLoLsUpuSiURnP3xuQsaBAN0Mk2eNCPKSZIFnxBv4uW91R+DZ4P\n"
                "2kfBy+vRPHwmSG+H4m9JdtHWCGhyVn1Cscevchs6rqJxZqoXyH3XradTLqL1uHya\n"
                "pn/mPajmTOiGL3X81Zn18Ez327Fy+UPpI/zdGXTeQ3Mlu/0J+XzLZ0+/QUb6G6sA\n"
                "URlIV8jVAgMBAAECggEATk2PdOJe8rNgU9oh2UtHgPU+qXyaI4jeULMetpd1eoYP\n"
                "bk75eD7LQjAFDfuP/z+YX9wONDtCty1h+VKe23FXDfcI4/4eIV2GsMEu9Tq4Wvf8\n"
                "PL4jjShk3MaFFDdzgjqYX70DtJvyiVnOS9CVoqsRWWz4UNAQ1cH6ar8lYwo0SlD5\n"
                "eCbQRs3cGKvkccpeVLilkkn7MH+x8W053RtJBOhA4lG9kjInuBbA4+N7ahDpvgOU\n"
                "3D9iZfU6y4D5BTCR27sSc37Ia3E4wndDWiVByuvvMD/zSs2LKDBqPordWlokYOmf\n"
                "IYr64HuAKQvNXUxQ79RD+hovu7JwVJUdelFfThdRIQKBgQDu0yIDkxhFFnGUQs2W\n"
                "jGC649lgtm/7wS0OFQ7LTz7AGUDbyywQE9AEuamt3/cWnyh1n1KGzl9xT+P1ld96\n"
                "6H5NiQcvRqCb86o1w5wFNBKaeEsX+UpZvsgAUjnFgxP4RAY9sHoCjF1V7CvY2THv\n"
                "oY8QyEc8zB1AtKduNx75TRljTwKBgQDN7gKTSZfnWYfWlHBC16CmMpXD4s0Xtg/C\n"
                "Z+xRllgPU4U/3iRUAhQrlzJGQYu5fholFLnQM0QvZFqKc1GZ8cDkCN56GQlFnJ8g\n"
                "aj2oKMxFvr+dYqRYEQOIebnJ4PdFTmctrQc+DuQi45BLx1kTzvMqA+jascwfWgao\n"
                "IP5be3LYmwKBgQDptGpgnRzu3puevhB49j3iJP2fimfjMJJqaWjkw1NgoFW7wAIK\n"
                "aZjyRs0ofTZKSM1K7PHRQTpcpBUrSdI7cC/IqAMD3FVmxvcVTanr3Z0m0/iIKUb8\n"
                "s5j713r5MN/l3otM6tk6jSj43/e4aDJZkPtzLMmpUQR/QUlmrUH+K9hgOQKBgQDJ\n"
                "ciFWz9EnYa++O2suGB1xN17GRuF2ZoU4Gc1VaosuQvfAqKBFBduRYNCvZYM3q6IL\n"
                "0CCNCPmUmsjvUyvqOlIFQJ/SNRea30HSxdsW2wIo4BY18b7u34XjRaB3WfjJ9Y59\n"
                "YhwJmyuU7aPEXXhIJlQ9L6Hj/bW+naSRZ+UqvLJ2LQKBgQDSg9HHXiA3Hcz7bwlf\n"
                "t5AaCORPrqSgZ5EAc4ICOYzpGG4IH1ibNyknZOk7Qm1as4KO/k93cU1qa/W3tepN\n"
                "CiXZI8yyMxjaz1PzRMn1qh3air682QGo340gUWKMX7ylpTyN4D83bFpAf8VlIy9C\n"
                "dYivzokWmIFaPYuWkHaDfbUtXw==\n"
                "-----END PRIVATE KEY-----\n"
            ),
            "client_email": "budgetbotv2@mystic-song-468921-r7.iam.gserviceaccount.com",
            "client_id": "116593513222553861356",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/budgetbotv2%40mystic-song-468921-r7.iam.gserviceaccount.com",
            "universe_domain": "googleapis.com"
        }
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        return client.open_by_key(GOOGLE_SHEET_ID)
    except Exception as e:
        print(f"Google Sheets error: {e}")
        return None

def sheets_init_headers():
    try:
        sh = get_sheet()
        if not sh: return
        ws = sh.sheet1
        if ws.row_count == 0 or ws.cell(1, 1).value != "Дата":
            ws.update("A1:F1", [["Дата", "Кто", "Категория", "Сумма €", "Комментарий", "Месяц"]])
            ws.format("A1:F1", {"textFormat": {"bold": True}})
    except Exception as e:
        print(f"Sheets init error: {e}")

def sheets_add_expense(username, amount_eur, category, description, dt):
    try:
        sh = get_sheet()
        if not sh: return
        ws = sh.sheet1
        month = dt[:7]
        ws.append_row([dt, f"@{username}", category, amount_eur, description or "", month])
    except Exception as e:
        print(f"Sheets append error: {e}")

def sheets_delete_expense(expense_id: int, dt: str, username: str, category: str, amount_eur: float):
    """Удалить строку из Google Sheets по совпадению даты/пользователя/категории/суммы."""
    try:
        sh = get_sheet()
        if not sh: return
        ws = sh.sheet1
        all_rows = ws.get_all_values()
        # Ищем строку (пропускаем заголовок)
        for i, row in enumerate(all_rows[1:], start=2):
            if (len(row) >= 4
                    and row[0] == dt
                    and row[1] == f"@{username}"
                    and row[2] == category
                    and abs(float(row[3] or 0) - amount_eur) < 0.01):
                ws.delete_rows(i)
                return
    except Exception as e:
        print(f"Sheets delete error: {e}")

# ── Keyboards ─────────────────────────────────────────────────
def main_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💸 Добавить трату"), KeyboardButton("📊 Обзор")],
        [KeyboardButton("🏦 Накопления"),     KeyboardButton("📈 Аналитика")],
        [KeyboardButton("🤝 Долги"),          KeyboardButton("📜 История")],
        [KeyboardButton("🏠 Фикс. расходы"), KeyboardButton("⚙️ Кошелёк")],
    ], resize_keyboard=True)

def back_kb():
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 Главное меню")]], resize_keyboard=True)

# ── DB ────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT, amount_eur REAL,
        category TEXT, description TEXT, date TEXT
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS debts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        debt_type TEXT DEFAULT 'external',
        direction TEXT DEFAULT 'they_owe',
        from_user TEXT, amount REAL, currency TEXT,
        description TEXT, date TEXT, settled INTEGER DEFAULT 0
    )''')
    # Добавляем колонки если их ещё нет (миграция)
    try:
        cur.execute("ALTER TABLE debts ADD COLUMN debt_type TEXT DEFAULT 'external'")
    except: pass
    try:
        cur.execute("ALTER TABLE debts ADD COLUMN direction TEXT DEFAULT 'they_owe'")
    except: pass
    cur.execute('''CREATE TABLE IF NOT EXISTS wallets (
        key TEXT PRIMARY KEY, label TEXT, currency TEXT, amount REAL
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS exchange_cache (
        pair TEXT PRIMARY KEY, rate REAL, updated TEXT
    )''')
    for w_key, w_label, w_currency, w_amount in WALLETS_DEFAULT:
        cur.execute("INSERT OR IGNORE INTO wallets VALUES (?,?,?,?)",
                    (w_key, w_label, w_currency, w_amount))
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(DB_PATH)

# ── Exchange ──────────────────────────────────────────────────
def get_usd_to_eur() -> float:
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT rate, updated FROM exchange_cache WHERE pair='USD_EUR'")
    row = cur.fetchone(); conn.close()
    if row and (datetime.now() - datetime.fromisoformat(row[1])).seconds < 3600:
        return row[0]
    try:
        if EXCHANGE_API_KEY:
            r = requests.get(f"https://v6.exchangerate-api.com/v6/{EXCHANGE_API_KEY}/pair/USD/EUR", timeout=5).json()
            rate = r["conversion_rate"]
        else:
            r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5).json()
            rate = r["rates"]["EUR"]
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO exchange_cache VALUES ('USD_EUR',?,?)",
                     (rate, datetime.now().isoformat()))
        conn.commit(); conn.close()
        return rate
    except:
        return 0.92

def usd_to_eur(usd): return round(usd * get_usd_to_eur(), 2)
def eur_to_usd(eur):
    r = get_usd_to_eur()
    return round(eur / r, 2) if r else round(eur / 0.92, 2)
def c(x): return math.ceil(x)

def get_username(update: Update) -> str:
    return (update.effective_user.username or update.effective_user.first_name or "unknown").lower()

def month_expenses_eur() -> float:
    today = date.today()
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT SUM(amount_eur) FROM expenses WHERE date LIKE ?",
                (f"{today.year}-{today.month:02d}%",))
    row = cur.fetchone(); conn.close()
    return row[0] or 0.0

def month_expenses_by(year, month) -> float:
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT SUM(amount_eur) FROM expenses WHERE date LIKE ?",
                (f"{year}-{month:02d}%",))
    row = cur.fetchone(); conn.close()
    return row[0] or 0.0

def month_by_category_for(year, month) -> dict:
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT category, SUM(amount_eur) FROM expenses WHERE date LIKE ? GROUP BY category",
                (f"{year}-{month:02d}%",))
    rows = cur.fetchall(); conn.close()
    return {r[0]: r[1] for r in rows}

def total_savings_usd() -> float:
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT currency, amount FROM wallets")
    rows = cur.fetchall(); conn.close()
    total = 0.0
    for cur_, amt in rows:
        total += amt if cur_ == "$" else eur_to_usd(amt)
    return round(total, 2)

def month_name_ru(month):
    names = ["Январь","Февраль","Март","Апрель","Май","Июнь",
             "Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"]
    return names[month - 1]

def prev_month(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)

def next_month(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)

# ── Helpers ───────────────────────────────────────────────────
async def go_home(update: Update, text="🏠 Главное меню"):
    await update.message.reply_text(text, reply_markup=main_kb())

async def is_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.message.text == "🔙 Главное меню":
        ctx.user_data.clear()
        await go_home(update)
        return True
    return False

# ── /start ────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Привет! Я ваш семейный бюджет-бот.*\n\nВыбери действие:",
        reply_markup=main_kb(), parse_mode="Markdown"
    )
    return MAIN_MENU

# ── Router ────────────────────────────────────────────────────
async def menu_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if   text == "💸 Добавить трату":  return await add_start(update, ctx)
    elif text == "📊 Обзор":           await show_overview(update, ctx)
    elif text == "🏦 Накопления":      await show_savings(update, ctx)
    elif text == "📈 Аналитика":       await show_analytics(update, ctx)
    elif text == "🤝 Долги":           return await debts_start(update, ctx)
    elif text == "📜 История":         await show_history(update, ctx)
    elif text == "🏠 Фикс. расходы":  await show_fixed(update, ctx)
    elif text == "⚙️ Кошелёк":        return await wallet_start(update, ctx)
    return MAIN_MENU

# ── Добавить трату ────────────────────────────────────────────
async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat:{cat}")] for cat in CATEGORIES]
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="go_main")])
    await update.message.reply_text(
        "📂 *Выбери категорию:*",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    await update.message.reply_text("Или назад:", reply_markup=back_kb())
    return ADD_CATEGORY

async def add_category_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["category"] = query.data.replace("cat:", "")
    await query.edit_message_text(
        f"✅ *{ctx.user_data['category']}*\n\n"
        "💶 Введи сумму в €\n_(или `40 USD` для автоконвертации)_",
        parse_mode="Markdown"
    )
    return ADD_AMOUNT

async def add_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_back(update, ctx): return MAIN_MENU
    parts = update.message.text.strip().split()
    try:
        raw = float(parts[0].replace(",", "."))
        currency = parts[1].upper() if len(parts) > 1 else "EUR"
        if currency in ("USD", "$"):
            eur = usd_to_eur(raw)
            note = f" _(${raw} → €{eur} по курсу)_"
        else:
            eur = raw; note = ""
        ctx.user_data["amount_eur"] = eur
        ctx.user_data["amount_note"] = note
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Пример: `350` или `40 USD`", parse_mode="Markdown")
        return ADD_AMOUNT
    await update.message.reply_text(
        f"💶 *€{eur}*{note}\n\n📝 Комментарий _(или `-` пропустить)_",
        parse_mode="Markdown", reply_markup=back_kb()
    )
    return ADD_DESC

async def add_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_back(update, ctx): return MAIN_MENU
    desc = update.message.text.strip()
    if desc == "-": desc = ""
    username = get_username(update)
    today_str = date.today().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO expenses (username, amount_eur, category, description, date) VALUES (?,?,?,?,?)",
        (username, ctx.user_data["amount_eur"], ctx.user_data["category"], desc, today_str)
    )
    conn.commit(); conn.close()
    sheets_add_expense(username, ctx.user_data["amount_eur"], ctx.user_data["category"], desc, today_str)

    spent = month_expenses_eur()
    left  = usd_to_eur(INCOME_USD) - FIXED_TOTAL - spent
    inline_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Ещё трату", callback_data="add_more"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="go_main"),
    ]])
    icon = "🟢" if left > 0 else "🔴"
    desc_line = f"\n📝 {desc}" if desc else ""
    await update.message.reply_text(
        f"✅ Записано!\n"
        f"👤 @{username} | {ctx.user_data['category']}\n"
        f"💶 €{ctx.user_data['amount_eur']}{ctx.user_data.get('amount_note','')}"
        f"{desc_line}\n\n"
        f"📊 Потрачено: €{c(spent)}\n"
        f"{icon} Остаток: €{c(left)}",
        reply_markup=inline_kb
    )
    ctx.user_data.clear()
    return MAIN_MENU

# ── Обзор ─────────────────────────────────────────────────────
async def show_overview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rate = get_usd_to_eur()
    income_eur = usd_to_eur(INCOME_USD)
    spent = month_expenses_eur()
    left  = income_eur - FIXED_TOTAL - spent
    today = date.today()
    days_left = monthrange(today.year, today.month)[1] - today.day
    daily = left / days_left if days_left > 0 else 0
    sav_usd = total_savings_usd()
    await update.message.reply_text(
        f"📊 *Обзор — {today.strftime('%B %Y')}*\n"
        f"💱 1 USD = {rate:.4f} EUR\n\n"
        f"💵 Зарплата: ${INCOME_USD:,.0f} → *€{income_eur:,.0f}*\n"
        f"🏠 Фикс. расходы: *€{FIXED_TOTAL:,.0f}*\n"
        f"🛒 Потрачено: *€{spent:,.0f}*\n"
        f"{'🟢' if left > 0 else '🔴'} Остаток: *€{left:,.0f}*\n"
        f"📅 До конца месяца: {days_left} дн. → *€{c(daily)}/день*\n\n"
        f"🏦 Накопления: *${sav_usd:,.0f}* ≈ *€{usd_to_eur(sav_usd):,.0f}*",
        parse_mode="Markdown", reply_markup=main_kb()
    )

# ── Накопления ────────────────────────────────────────────────
async def show_savings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT key, label, currency, amount FROM wallets")
    rows = cur.fetchall(); conn.close()
    rate = get_usd_to_eur()
    lines = ["🏦 *Накопления по кошелькам:*\n"]
    total_usd = 0.0
    for _, label, cur_, amt in rows:
        if cur_ == "$":
            equiv = f"≈ €{usd_to_eur(amt):,.0f}"
            total_usd += amt
        else:
            equiv = f"≈ ${eur_to_usd(amt):,.0f}"
            total_usd += eur_to_usd(amt)
        lines.append(f"• {label}: *{cur_}{amt:,.0f}* {equiv}")
    lines.append(f"\n💰 *Всего: ~${total_usd:,.0f}* ≈ *€{usd_to_eur(total_usd):,.0f}*")
    lines.append(f"💱 Курс: 1 USD = {rate:.4f} EUR")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

# ── История ───────────────────────────────────────────────────
async def show_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id, username, amount_eur, category, description, date FROM expenses ORDER BY id DESC LIMIT 10")
    rows = cur.fetchall(); conn.close()
    if not rows:
        await update.message.reply_text("📭 Записей пока нет.", reply_markup=main_kb())
        return
    await update.message.reply_text(
        "📜 *Последние 10 трат:*\nНажми 🗑 чтобы удалить",
        parse_mode="Markdown", reply_markup=main_kb()
    )
    for eid, user, amt, cat, desc, dt in rows:
        date_str = dt[5:10] if dt else "??-??"
        text = f"{date_str} | {cat}\n💶 €{c(amt)} 👤 @{user or '—'}" + (f"\n📝 {desc}" if desc else "")
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Удалить", callback_data=f"del_expense:{eid}")
        ]])
        await update.message.reply_text(text, reply_markup=keyboard)

async def delete_expense_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    eid = int(query.data.replace("del_expense:", ""))
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT amount_eur, category, description, username, date FROM expenses WHERE id=?", (eid,))
    row = cur.fetchone()
    if row:
        conn.execute("DELETE FROM expenses WHERE id=?", (eid,))
        conn.commit()
        amt, cat, desc, username, dt = row
        # ← Удаляем и из Google Sheets
        sheets_delete_expense(eid, dt, username or "", cat, amt)
        await query.edit_message_text(
            f"🗑 *Удалено:* {cat} — €{c(amt)}" + (f"\n_{desc}_" if desc else ""),
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text("❌ Запись не найдена.")
    conn.close()

# ── Фикс расходы ──────────────────────────────────────────────
async def show_fixed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = ["🏠 *Фиксированные расходы:*\n"]
    for name, amt in FIXED_EXPENSES:
        lines.append(f"• {name}: *€{c(amt)}*")
    lines.append(f"\n💶 *Итого: €{c(FIXED_TOTAL)}/мес*")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

# ══════════════════════════════════════════════════════════════
# ── ДОЛГИ — полностью переписано ─────────────────────────────
# ══════════════════════════════════════════════════════════════

SYM = {"USD": "$", "EUR": "€", "CASH_USD": "$ нал", "CASH_EUR": "€ нал"}

def debt_label(d) -> str:
    """Красивая строка для одного долга."""
    sym  = SYM.get(d[5] if len(d) > 5 else "EUR", "€")
    who  = d[3]
    amt  = d[4]
    desc = d[6] if len(d) > 6 else ""
    dt   = (d[7] if len(d) > 7 else "")[:10]
    direction = d[2] if len(d) > 2 else "they_owe"
    debt_type = d[1] if len(d) > 1 else "external"

    if debt_type == "partner":
        arrow = "← мне должен(а)" if direction == "they_owe" else "→ я должен(а)"
        who_str = f"Тима/Маша {arrow}"
    else:
        arrow = "← мне должен(а)" if direction == "they_owe" else "→ я должен(а)"
        who_str = f"{who} {arrow}"

    line = f"*{sym}{amt:,.0f}* | {who_str}"
    if desc: line += f"\n  _{desc}_"
    if dt:   line += f"\n  📅 {dt}"
    return line

async def debts_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Главный экран долгов — показывает сводку и кнопки."""
    conn = get_db(); cur = conn.cursor()
    cur.execute("""SELECT id, debt_type, direction, from_user, amount, currency, description, date
                   FROM debts WHERE settled=0 ORDER BY id DESC""")
    rows = cur.fetchall(); conn.close()

    # Разбиваем по типам
    partner_owe_me = [r for r in rows if r[1]=="partner" and r[2]=="they_owe"]
    partner_i_owe  = [r for r in rows if r[1]=="partner" and r[2]=="i_owe"]
    ext_owe_me     = [r for r in rows if r[1]=="external" and r[2]=="they_owe"]
    ext_i_owe      = [r for r in rows if r[1]=="external" and r[2]=="i_owe"]

    lines = ["🤝 *Долги*\n"]

    if partner_owe_me or partner_i_owe:
        lines.append("👫 *Между нами:*")
        for r in partner_owe_me:
            lines.append(f"  #{r[0]} Тима должен(а) мне — *{SYM.get(r[5],'€')}{r[4]:,.0f}*" + (f" _{r[6]}_" if r[6] else ""))
        for r in partner_i_owe:
            lines.append(f"  #{r[0]} Я должен(а) Тиме — *{SYM.get(r[5],'€')}{r[4]:,.0f}*" + (f" _{r[6]}_" if r[6] else ""))
        lines.append("")

    if ext_owe_me or ext_i_owe:
        lines.append("🌍 *С другими людьми:*")
        for r in ext_owe_me:
            lines.append(f"  #{r[0]} {r[3]} должен(а) мне — *{SYM.get(r[5],'€')}{r[4]:,.0f}*" + (f" _{r[6]}_" if r[6] else ""))
        for r in ext_i_owe:
            lines.append(f"  #{r[0]} Я должен(а) {r[3]} — *{SYM.get(r[5],'€')}{r[4]:,.0f}*" + (f" _{r[6]}_" if r[6] else ""))

    if not rows:
        lines.append("✅ Долгов нет!")

    # Кнопки закрытия долгов
    settle_buttons = []
    for r in rows:
        sym = SYM.get(r[5], "€")
        who = "партнёр" if r[1]=="partner" else r[3]
        direction_str = "мне должен" if r[2]=="they_owe" else "я должен"
        settle_buttons.append([InlineKeyboardButton(
            f"✅ #{r[0]} {who} ({direction_str}) {sym}{r[4]:,.0f}",
            callback_data=f"debt_settle:{r[0]}"
        )])

    settle_buttons.append([InlineKeyboardButton("➕ Записать долг", callback_data="debt_new")])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(settle_buttons) if settle_buttons else None
    )
    await update.message.reply_text("Или назад:", reply_markup=back_kb())
    return DEBT_TYPE  # входим в ConversationHandler

async def debt_new_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Нажали «Записать долг» — выбираем тип."""
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👫 Между нами (с партнёром)", callback_data="dtype_partner")],
        [InlineKeyboardButton("🌍 С другим человеком",       callback_data="dtype_external")],
    ])
    await query.edit_message_text("*Какой тип долга?*", parse_mode="Markdown", reply_markup=kb)
    return DEBT_TYPE

async def debt_settle_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Закрыть долг."""
    query = update.callback_query
    await query.answer()
    debt_id = int(query.data.split(":")[-1])
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT from_user, amount, currency FROM debts WHERE id=? AND settled=0", (debt_id,))
    row = cur.fetchone()
    if row:
        conn.execute("UPDATE debts SET settled=1 WHERE id=?", (debt_id,))
        conn.commit()
        sym = SYM.get(row[2], "€")
        await query.edit_message_text(f"✅ Долг #{debt_id} закрыт!\n{row[0]} — {sym}{row[1]:,.0f}")
    else:
        await query.edit_message_text("❌ Долг не найден или уже закрыт.")
    conn.close()
    return MAIN_MENU

async def debt_type_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Выбрали тип: партнёр или внешний."""
    query = update.callback_query
    await query.answer()
    dtype = query.data.replace("dtype_", "")
    ctx.user_data["debt_type"] = dtype

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Мне должны",  callback_data="ddir_they_owe")],
        [InlineKeyboardButton("📤 Я должен(а)", callback_data="ddir_i_owe")],
    ])
    await query.edit_message_text("*В какую сторону долг?*", parse_mode="Markdown", reply_markup=kb)
    return DEBT_DIRECTION

async def debt_direction_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Выбрали направление."""
    query = update.callback_query
    await query.answer()
    ctx.user_data["debt_direction"] = query.data.replace("ddir_", "")

    dtype = ctx.user_data.get("debt_type", "external")
    if dtype == "partner":
        # Имя не нужно — это партнёр
        ctx.user_data["debt_who"] = "партнёр"
        await query.edit_message_text("💰 *Сколько?*\nВведи сумму:", parse_mode="Markdown")
        return DEBT_AMOUNT_S
    else:
        await query.edit_message_text("👤 *Кто?* Введи имя или @username:", parse_mode="Markdown")
        return DEBT_WHO

async def debt_who(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_back(update, ctx): return MAIN_MENU
    ctx.user_data["debt_who"] = update.message.text.strip()
    await update.message.reply_text("💰 *Сколько?* Введи сумму:", parse_mode="Markdown", reply_markup=back_kb())
    return DEBT_AMOUNT_S

async def debt_amount_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_back(update, ctx): return MAIN_MENU
    try:
        ctx.user_data["debt_amount"] = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Введи число.")
        return DEBT_AMOUNT_S
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("$ USD",     callback_data="dcur:USD"),
        InlineKeyboardButton("€ EUR",     callback_data="dcur:EUR"),
    ],[
        InlineKeyboardButton("💵 Нал $", callback_data="dcur:CASH_USD"),
        InlineKeyboardButton("💶 Нал €", callback_data="dcur:CASH_EUR"),
    ]])
    await update.message.reply_text("💱 *Валюта:*", parse_mode="Markdown", reply_markup=keyboard)
    return DEBT_CURRENCY

async def debt_currency_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["debt_currency"] = query.data.replace("dcur:", "")
    await query.edit_message_text("📝 Комментарий _(или `-` пропустить)_:", parse_mode="Markdown")
    return DEBT_DESC_S

async def debt_desc_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_back(update, ctx): return MAIN_MENU
    desc = update.message.text.strip()
    if desc == "-": desc = ""

    dtype     = ctx.user_data.get("debt_type", "external")
    direction = ctx.user_data.get("debt_direction", "they_owe")
    who       = ctx.user_data.get("debt_who", "")
    amount    = ctx.user_data.get("debt_amount", 0)
    currency  = ctx.user_data.get("debt_currency", "EUR")

    conn = get_db()
    conn.execute(
        "INSERT INTO debts (debt_type, direction, from_user, amount, currency, description, date) VALUES (?,?,?,?,?,?,?)",
        (dtype, direction, who, amount, currency, desc, date.today().isoformat())
    )
    conn.commit(); conn.close()

    sym = SYM.get(currency, "€")
    direction_str = "тебе должны" if direction == "they_owe" else "ты должен(а)"
    type_str = "партнёру" if dtype == "partner" else who

    await update.message.reply_text(
        f"✅ *Записано!*\n"
        f"{'👫' if dtype=='partner' else '🌍'} {type_str} — {direction_str}\n"
        f"*{sym}{amount:,.0f}*" + (f"\n📝 _{desc}_" if desc else ""),
        parse_mode="Markdown", reply_markup=main_kb()
    )
    ctx.user_data.clear()
    return MAIN_MENU

# ── Кошелёк ───────────────────────────────────────────────────
async def wallet_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT key, label, currency, amount FROM wallets")
    rows = cur.fetchall(); conn.close()
    keyboard = [
        [InlineKeyboardButton(f"{label} {cur_}{amt:,.0f}", callback_data=f"wlt:{key}")]
        for key, label, cur_, amt in rows
    ]
    await update.message.reply_text(
        "🏦 *Выбери кошелёк:*",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    await update.message.reply_text("Или назад:", reply_markup=back_kb())
    return WALLET_CHOOSE

async def wallet_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["wallet_key"] = query.data.replace("wlt:", "")
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Пополнить",        callback_data="wop:add"),
        InlineKeyboardButton("➖ Снять",            callback_data="wop:sub"),
    ],[
        InlineKeyboardButton("✏️ Установить сумму", callback_data="wop:set"),
    ]])
    await query.edit_message_text("Что сделать?", reply_markup=keyboard)
    return WALLET_OP

async def wallet_op(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["wallet_op"] = query.data.replace("wop:", "")
    labels = {"add": "пополнить на", "sub": "снять", "set": "установить"}
    await query.edit_message_text(f"Введи сумму ({labels[ctx.user_data['wallet_op']]}):")
    return WALLET_AMOUNT_S

async def wallet_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_back(update, ctx): return MAIN_MENU
    try:
        amount = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Введи число.")
        return WALLET_AMOUNT_S
    key = ctx.user_data["wallet_key"]
    op  = ctx.user_data["wallet_op"]
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT label, currency, amount FROM wallets WHERE key=?", (key,))
    label, cur_, old = cur.fetchone()
    new = old + amount if op == "add" else (old - amount if op == "sub" else amount)
    conn.execute("UPDATE wallets SET amount=? WHERE key=?", (new, key))
    conn.commit(); conn.close()
    await update.message.reply_text(
        f"✅ *{label}* обновлён\n{cur_}{old:,.0f} → *{cur_}{new:,.0f}*",
        parse_mode="Markdown", reply_markup=main_kb()
    )
    ctx.user_data.clear()
    return MAIN_MENU

# ── Аналитика ─────────────────────────────────────────────────
def analytics_nav_kb(year, month):
    py, pm = prev_month(year, month)
    ny, nm = next_month(year, month)
    today = date.today()
    row1 = [InlineKeyboardButton(f"◀️ {month_name_ru(pm)[:3]}", callback_data=f"an_month:{py}:{pm}")]
    row1.append(InlineKeyboardButton(f"📅 {month_name_ru(month)[:3]} {year}", callback_data="noop"))
    if (ny, nm) <= (today.year, today.month):
        row1.append(InlineKeyboardButton(f"{month_name_ru(nm)[:3]} ▶️", callback_data=f"an_month:{ny}:{nm}"))
    row2 = [
        InlineKeyboardButton("⚖️ Сравнить с пред.", callback_data=f"an_compare:{year}:{month}"),
        InlineKeyboardButton("📆 Год",              callback_data=f"an_year:{year}"),
    ]
    return InlineKeyboardMarkup([row1, row2])

def format_month_stats(year, month):
    cats  = month_by_category_for(year, month)
    income_eur = usd_to_eur(INCOME_USD)
    total = sum(cats.values()) if cats else 0.0
    left  = income_eur - FIXED_TOTAL - total
    lines = [f"📈 *{month_name_ru(month)} {year}*\n"]
    if cats:
        for cat, amt in sorted(cats.items(), key=lambda x: -x[1]):
            pct = amt / total * 100
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            lines.append(f"{cat}\n`{bar}` {pct:.0f}% €{c(amt)}\n")
        lines.append(f"💶 *Потрачено: €{c(total)}*")
        lines.append(f"{'🟢' if left > 0 else '🔴'} Остаток: *€{c(left)}*")
    else:
        lines.append("📭 Трат нет")
    return "\n".join(lines)

async def show_analytics(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    await update.message.reply_text(
        format_month_stats(today.year, today.month),
        parse_mode="Markdown",
        reply_markup=analytics_nav_kb(today.year, today.month)
    )

async def analytics_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "noop": return

    if data.startswith("an_month:"):
        _, year, month = data.split(":")
        year, month = int(year), int(month)
        await query.edit_message_text(
            format_month_stats(year, month), parse_mode="Markdown",
            reply_markup=analytics_nav_kb(year, month)
        )
    elif data.startswith("an_compare:"):
        _, year, month = data.split(":")
        year, month = int(year), int(month)
        py, pm = prev_month(year, month)
        cats_cur  = month_by_category_for(year, month)
        cats_prev = month_by_category_for(py, pm)
        total_cur  = sum(cats_cur.values())  if cats_cur  else 0.0
        total_prev = sum(cats_prev.values()) if cats_prev else 0.0
        diff = total_cur - total_prev
        all_cats = set(list(cats_cur.keys()) + list(cats_prev.keys()))
        lines = [f"⚖️ *{month_name_ru(pm)[:3]} vs {month_name_ru(month)[:3]} {year}*\n"]
        for cat in sorted(all_cats):
            a = cats_prev.get(cat, 0.0)
            b = cats_cur.get(cat, 0.0)
            delta = b - a
            short = cat.split(" ", 1)[-1][:12]
            sign  = "+" if delta > 0 else ""
            lines.append(f"`{short:<12}` €{c(a):>5} → €{c(b):>5} {sign}{c(delta)}")
        lines.append(f"\n💶 *{month_name_ru(pm)}: €{total_prev:.0f}*")
        lines.append(f"💶 *{month_name_ru(month)}: €{total_cur:.0f}*")
        lines.append(f"{'📈' if diff > 0 else '📉'} Разница: *{'+' if diff>0 else ''}{c(diff)} €*")
        back = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data=f"an_month:{year}:{month}")]])
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=back)
    elif data.startswith("an_year:"):
        _, year = data.split(":")
        year = int(year)
        income_eur = usd_to_eur(INCOME_USD)
        lines = [f"📆 *Годовой обзор — {year}*\n"]
        grand = 0.0
        for m in range(1, 13):
            total = month_expenses_by(year, m)
            grand += total
            if total > 0:
                saved = income_eur - FIXED_TOTAL - total
                bar   = "█" * min(int(total / 200), 15)
                lines.append(f"`{month_name_ru(m)[:3]}` `{bar:<15}` €{c(total)} {'🟢' if saved>0 else '🔴'}€{c(abs(saved))}")
            else:
                lines.append(f"`{month_name_ru(m)[:3]}` —")
        lines.append(f"\n💶 *Итого: €{c(grand)}* | Среднее: *€{c(grand/12)}/мес*")
        back = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data=f"an_month:{year}:{date.today().month}")]])
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=back)

# ── После записи траты ────────────────────────────────────────
async def after_record_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "go_main":
        ctx.user_data.clear()
        await query.edit_message_reply_markup(reply_markup=None)
        await ctx.bot.send_message(query.message.chat_id, "🏠 Главное меню", reply_markup=main_kb())
        return MAIN_MENU
    elif query.data == "add_more":
        await query.edit_message_reply_markup(reply_markup=None)
        keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat:{cat}")] for cat in CATEGORIES]
        keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="go_main")])
        await ctx.bot.send_message(
            query.message.chat_id, "📂 *Выбери категорию:*",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        return ADD_CATEGORY

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await go_home(update, "❌ Отменено.")
    return MAIN_MENU

# ── main ──────────────────────────────────────────────────────
def main():
    init_db()
    sheets_init_headers()
    app = Application.builder().token(BOT_TOKEN).build()

    menu_pattern = "^(💸 Добавить трату|📊 Обзор|🏦 Накопления|📈 Аналитика|🤝 Долги|📜 История|🏠 Фикс. расходы|⚙️ Кошелёк)$"

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex(menu_pattern), menu_router),
        ],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex(menu_pattern), menu_router),
                CallbackQueryHandler(after_record_callback, pattern="^(add_more|go_main)$"),
            ],
            ADD_CATEGORY: [
                CallbackQueryHandler(add_category_chosen, pattern="^cat:"),
                CallbackQueryHandler(after_record_callback, pattern="^(add_more|go_main)$"),
                MessageHandler(filters.Regex("^🔙 Главное меню$"), cancel),
            ],
            ADD_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_amount)],
            ADD_DESC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_desc),
                CallbackQueryHandler(after_record_callback, pattern="^(add_more|go_main)$"),
            ],

            # ── Долги ────────────────────────────────────────
            DEBT_TYPE: [
                CallbackQueryHandler(debt_new_cb,        pattern="^debt_new$"),
                CallbackQueryHandler(debt_settle_cb,     pattern="^debt_settle:"),
                CallbackQueryHandler(debt_type_chosen,   pattern="^dtype_"),
                MessageHandler(filters.Regex("^🔙 Главное меню$"), cancel),
            ],
            DEBT_DIRECTION: [
                CallbackQueryHandler(debt_direction_chosen, pattern="^ddir_"),
                MessageHandler(filters.Regex("^🔙 Главное меню$"), cancel),
            ],
            DEBT_WHO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, debt_who),
            ],
            DEBT_AMOUNT_S: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, debt_amount_handler),
            ],
            DEBT_CURRENCY: [
                CallbackQueryHandler(debt_currency_handler, pattern="^dcur:"),
            ],
            DEBT_DESC_S: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, debt_desc_handler),
            ],

            # ── Кошелёк ──────────────────────────────────────
            WALLET_CHOOSE: [
                CallbackQueryHandler(wallet_chosen, pattern="^wlt:"),
                MessageHandler(filters.Regex("^🔙 Главное меню$"), cancel),
            ],
            WALLET_OP:     [CallbackQueryHandler(wallet_op,     pattern="^wop:")],
            WALLET_AMOUNT_S:[MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_amount)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^🔙 Главное меню$"), cancel),
        ],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("ping", lambda u, c: u.message.reply_text("pong")))
    app.add_handler(CallbackQueryHandler(analytics_callback,     pattern="^(an_month:|an_compare:|an_year:|noop)"))
    app.add_handler(CallbackQueryHandler(delete_expense_callback, pattern="^del_expense:"))
    app.add_handler(conv)

    print("🤖 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
