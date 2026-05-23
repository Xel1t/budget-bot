import os
import sqlite3
import requests
from datetime import datetime, date
from calendar import monthrange
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

DB_PATH = os.environ.get("DB_PATH", "budget.db")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
EXCHANGE_API_KEY = os.environ.get("EXCHANGE_API_KEY", "")

USER1 = os.environ.get("USER1", "tim")
USER2 = os.environ.get("USER2", "masha")

CATEGORIES = [
    "🛒 Продукты", "🍽 Рестораны", "🛵 Доставка", "☕️ Кофик",
    "💪 Спортзал", "🏥 Страховка", "🏠 Дом",
    "👗 Одежда", "📦 Онлайн-покупки", "🎁 Подарки", "📝 Другое",
]

WALLETS_DEFAULT = [
    ("нз_брат",    "НЗ у брата",       "$",  6000.0),
    ("крипто_хол", "Крипто холодный",  "$",  3000.0),
    ("крипто_теп", "Крипто тёплый",    "$",  1500.0),
    ("оборот",     "В обороте",        "$",  4000.0),
    ("нал_usd",    "Наличка $",        "$",  0.0),
    ("нал_eur",    "Наличка €",        "€",  0.0),
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

# ── Conversation states ───────────────────────────────────────
(
    MAIN_MENU,
    ADD_CATEGORY, ADD_AMOUNT, ADD_DESC,
    DEBT_WHO, DEBT_AMOUNT_S, DEBT_CURRENCY, DEBT_DESC_S,
    WALLET_CHOOSE, WALLET_OP, WALLET_AMOUNT_S,
) = range(11)

# ── Keyboards ─────────────────────────────────────────────────
def main_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💸 Добавить трату"),  KeyboardButton("📊 Обзор")],
        [KeyboardButton("🏦 Накопления"),       KeyboardButton("📈 Статистика")],
        [KeyboardButton("🤝 Долги"),            KeyboardButton("📜 История")],
        [KeyboardButton("🏠 Фикс. расходы"),   KeyboardButton("⚙️ Кошелёк")],
    ], resize_keyboard=True)

def back_kb():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🔙 Главное меню")]],
        resize_keyboard=True
    )

MAIN_BUTTONS = {
    "💸 Добавить трату", "📊 Обзор", "🏦 Накопления",
    "📈 Статистика", "🤝 Долги", "📜 История",
    "🏠 Фикс. расходы", "⚙️ Кошелёк",
}

# ── DB ────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT, amount_eur REAL,
        category TEXT, description TEXT, date TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS debts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user TEXT, amount REAL, currency TEXT,
        description TEXT, date TEXT, settled INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS wallets (
        key TEXT PRIMARY KEY, label TEXT, currency TEXT, amount REAL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS exchange_cache (
        pair TEXT PRIMARY KEY, rate REAL, updated TEXT
    )''')
    for key, label, cur, amount in WALLETS_DEFAULT:
        c.execute("INSERT OR IGNORE INTO wallets VALUES (?,?,?,?)", (key, label, cur, amount))
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(DB_PATH)

# ── Exchange ──────────────────────────────────────────────────
def get_usd_to_eur() -> float:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT rate, updated FROM exchange_cache WHERE pair='USD_EUR'")
    row = c.fetchone()
    conn.close()
    if row:
        if (datetime.now() - datetime.fromisoformat(row[1])).seconds < 3600:
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
        conn.commit()
        conn.close()
        return rate
    except Exception:
        return 0.92

def usd_to_eur(usd): return round(usd * get_usd_to_eur(), 2)
def eur_to_usd(eur):
    r = get_usd_to_eur()
    return round(eur / r, 2) if r else round(eur / 0.92, 2)

def get_username(update: Update) -> str:
    return (update.effective_user.username or update.effective_user.first_name or "unknown").lower()

def month_expenses_eur() -> float:
    today = date.today()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT SUM(amount_eur) FROM expenses WHERE date LIKE ?", (f"{today.year}-{today.month:02d}%",))
    row = c.fetchone(); conn.close()
    return row[0] or 0.0

def month_by_category() -> dict:
    today = date.today()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT category, SUM(amount_eur) FROM expenses WHERE date LIKE ? GROUP BY category",
              (f"{today.year}-{today.month:02d}%",))
    rows = c.fetchall(); conn.close()
    return {r[0]: r[1] for r in rows}

def total_savings_usd() -> float:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT currency, amount FROM wallets")
    rows = c.fetchall(); conn.close()
    total = 0.0
    for cur, amt in rows:
        total += amt if cur == "$" else eur_to_usd(amt)
    return round(total, 2)

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
        reply_markup=main_kb(),
        parse_mode="Markdown"
    )
    return MAIN_MENU

# ── Router — главное меню ─────────────────────────────────────
async def menu_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "💸 Добавить трату":
        return await add_start(update, ctx)
    elif text == "📊 Обзор":
        await show_overview(update, ctx); return MAIN_MENU
    elif text == "🏦 Накопления":
        await show_savings(update, ctx); return MAIN_MENU
    elif text == "📈 Статистика":
        await show_stats(update, ctx); return MAIN_MENU
    elif text == "🤝 Долги":
        await show_debts_menu(update, ctx); return MAIN_MENU
    elif text == "📜 История":
        await show_history(update, ctx); return MAIN_MENU
    elif text == "🏠 Фикс. расходы":
        await show_fixed(update, ctx); return MAIN_MENU
    elif text == "⚙️ Кошелёк":
        return await wallet_start(update, ctx)
    return MAIN_MENU

# ── Добавить трату ────────────────────────────────────────────
async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat:{cat}")] for cat in CATEGORIES]
    await update.message.reply_text(
        "📂 *Выбери категорию:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    await update.message.reply_text("Или нажми назад:", reply_markup=back_kb())
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
    conn = get_db()
    conn.execute(
        "INSERT INTO expenses (username, amount_eur, category, description, date) VALUES (?,?,?,?,?)",
        (username, ctx.user_data["amount_eur"], ctx.user_data["category"], desc, date.today().isoformat())
    )
    conn.commit(); conn.close()
    spent = month_expenses_eur()
    left = usd_to_eur(INCOME_USD) - FIXED_TOTAL - spent
    await update.message.reply_text(
        f"✅ *Записано!*\n"
        f"👤 @{username}  |  {ctx.user_data['category']}\n"
        f"💶 €{ctx.user_data['amount_eur']}{ctx.user_data.get('amount_note','')}"
        + (f"\n📝 _{desc}_" if desc else "") +
        f"\n\n📊 Потрачено: *€{spent:.0f}*\n"
        f"{'🟢' if left > 0 else '🔴'} Остаток: *€{left:.0f}*",
        parse_mode="Markdown", reply_markup=main_kb()
    )
    ctx.user_data.clear()
    return MAIN_MENU

# ── Обзор ─────────────────────────────────────────────────────
async def show_overview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rate = get_usd_to_eur()
    income_eur = usd_to_eur(INCOME_USD)
    spent = month_expenses_eur()
    left = income_eur - FIXED_TOTAL - spent
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
        f"📅 До конца месяца: {days_left} дн. → *€{daily:.0f}/день*\n\n"
        f"🏦 Накопления: *${sav_usd:,.0f}* ≈ *€{usd_to_eur(sav_usd):,.0f}*",
        parse_mode="Markdown", reply_markup=main_kb()
    )

# ── Накопления ────────────────────────────────────────────────
async def show_savings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT key, label, currency, amount FROM wallets")
    rows = c.fetchall(); conn.close()
    rate = get_usd_to_eur()
    lines = ["🏦 *Накопления по кошелькам:*\n"]
    total_usd = 0.0
    for _, label, cur, amt in rows:
        if cur == "$":
            equiv = f"≈ €{usd_to_eur(amt):,.0f}"
            total_usd += amt
        else:
            equiv = f"≈ ${eur_to_usd(amt):,.0f}"
            total_usd += eur_to_usd(amt)
        lines.append(f"• {label}: *{cur}{amt:,.0f}*  {equiv}")
    lines.append(f"\n💰 *Всего: ~${total_usd:,.0f}* ≈ *€{usd_to_eur(total_usd):,.0f}*")
    lines.append(f"💱 Курс: 1 USD = {rate:.4f} EUR")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

# ── Статистика ────────────────────────────────────────────────
async def show_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cats = month_by_category()
    today = date.today()
    if not cats:
        await update.message.reply_text("📭 Трат за этот месяц ещё нет.", reply_markup=main_kb())
        return
    total = sum(cats.values())
    lines = [f"📈 *Статистика — {today.strftime('%B %Y')}*\n"]
    for cat, amt in sorted(cats.items(), key=lambda x: -x[1]):
        pct = amt / total * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        lines.append(f"{cat}\n`{bar}` {pct:.0f}%  €{amt:.0f}\n")
    lines.append(f"💶 *Итого: €{total:.0f}*")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

# ── История ───────────────────────────────────────────────────
async def show_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, amount_eur, category, description, date FROM expenses ORDER BY id DESC LIMIT 10")
    rows = c.fetchall(); conn.close()
    if not rows:
        await update.message.reply_text("📭 Записей пока нет.", reply_markup=main_kb())
        return
    lines = ["📜 *Последние 10 трат:*\n"]
    for user, amt, cat, desc, dt in rows:
        lines.append(f"`{dt[5:]}` {cat} — *€{amt:.0f}*  @{user}" + (f"\n    _{desc}_" if desc else ""))
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

# ── Фикс расходы ──────────────────────────────────────────────
async def show_fixed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = ["🏠 *Фиксированные расходы:*\n"]
    for name, amt in FIXED_EXPENSES:
        lines.append(f"• {name}: *€{amt:.0f}*")
    lines.append(f"\n💶 *Итого: €{FIXED_TOTAL:.0f}/мес*")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

# ── Долги ─────────────────────────────────────────────────────
async def show_debts_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, from_user, amount, currency, description, date FROM debts WHERE settled=0 ORDER BY id DESC")
    rows = c.fetchall(); conn.close()
    sym_map = {"USD": "$", "EUR": "€", "CASH_USD": "$ нал", "CASH_EUR": "€ нал"}

    keyboard = [
        [InlineKeyboardButton("➕ Записать долг", callback_data="debt:new")],
    ]
    if rows:
        for rid, who, amt, cur, desc, dt in rows:
            sym = sym_map.get(cur, "")
            label = f"#{rid} {who} — {sym}{amt:,.0f} ✅ закрыть"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"debt:settle:{rid}")])

    text = "🤝 *Долги*\n\n"
    if rows:
        lines = []
        for rid, who, amt, cur, desc, dt in rows:
            sym = sym_map.get(cur, "")
            lines.append(f"#{rid} | {who} — *{sym}{amt:,.0f}*  `{dt[5:]}`" + (f"\n    _{desc}_" if desc else ""))
        text += "\n".join(lines)
    else:
        text += "✅ Долгов нет!"

    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def debt_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "debt:new":
        await query.edit_message_reply_markup(reply_markup=None)
        await ctx.bot.send_message(
            query.message.chat_id,
            "🤝 *Кто взял деньги?* Введи имя или @username:",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
        return DEBT_WHO

    if data.startswith("debt:settle:"):
        debt_id = int(data.split(":")[-1])
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT from_user, amount, currency FROM debts WHERE id=? AND settled=0", (debt_id,))
        row = c.fetchone()
        if row:
            conn.execute("UPDATE debts SET settled=1 WHERE id=?", (debt_id,))
            conn.commit()
            sym = {"USD": "$", "EUR": "€", "CASH_USD": "$ нал", "CASH_EUR": "€ нал"}.get(row[2], "")
            await query.edit_message_text(f"✅ Долг #{debt_id} закрыт!\n{row[0]} вернул {sym}{row[1]:,.0f}")
        conn.close()
    return MAIN_MENU

async def debt_who(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_back(update, ctx): return MAIN_MENU
    ctx.user_data["debt_who"] = update.message.text.strip()
    await update.message.reply_text("💰 Сколько?", reply_markup=back_kb())
    return DEBT_AMOUNT_S

async def debt_amount_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_back(update, ctx): return MAIN_MENU
    try:
        ctx.user_data["debt_amount"] = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Введи число.")
        return DEBT_AMOUNT_S
    keyboard = [[
        InlineKeyboardButton("$ USD",    callback_data="dcur:USD"),
        InlineKeyboardButton("€ EUR",    callback_data="dcur:EUR"),
    ],[
        InlineKeyboardButton("💵 Нал $", callback_data="dcur:CASH_USD"),
        InlineKeyboardButton("💶 Нал €", callback_data="dcur:CASH_EUR"),
    ]]
    await update.message.reply_text("Валюта:", reply_markup=InlineKeyboardMarkup(keyboard))
    return DEBT_CURRENCY

async def debt_currency_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["debt_currency"] = query.data.replace("dcur:", "")
    await query.edit_message_text("📝 Комментарий (или `-` пропустить):")
    return DEBT_DESC_S

async def debt_desc_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_back(update, ctx): return MAIN_MENU
    desc = update.message.text.strip()
    if desc == "-": desc = ""
    conn = get_db()
    conn.execute(
        "INSERT INTO debts (from_user, amount, currency, description, date) VALUES (?,?,?,?,?)",
        (ctx.user_data["debt_who"], ctx.user_data["debt_amount"],
         ctx.user_data["debt_currency"], desc, date.today().isoformat())
    )
    conn.commit(); conn.close()
    sym = {"USD": "$", "EUR": "€", "CASH_USD": "$ нал", "CASH_EUR": "€ нал"}.get(ctx.user_data["debt_currency"], "")
    await update.message.reply_text(
        f"✅ *Записано:* {ctx.user_data['debt_who']} взял *{sym}{ctx.user_data['debt_amount']:,.0f}*"
        + (f"\n📝 _{desc}_" if desc else ""),
        parse_mode="Markdown", reply_markup=main_kb()
    )
    ctx.user_data.clear()
    return MAIN_MENU

# ── Кошелёк ───────────────────────────────────────────────────
async def wallet_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT key, label, currency, amount FROM wallets")
    rows = c.fetchall(); conn.close()
    keyboard = [
        [InlineKeyboardButton(f"{label}  {cur}{amt:,.0f}", callback_data=f"wlt:{key}")]
        for key, label, cur, amt in rows
    ]
    await update.message.reply_text(
        "🏦 *Выбери кошелёк:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    await update.message.reply_text("Или назад:", reply_markup=back_kb())
    return WALLET_CHOOSE

async def wallet_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["wallet_key"] = query.data.replace("wlt:", "")
    keyboard = [[
        InlineKeyboardButton("➕ Пополнить", callback_data="wop:add"),
        InlineKeyboardButton("➖ Снять",     callback_data="wop:sub"),
    ],[
        InlineKeyboardButton("✏️ Установить сумму", callback_data="wop:set")
    ]]
    await query.edit_message_text("Что сделать?", reply_markup=InlineKeyboardMarkup(keyboard))
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
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT label, currency, amount FROM wallets WHERE key=?", (key,))
    label, cur, old = c.fetchone()
    new = old + amount if op == "add" else (old - amount if op == "sub" else amount)
    conn.execute("UPDATE wallets SET amount=? WHERE key=?", (new, key))
    conn.commit(); conn.close()
    await update.message.reply_text(
        f"✅ *{label}* обновлён\n{cur}{old:,.0f} → *{cur}{new:,.0f}*",
        parse_mode="Markdown", reply_markup=main_kb()
    )
    ctx.user_data.clear()
    return MAIN_MENU

# ── cancel ────────────────────────────────────────────────────
async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await go_home(update, "❌ Отменено.")
    return MAIN_MENU

# ── main ──────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^(💸 Добавить трату|📊 Обзор|🏦 Накопления|📈 Статистика|🤝 Долги|📜 История|🏠 Фикс. расходы|⚙️ Кошелёк)$"), menu_router),
        ],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex("^(💸 Добавить трату|📊 Обзор|🏦 Накопления|📈 Статистика|🤝 Долги|📜 История|🏠 Фикс. расходы|⚙️ Кошелёк)$"), menu_router),
            ],
            ADD_CATEGORY: [
                CallbackQueryHandler(add_category_chosen, pattern="^cat:"),
                MessageHandler(filters.Regex("^🔙 Главное меню$"), cancel),
            ],
            ADD_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_amount),
            ],
            ADD_DESC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_desc),
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
            WALLET_CHOOSE: [
                CallbackQueryHandler(wallet_chosen, pattern="^wlt:"),
                MessageHandler(filters.Regex("^🔙 Главное меню$"), cancel),
            ],
            WALLET_OP: [
                CallbackQueryHandler(wallet_op, pattern="^wop:"),
            ],
            WALLET_AMOUNT_S: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_amount),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^🔙 Главное меню$"), cancel),
        ],
        allow_reentry=True,
    )

    # Debt callback outside conversation (settle button)
    app.add_handler(CallbackQueryHandler(debt_callback, pattern="^debt:"))
    app.add_handler(conv)

    print("🤖 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
